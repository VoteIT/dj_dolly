from collections import defaultdict
from functools import partial
from typing import Optional
from typing import Type
from typing import TypedDict
from typing import Union

from django.conf import settings
from django.db.models import Model, Field

from dolly.utils import get_all_dependencies
from dolly.utils import get_concrete_superclasses
from dolly.utils import get_dependencies
from dolly.utils import get_fk_fields
from dolly.utils import get_m2m_fields
from dolly.utils import topological_sort


class LogAction(TypedDict):
    act: str
    mod: Type[Model]
    msg: str


class LiveCloner:
    """
    Clones models already in the database.

    Any data passed here should be data that you want to clone. If it's not within the data, the relation will be kept.
    """

    data: dict[Type[Model], set[Model]]
    prepped_data: dict[Type[Model], dict[int, Model]]
    # preprocessors: dict[Type[Model], list[callable]]
    # { <Model>: { pk: { field_name: [pk, ...]}}}
    m2m_data: dict[Type[Model], dict[int, dict[str, list[int]]]]
    # Map of new_pk -> old_pk
    pk_map: dict[Type[Model], dict[int, int]]
    clear_model_attrs: dict[Type[Model], set[str]]
    log: list[LogAction]

    # FIXME: Remove ordering and make it automatic instead
    # FIXME: Warn when resetting attributes makes cloning something pointless
    # FIXME: Maybe be smarter when fetching something in the middle of a tree

    def __init__(
        self,
        *,
        data: dict[Type[Model], set[Model]],
        order: list[Type[Model]] = (),
    ):
        self.data = data
        # self.preprocessors = defaultdict(list)
        self.prepped_data = defaultdict(dict)
        self.pk_map = defaultdict(dict)
        self.m2m_data = defaultdict(dict)
        self.clear_model_attrs = {}
        self.log = []
        self.logging_enabled = getattr(settings, "DEBUG", False)
        if order:
            order = list(order)
            for m in data:
                if m not in order:
                    order.append(m)
            self.add_log(mod=None, act="order", msg=f"Manual order set to: {order}")
        else:
            assert data
            dependencies = get_all_dependencies(*data.keys())
            order = list(topological_sort(dependencies))
            self.add_log(mod=None, act="order", msg=f"Automatic order set to: {order}")
        self.data = {model: data[model] for model in order if data.get(model)}

    def __call__(self):
        """
        Do all cloning operations
        """
        self.prepare_clone()
        for model, values in self.data.items():
            self.add_log(mod=model, act="clone", msg=f"{len(values)} items")
            self.clone(*values)
        for model, values in self.data.items():
            self.remap_m2ms(*values)

    def add_log(self, *, mod: Optional[Type[Model]], act: str, msg: str):
        if self.logging_enabled:
            self.log.append(dict(act=act, mod=mod, msg=msg))

    # def add_preprocessor(self, model: Type[Model], _callable: callable):
    #     assert issubclass(model, Model)
    #     if _callable in self.preprocessors[model]:
    #         raise ValueError(f"{_callable} is already in preprocessors")
    #     self.preprocessors[model].append(_callable)

    def add_clear_attrs(self, model: Type[Model], *attrs):
        """
        Clear specified attributes for model. Remember to specify subclasses too + that some models might not be nullable.
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
        vals = self.clear_model_attrs.setdefault(model, set())
        vals.update(attrs)

    def remove_superclasses(self):
        """
        Remove things that shouldn't be cloned, since the superclass of any child will be created
        automatically.
        """
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
                            else:
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

    def prepare_clone(self):
        """
        Store current pk and map to object. Remove set pk to force new insert later on
        """
        # All objects will be stored in prepped data, even superclasses that'll be removed later
        for model, values in self.data.items():
            for inst in values:
                self.prepped_data[model][inst.pk] = inst
            self.add_log(
                mod=model, act="prepare_clone", msg=f"{len(values)} instances in data"
            )
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
            inst: Model
            for f in remap_fields:
                remap_to = self.get_remap_obj(inst, f)
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
        m2m_to_clear = set(self.clear_model_attrs.get(model, ())) & m2m_field_names
        for inst in values:
            if inst.pk is None:
                raise ValueError(f"{inst} has no pk, it's probably not saved")
            m2m_data = self.get_m2m_data_for_clone(inst, {})
            for k, pks in m2m_data.items():
                assert k in m2m_field_names, f"{inst} has no m2m field named {k}"
                field = getattr(inst, k)
                field.set(m2m_data[k])
            # m2m_data won't contain stuff that should be cleared
            for k in m2m_to_clear:
                field = getattr(inst, k)
                field.clear()

    def get_remap_obj(self, inst: Model, field: Field) -> Optional[Model]:
        id_name = f"{field.name}_id"
        curr_val = getattr(inst, id_name)
        if curr_val is None:
            return
        if field.related_model not in self.prepped_data:
            raise ValueError(f"{field.related_model} not in prepped_data")
        remap_to = self.prepped_data[field.related_model][curr_val]
        assert remap_to.pk is not None, (
            f"Relation {field.name} for instance {inst} points to {remap_to} but that object hasn't "
            f"been saved yet. The order is probably wrong."
        )
        assert remap_to.pk != curr_val, (
            f"remap_to returned an object with the same pk as the original. "
            f"Maybe they got called in the wrong order? "
            f"Adjusted {inst.__class__} object should be processed after {remap_to.__class__}"
        )
        return remap_to

    def _reset(self, inst: Model):
        inst.pk = None
        inst.id = None
        inst._state.adding = True

    def clone(self, *values: Model):
        pks = []
        for inst in values:
            assert inst.pk is not None, f"pk already None for {inst}"
            pks.append(inst.pk)
            self._reset(inst)
        self.remap_fks(*values)
        for old_pk, inst in zip(pks, values):
            inst.save()
            self.pk_map[inst.__class__][inst.pk] = old_pk

    def is_clone(self, inst: Model):
        return (
            inst.pk
            and inst.__class__ in self.pk_map
            and inst.pk in self.pk_map[inst.__class__]
        )

    def get_original(self, inst: Model):
        original_pk = self.pk_map[inst.__class__].get(inst.pk)
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
