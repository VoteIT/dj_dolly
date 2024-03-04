import doctest
from pkgutil import walk_packages

from django.test import TestCase

from dolly_testing.testing import options
import dolly


class DocTests(TestCase):
    fixtures = ["dolly_testing"]

    def run_file(self, fn):
        result = doctest.testfile(fn, optionflags=options)
        self.assertFalse(result.failed, f"Failing {fn}")

    def test_readme(self):
        self.run_file("../../README.md")


def load_tests(loader, tests, pattern):
    load_doctests(tests, dolly)
    return tests


def load_doctests(tests, package) -> None:
    """
    Load doctests from a specific package/module. Must be called from a test_ file with the following function:

    def load_tests(loader, tests, pattern):
        load_doctests(tests, <module>)
        return tests

    Where module is idproxy.core for instance.
    """
    opts = (
        doctest.NORMALIZE_WHITESPACE
        | doctest.ELLIPSIS
        | doctest.FAIL_FAST
        | doctest.IGNORE_EXCEPTION_DETAIL
    )
    for importer, name, ispkg in walk_packages(
        package.__path__, package.__name__ + "."
    ):
        tests.addTests(doctest.DocTestSuite(name, optionflags=opts))
