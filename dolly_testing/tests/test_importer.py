import os

from django.core import serializers
from django.test import TestCase

from dolly.core import Importer
from dolly_testing.models import Meeting
from dolly_testing.models import Organisation
from dolly_testing.models import Proposal

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

    def test_guard_against_returning_same_object(self):
        importer = self._mk_one()
        deserialized_org = None
        for item in importer.data[Organisation]:
            deserialized_org = item
            break
        self.assertEqual(Organisation, deserialized_org.object.__class__)
        org = deserialized_org.object
        importer.tracked_data[Organisation] = {org.pk: org}
        with self.assertRaises(ValueError):
            importer.get_remap_obj_from_existing(org)
        self.assertFalse(importer.same_pk_allowed(Organisation, org.pk))
        importer.allow_same_pk[Organisation].add(org.pk)
        self.assertTrue(importer.same_pk_allowed(Organisation, org.pk))
        self.assertEqual(org, importer.get_remap_obj_from_existing(org))

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
        self.assertEqual(
            self.org, importer.get_remap_obj_from_existing(deserialized_org.object)
        )
        self.assertEqual({1: -1}, importer.pk_map[Organisation])
