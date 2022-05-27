from collections import Counter
from collections import defaultdict
from inspect import signature
from typing import Callable
from typing import Iterable
from typing import Optional
from typing import Type
from typing import TypedDict

from django.conf import settings
from django.core import serializers
from django.core.serializers.base import DeserializedObject
from django.db.models import Field
from django.db.models import Model
from django.db.models import QuerySet

from dolly.utils import get_all_dependencies
from dolly.utils import get_all_related_models
from dolly.utils import get_concrete_superclasses
from dolly.utils import get_fk_fields
from dolly.utils import get_m2m_fields
from dolly.utils import get_nat_key
from dolly.utils import is_pointer
from dolly.utils import topological_sort


class LogAction(TypedDict):
    act: str
    mod: Optional[str]
    msg: str


_marker = object()


class BaseRemapper:
    log: list[LogAction]
    data: dict[Type[Model], set[Model]]
    allow_same_pk: dict[Type[Model], set[int]]
    # Contained dict: old pk as key
    tracked_data: dict[Type[Model], dict[int, Model]]
    # Contained dict: new pk as key, old pk as value
    pk_map: dict[Type[Model], dict[int, int]]
    remapped_objs: dict[Type[Model], set[Model]]
    # actions
    pre_save_actions: dict[Type[Model], list[Callable]]
    post_save_actions: dict[Type[Model], list[Callable]]
    # Stuff we don't care about that won't have an effect on relations
    clear_model_attrs: dict[Type[Model], set[str]]

    def __init__(self):
        self.log = []
        self.logging_enabled = getattr(settings, "DEBUG", False)
        self.tracked_data = defaultdict(dict)
        self.pk_map = defaultdict(dict)
        self.allow_same_pk = defaultdict(set)
        self.remapped_objs = defaultdict(set)
        self.pre_save_actions = defaultdict(list)
        self.post_save_actions = defaultdict(list)
        self.clear_model_attrs = defaultdict(set)

    def add_pre_save(self, model: Type[Model], _callable: Callable):
        assert issubclass(model, Model)
        assert validate_callable(_callable)
        self.pre_save_actions[model].append(_callable)

    def add_post_save(self, model: Type[Model], _callable: Callable):
        assert issubclass(model, Model)
        assert validate_callable(_callable)
        self.post_save_actions[model].append(_callable)

    def run_pre_save(self, *values: Model):
        if not values:
            return
        model = values[0].__class__
        if model in self.pre_save_actions:
            actions = self.pre_save_actions[model]
            self.add_log(
                mod=model,
                act="run_pre_save",
                msg=f"Actions: {', '.join(str(x) for x in actions)}",
            )
            for action in actions:
                action(self, *values)

    def run_post_save(self, *values: Model):
        if not values:
            return
        model = values[0].__class__
        if model in self.post_save_actions:
            actions = self.post_save_actions[model]
            self.add_log(
                mod=model,
                act="run_post_save",
                msg=f"Actions: {', '.join(str(x) for x in actions)}",
            )
            for action in actions:
                action(self, *values)

    def add_clear_attrs(self, model: Type[Model], *attrs):
        """
        Clear specified attributes for model.
        Remember to specify subclasses too + that some models might not be nullable.

        >>> from dolly_testing.models import Proposal
        >>> remapper = BaseRemapper()
        >>> remapper.add_clear_attrs(Proposal, 'meeting_group')
        >>> remapper.clear_model_attrs.get(Proposal)
        {'meeting_group'}
        >>> remapper.add_clear_attrs(Proposal, 'meeting')
        Traceback (most recent call last):
        ...
        ValueError: meeting can't be cleared automatically - it's not allowed to be null.

        """
        allowed_names = set(
            f.name for f in get_fk_fields(model) | get_m2m_fields(model)
        )
        if missing := set(attrs) - allowed_names:
            raise ValueError(
                f"{model} doesn't have m2m or fk fields called: {','.join(missing)}"
            )
        for f in [f for f in get_fk_fields(model) if f.name in attrs]:
            if not f.null:
                raise ValueError(
                    f"{f.name} can't be cleared automatically - it's not allowed to be null."
                )
        self.clear_model_attrs[model].update(attrs)

    def add_log(self, *, mod: Optional[Type[Model]], act: str, msg: str):
        if self.logging_enabled:
            if isinstance(mod, type) and issubclass(mod, Model):
                mod_name = get_nat_key(mod)
            elif isinstance(mod, str):
                mod_name = mod
            elif mod is None:
                mod_name = None
            else:
                raise TypeError(f"{mod} must be a string or a Django model")
            self.log.append(LogAction(act=act, mod=mod_name, msg=msg))

    def sort(self) -> list[Type[Model]]:
        """
        Sort data in relevant import order. Returns sorted order too.
        """
        assert self.data
        ignorable_ordering_models = get_all_related_models(*self.data.keys()) - set(
            self.data.keys()
        )
        dependencies = get_all_dependencies(
            *self.data.keys(), ignore=ignorable_ordering_models
        )
        order = list(topological_sort(dependencies))
        self.add_log(
            mod=None,
            act="sort",
            msg=f"Order set to: {', '.join(get_nat_key(x) for x in order)}",
        )
        self.data = {model: self.data[model] for model in order if self.data.get(model)}
        return order

    def reset_obj(self, inst: Model):
        inst.pk = None
        inst.id = None
        inst._state.adding = True

    def same_pk_allowed(self, model: Type[Model], pk) -> bool:
        return pk in self.allow_same_pk.get(model, ())

    def track_obj(self, obj: Model, old_pk: int = _marker, allow_same=False):
        """
        Add and object that either will change pk later on or has a pk that we can't use to find the object.
        This will happen in the following situations:
        -   This is an object waiting to be cloned. When cloning, it will be assigned a new pk
        -   This object existed in the database and will be reused via remapping. For instance a user
            that was found via email. The imported object may have another pk in that case.
        """
        if old_pk is _marker:
            old_pk = obj.pk
        assert isinstance(old_pk, int)
        tracked_class = self.tracked_data[obj.__class__]
        if old_pk in tracked_class:
            raise ValueError(
                f"pk {old_pk} already found in tracked data for {obj.__class__}"
            )
        tracked_class[old_pk] = obj
        if allow_same:
            self.allow_same_pk[obj.__class__].add(old_pk)

    def get_remap_obj_from_field(self, inst: Model, field: Field) -> Optional[Model]:
        """
        Get object from another models foreign key field
        """
        id_name = f"{field.name}_id"
        curr_val = getattr(inst, id_name)
        if curr_val is not None:
            return self.get_remap_obj(field.related_model, curr_val)
        return None

    def get_remap_obj_from_existing(self, inst: Model):
        assert inst.pk is not None, f"{inst} has no pk"
        return self.get_remap_obj(inst.__class__, inst.pk)

    def get_remap_obj(self, model: Type[Model], old_pk: int) -> Model:
        if model not in self.tracked_data:
            raise ValueError(f"{model} not in tracked_data")
        remap_to = self.tracked_data[model][old_pk]
        assert remap_to.pk is not None, f"{remap_to} hasn't been saved yet."
        if remap_to.pk == old_pk:
            if not self.same_pk_allowed(model, old_pk):
                raise ValueError(
                    f"remap_to for {model} with pk {old_pk} returned an object with the same pk as the original. "
                    f"Maybe they got called in the wrong order? "
                    f"If the object is allowed to keep pk, set allow_same=True when adding it with track_obj."
                )
        self.remapped_objs[model].add(remap_to)
        return remap_to

    def get_old_pk(self, inst: Model, default=None):
        assert inst.pk
        return self.pk_map.get(inst.__class__, {}).get(inst.pk, default)

    def register_new_pk(self, inst: Model, old_pk: int):
        assert inst.pk is not None
        assert isinstance(old_pk, int)
        assert old_pk in self.tracked_data[inst.__class__], (
            f"PK {old_pk} not found in tracked_data. "
            f"Before clearing the old pk, make sure to add the instance via track_obj()"
        )
        assert (
            inst.pk not in self.pk_map[inst.__class__]
        ), f"PK {old_pk} already registered. {inst} may be a duplicate with the same pk."
        self.pk_map[inst.__class__][inst.pk] = old_pk

    def is_new(self, inst: Model) -> bool:
        """
        Is this instance newly created via a remapper of some kind?
        """
        return (
            inst.pk
            and inst.__class__ in self.pk_map
            and inst.pk in self.pk_map[inst.__class__]
        )

    def report_remapping(self, *values: Model) -> set[Model]:
        if not values:
            return set()
        model = values[0].__class__
        assert issubclass(model, Model)
        if model not in self.data:
            return set()
        not_remapped = self.remapped_objs[model] - set(values)
        if not_remapped:
            self.add_log(
                mod=model,
                act="report_remapping",
                msg=f"{len(not_remapped)} item(s) were never used for remapping.",
            )
        return not_remapped


