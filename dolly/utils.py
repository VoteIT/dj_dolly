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
        if model._meta.auto_created:
            continue
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
