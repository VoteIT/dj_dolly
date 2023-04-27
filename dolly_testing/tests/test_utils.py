import doctest
import unittest

from deep_collector.core import DeepCollector
from django.contrib.auth.models import User
from django.test import TestCase

from dolly.exceptions import CrossLinkedCloneError
from dolly.utils import get_data_id_struct
from dolly.utils import get_model_formatted_dict
from dolly.utils import get_nat_key
from dolly.utils import get_parents
from dolly.utils import safe_clone
from dolly_testing import models
from dolly_testing.models import DiffProposal
from dolly_testing.models import Meeting
from dolly_testing.models import Proposal
from dolly_testing.testing import options


class SafeCloneOutsideTransactionTests(unittest.TestCase):
    def test_no_transaction(self):
        with self.assertRaises(RuntimeError):
            safe_clone(object())


class SafeCloneTests(TestCase):
    fixtures = ["dolly_testing"]

    @classmethod
    def setUpTestData(cls):
        cls.meeting = Meeting.objects.get(pk=1)

    def _obj_key(self, obj):
        assert obj.pk is not None
        return f"{get_nat_key(obj)}.{obj.pk}"

    def test_collection_after_clone_yields_same_results(self):
        safe_clone(
            self.meeting,
            exclude_models=[
                "auth.user",
                "dolly_testing.organisation",
                "dolly_testing.tag",
            ],
        )

    def test_collection_tags_causes_problems(self):
        with self.assertRaises(CrossLinkedCloneError):
            safe_clone(
                self.meeting,
                exclude_models=[
                    "auth.user",
                    "dolly_testing.organisation",
                ],
            )


class UtilTests(TestCase):
    fixtures = ["dolly_testing"]

    def test_util_docs(self):
        from dolly import utils

        doctest.testmod(utils, optionflags=options)

    def test_get_data_id_struct(self):
        org = models.Organisation.objects.get(pk=1)
        collector = DeepCollector()
        collector.collect(org)
        related_objects = collector.get_collected_objects()
        collected_data = get_model_formatted_dict(related_objects)
        result = get_data_id_struct(collected_data)
        self.assertEqual({1}, result.pop(models.Organisation))
        self.assertEqual({1}, result.pop(models.Meeting))
        self.assertEqual({1, 2}, result.pop(models.MeetingGroup))
        self.assertEqual({1, 2}, result.pop(models.AgendaItem))
        self.assertEqual({1, 2}, result.pop(models.Proposal))
        self.assertEqual({2}, result.pop(models.DiffProposal))
        self.assertEqual({1}, result.pop(models.Text))
        self.assertEqual({1, 2}, result.pop(models.MeetingRole))
        self.assertEqual({1}, result.pop(models.OrganisationRole))
        self.assertEqual({1, 2}, result.pop(models.Tag))
        self.assertEqual({1}, result.pop(models.SingletonFlag))
        self.assertEqual({1}, result.pop(models.NullableRelated))
        self.assertEqual({1, 3}, result.pop(User))  # Unrelated user 2 skipped
        self.assertFalse(result)

    def test_get_parents(self):
        diff_prop = DiffProposal.objects.get(pk=2)
        proposal = Proposal.objects.get(pk=1)
        meeting = Meeting.objects.get(pk=1)
        self.assertEqual(diff_prop.proposal_ptr, list(get_parents(diff_prop))[0])
        self.assertFalse(get_parents(proposal))
        self.assertFalse(get_parents(meeting))
