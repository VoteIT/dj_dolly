from collections import Counter
from copy import deepcopy

from django.contrib.auth.models import User
from django.db.models import Model
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.test import TestCase

from dolly.core import LiveCloner
from dolly.utils import get_inf_collector
from dolly.utils import get_model_formatted_dict
from dolly_testing.models import AgendaItem
from dolly_testing.models import DiffProposal
from dolly_testing.models import Meeting
from dolly_testing.models import MeetingGroup
from dolly_testing.models import MeetingRole
from dolly_testing.models import Organisation
from dolly_testing.models import OrganisationRole
from dolly_testing.models import Proposal
from dolly_testing.models import SingletonFlag
from dolly_testing.models import Tag
from dolly_testing.models import Text


class LiveClonerTests(TestCase):
    fixtures = ["dolly_testing"]

    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.get(pk=1)
        cls.meeting = Meeting.objects.get(pk=1)
        collector = get_inf_collector()
        collector.EXCLUDE_MODELS = ["auth.user"]
        collector.collect(cls.org)
        related_objects = collector.get_collected_objects()
        cls.fixture_data = get_model_formatted_dict(related_objects)

    def get_fixture(self):
        return self.fixture_data

    def _mk_one(self, data=None):
        return LiveCloner(
            data=data and data or self.get_fixture(),
        )

    def test_remove_superclasses(self):
        cloner = self._mk_one()
        self.assertEqual(2, len(cloner.data[Proposal]))
        self.assertEqual(1, len(cloner.data[DiffProposal]))
        cloner.remove_superclasses()
        self.assertEqual(1, len(cloner.data[DiffProposal]))
        self.assertEqual(1, len(cloner.data[Proposal]))
        diff_prop = list(cloner.data[DiffProposal])[0]
        parent_prop = diff_prop.proposal_ptr
        self.assertNotIn(parent_prop, cloner.data[Proposal])

    def test_prepare_clone_m2m(self):
        cloner = self._mk_one()
        cloner.prepare_clone()
        self.assertEqual({1: {"users": [3]}}, cloner.m2m_data.pop(Organisation))
        self.assertEqual({1: {"participants": [3, 1]}}, cloner.m2m_data.pop(Meeting))
        self.assertEqual({2: {"tags": [1]}}, cloner.m2m_data.pop(DiffProposal))
        self.assertEqual({1: {"tags": [2]}}, cloner.m2m_data.pop(Proposal))
        self.assertEqual({1: {"members": [1, 3]}}, cloner.m2m_data.pop(MeetingGroup))
        self.assertFalse(cloner.m2m_data)

    def test_count_duplicated(self):
        duplicated = {
            Proposal,
            Meeting,
            Organisation,
            Tag,
            MeetingRole,
            OrganisationRole,
            DiffProposal,
        }
        referenced = {User}
        counter = Counter()
        for m in duplicated | referenced:
            counter[m] = m.objects.count()
        cloner = self._mk_one()
        cloner()
        for m in duplicated:
            self.assertEqual(
                m.objects.count(),
                counter[m] * 2,
                f"Obj count for {m} doesn't match",
            )
        for m in referenced:
            self.assertEqual(m.objects.count(), counter[m])

    def test_remap_fks(self):
        cloner = self._mk_one()
        cloner()
        orgs = Organisation.objects.order_by("pk")
        meetings = Meeting.objects.order_by("pk")
        proposals = Proposal.objects.order_by("pk")
        diff_props = DiffProposal.objects.order_by("pk")

        self.assertEqual(2, orgs.count())
        self.assertEqual(2, meetings.count())
        self.assertEqual(4, proposals.count())
        self.assertEqual(2, diff_props.count())

        new_org = orgs.last()
        new_meeting = meetings.last()
        # Points to new org
        self.assertEqual(new_org, new_meeting.organisation)
        old_diff_prop = diff_props.first()
        new_diff_prop = diff_props.last()
        # User wasn't remapped
        self.assertEqual(old_diff_prop.author, new_diff_prop.author)
        # Points to another ai
        self.assertNotEqual(old_diff_prop.agenda_item, new_diff_prop.agenda_item)

    def test_remap_m2ms(self):
        cloner = self._mk_one()
        cloner()
        diff_props = DiffProposal.objects.order_by("pk")
        old_diff_prop = diff_props.first()
        new_diff_prop = diff_props.last()
        new_tags = new_diff_prop.tags.all()
        self.assertEqual(1, new_tags.count())
        self.assertNotEqual(old_diff_prop.tags.all(), new_diff_prop.tags.all())

    def test_relation_ignored_in_clone(self):
        collector = get_inf_collector()
        collector.EXCLUDE_MODELS = ["auth.user", "dolly_testing.singletonflag"]
        collector.collect(self.org)
        related_objects = collector.get_collected_objects()
        data = get_model_formatted_dict(related_objects)
        self.assertNotIn(SingletonFlag, data)
        cloner = self._mk_one(data)
        cloner()
        self.assertNotIn(SingletonFlag, cloner.data)
        diff_props = DiffProposal.objects.order_by("pk")
        old_diff_prop = diff_props.first()
        new_diff_prop = diff_props.last()
        self.assertEqual(old_diff_prop.flag, new_diff_prop.flag)

    def test_add_clear_attrs(self):
        cloner = self._mk_one()
        cloner.add_clear_attrs(DiffProposal, "flag")

    def test_add_clear_attrs_doesnt_exist(self):
        cloner = self._mk_one()
        with self.assertRaises(ValueError):
            cloner.add_clear_attrs(DiffProposal, "doesntexist")

    def test_add_clear_attrs_not_nullable(self):
        cloner = self._mk_one()
        with self.assertRaises(ValueError):
            # Can't be nulled
            cloner.add_clear_attrs(DiffProposal, "meeting")

    def test_add_clear_attrs_not_a_relation(self):
        cloner = self._mk_one()
        with self.assertRaises(ValueError):
            cloner.add_clear_attrs(DiffProposal, "name")

    def test_clear_attrs(self):
        cloner = self._mk_one()
        cloner.add_clear_attrs(Proposal, "author")
        cloner.add_clear_attrs(DiffProposal, "author")
        cloner()
        diff_props = DiffProposal.objects.order_by("pk")
        old_diff_prop = diff_props.first()
        new_diff_prop = diff_props.last()
        self.assertIsNotNone(old_diff_prop.author)
        self.assertIsNone(new_diff_prop.author)
        props = Proposal.objects.filter(diffproposal__isnull=True).order_by("pk")
        old_prop = props.first()
        new_prop = props.last()
        self.assertIsNotNone(old_prop.author)
        self.assertIsNone(new_prop.author)

    def test_clear_attrs_m2m(self):
        cloner = self._mk_one()
        cloner.add_clear_attrs(Proposal, "tags")
        cloner.add_clear_attrs(DiffProposal, "tags")
        cloner()
        diff_props = DiffProposal.objects.order_by("pk")
        old_diff_prop = diff_props.first()
        new_diff_prop = diff_props.last()
        self.assertTrue(old_diff_prop.tags.count())
        self.assertFalse(new_diff_prop.tags.count())
        props = Proposal.objects.filter(diffproposal__isnull=True).order_by("pk")
        old_prop = props.first()
        new_prop = props.last()
        self.assertTrue(old_prop.tags.count())
        self.assertFalse(new_prop.tags.count())

    def test_remap_m2ms_unsaved_clone(self):
        self.org.pk = None
        cloner = LiveCloner(data={Meeting: {self.meeting}})
        with self.assertRaises(ValueError):
            cloner.remap_m2ms(self.org)

    def test_get_remap_obj_not_prepped(self):
        cloner = self._mk_one()
        f = self.meeting._meta.get_field("organisation")
        with self.assertRaises(ValueError):
            cloner.get_remap_obj_from_field(self.meeting, f)

    def test_is_clone(self):
        cloner = self._mk_one()
        cloner()
        meetings = Meeting.objects.order_by("pk")
        old_meeting = meetings.first()
        new_meeting = meetings.last()
        self.assertFalse(cloner.is_new(old_meeting))
        self.assertTrue(cloner.is_new(new_meeting))

    def test_add_clear_attrs_bad_attr(self):
        cloner = self._mk_one()
        with self.assertRaises(ValueError):
            cloner.add_clear_attrs(Meeting, "name")

    def test_get_original(self):
        cloner = self._mk_one()
        cloner()
        meetings = Meeting.objects.order_by("pk")
        old_meeting = meetings.first()
        new_meeting = meetings.last()
        self.assertEqual(old_meeting, cloner.get_original(new_meeting))
