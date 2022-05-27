from __future__ import annotations
from collections import defaultdict
from sys import maxsize
from typing import Iterable
from typing import Optional
from typing import TYPE_CHECKING
from typing import Type
from typing import Union

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.db.models import ForeignKey
from django.db import models
from django.db.transaction import get_connection

from dolly.exceptions import CrossLinkedCloneError

try:
    from deep_collector.core import DeepCollector
except ImportError:  # pragma: no cover
    DeepCollector = None

if TYPE_CHECKING:
    from dolly.core import LiveCloner


def get_local_m2m_fields(model: Type[models.Model]) -> set[models.Field]:
    """
    This is to make sure we have some API-stable way of fetching m2m fields.

    >>> from django.contrib.auth.models import User
    >>> sorted(f.name for f in get_local_m2m_fields(User))
    ['groups', 'user_permissions']

    >>> from django.contrib.auth.models import Group
    >>> sorted(f.name for f in get_local_m2m_fields(Group))
    ['permissions']

    >>> from dolly_testing.models import MeetingGroup
    >>> sorted(f.name for f in get_local_m2m_fields(MeetingGroup))
    ['members']

    >>> from dolly_testing.models import DiffProposal
    >>> sorted(f.name for f in get_local_m2m_fields(DiffProposal))
    []

    """
    return set(model._meta.local_many_to_many)


def get_m2m_fields(model: Type[models.Model]) -> set[models.Field]:
    """
    This is to make sure we have some API-stable way of fetching m2m fields.

    >>> from django.contrib.auth.models import User
    >>> sorted(f.name for f in get_m2m_fields(User))
    ['groups', 'user_permissions']

    >>> from django.contrib.auth.models import Group
    >>> sorted(f.name for f in get_m2m_fields(Group))
    ['permissions']

    >>> from dolly_testing.models import MeetingGroup
    >>> sorted(f.name for f in get_m2m_fields(MeetingGroup))
    ['members']

    >>> from dolly_testing.models import DiffProposal
    >>> sorted(f.name for f in get_m2m_fields(DiffProposal))
    ['tags']

    """
    return set(model._meta.many_to_many)


def is_pointer(field: models.Field) -> bool:
    return field.one_to_one and getattr(field.remote_field, "parent_link", False)


def get_concrete_superclasses(model: Type[models.Model]) -> list[Type[models.Model]]:
    """
    All concrete superclasses

    >>> from dolly_testing.models import Child
    >>> get_concrete_superclasses(Child)
    [<class 'dolly_testing.models.Grandparent'>, <class 'dolly_testing.models.Parent'>]

    >>> from django.contrib.auth.models import Group
    >>> get_concrete_superclasses(Group)
    []

    >>> from dolly_testing.models import DiffProposal
    >>> get_concrete_superclasses(DiffProposal)
    [<class 'dolly_testing.models.Proposal'>]
    """

    results = []
    for f in model._meta.get_fields():
        if is_pointer(f) and issubclass(model, f.related_model):
            results.append(f.related_model)
    return results


def get_parents(obj: models.Model) -> set[models.Model]:
    """
    Get closest parent(s)
    """
    results = set()
    for f in obj._meta.get_fields():
        if is_pointer(f):
            results.add(getattr(obj, f.name))
    return results


def get_fk_fields(model: Type[models.Model], exclude_ptr=True) -> set[models.Field]:
    """
    This is to make sure we have some API-stable way of fetching fk fields.

    >>> from django.contrib.auth.models import User
    >>> sorted(get_fk_fields(User))
    []

    >>> from dolly_testing.models import MeetingGroup
    >>> sorted(f.name for f in get_fk_fields(MeetingGroup))
    ['content_type', 'likes_content', 'meeting']

    >>> from dolly_testing.models import DiffProposal
    >>> sorted(f.name for f in get_fk_fields(DiffProposal))
    ['agenda_item', 'author', 'flag', 'meeting', 'meeting_group', 'text']

    >>> sorted(f.name for f in get_fk_fields(DiffProposal, exclude_ptr=False))
    ['agenda_item', 'author', 'flag', 'meeting', 'meeting_group', 'proposal_ptr', 'text']

    """
    return set(
        f
        for f in model._meta.get_fields()
        if (isinstance(f, ForeignKey) or isinstance(f, GenericForeignKey))
        and (exclude_ptr is False or not is_pointer(f))
    )


def get_data_id_struct(
    data: dict[Type[models.Model], set[models.Model]]
) -> dict[Type[models.Model], set[int]]:
    """
    Return a structure with model as key and then sets with all pks.
    """
    result = defaultdict(set)
    for model, items in data.items():
        if items:
            result[model].update([x.pk for x in items])
    return result


