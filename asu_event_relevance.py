"""Deprecated compatibility shim. The relevance policy this module used to
own directly has moved to event_relevance.py - a source-agnostic scoring
model shared by ASU Events, Arizona Technology Council, and future sources
(Venture Cafe, Meetup, Luma, Eventbrite, ...). See event_relevance.py's
module docstring for the full model.

Kept only so `from asu_event_relevance import classify_relevance` and
test_asu_event_relevance.py (the pre-scoring-model regression baseline)
keep working unmodified. New code should import event_relevance directly.
"""
from event_relevance import classify_relevance  # noqa: F401