class LiveCloner(BaseRemapper):
    """
    Clones models already in the database.

    Any data passed here should be data that you want to clone. If it's not within the data, the relation will be kept.
    """

    data: dict[Type[Model], set[Model]]
    m2m_data: dict[Type[Model], dict[int, dict[str, list[int]]]]

    # FIXME: Warn when resetting attributes makes cloning something pointless
    # FIXME: Maybe be smarter when fetching something in the middle of a tree

    def __init__(
        self,
        *,
        data: dict[Type[Model], set[Model]],
    ):
        super().__init__()
        self.data = data
        self.m2m_data = defaultdict(dict)

    def __call__(self):
        """
        Do all cloning operations
        """
        self.prepare_clone()
        self.sort()
        for model, values in self.data.items():
            self.add_log(mod=model, act="clone", msg=f"{len(values)} items")
            self.clone(*values)
        for model, values in self.data.items():
            self.remap_m2ms(*values)
        for values in self.data.values():
            self.report_remapping(*values)

    def remove_superclasses(self):
        """
        Remove things that shouldn't be cloned, since the superclass of any child will be created
        automatically.
        """
        superclasses_post_check = set()
        for model in self.data:
            if superclasses := get_concrete_superclasses(model):
                if to_clear_pks := set(x.pk for x in self.data[model]):
                    for superclass in superclasses:
                        if superclass in self.data:
                            superclass: Type[Model]
                            to_remove = superclass.objects.filter(pk__in=to_clear_pks)
                            before_remove_count = len(self.data[superclass])
                            if to_remove:
                                self.data[superclass].difference_update(to_remove)
                                self.add_log(
                                    mod=model,
                                    act="remove_superclasses:removed",
                                    msg=f"To remove: {to_remove.count()} Before: {before_remove_count} After: {len(self.data[superclass])}",
                                )
                                superclasses_post_check.add(superclass)
                            else:  # pragma: no cover
                                # This should never happen i guess? :)
                                self.add_log(
                                    mod=model,
                                    act="remove_superclasses",
                                    msg=f"No superclasses for this type existed",
                                )
                else:
                    self.add_log(
                        mod=model,
                        act="remove_superclasses",
                        msg=f"There are superclasses but no data for subclass, nothing will be removed.",
                    )
        for sclass in superclasses_post_check:
            if sclass in self.data and not self.data[sclass]:
                del self.data[sclass]

    def prepare_data(self):
        """
        Store current pk and map to object.
        All objects will be stored in prepped data, even superclasses that might be removed later.

        Always use old pk to point to the data since it will be used by the remapper.
        """
        for model, values in self.data.items():
            for inst in values:
                self.track_obj(inst)
            self.add_log(
                mod=model, act="prepare_data", msg=f"{len(values)} instances in data"
            )

    def prepare_clone(self):
        self.prepare_data()
        self.remove_superclasses()
        # And the next pass prep m2m data
        for model, values in self.data.items():
            m2m_fields = [
                x
                for x in get_m2m_fields(model)
                if x.name not in self.clear_model_attrs.get(model, ())
            ]
            if m2m_fields:
                self.add_log(
                    mod=model,
                    act="prepare_clone",
                    msg=f"m2m fields: {','.join(f.name for f in m2m_fields)}",
                )
                for inst in values:
                    m2m_results = {}
                    for m2m_field in m2m_fields:
                        inst_relation = getattr(inst, m2m_field.name)
                        values = list(inst_relation.values_list("pk", flat=True))
                        if values:
                            m2m_results[m2m_field.name] = values
                    if m2m_results:
                        self.m2m_data[model][inst.pk] = m2m_results

    def remap_fks(self, *values: Model):
        """
        Remap values, must be a list of exactly the same models
        """
        if not values:  # pragma: no cover
            return
        model = values[0].__class__
        remap_fields = set()
        clear_fields = set()
        skipped_fields = set()
        clear_fieldnames = self.clear_model_attrs.get(model, ())
        for f in get_fk_fields(model):
            if f.name in clear_fieldnames:
                clear_fields.add(f)
                continue  # Should not be remapped
            if f.related_model in self.data:
                remap_fields.add(f)
            else:
                skipped_fields.add(f)
        if skipped_fields:
            self.add_log(
                mod=model,
                act="remap_fks:maintained",
                msg=f"Not in data so not remapped: {','.join(f.name for f in skipped_fields)}",
            )
        if clear_fields:
            self.add_log(
                mod=model,
                act="remap_fks:clearing",
                msg=f"{','.join(f.name for f in clear_fields)}",
            )
        if remap_fields:
            self.add_log(
                mod=model,
                act="remap_fks:remap",
                msg=f"{','.join(f.name for f in remap_fields)}",
            )
        for inst in values:
            for f in remap_fields:
                remap_to = self.get_remap_obj_from_field(inst, f)
                if remap_to is not None:
                    setattr(inst, f.name, remap_to)
            for f in clear_fields:
                # May cause not nullable, so it's not usable in all cases
                setattr(inst, f.name, None)

    def remap_m2ms(self, *values: Model):
        """
        Remap values, must be a list of exactly the same models that have already been saved. You can't do m2ms
        until the new object exist since they're just an intermediary object with FKs.
        """
        if not values:  # pragma: no cover
            return
        model = values[0].__class__
        m2m_fields = get_m2m_fields(model)
        m2m_field_names = set(x.name for x in m2m_fields)
        for inst in values:
            if inst.pk is None:
                raise ValueError(f"{inst} has no pk, it's probably not saved")
            m2m_data = self.get_m2m_data_for_clone(inst, {})
            for k, pks in m2m_data.items():
                # FIXME: Some fields may need to be remapped here
                assert k in m2m_field_names, f"{inst} has no m2m field named {k}"
                field = getattr(inst, k)
                field.set(m2m_data[k])

    def clone(self, *values: Model):
        pks = []
        for inst in values:
            assert inst.pk is not None, f"pk already None for {inst}"
            pks.append(inst.pk)
            self.reset_obj(inst)
        self.remap_fks(*values)
        self.run_pre_save(*values)
        for old_pk, inst in zip(pks, values):
            inst.save()
            self.register_new_pk(inst, old_pk)
        self.run_post_save(*values)

    def get_original(self, inst: Model):
        original_pk = self.get_old_pk(inst)
        return inst.__class__.objects.get(pk=original_pk)

    def get_m2m_data_for_clone(self, inst: Model, default=None) -> dict[str, list[int]]:
        """
        Return m2m_data or default
        """
        klass = inst.__class__
        if klass not in self.m2m_data:
            return default
        assert inst.pk in self.pk_map[klass], f"{inst} is not a clone"
        orig_pk = self.pk_map[klass][inst.pk]
        return self.m2m_data[klass].get(orig_pk, default)


