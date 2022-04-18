from __future__ import annotations

import sys
from collections import defaultdict

from django.apps import apps
from django.core.management import BaseCommand
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
            help="Exclude model <app_name>.<model_name - It's usually a good idea to exclude auth.user. "
            "Relations will be kept even if the model is excluded.",
        )
        parser.add_argument(
            "-c",
            "--clear",
            action="append",
            default=[],
            help="Clear relation (attribute) specified as <app_name>.<model_name>:<attr1>,<attr2>,...",
        )
        parser.add_argument(
            "--quiet",
            default=False,
            help="Don't print stuff.",
            action="store_true",
        )

    def handle(self, *args, **options):
        model_name = options["model_name"]
        dry_run = options["dry_run"]
        exclude = options["exclude"]
        quiet = options["quiet"]
        for mname in exclude:
            # Just to make sure they're correct
            apps.get_model(mname)
        clear_data = defaultdict(set)
        for cname in options["clear"]:
            model_name_to_adj, attrs = cname.split(":")
            model_to_adj = apps.get_model(model_name_to_adj)
            attrs = attrs.split(",")
            if not attrs:
                raise ValueError(
                    f"Specified relations to clear doesn't contain any attribute names. Value: {cname}"
                )
            clear_data[model_to_adj].update(attrs)
        model = apps.get_model(model_name)
        root_pk = options["pk"]
        root_obj = model.objects.get(pk=root_pk)
        if dry_run and not quiet:
            print("!! Dry run - nothing will be saved !!")
        collector = get_inf_collector()
        collector.EXCLUDE_MODELS = exclude
        collector.collect(root_obj)
        related_objects = collector.get_collected_objects()
        if not quiet:
            print(
                f"Initial find: {len(related_objects)} objects. "
                f"Some may be removed from the collection during the clone process. "
                f"(For instance superclasses of multi-table inheritance models)"
            )
        data = get_model_formatted_dict(related_objects)
        cloner = LiveCloner(data=data)
        for cmodel, attrs in clear_data.items():
            cloner.add_clear_attrs(cmodel, *attrs)
        with transaction.atomic(durable=True):
            cloner()
            if dry_run:
                transaction.set_rollback(True)
                if not quiet:
                    print("!! Dry run - nothing was saved !!")
        if not quiet:
            print("=" * 80)
            print(f"{len(cloner.log)} items recorded")
            for item in cloner.log:
                model = item["mod"]
                if model:
                    model_name = f"{model._meta.app_label}.{model._meta.model_name}"
                else:
                    model_name = "GLOBAL"
                print(
                    model_name.ljust(40),
                    item["act"].ljust(30),
                    item["msg"],
                )
