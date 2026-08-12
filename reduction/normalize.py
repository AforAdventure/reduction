"""Turning apples and oranges into fruit.

Two jobs live here:

  1. Normalising NAMES, so "Café Adélia" and "Cafe Adelia Lda." can be
     recognised as the same place.
  2. Normalising RATINGS, so a 4.4 on Google and a 4.4 on Yelp stop
     pretending to be the same statement.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import FrozenSet

from .models import PlatformProfile

# Words that describe what a place IS, not which place it is. Dropping them
# stops "Restaurant Bella" and "Bella" from looking like strangers. Covers the
# common US + Western European cases; extend as you add countries.
GENERIC_TOKENS: FrozenSet[str] = frozenset(
    """
    restaurant restaurante ristorante restaurang restaurace resto
    cafe caffe caffè kaffe koffie kavarna
    bar bistro brasserie taberna taverna tavern trattoria osteria
    pizzeria churrasqueira cervejaria marisqueira gastropub pub inn
    grill grillhouse kitchen eatery diner steakhouse
    the la le les el los las il lo gli i de da do dos das du des der die das
    and et und e y i
    ltd limited llc inc incorporated gmbh srl sarl sa sas bv nv ab as oy
    lda unipessoal spa plc co company
    """.split()
)

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    """Fold "Adélia" down to "Adelia".

    NFKD splits an accented character into (letter + accent mark); we then
    throw away anything Unicode classifies as a combining mark.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name(name: str) -> str:
    """Lowercase, de-accent, strip punctuation, collapse whitespace."""
    folded = strip_accents(name).lower()
    folded = _PUNCT.sub(" ", folded)
    return _SPACES.sub(" ", folded).strip()


def name_tokens(name: str) -> FrozenSet[str]:
    """The distinctive words of a name, with the boilerplate removed.

    If a name is *nothing but* boilerplate ("The Restaurant"), we keep the
    boilerplate rather than return an empty set — an empty set would match
    every other empty set, which is exactly the wrong answer.
    """
    tokens = normalize_name(name).split()
    distinctive = [t for t in tokens if t not in GENERIC_TOKENS]
    return frozenset(distinctive or tokens)


def name_similarity(a: str, b: str) -> float:
    """How alike are two restaurant names? 0.0 to 1.0.

    We blend two different notions of similarity because each fails alone:

      * Token overlap (Jaccard) shrugs off word order and extra words, but
        cannot see that "Adelia" and "Adelias" are near-identical.
      * Character similarity catches typos and inflections, but is fooled by
        reordering and by long shared boilerplate.

    Taking the max is deliberate: either kind of evidence is enough.
    """
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return 0.0

    overlap = len(ta & tb) / len(ta | tb)

    seq = SequenceMatcher(
        None, " ".join(sorted(ta)), " ".join(sorted(tb))
    ).ratio()

    # Full containment ("Adelia Marisqueira" swallowing "Adelia") is strong
    # evidence that plain token overlap would score at only 0.5.
    #
    # But we demand at least two shared words before trusting it. Otherwise
    # "Bella" would be judged near-identical to "Bella Vista" — and in a city
    # centre those are usually two different restaurants.
    contained = (ta <= tb or tb <= ta) and min(len(ta), len(tb)) >= 2

    return max(overlap, seq, 0.95 if contained else 0.0)


def shrink_rating(rating: float, review_count: int, profile: PlatformProfile) -> float:
    """Pull a rating toward the platform average in proportion to our doubt.

    This is the Bayesian average (the same trick IMDb uses on its Top 250):

        adjusted = (v / (v + m)) * R  +  (m / (v + m)) * C

    R is the observed rating, v how many reviews produced it, C the platform's
    prior mean, and m the review count at which we split the difference.

    With v = 0 the answer is simply C: knowing nothing, assume average.
    With v enormous, the prior washes out and the observed rating stands.
    A perfect 5.0 from nine friends of the owner lands somewhere sensible in
    between — which is the entire point.
    """
    v = max(0, review_count)
    m = profile.confidence_reviews
    return (v / (v + m)) * rating + (m / (v + m)) * profile.prior_mean


def to_common_scale(rating: float, profile: PlatformProfile) -> float:
    """Express a rating as "how unusual is this, for this platform?".

    We compute a z-score — distance from the platform's mean, measured in
    standard deviations — then map it onto a friendly 0-100 line where 50 is
    perfectly average and each standard deviation is worth 15 points.

    That is the IQ scale, borrowed shamelessly, and for the same reason: it
    makes an abstract statistical distance legible to a human being.
    """
    z = (rating - profile.prior_mean) / profile.prior_sd
    return max(0.0, min(100.0, 50.0 + 15.0 * z))