def get_model_formatted_dict(objs) -> dict[Type[models.Model], set[models.Model]]:
    result = defaultdict(set)
    for obj in objs:
        result[obj.__class__].add(obj)
    return result


def get_inf_collector(
    exclude_models: Iterable[Union[Type[models.Model], str]] = ()
) -> DeepCollector:
    """
    Make sure everything gets exported.
    Specify exclude_models as natural key or model

    >>> from django.contrib.auth.models import User
    >>> collector=get_inf_collector(exclude_models=[User])
    >>> collector.EXCLUDE_MODELS
    ['auth.user']
    >>> collector=get_inf_collector(exclude_models=['auth.user'])
    >>> collector.EXCLUDE_MODELS
    ['auth.user']

    >>> get_inf_collector(exclude_models=['404'])
    Traceback (most recent call last):
    ...
    ValueError: not enough values to unpack (expected 2, got 1)

    >>> get_inf_collector(exclude_models=['auth.404'])
    Traceback (most recent call last):
    ...
    LookupError: App 'auth' doesn't have a '404' model.
    """
    if DeepCollector is None:  # pragma: no cover
        raise ImportError("django-deep-collector not installed")
    dc = DeepCollector()
    dc.MAXIMUM_RELATED_INSTANCES = maxsize
    exclude = set()
    for item in exclude_models:
        if isinstance(item, str):
            assert apps.get_model(item)
            exclude.add(item)
        elif issubclass(item, models.Model):
            exclude.add(get_nat_key(item))
        else:
            raise TypeError(f"{item} must be str or Model")
    dc.EXCLUDE_MODELS = list(exclude)
    return dc


def get_all_related_models(
    *items: Type[models.Model], ignore_attrs: dict[Type[models.Model], set[str]] = None
) -> set[Type[models.Model]]:
    """
    >>> from dolly_testing.models import Meeting, Proposal, MeetingGroup, DiffProposal
    >>> sorted(x.__name__ for x in get_all_related_models(Meeting, Proposal, MeetingGroup, DiffProposal))
    ['AgendaItem', 'ContentType', 'DiffProposal', 'Meeting', 'MeetingGroup', 'Organisation', 'Proposal', 'SingletonFlag', 'Text', 'User']
    """
    results: set[Type[models.Model]] = set()
    results.update(items)
    for (m, deps) in get_all_dependencies(*items, ignore_attrs=ignore_attrs):
        results.update(deps)
    return results


def get_all_dependencies(
    *items: Type[models.Model],
    ignore=(),
    ignore_attrs: dict[Type[models.Model], set[str]] = None,
):
    """
    >>> from dolly_testing.models import Meeting, Proposal, MeetingGroup, DiffProposal, A, B

    >>> sorted(x[0].__name__ for x in get_all_dependencies(Meeting))
    ['Meeting', 'Organisation', 'User']

    >>> sorted(x[0].__name__ for x in get_all_dependencies(Proposal))
    ['AgendaItem', 'ContentType', 'Meeting', 'MeetingGroup', 'Organisation', 'Proposal', 'User']

    >>> sorted(x[0].__name__ for x in get_all_dependencies(MeetingGroup))
    ['ContentType', 'Meeting', 'MeetingGroup', 'Organisation', 'User']

    >>> sorted(x[0].__name__ for x in get_all_dependencies(DiffProposal))
    ['AgendaItem', 'ContentType', 'DiffProposal', 'Meeting', 'MeetingGroup', 'Organisation', 'Proposal', 'SingletonFlag', 'Text', 'User']

    >>> from django.contrib.auth.models import User
    >>> sorted(x[0].__name__ for x in get_all_dependencies(DiffProposal, ignore={User}))
    ['AgendaItem', 'ContentType', 'DiffProposal', 'Meeting', 'MeetingGroup', 'Organisation', 'Proposal', 'SingletonFlag', 'Text']

    Make sure pointers we don't need are ignored
    >>> sorted(x[0].__name__ for x in get_all_dependencies(A, ignore_attrs={A: {'friend'}}))
    ['A']
    """
    handled = set()
    to_check = set(items)
    result = []
    if ignore_attrs is None:
        ignore_attrs = {}
    assert isinstance(ignore_attrs, dict), "Must be a dict"
    while to_check:
        m = to_check.pop()
        deps = get_dependencies(
            m, ignore=ignore, ignore_attrs=ignore_attrs.get(m, set())
        )
        result.append((m, deps))
        handled.add(m)
        to_check.update(x for x in deps if x not in handled)
    return result


