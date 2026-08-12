"""Entity resolution: deciding which listings are the same restaurant.

This is the hard part of the whole project, and it is worth being honest about
why. Nobody issues restaurants a universal ID. Google calls a place
"Taberna do Marques", TripAdvisor calls it "Taberna do Marquês Lda", Yelp has
the pin thirty metres up the street, and two doors down there is a completely
different restaurant with a suspiciously similar name.

So we do what humans do: we look at the name, we look at the location, and we
insist on both agreeing before we call it a match.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple

from .models import Listing, Venue
from .normalize import name_similarity

EARTH_RADIUS_M = 6_371_000.0

# Two listings may merge if they are physically close AND similarly named.
# The two thresholds trade off against each other: a near-identical name buys
# you slack on distance, and vice versa.
NEAR_M = 60.0  # Same doorway, near enough.
FAR_M = 250.0  # Absolute limit; beyond this, no name is convincing.
NAME_STRICT = 0.86  # Required when the pins are far apart.
NAME_LOOSE = 0.55  # Required when the pins are right on top of each other.

GRID_DEG = 0.003  # ~330m of latitude. Our spatial "blocking" cell size.


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two points on Earth.

    Straight-line subtraction of latitude and longitude would be wrong: a
    degree of longitude is ~111km at the equator and nearly nothing at the
    pole. Haversine does the spherical trigonometry properly.
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def is_match(a: Listing, b: Listing) -> bool:
    """Should these two listings be treated as one restaurant?"""
    if a.platform == b.platform:
        # One platform listing the same restaurant twice is a data-quality
        # problem, not a cross-reference. Handled separately, after clustering.
        return False

    metres = haversine_m(a.lat, a.lon, b.lat, b.lon)
    if metres > FAR_M:
        return False

    similarity = name_similarity(a.name, b.name)

    # Interpolate the name bar between LOOSE (very close) and STRICT (far).
    if metres <= NEAR_M:
        required = NAME_LOOSE
    else:
        t = (metres - NEAR_M) / (FAR_M - NEAR_M)
        required = NAME_LOOSE + t * (NAME_STRICT - NAME_LOOSE)

    return similarity >= required


class DisjointSet:
    """Union-Find: the right shape for "these things belong together".

    Matching is naturally transitive. If Google's listing matches Yelp's, and
    Yelp's matches TripAdvisor's, all three describe one restaurant — even
    though we may never have compared Google directly against TripAdvisor.

    Union-Find tracks exactly that, and does it in effectively constant time
    per operation thanks to two tricks: path compression (flatten the tree as
    you walk it) and union by size (hang the smaller tree off the larger).
    """

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.size = [1] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]  # Path compression.
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]


def _grid_key(lat: float, lon: float) -> Tuple[int, int]:
    return (int(math.floor(lat / GRID_DEG)), int(math.floor(lon / GRID_DEG)))


def _candidate_pairs(listings: List[Listing]) -> Iterable[Tuple[int, int]]:
    """Yield only pairs worth comparing.

    Comparing every listing against every other is O(n^2): a thousand listings
    means half a million name comparisons. But two restaurants 4km apart can
    never match, so most of that work is wasted.

    So we drop each listing into a grid cell and only compare within a cell
    and its eight neighbours. This is called *blocking*, and it is the
    standard way to make record linkage tractable.
    """
    buckets: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    for i, listing in enumerate(listings):
        buckets[_grid_key(listing.lat, listing.lon)].append(i)

    seen = set()
    for (gx, gy), indices in buckets.items():
        neighbourhood: List[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbourhood.extend(buckets.get((gx + dx, gy + dy), ()))
        for i in indices:
            for j in neighbourhood:
                if i >= j:
                    continue
                if (i, j) not in seen:
                    seen.add((i, j))
                    yield (i, j)


def _resolve_duplicates(cluster: List[Listing]) -> List[Listing]:
    """Keep at most one listing per platform in a cluster.

    Transitive merging can drag in two Google listings by accident. When that
    happens we keep the one with the most reviews — the busier record is
    almost always the canonical one, the other a stale or duplicate pin.
    """
    best: Dict[str, Listing] = {}
    for listing in cluster:
        current = best.get(listing.platform.value)
        if current is None or listing.review_count > current.review_count:
            best[listing.platform.value] = listing
    return list(best.values())


def cluster_listings(listings: List[Listing]) -> List[Venue]:
    """Group raw listings from every platform into real-world venues."""
    usable = [x for x in listings if x.is_usable]
    if not usable:
        return []

    dsu = DisjointSet(len(usable))
    for i, j in _candidate_pairs(usable):
        if is_match(usable[i], usable[j]):
            dsu.union(i, j)

    groups: Dict[int, List[Listing]] = defaultdict(list)
    for i, listing in enumerate(usable):
        groups[dsu.find(i)].append(listing)

    return [Venue(listings=_resolve_duplicates(g)) for g in groups.values()]
