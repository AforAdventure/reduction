"""The contract every data source must honour.

Every provider — Google, Yelp, TripAdvisor, Foursquare, a JSON file on disk —
promises exactly one thing: given a city, hand back a list of Listings.

Because the rest of the system only knows about that promise, adding a fifth
platform later means writing one new file and changing nothing else. That is
the whole payoff of programming against an interface instead of a vendor.
"""

from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from ..models import Listing, Platform


@runtime_checkable
class Provider(Protocol):
    """Anything that can produce restaurant listings for a city."""

    platform: Platform

    def search(self, city: str, limit: int = 50) -> List[Listing]:
        """Return restaurant listings for `city`.

        `limit` is PER PLATFORM, not a cap on the returned list. A real
        provider speaks for one platform, so the distinction looks academic —
        but a provider that serves several (the fixtures do) must honour it,
        or truncation will slice a venue in half and silently destroy the
        cross-referencing this whole project exists to do.
        """
        ...


class ProviderError(RuntimeError):
    """A provider failed in a way the caller may want to survive.

    One platform being down, rate-limited, or missing a key should degrade the
    ranking, not destroy it. Callers are expected to catch this, drop that
    source, and carry on with the rest.
    """

    def __init__(self, platform: Platform, message: str) -> None:
        super().__init__("[{}] {}".format(platform.value, message))
        self.platform = platform
