from django.test import TestCase

from dolly_testing.models import BookReview
from dolly_testing.models import Child
from dolly_testing.models import Grandparent
from dolly_testing.models import Parent


def _reset(obj):
    obj.pk = None
    obj.id = None
    obj._state.adding = True


class ChildTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.child = Child.objects.create(name="child")
        cls.parent = Parent.objects.create(name="parent")
        cls.grandparent = cls.parent.grandparent_ptr

    def test_inheritance_one_level(self):
        parent_count = Parent.objects.count()
        grandparent_count = Grandparent.objects.count()
        initial_pk = self.parent.pk
        _reset(self.parent)
        self.parent.save()
        self.assertNotEqual(initial_pk, self.parent.pk)
        self.assertEqual(parent_count + 1, Parent.objects.count())
        self.assertEqual(grandparent_count + 1, Grandparent.objects.count())

    # def test_inheritance_two_levels(self):
    #     initial_pk = self.child.pk
    #     _reset(self.child.parent)
    #     _reset(self.child)
    #     self.child.save(force_insert=True)
    #     self.assertNotEqual(initial_pk, self.child.pk)


# class BookReviewTests(TestCase):
#     @classmethod
#     def setUpTestData(cls):
#         cls.br = BookReview.objects.create(name="Hello")
#
#     def test_inheritance(self):
#         initial_pk = self.br.pk
#         _reset(self.br)
#         self.br.save()
#         self.assertNotEqual(initial_pk, self.br.pk)
