import doctest

from django.test import TestCase

from dolly_testing.testing import options


class DocTests(TestCase):
    fixtures = ["dolly_testing"]

    def run_file(self, fn):
        result = doctest.testfile(fn, optionflags=options)
        self.assertFalse(result.failed, f"Failing {fn}")

    def test_readme(self):
        self.run_file("../../README.md")
