from django.contrib import admin

from dolly_testing.models import AgendaItem
from dolly_testing.models import DiffProposal
from dolly_testing.models import Meeting
from dolly_testing.models import MeetingGroup
from dolly_testing.models import MeetingRole
from dolly_testing.models import NullableRelated
from dolly_testing.models import Organisation
from dolly_testing.models import OrganisationRole
from dolly_testing.models import Proposal
from dolly_testing.models import SingletonFlag
from dolly_testing.models import Tag
from dolly_testing.models import Text


@admin.register(Organisation, Tag, SingletonFlag)
class DefaultAdmin(admin.ModelAdmin):
    list_display = ("__str__",)


@admin.register(OrganisationRole, MeetingRole)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("__str__", "context")


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ("__str__", "organisation")


@admin.register(MeetingGroup, AgendaItem, Proposal, DiffProposal)
class MeetingContextAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "meeting",
        "agenda",
    )

    def agenda(self, instance):
        if hasattr(instance, "agenda_item"):
            return instance.agenda_item
        return ""


@admin.register(Text)
class TextAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "agenda_item",
        "meeting",
    )

    def meeting(self, instance):
        return instance.agenda_item.meeting


@admin.register(NullableRelated)
class NullableRelatedAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "diff_prop",
    )
