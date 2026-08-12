"""Michelin Guide awards, attached to venues after clustering.

WHERE THE DATA COMES FROM — read this before extending it.

Michelin publishes no API, and their terms prohibit scraping the guide. So
this module reads a small curated JSON file per city that you populate by
hand: restaurant name, coordinates, award, year. That is slower than a
scraper, and it is the only version of this that you can actually ship.

It is also less painful than it sounds. A city has a few dozen distinctions,
they change once a year when the guide is announced, and the facts themselves
(who holds a star) are not anyone's property — it is the guide's database and
presentation that are protected. Curating a list of award-holders from the
published guide is ordinary reference work.

Matching those entries to venues reuses the same name-plus-geography logic as
cross-platform matching, because it is exactly the same problem wearing a
different hat.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from .matching import haversine_m
from .models import Distinction, Venue
from .normalize import name_similarity

MICHELIN_DIR = Path(__file__).resolve().parent.parent / "data" / "michelin"

# Stricter than cross-platform matching, and the asymmetry is deliberate.
# Wrongly merging two review listings costs you one muddled venue. Wrongly
# awarding a Michelin star to the sandwich shop next door is a claim about the
# world that a reader will notice, and rightly not forgive.
MATCH_MAX_M = 150.0
MATCH_MIN_NAME = 0.80


class MichelinIndex:
    """The Michelin entries for one city, ready to match against venues."""

    def __init__(self, entries: List[Dict]) -> None:
        self.entries = entries

    @classmethod
    def for_city(cls, city: str, directory: Path = MICHELIN_DIR) -> "MichelinIndex":
        """Load a city's awards, or an empty index if we have none.

        Missing data is the normal case, not an error — most cities in the
        list will have no file. An empty index simply awards nothing.
        """
        slug = city.strip().lower().replace(" ", "-").replace(",", "")
        path = Path(directory) / "{}.json".format(slug)
        if not path.exists():
            return cls([])
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(list(payload.get("entries", [])))

    def _best_match(self, venue: Venue) -> Optional[Dict]:
        """Find the award entry for this venue, if any.

        We take the best candidate rather than the first one that clears the
        bar — with two similarly-named entries nearby, "first past the post"
        would hand the award to whichever happened to be earlier in the file.
        """
        best, best_score = None, 0.0
        for entry in self.entries:
            distance = haversine_m(
                venue.lat, venue.lon, float(entry["lat"]), float(entry["lon"])
            )
            if distance > MATCH_MAX_M:
                continue
            similarity = max(
                name_similarity(venue.display_name, entry["name"]),
                # Venues carry several spellings; any of them may be the one
                # the guide used.
                max(
                    (name_similarity(x.name, entry["name"]) for x in venue.listings),
                    default=0.0,
                ),
            )
            if similarity >= MATCH_MIN_NAME and similarity > best_score:
                best, best_score = entry, similarity
        return best

    def annotate(self, venues: List[Venue]) -> List[Venue]:
        """Attach awards to venues in place, and hand them back."""
        for venue in venues:
            entry = self._best_match(venue)
            if entry is None:
                continue
            awards = entry.get("awards") or [entry.get("award")]
            venue.distinctions = [
                Distinction(a) for a in awards if a in Distinction._value2member_map_
            ]
        return venues
