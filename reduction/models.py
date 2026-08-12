"""Core data types.

The whole system is built around one distinction:

    Listing  = one restaurant, as ONE platform sees it.
    Venue    = one real-world restaurant, assembled from several Listings.

Keeping those apart is what makes cross-referencing possible at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class Platform(str, Enum):
    GOOGLE = "google"
    YELP = "yelp"
    TRIPADVISOR = "tripadvisor"
    FOURSQUARE = "foursquare"


@dataclass(frozen=True)
class PlatformProfile:
    """How a given platform's rating numbers actually behave.

    Two platforms both saying "4.2" do not mean the same thing. Google's
    ratings cluster high; Yelp's reviewers are famously stingier. Before we
    can compare them we need to know each platform's centre of gravity.

    prior_mean / prior_sd are empirical starting points, not laws of nature.
    Recalibrate them from real data once you have some (see calibrate.py).
    """

    scale_max: float  # Foursquare rates out of 10; everyone else out of 5.
    prior_mean: float  # Typical rating on this platform, in its own units.
    prior_sd: float  # Typical spread, in its own units.
    confidence_reviews: int  # Review count at which we half-trust a rating.
    trust: float = 1.0  # Manual thumb on the scale for source quality.


PROFILES: Dict[Platform, PlatformProfile] = {
    Platform.GOOGLE: PlatformProfile(
        scale_max=5.0, prior_mean=4.30, prior_sd=0.45,
        confidence_reviews=250, trust=1.0,
    ),
    Platform.YELP: PlatformProfile(
        scale_max=5.0, prior_mean=3.75, prior_sd=0.75,
        confidence_reviews=60, trust=0.9,
    ),
    Platform.TRIPADVISOR: PlatformProfile(
        scale_max=5.0, prior_mean=4.05, prior_sd=0.65,
        confidence_reviews=120, trust=0.85,
    ),
    Platform.FOURSQUARE: PlatformProfile(
        scale_max=10.0, prior_mean=7.10, prior_sd=1.40,
        confidence_reviews=90, trust=0.7,
    ),
}


class Distinction(str, Enum):
    """A Michelin Guide award.

    Deliberately NOT a Platform, and deliberately not a rating.

    Everything in `Platform` is a crowd measurement: noisy, plentiful, and
    meaningful only relative to that platform's own habits. A Michelin star is
    the opposite — one expert verdict, rare, with no review count to weigh and
    no distribution to normalise against. Pushing it through shrinkage and
    z-scores would be answering a question it never asked.

    So it travels alongside the ratings rather than inside them: a badge to
    display, and a small bounded nudge to the score.
    """

    THREE_STARS = "three_stars"
    TWO_STARS = "two_stars"
    ONE_STAR = "one_star"
    BIB_GOURMAND = "bib_gourmand"
    GREEN_STAR = "green_star"  # Sustainability; orthogonal to the stars.

    @property
    def label(self) -> str:
        return {
            Distinction.THREE_STARS: "Three Michelin Stars",
            Distinction.TWO_STARS: "Two Michelin Stars",
            Distinction.ONE_STAR: "One Michelin Star",
            Distinction.BIB_GOURMAND: "Bib Gourmand",
            Distinction.GREEN_STAR: "Michelin Green Star",
        }[self]

    @property
    def short(self) -> str:
        return {
            Distinction.THREE_STARS: "★★★",
            Distinction.TWO_STARS: "★★",
            Distinction.ONE_STAR: "★",
            Distinction.BIB_GOURMAND: "Bib",
            Distinction.GREEN_STAR: "Green",
        }[self]


# Bonus points added AFTER the confidence discount, because a star is not a
# crowd estimate we are unsure of — it is a fact about the restaurant.
#
# Kept small on purpose. Michelin is a real signal, but it is one guide's
# opinion, it skews expensive and European, and it says little about whether
# an ordinary person will enjoy their dinner. It should nudge a ranking, never
# dictate one. Bib Gourmand is arguably the most useful award here: it means
# good food at a moderate price, which is what most people actually want.
DISTINCTION_BONUS: Dict["Distinction", float] = {
    Distinction.THREE_STARS: 6.0,
    Distinction.TWO_STARS: 4.5,
    Distinction.ONE_STAR: 3.0,
    Distinction.BIB_GOURMAND: 2.5,
    Distinction.GREEN_STAR: 0.5,
}


@dataclass(frozen=True)
class Listing:
    """One restaurant as a single platform reports it."""

    platform: Platform
    platform_id: str
    name: str
    lat: float
    lon: float
    rating: Optional[float]
    review_count: int
    address: Optional[str] = None
    url: Optional[str] = None
    price_level: Optional[int] = None  # 1-4, or None if unknown.

    @property
    def profile(self) -> PlatformProfile:
        return PROFILES[self.platform]

    @property
    def is_usable(self) -> bool:
        """A rating with no reviews behind it is not evidence."""
        return self.rating is not None and self.review_count > 0


@dataclass
class Venue:
    """One real-world restaurant, stitched together from platform listings."""

    listings: List[Listing]
    distinctions: List[Distinction] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        """Prefer the name from whichever platform has seen the most reviews.

        A crude heuristic, but the busiest platform usually has the spelling
        the locals would recognise.
        """
        return max(self.listings, key=lambda x: x.review_count).name

    @property
    def lat(self) -> float:
        return sum(x.lat for x in self.listings) / len(self.listings)

    @property
    def lon(self) -> float:
        return sum(x.lon for x in self.listings) / len(self.listings)

    @property
    def platforms(self) -> List[Platform]:
        return sorted({x.platform for x in self.listings}, key=lambda p: p.value)

    @property
    def total_reviews(self) -> int:
        return sum(x.review_count for x in self.listings)


@dataclass
class ScoredVenue:
    """A Venue plus the arithmetic that ranked it.

    We keep the breakdown rather than just the number, because a ranking you
    cannot explain is a ranking nobody should trust.
    """

    venue: Venue
    score: float  # 0-100, higher is better.
    per_platform: Dict[Platform, float] = field(default_factory=dict)
    corroboration: float = 1.0  # Bonus for agreeing across platforms.
    disagreement: float = 0.0  # Spread between platforms, 0-100 units.
    distinction_bonus: float = 0.0  # Points added for Michelin awards.

    def explain(self) -> str:
        parts = [
            "{}={:.1f}".format(p.value, s) for p, s in sorted(
                self.per_platform.items(), key=lambda kv: kv[0].value
            )
        ]
        summary = "{} | sources: {} | corroboration x{:.2f} | spread {:.1f}".format(
            " ".join(parts),
            len(self.per_platform),
            self.corroboration,
            self.disagreement,
        )
        if self.venue.distinctions:
            summary += " | {} +{:.1f}".format(
                " ".join(d.short for d in self.venue.distinctions),
                self.distinction_bonus,
            )
        return summary
