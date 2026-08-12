"""Turning a pile of evidence into a single, defensible number.

The temptation is to average the star ratings and be done. Resist it. A naive
average is wrong three separate ways:

  1. It treats 5.0-from-9-reviews as better than 4.6-from-3,000.
  2. It treats a Google 4.4 and a Yelp 4.4 as equivalent claims.
  3. It rewards a place that one platform loves exactly as much as a place
     that four platforms independently agree on.

So instead of one average we do three things, in order: shrink, translate,
then discount by how much we actually believe the evidence.
"""

from __future__ import annotations

import math
import statistics
from typing import Dict, List

from .models import DISTINCTION_BONUS, PROFILES, Listing, Platform, ScoredVenue, Venue
from .normalize import shrink_rating, to_common_scale

NEUTRAL = 50.0  # The "we have no idea" score, on our 0-100 line.

# How fast extra platforms buy confidence. Diminishing returns: the jump from
# one source to two matters far more than from three to four.
CORROBORATION_FLOOR = 0.72  # Confidence granted by a single source.
CORROBORATION_DECAY = 0.45

SPREAD_TOLERANCE = 60.0  # Points of cross-platform disagreement we forgive.
MIN_AGREEMENT = 0.60  # Never discount below this on disagreement alone.


def platform_score(listing: Listing) -> float:
    """One listing's verdict, expressed on the shared 0-100 scale.

    Two steps, and the order matters. We shrink FIRST, in the platform's own
    units, so that thin evidence is pulled toward that platform's normal.
    Only then do we translate to the common scale.

    Shrinking after translating would be measuring doubt in the wrong units.
    """
    shrunk = shrink_rating(listing.rating, listing.review_count, listing.profile)
    return to_common_scale(shrunk, listing.profile)


def _evidence_weight(listing: Listing) -> float:
    """How loudly should this listing get to speak?

    Logarithmic, not linear. A platform with 3,000 reviews genuinely knows
    more than one with 30 — but not a hundred times more. The log keeps the
    biggest platform influential without letting it drown out the others.
    """
    return listing.profile.trust * math.log10(10 + listing.review_count)


def _corroboration(n_platforms: int) -> float:
    """Confidence earned by independent sources agreeing that a place exists."""
    return 1.0 - (1.0 - CORROBORATION_FLOOR) * (
        CORROBORATION_DECAY ** (n_platforms - 1)
    )


def _agreement(scores: List[float]) -> float:
    """Confidence lost when the sources contradict each other.

    If Google says wonderful and Yelp says mediocre, the honest response is
    not to split the difference and state it confidently — it is to state the
    midpoint *less* confidently.
    """
    if len(scores) < 2:
        return 1.0
    spread = statistics.pstdev(scores)
    return max(MIN_AGREEMENT, 1.0 - spread / SPREAD_TOLERANCE)


def score_venue(venue: Venue) -> ScoredVenue:
    """Collapse a venue's cross-platform evidence into one 0-100 score."""
    per_platform: Dict[Platform, float] = {}
    weights: Dict[Platform, float] = {}

    for listing in venue.listings:
        per_platform[listing.platform] = platform_score(listing)
        weights[listing.platform] = _evidence_weight(listing)

    total_weight = sum(weights.values())
    base = sum(per_platform[p] * weights[p] for p in per_platform) / total_weight

    scores = list(per_platform.values())
    spread = statistics.pstdev(scores) if len(scores) > 1 else 0.0

    # Source quality has to enter as *confidence*, not merely as relative
    # weight. A weight only decides who wins an argument between platforms —
    # so when a venue has just one source, its weight cancels out entirely and
    # a low-trust platform speaks with the same authority as Google. Folding
    # trust into confidence is what makes a lone weak source stay quiet.
    trust = sum(
        PROFILES[p].trust * weights[p] for p in per_platform
    ) / total_weight

    confidence = _corroboration(len(per_platform)) * _agreement(scores) * trust

    # The key move: confidence does not inflate a score, it pulls it toward
    # neutral. Weak evidence cannot make a restaurant look bad OR good — it
    # simply cannot make it look like much of anything.
    final = NEUTRAL + (base - NEUTRAL) * confidence

    # Michelin is added AFTER the discount, not inside it. The confidence
    # multiplier expresses "how sure are we what the crowd thinks" — and a
    # star is not the crowd. Multiplying it by our uncertainty about Yelp
    # would be mixing two unrelated kinds of doubt.
    bonus = sum(DISTINCTION_BONUS.get(d, 0.0) for d in venue.distinctions)
    final = min(100.0, final + bonus)

    return ScoredVenue(
        venue=venue,
        score=round(final, 2),
        distinction_bonus=round(bonus, 2),
        per_platform={p: round(s, 2) for p, s in per_platform.items()},
        corroboration=round(confidence, 3),
        disagreement=round(spread, 2),
    )
