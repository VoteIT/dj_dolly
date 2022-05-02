import doctest
import os

from django.test import TestCase

from dolly.core import BaseRemapper
from dolly_testing.models import Meeting
from dolly_testing.models import Organisation
from dolly_testing.testing import options

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_FN = os.path.join(BASE_DIR, "fixtures", "dolly_testing.yaml")


class BaseRemapperTests(TestCase):
    fixtures = ["dolly_testing"]

    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.get(pk=1)
        cls.meeting = Meeting.objects.get(pk=1)

    def _mk_one(self):
        return BaseRemapper()

    def test_docs(self):
        from dolly import core

        result = doctest.testmod(core, optionflags=options)
        self.assertFalse(result.failed)

    def test_track_obj_require_changed(self):
        remapper = self._mk_one()
        remapper.track_obj(self.org, 1)
        with self.assertRaises(ValueError):
            remapper.get_remap_obj(Organisation, 1)

    def test_track_obj_same_allowed(self):
        remapper = self._mk_one()
        remapper.track_obj(self.org, 1, allow_same=True)
        self.assertEqual(self.org, remapper.get_remap_obj(Organisation, 1))

    def test_track_obj_adding_duplicate(self):
        remapper = self._mk_one()
        remapper.track_obj(self.org, 1)
        with self.assertRaises(ValueError):
            remapper.track_obj(self.org, 1)

    def test_get_remap_obj_from_field(self):
        remapper = self._mk_one()
        remapper.track_obj(self.org, 1, allow_same=True)
        f = Meeting._meta.get_field("organisation")
        self.assertEqual(self.org, remapper.get_remap_obj_from_field(self.meeting, f))

    def test_get_remap_from_existing(self):
        remapper = self._mk_one()
        remapper.track_obj(self.org, 1, allow_same=True)
        remapper.track_obj(self.meeting, 1)
        self.assertEqual(self.org, remapper.get_remap_obj_from_existing(self.org))
        with self.assertRaises(ValueError):
            # Same pk
            remapper.get_remap_obj_from_existing(self.meeting)

    def test_get_old_pk(self):
        remapper = self._mk_one()
        remapper.track_obj(self.org)
        self.org.pk = 10
        remapper.register_new_pk(self.org, 1)
        self.assertEqual(1, remapper.get_old_pk(self.org))
        self.assertIsNone(remapper.get_old_pk(self.meeting))

    def test_register_new_pk(self):
        remapper = self._mk_one()
        remapper.track_obj(self.org)
        self.org.pk = 10
        remapper.register_new_pk(self.org, 1)
        with self.assertRaises(AssertionError):
            remapper.register_new_pk(self.org, 1)
        self.assertEqual(1, remapper.get_old_pk(self.org))

    def test_is_new(self):
        remapper = self._mk_one()
        remapper.track_obj(self.org)
        self.org.pk = 10
        remapper.register_new_pk(self.org, 1)
        self.assertTrue(remapper.is_new(self.org))
        self.org.pk = -1
        self.assertFalse(remapper.is_new(self.org))
