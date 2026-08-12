"""A provider backed by a JSON file instead of the internet.

Worth its own file, because it is what lets you develop the interesting parts
of this project today: no API keys, no billing account, no rate limits, and a
test suite that runs in milliseconds and gives the same answer every time.

When the real providers arrive they will satisfy the same Protocol, and the
ranking code will not know the difference.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from ..models import Listing, Platform
from .base import ProviderError

FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "fixtures"


def _to_listing(raw: Dict) -> Listing:
    return Listing(
        platform=Platform(raw["platform"]),
        platform_id=raw["platform_id"],
        name=raw["name"],
        lat=float(raw["lat"]),
        lon=float(raw["lon"]),
        rating=raw.get("rating"),
        review_count=int(raw.get("review_count", 0)),
        address=raw.get("address"),
        url=raw.get("url"),
        price_level=raw.get("price_level"),
    )


class FixtureProvider:
    """Serves listings for every platform from one JSON file per city."""

    platform = Platform.GOOGLE  # Nominal; fixtures carry their own platform.

    def __init__(self, directory: Path = FIXTURE_DIR) -> None:
        self.directory = Path(directory)

    def _path_for(self, city: str) -> Path:
        slug = city.strip().lower().replace(" ", "-")
        return self.directory / "{}.json".format(slug)

    def available_cities(self) -> List[str]:
        return sorted(p.stem for p in self.directory.glob("*.json"))

    def search(self, city: str, limit: int = 50) -> List[Listing]:
        path = self._path_for(city)
        if not path.exists():
            raise ProviderError(
                Platform.GOOGLE,
                "no fixture for {!r}; have: {}".format(
                    city, ", ".join(self.available_cities()) or "none"
                ),
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        listings = [_to_listing(raw) for raw in payload["listings"]]

        # Apply `limit` per platform, exactly as a real single-platform
        # provider would. Slicing the flat list instead would chop the tail
        # off whichever platforms happen to sort last, leaving venues with
        # some of their listings missing — a silent, and very confusing,
        # corruption of the thing we are trying to measure.
        counts: Dict[str, int] = {}
        kept: List[Listing] = []
        for listing in listings:
            key = listing.platform.value
            counts[key] = counts.get(key, 0) + 1
            if counts[key] <= limit:
                kept.append(listing)
        return kept
