"""The assembly line, start to finish.

Every stage below is a plain function that takes data and returns data. That
is not an accident: it means the whole ranking can be tested with a list of
dictionaries and no network, no API keys, and no waiting.
"""

from __future__ import annotations

from typing import List, Optional

from .distinctions import MichelinIndex
from .matching import cluster_listings
from .models import Listing, ScoredVenue
from .scoring import score_venue


# A venue nobody has reviewed is not a bad restaurant — it is an unknown one.
# Scoring says so correctly (it lands near 50, "no information"), but a near-50
# score still wins the last slot on a short list. Eligibility is the right
# place to fix that, not the arithmetic.
DEFAULT_MIN_REVIEWS = 50


def rank(
    listings: List[Listing],
    limit: int = 10,
    min_platforms: int = 1,
    min_total_reviews: int = DEFAULT_MIN_REVIEWS,
    city: Optional[str] = None,
) -> List[ScoredVenue]:
    """Raw listings in, ranked venues out.

        gather -> cluster -> score -> filter -> sort -> take the top N

    min_platforms is the honest lever for quality. Setting it to 2 means
    "only show me places that at least two independent platforms have seen",
    which quietly eliminates most astroturfed listings — at the cost of
    dropping genuinely good places that only Google knows about. It is the
    recommended setting anywhere both Google and TripAdvisor have coverage.
    """
    venues = cluster_listings(listings)

    # Awards are attached after clustering, never before. A venue must be
    # whole — all its listings, all its name spellings, its averaged position
    # — before we ask whether the guide has anything to say about it.
    if city:
        MichelinIndex.for_city(city).annotate(venues)

    scored = [score_venue(v) for v in venues]

    kept = [
        s for s in scored
        if len(s.per_platform) >= min_platforms
        and s.venue.total_reviews >= min_total_reviews
    ]

    # Sort by score, then by review volume as a tie-breaker: when two places
    # are statistically indistinguishable, prefer the better-evidenced one.
    kept.sort(key=lambda s: (s.score, s.venue.total_reviews), reverse=True)
    return kept[:limit]


def format_table(results: List[ScoredVenue], city: Optional[str] = None) -> str:
    """A human-readable leaderboard, including the reasoning."""
    if not results:
        return "No restaurants matched the criteria."

    header = "Top {} restaurants{}".format(
        len(results), " in {}".format(city) if city else ""
    )
    lines = [header, "=" * len(header), ""]

    for i, result in enumerate(results, start=1):
        badge = (
            " " + " ".join(d.short for d in result.venue.distinctions)
            if result.venue.distinctions
            else ""
        )
        lines.append(
            "{:>2}. {:<34} {:>6.1f}".format(
                i, (result.venue.display_name + badge)[:34], result.score
            )
        )
        lines.append("    {}".format(result.explain()))
        lines.append(
            "    {:,} reviews across {} platform(s)".format(
                result.venue.total_reviews, len(result.per_platform)
            )
        )
        lines.append("")

    return "\n".join(lines)
