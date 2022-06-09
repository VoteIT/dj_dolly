import os

from django.contrib.auth.models import User
from django.core import serializers
from django.test import TestCase

from dolly.core import Importer
from dolly_testing.models import Meeting
from dolly_testing.models import MeetingGroup
from dolly_testing.models import Organisation
from dolly_testing.models import Proposal
from dolly_testing.models import Tag

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_FN = os.path.join(BASE_DIR, "fixtures", "dolly_testing.yaml")


class ImporterTests(TestCase):
    fixtures = ["dolly_testing"]

    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.get(pk=1)
        cls.meeting = Meeting.objects.get(pk=1)

    def get_fixture(self):
        with open(FIXTURE_FN, "r") as fixture:
            objects = list(
                serializers.deserialize(
                    "yaml",
                    fixture,
                    handle_forward_references=True,
                )
            )
        return objects

    def _mk_one(self):
        return Importer(
            data=self.get_fixture(),
        )

    def test_find_existing(self):
        importer = self._mk_one()
        importer.add_auto_find_existing(Organisation, "pk")
        importer.find_existing()
        self.assertIn(Meeting, importer.data)
        importer.add_auto_find_existing(Meeting, "name")
        importer.find_existing()
        self.assertNotIn(Meeting, importer.data)
        self.assertNotIn(Organisation, importer.data)
        self.assertIn(Meeting, importer.tracked_data)
        self.assertIn(Organisation, importer.tracked_data)
        importer.prepped_models.update((Organisation, Meeting))
        self.assertEqual(self.org, importer.get_remap_obj(Organisation, 1))
        self.assertEqual(self.meeting, importer.get_remap_obj(Meeting, 1))

    def test_sort(self):
        importer = self._mk_one()
        org_data = importer.data.pop(Organisation)
        importer.data[Organisation] = org_data
        curr_order = list(importer.data)
        self.assertLess(curr_order.index(Meeting), curr_order.index(Organisation))
        data_sorted = importer.sort()
        self.assertGreater(data_sorted.index(Meeting), data_sorted.index(Organisation))
        self.assertGreater(data_sorted.index(Proposal), data_sorted.index(Meeting))

    def test_match_and_update(self):
        importer = self._mk_one()
        # Change pk just for the test so existing_pk_map will be updated
        deserialized_org = importer.data[Organisation].pop()
        deserialized_org.object.pk = -1
        importer.data[Organisation].add(deserialized_org)
        found_qs = importer.match_and_update(Organisation, "name")
        self.assertEqual(self.org, found_qs.first())
        self.assertNotIn(Organisation, importer.data)
        # The import has pk -1 which should now map to the existing object
        self.assertEqual({-1: self.org}, importer.tracked_data[Organisation])
        importer.prepped_models.add(Organisation)
        self.assertEqual(
            self.org, importer.get_remap_obj_from_existing(deserialized_org.object)
        )
        self.assertEqual({1: -1}, importer.pk_map[Organisation])

    def test_pre_save_action(self):
        def change_name(self, *items):
            for obj in items:
                obj.name = "Hello world!"

        importer = self._mk_one()
        importer.add_auto_find_existing(User, "pk")
        importer.add_pre_save(Meeting, change_name)
        importer()
        meetings = Meeting.objects.order_by("pk")
        old_meeting = meetings.first()
        new_meeting = meetings.last()
        self.assertEqual(
            "First meeting",
            old_meeting.name,
        )
        self.assertEqual(
            "Hello world!",
            new_meeting.name,
        )

    def test_post_save_action(self):
        class MyCallable:
            def __init__(self):
                self.seen = False

            def __call__(self, cloner, *items):
                self.seen = bool(items)
                for obj in items:
                    obj.name = "Hello world!"

        my_callable = MyCallable()
        importer = self._mk_one()
        importer.add_auto_find_existing(User, "pk")
        importer.add_post_save(Meeting, my_callable)
        importer()
        meetings = Meeting.objects.order_by("pk")
        old_meeting = meetings.first()
        new_meeting = meetings.last()
        self.assertEqual(
            "First meeting",
            old_meeting.name,
        )
        self.assertEqual(
            "First meeting",
            new_meeting.name,
        )
        self.assertTrue(my_callable.seen)

    def test_m2m_relations_intact(self):
        importer = self._mk_one()
        importer.add_auto_find_existing(User, "pk")
        original_tag = Tag.objects.get(pk=2)
        importer()
        proposals = Proposal.objects.filter(name="Eat more veggies").order_by("pk")
        first_prop = proposals.first()
        last_prop = proposals.last()
        # Tags should be new
        self.assertIn(original_tag, first_prop.tags.all())
        self.assertNotIn(original_tag, last_prop.tags.all())
        self.assertEqual(1, last_prop.tags.count())
        # Users kept
        meeting_groups = MeetingGroup.objects.all().order_by("pk")
        first_mg = meeting_groups.first()
        last_mg = meeting_groups.last()
        self.assertEqual(
            set(first_mg.members.all().values_list("pk", flat=True)),
            set(last_mg.members.all().values_list("pk", flat=True)),
        )

    def test_pre_commit_hook(self):
        importer = self._mk_one()
        importer.add_auto_find_existing(User, "pk")

        def hook(imp: Importer):
            org = list(imp.data[Organisation])[0]
            org.name = "Whatever"
            org.save()

        importer.add_pre_commit(hook)
        importer()
        org = list(importer.data[Organisation])[0]
        self.assertEqual("Whatever", org.name)
