"""TripAdvisor Content API provider.

SUPERSEDED by terra.py for most purposes — Terra returns ratings and review
counts in its list response, where this API charges you a call per venue to
get them. Kept because it searches by name rather than coordinates, which is
still useful for a city you have not geocoded.

QUOTA MATTERS HERE, so understand the shape of it before you run this. The
API is two-stage: a search returns location IDs and names but no ratings, so
every venue costs a second "details" call. One city of 30 venues is therefore
about 31 calls. Against a ~5,000/month free tier that is roughly 160 city
refreshes a month — which is precisely why precompute.py exists, and why the
HTTP client caches to disk.

BEFORE YOU TRUST THIS FILE: verify the endpoint paths and JSON field names
against the current TripAdvisor Content API docs. They have changed before.
Everything here parses defensively, so a renamed field degrades one venue
rather than crashing the run — but degrading quietly is still degrading.

Their terms also require visible attribution and a link back to TripAdvisor
on any page displaying this data. That is handled in site/index.html; if you
restyle the page, do not remove it.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ..cities import City
from ..http import HttpError, JsonClient
from ..models import Listing, Platform
from .base import ProviderError

BASE_URL = "https://api.content.tripadvisor.com/api/v1"

# TripAdvisor reports price as a string like "$$ - $$$". We keep the top of
# the range, mapped onto the 1-4 scale everyone else uses.
_PRICE_MAP = {"$": 1, "$$": 2, "$$$": 3, "$$$$": 4}


def _parse_price(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    symbols = [part.strip() for part in raw.split("-")]
    levels = [_PRICE_MAP[s] for s in symbols if s in _PRICE_MAP]
    return max(levels) if levels else None


def _parse_float(value: Any) -> Optional[float]:
    """Coerce whatever the API sent into a float, or None.

    The API returns numbers as strings more often than you would expect, and
    occasionally as an empty string. Both are handled here so that no caller
    downstream ever has to think about it again.
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value: Any) -> int:
    parsed = _parse_float(value)
    return int(parsed) if parsed is not None else 0


class TripAdvisorProvider:
    """Fetches restaurant listings for a city from the Content API."""

    platform = Platform.TRIPADVISOR

    def __init__(
        self,
        api_key: Optional[str] = None,
        client: Optional[JsonClient] = None,
        language: str = "en",
    ) -> None:
        key = api_key or os.environ.get("TRIPADVISOR_API_KEY")
        if not key:
            raise ProviderError(
                Platform.TRIPADVISOR,
                "no API key: set TRIPADVISOR_API_KEY or pass api_key=",
            )
        self.api_key = key
        self.language = language
        # Injected rather than constructed, so tests can hand in a fake and
        # the whole provider becomes testable without a network or a key.
        self.client = client or JsonClient()

    # -- API calls -------------------------------------------------------

    def _search(self, city: City, limit: int) -> List[Dict[str, Any]]:
        payload = self.client.get(
            "{}/location/search".format(BASE_URL),
            {
                "key": self.api_key,
                "searchQuery": city.label,
                "category": "restaurants",
                "language": self.language,
            },
        )
        return list(payload.get("data", []))[:limit]

    def _details(self, location_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self.client.get(
                "{}/location/{}/details".format(BASE_URL, location_id),
                {"key": self.api_key, "language": self.language},
            )
        except HttpError as exc:
            if exc.status == 404:
                return None  # Venue vanished between search and details.
            raise

    # -- translation -----------------------------------------------------

    def _to_listing(self, details: Dict[str, Any]) -> Optional[Listing]:
        """Convert one API response into our own Listing, or None if unusable.

        This function is the border crossing. Everything on the far side of it
        speaks our vocabulary; nothing downstream knows or cares that
        TripAdvisor calls it `num_reviews` and puts price in a string.
        """
        lat = _parse_float(details.get("latitude"))
        lon = _parse_float(details.get("longitude"))
        rating = _parse_float(details.get("rating"))
        location_id = details.get("location_id")
        name = details.get("name")

        if lat is None or lon is None or not location_id or not name:
            return None  # Without a name and a pin we cannot match it to anything.

        address = None
        address_obj = details.get("address_obj")
        if isinstance(address_obj, dict):
            address = address_obj.get("address_string")

        return Listing(
            platform=Platform.TRIPADVISOR,
            platform_id=str(location_id),
            name=str(name),
            lat=lat,
            lon=lon,
            rating=rating,
            review_count=_parse_int(details.get("num_reviews")),
            address=address,
            url=details.get("web_url"),
            price_level=_parse_price(details.get("price_level")),
        )

    # -- the Provider contract -------------------------------------------

    def search(self, city: City, limit: int = 30) -> List[Listing]:
        try:
            hits = self._search(city, limit)
        except HttpError as exc:
            raise ProviderError(Platform.TRIPADVISOR, "search failed: {}".format(exc))

        listings: List[Listing] = []
        for hit in hits:
            location_id = hit.get("location_id")
            if not location_id:
                continue
            try:
                details = self._details(str(location_id))
            except HttpError as exc:
                # One bad venue must not cost us the other twenty-nine.
                if exc.status in (401, 403):
                    raise ProviderError(
                        Platform.TRIPADVISOR, "key rejected: {}".format(exc)
                    )
                continue
            if not details:
                continue
            listing = self._to_listing(details)
            if listing is not None:
                listings.append(listing)

        return listings
