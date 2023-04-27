import doctest
import os

from django.test import TestCase

from dolly.core import BaseRemapper
from dolly.exceptions import CyclicOrMissingDependencyError
from dolly_testing.models import A
from dolly_testing.models import B
from dolly_testing.models import Meeting
from dolly_testing.models import MeetingGroup
from dolly_testing.models import Organisation
from dolly_testing.models import Proposal
from dolly_testing.models import SingletonFlag
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

    def test_track_obj_handled(self):
        remapper = self._mk_one()
        remapper.track_obj(self.org, 1)
        remapper.prepped_models.add(Organisation)
        self.assertEqual(self.org, remapper.get_remap_obj(Organisation, 1))

    def test_track_obj_adding_duplicate(self):
        remapper = self._mk_one()
        remapper.track_obj(self.org, 1)
        two = Organisation.objects.create()
        with self.assertRaises(ValueError):
            remapper.track_obj(two, 1)

    def test_get_remap_obj_from_field(self):
        remapper = self._mk_one()
        remapper.track_obj(self.org, 1)
        f = Meeting._meta.get_field("organisation")
        remapper.prepped_models.add(Organisation)
        self.assertEqual(self.org, remapper.get_remap_obj_from_field(self.meeting, f))

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

    def test_sort_with_cyclic_dependency(self):
        remapper = self._mk_one()
        a_obj = A.objects.create(
            name="A",
        )
        b_obj = B.objects.create(name="B")
        remapper.data[A] = {a_obj}
        remapper.data[B] = {b_obj}
        self.assertRaises(CyclicOrMissingDependencyError, remapper.sort)

    def test_sort_with_cyclic_dependency_but_ignored_attributes(self):
        remapper = self._mk_one()
        a_obj = A.objects.create(
            name="A",
        )
        b_obj = B.objects.create(name="B")
        remapper.data[A] = {a_obj}
        remapper.data[B] = {b_obj}
        remapper.add_clear_attrs(A, "friend")
        self.assertEqual([A, B], remapper.sort())

    def test_explicit_dependency_and_sort(self):
        remapper = self._mk_one()
        remapper.data[Organisation] = {Organisation.objects.get(pk=1)}
        remapper.data[Meeting] = {Meeting.objects.get(pk=1)}
        remapper.data[Proposal] = {Proposal.objects.get(pk=1)}
        remapper.data[SingletonFlag] = {SingletonFlag.objects.get(pk=1)}
        sorted_models = remapper.sort()
        self.assertGreater(
            sorted_models.index(Meeting), sorted_models.index(Organisation)
        )
        self.assertGreater(sorted_models.index(Proposal), sorted_models.index(Meeting))
        remapper.add_explicit_dependency(SingletonFlag, Proposal)
        sorted_models = remapper.sort()
        self.assertGreater(
            sorted_models.index(SingletonFlag), sorted_models.index(Proposal)
        )

    def test_add_defer_via_null(self):
        remapper = self._mk_one()
        remapper.add_defer_via_null(MeetingGroup, "delegated_to")
        self.assertEqual({MeetingGroup: {"delegated_to": []}}, remapper.deferred_map)
        self.assertEqual({MeetingGroup: {"delegated_to"}}, remapper.defer_via_null)

    def test_add_defer_via_null_bad_name(self):
        remapper = self._mk_one()
        with self.assertRaises(TypeError):
            remapper.add_defer_via_null(MeetingGroup, "404")

    def test_add_defer_via_null_not_nullable(self):
        remapper = self._mk_one()
        with self.assertRaises(TypeError):
            remapper.add_defer_via_null(MeetingGroup, "meeting")
