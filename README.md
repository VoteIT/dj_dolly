# Django Dolly - cloning, importing and exporting

## Short summary

This software was built to handle complex imports or duplications when there's 
already an existing system. I.e. merging a new structure to a live database
or cutting a resource tree from the database, exporting it, changing the data
and then importing it again without loosing any relations.

If you only want to import data once to a newly built system you probably don't need
something like this.

## Narrative docs

Within the project there's a Django package called `dolly_testing`. It's only for unittest
and this doctest. The models reflect roughly how the VoteIT.se-project is modelled, but
we've also added some relations that will reflect typically tricky situations for cloning/importing
in Django.

An important structure in the project is the relation between these parts:

Organisation -> Meeting -> Agenda item -> Proposal

We've also imported a fixture already, with some users, roles, tags etc. 
The models are in `dolly_testing/models.py`

There's no dependency to VoteIT and you don't need to understand how VoteIT works though.

```python

>>> from dolly_testing.models import Organisation, Meeting

``` 

### Cloning a tree of objects

- An existing a resource and everything related to it.
- Parts of a resource tree but maintaining relations to some objects.
- A resource tree and clearing some relations while maintaining others.
- Handles subclasses from concrete models (multiple inheritance)
- Verbose error checking to guard against accidental cross-linking between tree structures.
- Automatically handles ordering.

We'll clone a meeting that belongs to an organisation.
We don't want to clone the organisation itself, and we don't want to clone the users.

The cloner accepts a data structure where the keys are models and values are sets of objects
corresponding to the same model.

To collect the objects we'll want to clone we use DeepCollector, but with a setting that doesn't limit
the number of collected objects. We'll ignore User and Organisation
from the collection to maintain their references.

The examples bellow use the LiveCloner directly,
but it's usually a good idea to use
`dolly.utils.safe_clone` since it double-checks your collector to make sure
nothing was cross-linked.

```python

>>> from dolly.utils import get_inf_collector, get_model_formatted_dict
>>> from django.contrib.auth.models import User

>>> meeting = Meeting.objects.get(pk=1)
>>> collector = get_inf_collector(exclude_models=["auth.user", "dolly_testing.organisation", "dolly_testing.tag"])
>>> collector.collect(meeting)
>>> data = get_model_formatted_dict(collector.get_collected_objects())

>>> Meeting in data
True
>>> meeting in data.get(Meeting)
True
>>> User not in data
True
>>> Organisation not in data
True

``` 

While not enforced by the code, the cloning process should always be run within a transaction.
Note that the cloning process causes all pointers to change to the new objects!

The LiveCloner has some methods to handle cloned objects though. It only keeps
references in memory though, they're not persistent in any way.

```python

>>> from dolly.core import LiveCloner
>>> from django.db import transaction


>>> initial_meeting_pk = meeting.pk
>>> cloner = LiveCloner(data=data)
>>> with transaction.atomic(durable=True):
...     cloner()
...     
>>> initial_meeting_pk == meeting.pk
False
>>> cloner.is_new(meeting)
True
>>> original_meeting = cloner.get_original(meeting)
>>> original_meeting.pk == initial_meeting_pk
True

``` 

Our newly created objects maintained links to the organisation and the users, (author)
The Proposal objects have an author that was maintained.

```python

>>> original_meeting.organisation is not None
True
>>> original_meeting.organisation == meeting.organisation
True

>>> from dolly_testing.models import Proposal
>>> prop = list(cloner.data.get(Proposal))[0]
>>> prop.author.username
'org_person'

>>> orig_prop = cloner.get_original(prop)
>>> orig_prop.author == prop.author
True

``` 

However, we may want to clear some relations. 
Cloners can register handlers to manipulate forward key relations.
We'll clear the author relation for proposal. We need to reload the original meeting.

```python

>>> meeting = Meeting.objects.get(pk=1)
>>> collector = get_inf_collector(exclude_models=["auth.user", "dolly_testing.organisation", "dolly_testing.tag"])
>>> collector.collect(meeting)
>>> data = get_model_formatted_dict(collector.get_collected_objects())

>>> cloner = LiveCloner(data=data)
>>> cloner.add_clear_attrs(Proposal, 'author')
>>> with transaction.atomic(durable=True):
...     cloner()
...     

>>> prop = list(cloner.data[Proposal])[0]
>>> prop.author is None
True

``` 

One big caveat when cloning is that any models that inherit from another concrete model
mustn't be cloned. In this code, the DiffProposal is a subclass of proposal.
So when cloning all Proposal objects that have a corresponding DiffProposal must be removed.
They'll be created anyway during the cloning process.

This is done automatically.

They're only removed from the dataset if they actually have a corresponding subclass.

```python

>>> from dolly_testing.models import DiffProposal

>>> meeting = Meeting.objects.get(pk=1)
>>> collector = get_inf_collector(exclude_models=["auth.user", "dolly_testing.organisation", "dolly_testing.tag"])
>>> collector.collect(meeting)
>>> data = get_model_formatted_dict(collector.get_collected_objects())

>>> len(data[Proposal])
2
>>> len(cloner.data[Proposal])
1
>>> len(data[DiffProposal]) == len(cloner.data[DiffProposal]) == 1
True

``` 

There's a utility called `safe_clone` you should normally run while testing,
since it tries to collect the initial object once more after cloning to make sure
nothing was cross-linked. For instance tags would be duplicated and assigned
to the old objects as well if we would include it in the initial collection. (Due to
how M2M realtions work in Django.) If something like that occurs we get a
`CrossLinkedCloneError`.

