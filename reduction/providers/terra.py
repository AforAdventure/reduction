"""TripAdvisor Terra platform provider.

Preferred over the older Content API for one decisive reason: this endpoint
returns the rating AND the review count in the list response, so a page of 20
restaurants costs one call. The Content API returns names in its search and
makes you fetch each venue's details separately — 41 calls for 40 venues
against Terra's 2.

That is not a micro-optimisation. It is the difference between a free tier
that covers a handful of cities and one that covers all of them.

Docs: https://docs.terra.tripadvisor.com/reference/cataloglocationsnearbyget

Two things to know about the response shape, both handled below:
  * `names` is a LIST, because a place can be called different things in
    different languages. We want the primary one.
  * There is no price field. `price_level` stays None, and the site simply
    omits the dollar signs rather than inventing them.
"""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional

from ..cities import City
from ..http import HttpError, JsonClient
from ..models import Listing, Platform
from .base import ProviderError

NEARBY_ENDPOINT = "https://terra.tripadvisor.com/api/catalog/locations/nearby"

# The API caps a page at 20 regardless of what you ask for. Encoding that here
# means the pagination loop below is honest about how many calls it will make.
MAX_PAGE_SIZE = 20


def _parse_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick_name(names: Any) -> Optional[str]:
    """Choose one name from Terra's list of them.

    Prefer the one flagged `primary`; otherwise take the first with a value.
    A restaurant with no name at all is unusable — we cannot match it to any
    other platform's listing, which is the only reason we collect it.
    """
    if not isinstance(names, list):
        return None
    for entry in names:
        if isinstance(entry, dict) and entry.get("primary") and entry.get("value"):
            return str(entry["value"])
    for entry in names:
        if isinstance(entry, dict) and entry.get("value"):
            return str(entry["value"])
    return None


def _pick_address(addresses: Any) -> Optional[str]:
    if not isinstance(addresses, list):
        return None
    for entry in addresses:
        if isinstance(entry, dict) and entry.get("formatted"):
            return str(entry["formatted"])
    return None


def _pick_url(urls: Any) -> Optional[str]:
    """Find a web link in the `urls` object without assuming its shape.

    The docs abbreviate this field, so rather than guess a key name we take
    the first value that looks like a URL. If the shape changes we lose a
    link, not a venue.
    """
    if isinstance(urls, str):
        return urls
    if isinstance(urls, dict):
        for value in urls.values():
            if isinstance(value, str) and value.startswith("http"):
                return value
    return None


class TerraProvider:
    """Fetches restaurants near a city centre from the Terra catalog."""

    platform = Platform.TRIPADVISOR

    def __init__(
        self,
        api_key: Optional[str] = None,
        client: Optional[JsonClient] = None,
        locale: str = "en",
    ) -> None:
        key = api_key or os.environ.get("TRIPADVISOR_API_KEY")
        if not key:
            raise ProviderError(
                Platform.TRIPADVISOR,
                "no API key: set TRIPADVISOR_API_KEY or pass api_key=",
            )
        self.api_key = key
        self.locale = locale
        self.client = client or JsonClient()

    def _to_listing(self, row: Dict[str, Any]) -> Optional[Listing]:
        """Translate one Terra row into our Listing, or None if unusable.

        The border crossing again: past this point nothing in the codebase
        knows that TripAdvisor nests coordinates under `coordinates` or calls
        a review count `overall_rating.count`.
        """
        location = row.get("location")
        if not isinstance(location, dict):
            return None

        location_id = location.get("id")
        name = _pick_name(location.get("names"))
        coords = location.get("coordinates") or {}
        lat = _parse_float(coords.get("latitude"))
        lon = _parse_float(coords.get("longitude"))

        if location_id is None or not name or lat is None or lon is None:
            return None

        rating_obj = location.get("overall_rating") or {}
        rating = _parse_float(rating_obj.get("rating"))
        count = _parse_float(rating_obj.get("count")) or 0.0

        return Listing(
            platform=Platform.TRIPADVISOR,
            platform_id=str(location_id),
            name=name,
            lat=lat,
            lon=lon,
            rating=rating,
            review_count=int(count),
            address=_pick_address(location.get("addresses")),
            url=_pick_url(location.get("urls")),
            price_level=None,  # Terra does not expose one. Don't invent it.
        )

    def search(self, city: City, limit: int = 40) -> List[Listing]:
        if not city.has_coordinates:
            raise ProviderError(
                Platform.TRIPADVISOR,
                "{} has no lat/lon; Terra searches by coordinates, so add "
                "them to data/cities.json".format(city.name),
            )

        listings: List[Listing] = []
        pages = max(1, math.ceil(limit / MAX_PAGE_SIZE))

        for page in range(1, pages + 1):
            try:
                payload = self.client.get(
                    NEARBY_ENDPOINT,
                    {
                        "lat": city.lat,
                        "lon": city.lon,
                        "radius": city.radius_km,
                        "unit": "KM",
                        "category": "RESTAURANT",
                        "size": MAX_PAGE_SIZE,
                        "page": page,
                        "sort": "rating,desc",
                        "locale": self.locale,
                    },
                    headers={"X-API-Key": self.api_key},
                )
            except HttpError as exc:
                if exc.status in (401, 403):
                    raise ProviderError(
                        Platform.TRIPADVISOR, "key rejected: {}".format(exc)
                    )
                if page == 1:
                    raise ProviderError(
                        Platform.TRIPADVISOR, "request failed: {}".format(exc)
                    )
                break  # Later page failed; keep what we already have.

            rows = payload.get("data") or []
            for row in rows:
                listing = self._to_listing(row)
                if listing is not None:
                    listings.append(listing)

            # Stop early rather than paying for pages that don't exist.
            pagination = payload.get("pagination") or {}
            total_pages = pagination.get("total_pages")
            if len(rows) < MAX_PAGE_SIZE:
                break
            if isinstance(total_pages, int) and page >= total_pages:
                break

        return listings[:limit]
