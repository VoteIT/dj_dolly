from django.contrib import admin
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.http import HttpResponse
from django.template import loader

from dolly.core import LiveCloner
from dolly.exceptions import CrossLinkedCloneError
from dolly.utils import safe_clone


@admin.action(description="Dry-run clone and report actions")
def report_structure(
    modeladmin: admin.ModelAdmin, request, queryset, exclude_models=None
):
    if queryset.count() != 1:
        modeladmin.message_user(
            request,
            "Select exactly 1 to report",
            messages.ERROR,
        )
    if exclude_models is None:
        exclude_models = [get_user_model(), ContentType]
    root_obj = queryset.first()
    cloner = LiveCloner(data={})
    cloner.logging_enabled = True
    bad_duplications = None
    try:
        with transaction.atomic(durable=True):
            safe_clone(root_obj, exclude_models=exclude_models, cloner=cloner)
            transaction.set_rollback(True)
    except CrossLinkedCloneError as cross_exc:
        bad_duplications = cross_exc.data
    template = loader.get_template("dolly/log.html")
    context = {
        "log": cloner.log,
        "title": "Dry-run clone report",
        "bad_duplications": bad_duplications,
        "ignoring": exclude_models,
    }
    return HttpResponse(template.render(context, request))