It also enforces atomic blocks which is nice.

```python

>>> from dolly.utils import safe_clone

>>> meeting = Meeting.objects.get(pk=1)
>>> with transaction.atomic():
...     safe_clone(meeting, exclude_models=["auth.user", "dolly_testing.organisation"])  # Removed tag!
Traceback (most recent call last):
...
dolly.exceptions.CrossLinkedCloneError

``` 


### Importing objects

- Making sure the import only appends objects and never overwrites existing objects.
- Reusing existing objects instead of creating new ones.
- Testing validity of imported objects.
- Automatically handles ordering. 
  (Your import file doesn't need to be in the correct order!)
- Can rename and reassign models with multiple inheritance.

Some caveats:

- Everything you want to import needs to fit into application memory. Since relations
  and primary keys need to be updated before actually saving.
- Multiple inheritance objects *must* have an object corresponding to their superclass
  with the same primary key! This is a big difference from cloning. 
  It's also djangos default behaviour, but it's worth noting.

We'll run the imports from the same fixture file we've used in the tests so far.
So, let's get a count first.

We want to keep track of Tags here too, since they only link to other objects
as M2M-relations which make them special.

```python

>>> from dolly_testing.models import Tag
>>> from dolly.core import Importer

>>> count = {}
>>> for m in (Meeting, DiffProposal, Proposal, User, Tag):
...     count[m] = m.objects.count()
...
>>> [(k.__name__, v) for k,v in count.items()]
[('Meeting', 3), ('DiffProposal', 3), ('Proposal', 6), ('User', 3), ('Tag', 2)]

>>> importer = Importer.from_fp("./dolly_testing/fixtures/dolly_testing.yaml")

``` 

Since we've already run this importer once, it will cause an exception.

```python

>>> with transaction.atomic():
...     importer()
Traceback (most recent call last):
...
django.db.utils.IntegrityError: UNIQUE constraint failed: auth_user.username

``` 

Since we can't have users with the same username, things break.
The importer can handle this by auto-finding things via a specific attribute.

It's specified per class. We'll add tags too.
(We need to create a new importer. There's no reset function yet.)

```python

>>> importer = Importer.from_fp("./dolly_testing/fixtures/dolly_testing.yaml")
>>> importer.add_auto_find_existing(User, 'username')
>>> importer.add_auto_find_existing(Tag, 'name')
>>> with transaction.atomic():
...     importer()
...

>>> count = {}
>>> for m in (Meeting, DiffProposal, Proposal, User, Tag):
...     count[m] = m.objects.count()
...
>>> [(k.__name__, v) for k,v in count.items()]
[('Meeting', 4), ('DiffProposal', 4), ('Proposal', 8), ('User', 3), ('Tag', 2)]

``` 

As you can see, there are some new proposals, diffproposals and meetings but
the same count for tag and user. 
The relations for tags and users have been updated though.

```python

>>> first_prop = Proposal.objects.order_by('pk').first()
>>> last_prop = Proposal.objects.order_by('pk').first()
>>> last_tag = Tag.objects.order_by('pk').last()
>>> first_prop in last_tag.proposal_tags_set.all()
True
>>> last_prop in last_tag.proposal_tags_set.all()
True

```


### Pre and post-processing data

This works for both imports and cloning.

* Pre-save hooks gets called exactly before save, after remaps. Use it to morph data.
* Post-save gets called when the new pk's registered so functions like `is_clone` work. 
  Use it to validate data since any exception will cause the transaction to abort.

To register a method, create a callable that accepts the remapper as first argument,
and *values as second argument where values will be the models of the same.

In this example we'll create a method that checks if newly imported users have
usernames that will clash with existing users, and simply randomize them in that case.

Note that finding existing objects is done before running pre_save too, so those
objects won't be passed to this method.

In this example we'll simply assign a new username rather than finding them.

```python

>>> def random_userid(importer, *users):
...     for user in users:
...         user.username = f"{user.username}-new"
...

>>> importer = Importer.from_fp("./dolly_testing/fixtures/dolly_testing.yaml")
>>> importer.add_pre_save(User, random_userid)
>>> importer.add_auto_find_existing(Tag, 'name')
>>> with transaction.atomic():
...     importer()
...

>>> count = {}
>>> for m in (Meeting, DiffProposal, Proposal, User, Tag):
...     count[m] = m.objects.count()
...
>>> [(k.__name__, v) for k,v in count.items()]
[('Meeting', 5), ('DiffProposal', 5), ('Proposal', 10), ('User', 6), ('Tag', 2)]

>>> last_user = User.objects.all().order_by('pk').last()
>>> last_user.username
'outsider-new'

```

Post-save works exactly the same, use it to validate data. Any raised exception there
will cause the database to roll back the transaction.


### Managements commands

If you include `dolly` in your settings file you'll have access to
clone_tree and import_tree. They're verbose by default and you can use them
to run imports or clone a structure from the command line. Always call them with
`--dry-run` first to see a report of what would be done!


### Admin integration

There's an action included you may want to try when developing. It does a dry-run
clone of existing objects and reports results.

The action is here:

```python

>>> from dolly.admin import report_structure

```

An integration example here:
```python

>>> from dolly_testing.admin import MeetingAdmin

```

### Bug-reports, suggestions, patches?

https://github.com/VoteIT/dj_dolly