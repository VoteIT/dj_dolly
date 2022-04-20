from io import StringIO

import yaml
from django.test import TestCase

from dolly.core import Exporter
from dolly.utils import get_inf_collector
from dolly.utils import get_model_formatted_dict
from dolly.utils import get_nat_key
from dolly_testing.models import Meeting
from dolly_testing.models import Organisation


class ExporterTests(TestCase):
    fixtures = ["dolly_testing"]

    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.get(pk=1)
        cls.meeting = Meeting.objects.get(pk=1)
        collector = get_inf_collector()
        collector.collect(cls.org)
        cls.related_objects = collector.get_collected_objects()


    def _mk_one(self, data=None):
        return Exporter(
            data=data and data or self.related_objects,
        )

    def test_serialize_stringio(self):
        exporter = self._mk_one()
        stream = StringIO()
        exporter.serialize(stream)
        stream.seek(0)
        rows = yaml.safe_load(stream)
        self.assertEqual(len(rows), len(exporter.data))
        for (model, values) in get_model_formatted_dict(exporter.data).items():
            nkey = get_nat_key(model)
            found = [x for x in rows if x["model"] == nkey]
            self.assertEqual(len(values), len(found))
