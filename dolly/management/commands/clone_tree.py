from __future__ import annotations

import sys

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.management import BaseCommand
from django.db import DEFAULT_DB_ALIAS
from django.db import transaction

from dolly.core import LiveCloner
from dolly.utils import get_inf_collector
from dolly.utils import get_model_formatted_dict


class Command(BaseCommand):
    help = "Import and create a new meeting. Won't overwrite existing meetings and can be run multiple times."

    def add_arguments(self, parser):
        parser.add_argument(
            "model_name",
            help="Model name in format app_label.model_name",
        )
        parser.add_argument(
            "pk",
            help="Primary key",
        )
        # parser.add_argument(
        #     "--database",
        #     default=DEFAULT_DB_ALIAS,
        #     help='Nominates a specific database to load fixtures into. Defaults to the "default" database.',
        # )
        parser.add_argument(
            "--dry-run",
            default=False,
            help="Dry-run - abort transaction",
            action="store_true",
        )
        parser.add_argument(
            "-e",
            "--exclude",
            action="append",
            default=[],
            help="Exclude model <app_name.model_name>. It's a good idea to exclude auth.user",
        )

    def handle(self, *args, **options):
        model_name = options["model_name"]
        dry_run = options["dry_run"]
        exclude = options["exclude"]
        # exclude_models = set(apps.get_model(x) for x in exclude)
        model = apps.get_model(model_name)
        root_pk = options["pk"]
        root_obj = model.objects.get(pk=root_pk)
        if dry_run:
            print("!! Dry run - nothing will be saved !!")
        collector = get_inf_collector()
        collector.EXCLUDE_MODELS = exclude
        collector.collect(root_obj)
        related_objects = collector.get_collected_objects()
        print(
            f"Initial find: {len(related_objects)} objects. "
            f"Some may be removed from the collection during the clone process. "
            f"(For instance superclasses of multi-table inheritance models)"
        )
        data = get_model_formatted_dict(related_objects)
        cloner = LiveCloner(data=data)
        with transaction.atomic(durable=True):
            cloner()
            if dry_run:
                transaction.set_rollback(True)

        print("All done - the following types cloned: ")
        print("=" * 45)
        for model, values in cloner.data.items():
            print(
                f"{model._meta.app_label}.{model._meta.model_name}".ljust(40),
                f"{len(values)}".rjust(4),
            )
