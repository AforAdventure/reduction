"""What a city is, as far as this program is concerned.

Until now a city was a string, and every provider quietly assumed it could do
something useful with that. The Content API could — it takes a search phrase.
Terra cannot: it locates by latitude, longitude, and radius.

That mismatch is worth naming rather than papering over. A city is not a word;
it is a place with a position and an extent. Once `City` says so, a provider
that needs coordinates can simply ask for them, and one that doesn't can
ignore them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

CITIES_FILE = Path(__file__).resolve().parent.parent / "data" / "cities.json"


@dataclass(frozen=True)
class City:
    """A place to rank restaurants in."""

    name: str
    country: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    # How far out from the centre to look. Dense European old towns need a
    # small radius or you drown in suburbs; sprawling US cities need more.
    radius_km: float = 3.0

    @property
    def slug(self) -> str:
        return self.name.strip().lower().replace(" ", "-").replace(",", "")

    @property
    def has_coordinates(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def label(self) -> str:
        return "{}, {}".format(self.name, self.country) if self.country else self.name


def load_cities(path: Path = CITIES_FILE) -> List[City]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        City(
            name=raw["name"],
            country=raw.get("country"),
            lat=raw.get("lat"),
            lon=raw.get("lon"),
            radius_km=float(raw.get("radius_km", 3.0)),
        )
        for raw in payload["cities"]
    ]


def find_city(slug: str, cities: Optional[List[City]] = None) -> Optional[City]:
    """Look up a city by slug, or None. Used by the CLI and --only."""
    for city in cities if cities is not None else load_cities():
        if city.slug == slug.strip().lower().replace(" ", "-"):
            return city
    return None
