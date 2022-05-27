from __future__ import annotations

import os
import sys
import traceback
from collections import defaultdict

from django.apps import apps
from django.core import serializers
from django.core.management import BaseCommand
from django.db import transaction

from dolly.core import Importer


class Command(BaseCommand):
    help = "Import a specific export file. Won't overwrite objects with the same pk"

    def add_arguments(self, parser):
        parser.add_argument(
            "fn",
            help="Filename, relative to current working directory",
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
        # parser.add_argument(
        #     "-e",
        #     "--exclude",
        #     action="append",
        #     default=[],
        #     help="Exclude model <app_name>.<model_name - It's usually a good idea to exclude auth.user. "
        #     "Relations will be kept even if the model is excluded.",
        # )
        parser.add_argument(
            "-r",
            "--reuse",
            action="append",
            default=[],
            help="Reuse existing objects in db, found via attribute specified as "
            "<app_name>.<model_name>:<attr1>,<attr2>,...",
        )
        parser.add_argument(
            "--quiet",
            default=False,
            help="Don't print stuff.",
            action="store_true",
        )

    def handle(self, *args, **options):
        rel_fn = options["fn"]
        filename = os.path.join(os.getcwd(), rel_fn)
        if not os.path.isfile(filename):
            sys.exit(f"File not found: {filename}")
        dry_run = options["dry_run"]
        quiet = options["quiet"]
        if dry_run and not quiet:
            print("!! Dry run - nothing will be saved !!")
        reuse_objects = defaultdict(set)
        for rname in options["reuse"]:
            model_name_to_adj, attrs = rname.split(":")
            model_to_adj = apps.get_model(model_name_to_adj)
            attrs = attrs.split(",")
            if not attrs:
                raise ValueError(
                    f"Specified relations to clear doesn't contain any attribute names. Value: {cname}"
                )
            reuse_objects[model_to_adj].update(attrs)
        importer = Importer.from_filename(filename)
        for rmodel, attrs in reuse_objects.items():
            importer.add_auto_find_existing(rmodel, *attrs)
        try:
            with transaction.atomic(durable=True):
                importer()
                if dry_run:
                    transaction.set_rollback(True)
        except Exception as exc:
            # file=sys.stdout
            traceback.print_exc()
        if not quiet:
            print("=" * 80)
            print(f"{len(importer.log)} items recorded")
            for item in importer.log:
                model_name = item["mod"]
                if not model_name:
                    model_name = "GLOBAL"
                print(
                    model_name.ljust(40),
                    item["act"].ljust(30),
                    item["msg"],
                )
            print("-" * 80)
            print("!! Dry run - nothing was saved !!")
