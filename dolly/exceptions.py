from typing import Type

from django.db.models import Model


class DollyBaseError(Exception):
    ...


class ImportTreeError(DollyBaseError):
    ...


class CloneTreeError(DollyBaseError):
    ...


class CrossLinkedCloneError(CloneTreeError):
    """
    Cloning this data will have bad side effects
    """

    def __init__(self, data: dict[Type[Model], set[int]]):
        self.data = data
        super().__init__()