class Importer(BaseRemapper):
    data: dict[Type[Model], set[DeserializedObject]]
    objs_with_deferred_fields: list[DeserializedObject]
    auto_find_existing: dict[Type[Model], set[str]]
    pointer_assigned_objs: set[Model]

    def __init__(self, *, data: Iterable[DeserializedObject]):
        super().__init__()
        # self.objs_with_deferred_fields = []
        self.auto_find_existing = defaultdict(set)
        self.data = defaultdict(set)
        self.pointer_assigned_objs = set()
        counter = 0
        for deserialized in data:
            assert isinstance(deserialized, DeserializedObject)
            self.data[deserialized.object.__class__].add(deserialized)
            counter += 1
        self.add_log(mod=None, act="init", msg=f"Loaded {counter} objects")

    def __call__(self):
        self.find_existing()
        self.sort()
        self.prepare_import()
        for model, values in self.data.items():
            self.add_log(mod=model, act="save_new", msg=f"{len(values)} items")
            self.save_new(*values)
        for values in self.data.values():
            self.report_remapping(*[v.object for v in values])

    @classmethod
    def from_filename(cls, filename: str):
        file_format = None
        if filename.endswith("yaml") or filename.endswith("yml"):
            file_format = "yaml"
        elif filename.endswith("json"):
            file_format = "json"
        assert (
            file_format
        ), "Can't figure out file format from file ending. Is it .yaml or .json?"
        with open(filename, "r") as fixture:
            objects = list(
                serializers.deserialize(
                    file_format,
                    fixture,
                    handle_forward_references=True,
                )
            )
        return cls(data=objects)

    def add_auto_find_existing(self, model: Type[Model], *attrs: str):
        for attr in attrs:
            if attr in ("pk", "id"):
                self.add_log(
                    mod=model,
                    act="add_auto_find_existing",
                    msg=f"Finding via {attr} might reuse objects that aren't similar "
                    f"if import doesn't already match existing db!",
                )
            else:
                assert (
                    model._meta.get_field(attr) is not None
                ), f"No field named {attr} on {model}"
        self.auto_find_existing[model].update(attrs)

    def find_existing(self):
        """
        Go through existing objects and compare them to import data.

        If you've already used exactly the same import before, it's safe to check via primary key.
        """
        # FIXME: Block find when using subclasses?
        for model, attrs in self.auto_find_existing.items():
            if model not in self.data:
                continue
            self.add_log(
                mod=model,
                act="find_existing",
                msg=f"Querying for existing via attrs {', '.join(attrs)}",
            )
            aggregated_qs = model.objects.none()
            for attr in attrs:
                round_qs = self.match_and_update(model, attr, exclude_qs=aggregated_qs)
                aggregated_qs = round_qs | aggregated_qs

    def match_and_update(
        self, model: Type[Model], attr: str, exclude_qs: Optional[QuerySet] = None
    ) -> Optional[QuerySet]:
        deserialized_map = {}
        if not self.data.get(model):
            return None
        for deserialized in self.data[model]:
            val = getattr(deserialized.object, attr)
            # Don't find objects via falsy values
            if val:
                if val in deserialized_map:
                    raise ValueError(
                        f"auto_find_existing found multiple {model} with {attr} = {val} in the import file."
                    )
                deserialized_map[val] = deserialized
        existing_qs = model.objects.filter(**{f"{attr}__in": deserialized_map})
        if exclude_qs:
            existing_qs = existing_qs.exclude(pk__in=exclude_qs).distinct()
        existing_vals = set()
        for item in existing_qs:
            val = getattr(item, attr)
            if val in existing_vals:
                raise ValueError(
                    f"auto_find_existing got multiple {model} with {attr} = {val}"
                )
            existing_vals.add(val)
            # Map import pk to existing object instead
            deserialized = deserialized_map.pop(val)
            self.replace_deserialized_object(deserialized, item)
        if not self.data[model]:
            del self.data[model]
        self.add_log(
            mod=model,
            act="match_and_update",
            msg=f"Found {existing_qs.count()} via attr {attr}",
        )
        return existing_qs

    def replace_deserialized_object(self, deserialized: DeserializedObject, obj: Model):
        """
        Instead of using a deserialized object, reuse an existing database object of the same type.
        For instance, if something's already imported or if you want to import a structure inside another
        existing structure.
        """
        model = obj.__class__
        assert deserialized.object.__class__ == model
        assert isinstance(deserialized.object.pk, int)
        self.track_obj(obj, deserialized.object.pk, allow_same=True)
        self.register_new_pk(obj, deserialized.object.pk)
        self.data[model].remove(deserialized)

    def prepare_import(self):
        """
        Store current pk and map to object.
        """
        for model, values in self.data.items():
            for deserialized in values:
                self.track_obj(deserialized.object)
            self.add_log(
                mod=model, act="prepare_import", msg=f"{len(values)} instances in data"
            )

    def remap_fks(self, *values: DeserializedObject):
        """
        Remap values, must be a list of exactly the same models
        """
        if not values:  # pragma: no cover
            return
        model = values[0].object.__class__
        remap_fields = set()
        maintained_fields = set()
        clear_fields = set()
        for f in get_fk_fields(model, exclude_ptr=False):
            if f.name in self.clear_model_attrs.get(model, set()):
                clear_fields.add(f)
            elif f.related_model in self.tracked_data:
                remap_fields.add(f)
            else:
                maintained_fields.add(f)
        if maintained_fields:
            self.add_log(
                mod=model,
                act="remap_fks:maintained",
                msg=f"Not in data so not remapped: {','.join(f.name for f in maintained_fields)}",
            )
        if remap_fields:
            self.add_log(
                mod=model,
                act="remap_fks:remap",
                msg=f"{','.join(f.name for f in remap_fields)}",
            )
        if clear_fields:
            self.add_log(
                mod=model,
                act="remap_fks:clear",
                msg=f"{','.join(f.name for f in clear_fields)}",
            )
        for deserialized in values:
            for f in remap_fields:
                remap_to = self.get_remap_obj_from_field(deserialized.object, f)
                if remap_to is not None:
                    curr_pk = deserialized.object.pk
                    setattr(deserialized.object, f.name, remap_to)
                    if is_pointer(f):
                        # Pointers force update of pk
                        assert deserialized.object.pk == remap_to.pk
                        self.register_new_pk(deserialized.object, curr_pk)
                        self.pointer_assigned_objs.add(deserialized.object)
            for f in clear_fields:
                setattr(deserialized.object, f.name, None)

    def remap_m2ms(self, *values: DeserializedObject):
        """
        Remap values, must be a list of exactly the same models that have already been saved. You can't do m2ms
        until the new object exist since they're just an intermediary object with FKs.
        """
        if not values:  # pragma: no cover
            return
        model = values[0].object.__class__
        m2m_fields = get_m2m_fields(model)
        field_names_to_remap = set(
            f.name for f in m2m_fields if f.related_model in self.tracked_data
        )
        # Remove clear-fields
        clear_field_names = self.clear_model_attrs.get(model, set())
        field_names_to_remap.difference_update(clear_field_names)
        remap_counter = Counter()
        maintained_field_names = set()
        for deserialized in values:
            for field in m2m_fields:
                old_pks = deserialized.m2m_data.get(field.name)
                if field.name in field_names_to_remap:
                    if old_pks:
                        deserialized.m2m_data[field.name] = [
                            self.get_remap_obj(field.related_model, pk).pk
                            for pk in old_pks
                        ]
                        remap_counter[field.name] += len(old_pks)
                elif field.name in clear_field_names:
                    if old_pks:
                        remap_counter[field.name] += len(old_pks)
                else:
                    maintained_field_names.add(field.name)
        if maintained_field_names:
            self.add_log(
                mod=model,
                act="remap_m2ms:maintained",
                msg=f"Kept m2m relations for fields: {', '.join(maintained_field_names)}",
            )
        for k, v in remap_counter.items():
            if k in field_names_to_remap:
                action = "remapped"
            elif k in maintained_field_names:
                action = "maintained"
            else:
                action = "cleared"
            if v:
                self.add_log(
                    mod=model,
                    act=f"remap_m2ms:{action}",
                    msg=f"{action.title()} {v} items for field {k}",
                )
            else:
                self.add_log(
                    mod=model,
                    act="remap_m2ms:missing",
                    msg=f"No data for field {k} to remap",
                )

    def save_new(self, *values: DeserializedObject):
        self.remap_fks(*values)
        # This will change pks of tracked data within the deserialized m2ms.
        # Since this import data doesn't exist in the db yet, it can be done prior to saving the object.
        self.remap_m2ms(*values)
        self.run_pre_save(*[x.object for x in values])
        for deserialized in values:
            assert (
                deserialized.object.pk is not None
            ), f"pk already None for {deserialized}"
            # Don't reset these, they've already been assigned a pk from their parent
            must_reset_pk = deserialized.object not in self.pointer_assigned_objs
            if must_reset_pk:
                old_pk = deserialized.object.pk
                self.reset_obj(deserialized.object)
            deserialized.save()
            assert (
                deserialized.object.pk is not None
            ), f"{deserialized} pk is None after save"
            if must_reset_pk:
                self.register_new_pk(deserialized.object, old_pk)
        self.run_post_save(*[x.object for x in values])


class Exporter:
    # This is just a stub, we might not need exporters
    data: list[Model]

    def __init__(self, *, data: list[Model]):
        self.data = data

    def serialize(self, stream, format="yaml"):
        serializers.serialize(
            format,
            self.data,
            # fields=exp.select_fields,
            # indent=indent,
            # use_natural_foreign_keys=use_natural_foreign_keys,
            # use_natural_primary_keys=use_natural_primary_keys,
            stream=stream,
            # progress_output=self.stdout,
            object_count=len(self.data),
        )


def validate_callable(_callable):
    """
    Make sure a callable conforms to expected params.

    >>> def callme(remapper, *values):
    ...     pass
    ...
    >>> validate_callable(callme)
    True

    >>> def callme(remapper):
    ...     pass
    ...
    >>> validate_callable(callme)
    False

    >>> validate_callable(object)
    False
    """
    # FIXME: Maybe better validation later on ;)
    sig = signature(_callable)
    return len(sig.parameters) == 2
