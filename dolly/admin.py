from django.contrib import admin
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.http import HttpResponse
from django.template import loader

from dolly.core import LiveCloner
from dolly.utils import get_inf_collector
from dolly.utils import get_model_formatted_dict
from dolly.utils import get_nat_key


def get_default_admin_collector():
    User = get_user_model()
    collector = get_inf_collector()
    collector.EXCLUDE_MODELS = [get_nat_key(User), get_nat_key(ContentType)]
    return collector


@admin.action(description="Dry-run clone and report actions")
def report_structure(modeladmin: admin.ModelAdmin, request, queryset):
    if queryset.count() != 1:
        modeladmin.message_user(
            request,
            "Select exactly 1 to report",
            messages.ERROR,
        )
    root_obj = queryset.first()
    collector = get_default_admin_collector()
    collector.collect(root_obj)
    related_objects = collector.get_collected_objects()
    data = get_model_formatted_dict(related_objects)
    cloner = LiveCloner(data=data)
    cloner.logging_enabled = True
    with transaction.atomic(durable=True):
        cloner()
        transaction.set_rollback(True)
    template = loader.get_template("dolly/log.html")
    context = {"log": cloner.log, "title": "Dry-run clone report"}
    return HttpResponse(template.render(context, request))