def get_dependencies(
    model: Type[models.Model],
    ignore: set[Type[models.Model]] = (),
    ignore_attrs: set[str] = (),
) -> set[Type[models.Model]]:
    """
    >>> from dolly_testing.models import Meeting, Proposal, MeetingGroup, DiffProposal, A
    >>> sorted(f.__name__ for f in get_dependencies(Meeting))
    ['Organisation', 'User']

    >>> sorted(f.__name__ for f in get_dependencies(Proposal))
    ['AgendaItem', 'Meeting', 'MeetingGroup', 'User']

    >>> sorted(f.__name__ for f in get_dependencies(DiffProposal))
    ['AgendaItem', 'Meeting', 'MeetingGroup', 'Proposal', 'SingletonFlag', 'Text', 'User']

    >>> sorted(f.__name__ for f in get_dependencies(MeetingGroup))
    ['ContentType', 'Meeting']

    >>> sorted(f.__name__ for f in get_dependencies(MeetingGroup, ignore={Meeting}))
    ['ContentType']

    >>> sorted(f.__name__ for f in get_dependencies(A))
    ['B']

    >>> sorted(f.__name__ for f in get_dependencies(A, ignore_attrs={'friend'}))
    []
    """
    deps = set()
    for f in get_fk_fields(model, exclude_ptr=False):
        if f.name in ignore_attrs:
            continue
        if f.related_model and f.related_model not in ignore:
            deps.add(f.related_model)
    return deps


def topological_sort(source: list[tuple[Type[models.Model], set[Type[models.Model]]]]):
    """
    perform topo sort on elements.

    :arg source: list of ``(name, [list of dependancies])`` pairs
    :returns: list of names, with dependancies listed first

    Credit to Eli Collins:
    https://stackoverflow.com/questions/11557241/python-sorting-a-dependency-list

    >>> from dolly_testing.models import Organisation, Meeting, Proposal, MeetingGroup, DiffProposal, SingletonFlag
    >>> source = get_all_dependencies(Organisation, Meeting, Proposal, MeetingGroup, DiffProposal, SingletonFlag)
    >>> models_names = [x.__name__ for x in topological_sort(source)]
    >>> models_names.index('Organisation') < models_names.index('Meeting')
    True
    >>> models_names.index('Meeting') < models_names.index('AgendaItem')
    True
    >>> models_names.index('AgendaItem') < models_names.index('Text')
    True
    >>> models_names.index('User') < models_names.index('MeetingGroup')
    True
    """
    pending = [
        (name, set(deps)) for name, deps in source
    ]  # copy deps so we can modify set in-place
    emitted = []
    while pending:
        next_pending = []
        next_emitted = []
        for entry in pending:
            name, deps = entry
            deps.difference_update(emitted)  # remove deps we emitted last pass
            if deps:  # still has deps? recheck during next pass
                next_pending.append(entry)
            else:  # no more deps? time to emit
                yield name
                emitted.append(
                    name
                )  # <-- not required, but helps preserve original ordering
                next_emitted.append(
                    name
                )  # remember what we emitted for difference_update() in next pass
        if (
            not next_emitted
        ):  # all entries have unmet deps, one of two things is wrong...
            raise ValueError(
                "cyclic or missing dependancy detected: %r" % (next_pending,)
            )
        pending = next_pending
        emitted = next_emitted


def get_nat_key(model: Union[Type[models.Model], models.Model]) -> str:
    """
    Djangos normal way of handling natural keys. Is there no util in django for this?

    >>> from django.contrib.auth.models import User
    >>> get_nat_key(User)
    'auth.user'
    """
    return f"{model._meta.app_label}.{model._meta.model_name}"


def safe_clone(root_obj, exclude_models=(), cloner: Optional[LiveCloner] = None):
    """
    Make sure collection -> cloning -> collection yields the same result so the cloning process doesn't cause some
    unexpected side effects.

    Returns anything fishy found or None
    """
    if cloner is None:
        from dolly.core import LiveCloner

        cloner = LiveCloner(data={})
    connection = get_connection()
    if not connection.in_atomic_block:
        raise RuntimeError("Must be run while atomic is enabled")

    initial_model = root_obj.__class__
    initial_pk = root_obj.pk
    assert root_obj.pk is not None

    collector = get_inf_collector(exclude_models=exclude_models)
    collector.collect(root_obj)
    data = get_model_formatted_dict(collector.get_collected_objects())
    cloner.data = data
    initial_collected_ids = get_data_id_struct(data)
    cloner()
    initial_root = initial_model.objects.get(pk=initial_pk)
    collector = get_inf_collector(exclude_models=exclude_models)
    collector.collect(initial_root)
    data = get_model_formatted_dict(collector.get_collected_objects())
    second_collected_ids = get_data_id_struct(data)
    extra_found = {}
    for (m, vals) in second_collected_ids.items():
        found = vals - initial_collected_ids.get(m, set())
        if found:
            extra_found[m] = found
    if extra_found:
        raise CrossLinkedCloneError(extra_found)
    return cloner
