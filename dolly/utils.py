from collections import defaultdict
from sys import maxsize
from typing import Type

from django.contrib.contenttypes.fields import GenericForeignKey
from django.db.models import ForeignKey
from django.db import models

try:
    from deep_collector.core import DeepCollector
except ImportError:
    DeepCollector = None


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
    # Is this correct or should it be a check on the local field?
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


def get_inf_collector() -> DeepCollector:
    """
    Make sure everything gets exported
    """
    if DeepCollector is not None:
        dc = DeepCollector()
        dc.MAXIMUM_RELATED_INSTANCES = maxsize
        return dc
    raise ImportError("django-deep-collector not installed")


def get_all_dependencies(*items: Type[models.Model], ignore=()):
    """
    >>> from dolly_testing.models import Meeting, Proposal, MeetingGroup, DiffProposal

    >>> sorted(x[0].__name__ for x in get_all_dependencies(Meeting))
    ['Meeting', 'Organisation', 'User']

    >>> sorted(x[0].__name__ for x in get_all_dependencies(Proposal))
    ['AgendaItem', 'ContentType', 'Meeting', 'MeetingGroup', 'Organisation', 'Proposal', 'User']

    >>> sorted(x[0].__name__ for x in get_all_dependencies(MeetingGroup))
    ['ContentType', 'Meeting', 'MeetingGroup', 'Organisation', 'User']

    >>> sorted(x[0].__name__ for x in get_all_dependencies(DiffProposal))
    ['AgendaItem', 'ContentType', 'DiffProposal', 'Meeting', 'MeetingGroup', 'Organisation', 'SingletonFlag', 'Text', 'User']

    >>> from django.contrib.auth.models import User
    >>> sorted(x[0].__name__ for x in get_all_dependencies(DiffProposal, ignore={User}))
    ['AgendaItem', 'ContentType', 'DiffProposal', 'Meeting', 'MeetingGroup', 'Organisation', 'SingletonFlag', 'Text']

    """
    handled = set()
    to_check = set(items)
    result = []
    while to_check:
        m = to_check.pop()
        deps = get_dependencies(m, ignore=ignore)
        result.append((m, deps))
        handled.add(m)
        to_check.update(x for x in deps if x not in handled)
    return result


def get_dependencies(
    model: Type[models.Model], ignore: set[Type[models.Model]] = ()
) -> set[Type[models.Model]]:
    """
    >>> from dolly_testing.models import Meeting, Proposal, MeetingGroup, DiffProposal
    >>> sorted(f.__name__ for f in get_dependencies(Meeting))
    ['Organisation', 'User']

    >>> sorted(f.__name__ for f in get_dependencies(Proposal))
    ['AgendaItem', 'Meeting', 'MeetingGroup', 'User']

    >>> sorted(f.__name__ for f in get_dependencies(DiffProposal))
    ['AgendaItem', 'Meeting', 'MeetingGroup', 'SingletonFlag', 'Text', 'User']

    >>> sorted(f.__name__ for f in get_dependencies(MeetingGroup))
    ['ContentType', 'Meeting']

    >>> sorted(f.__name__ for f in get_dependencies(MeetingGroup, ignore={Meeting}))
    ['ContentType']
    """
    deps = set()
    for f in get_fk_fields(model, exclude_ptr=True):
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
