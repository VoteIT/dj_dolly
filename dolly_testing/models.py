from abc import ABC
from abc import abstractmethod

from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models


class _Default(models.Model):
    name = models.CharField(max_length=20, default="")

    class Meta:
        abstract = True

    def __str__(self):
        return self.name and self.name or super().__str__()

    objects: models.Manager


class _DefaultContent(_Default):
    author = models.ForeignKey(
        User,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="%(class)s_author_set",
    )
    tags = models.ManyToManyField("Tag", related_name="%(class)s_tags_set", blank=True)

    class Meta:
        abstract = True


class _Role(_Default):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="%(class)s_role_set",
    )

    @property
    @abstractmethod
    def context(self):
        ...

    class Meta:
        abstract = True


class Tag(_Default):
    ...


class OrganisationRole(_Role):
    context = models.ForeignKey("Organisation", on_delete=models.CASCADE)


class Organisation(_Default):
    users = models.ManyToManyField(User, through=OrganisationRole)


class MeetingRole(_Role):
    context = models.ForeignKey("Meeting", on_delete=models.CASCADE)


class Meeting(_DefaultContent):
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE)
    participants = models.ManyToManyField(User, through=MeetingRole)


class MeetingGroup(_Default):
    members = models.ManyToManyField(User)
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE)
    likes_content = GenericForeignKey()
    content_type = models.ForeignKey(
        ContentType, on_delete=models.SET_NULL, null=True, blank=True
    )
    object_id = models.PositiveIntegerField(null=True, blank=True)


class AgendaItem(_DefaultContent):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE)


class Proposal(_DefaultContent):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE)
    agenda_item = models.ForeignKey(AgendaItem, on_delete=models.CASCADE)
    meeting_group = models.ForeignKey(
        MeetingGroup, null=True, blank=True, on_delete=models.SET_NULL
    )


class Text(_DefaultContent):
    text = models.CharField(max_length=100, default="")
    agenda_item = models.ForeignKey(AgendaItem, on_delete=models.CASCADE)


class DiffProposal(Proposal):
    text = models.ForeignKey(Text, on_delete=models.RESTRICT)
    flag = models.ForeignKey(
        "SingletonFlag", on_delete=models.SET_NULL, null=True, blank=True
    )


class NullableRelated(_Default):
    diff_prop = models.ForeignKey(
        DiffProposal, on_delete=models.SET_NULL, null=True, blank=True
    )


class SingletonFlag(_Default):
    ...


# Test linear multi inheritance - django only seems to be able to copy 1 leve deep!
class Grandparent(_Default):
    ...


class Parent(Grandparent):
    ...


class Child(Parent):
    ...


# Multi inheritance tree django example - this doesn't work with clone at all?
class Piece(_Default):
    pass


class Article(Piece):
    article_piece = models.OneToOneField(
        Piece, on_delete=models.CASCADE, parent_link=True
    )


class Book(Piece):
    book_piece = models.OneToOneField(Piece, on_delete=models.CASCADE, parent_link=True)


class BookReview(Book, Article):
    pass
