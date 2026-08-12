"""Tests for the live provider — without going anywhere near the network.

The trick is that TripAdvisorProvider takes its HTTP client as an argument
instead of building one. That single decision means we can hand it a fake and
test every branch — malformed rows, missing venues, rejected keys — instantly,
repeatably, and without spending a single call of a finite quota.

This is dependency injection, and this is what it is actually for.
"""

from __future__ import annotations

import unittest

from ranker.http import HttpError
from ranker.models import Platform
from ranker.precompute import build_city, slugify
from ranker.providers.base import ProviderError
from ranker.providers.tripadvisor import TripAdvisorProvider, _parse_price
from ranker.providers import FixtureProvider

SEARCH = {
    "data": [
        {"location_id": "1", "name": "Taberna do Marques"},
        {"location_id": "2", "name": "Quinta das Rosas"},
        {"location_id": "3", "name": "Broken Row"},
        {"location_id": "4", "name": "Vanished Place"},
        {"name": "No Id At All"},
    ]
}

DETAILS = {
    "1": {
        "location_id": "1",
        "name": "Taberna do Marques",
        "latitude": "38.71503",  # Note: the API sends numbers as strings.
        "longitude": "-9.13996",
        "rating": "4.5",
        "num_reviews": "1840",
        "price_level": "$$ - $$$",
        "web_url": "https://example.invalid/1",
        "address_obj": {"address_string": "Rua Um 1, Lisboa"},
    },
    "2": {
        "location_id": "2",
        "name": "Quinta das Rosas",
        "latitude": 38.71754,
        "longitude": -9.14643,
        "rating": 4.7,
        "num_reviews": 1210,
    },
    "3": {
        "location_id": "3",
        "name": "Broken Row",
        "latitude": "",  # Unusable: no pin means nothing to match against.
        "longitude": "",
        "rating": "4.9",
        "num_reviews": "500",
    },
}


class FakeClient:
    """Stands in for JsonClient. Records what was asked for."""

    def __init__(self, status_for=None):
        self.requests = []
        self.status_for = status_for or {}

    def get(self, url, params=None):
        self.requests.append(url)
        if url.endswith("/location/search"):
            return SEARCH
        location_id = url.split("/location/")[1].split("/")[0]
        if location_id in self.status_for:
            raise HttpError(self.status_for[location_id], "forced")
        if location_id not in DETAILS:
            raise HttpError(404, "not found")
        return DETAILS[location_id]


class TestTripAdvisorProvider(unittest.TestCase):
    def test_requires_a_key(self):
        with self.assertRaises(ProviderError):
            TripAdvisorProvider(api_key="", client=FakeClient())

    def test_returns_only_usable_listings(self):
        provider = TripAdvisorProvider(api_key="k", client=FakeClient())
        listings = provider.search("Lisbon")
        # Row 3 has no coordinates, row 4 is a 404, row 5 has no id.
        self.assertEqual([x.platform_id for x in listings], ["1", "2"])

    def test_parses_string_numbers(self):
        provider = TripAdvisorProvider(api_key="k", client=FakeClient())
        first = provider.search("Lisbon")[0]
        self.assertAlmostEqual(first.lat, 38.71503)
        self.assertEqual(first.review_count, 1840)
        self.assertEqual(first.rating, 4.5)
        self.assertIs(first.platform, Platform.TRIPADVISOR)

    def test_optional_fields_default_to_none(self):
        provider = TripAdvisorProvider(api_key="k", client=FakeClient())
        second = provider.search("Lisbon")[1]
        self.assertIsNone(second.address)
        self.assertIsNone(second.price_level)

    def test_one_broken_venue_does_not_sink_the_others(self):
        provider = TripAdvisorProvider(api_key="k", client=FakeClient({"1": 500}))
        self.assertEqual([x.platform_id for x in provider.search("Lisbon")], ["2"])

    def test_a_rejected_key_stops_everything(self):
        # A 500 on one venue is bad luck. A 401 is a broken configuration, and
        # grinding through 200 more calls to confirm it helps nobody.
        provider = TripAdvisorProvider(api_key="k", client=FakeClient({"1": 401}))
        with self.assertRaises(ProviderError):
            provider.search("Lisbon")

    def test_price_parsing(self):
        self.assertEqual(_parse_price("$$ - $$$"), 3)
        self.assertEqual(_parse_price("$"), 1)
        self.assertIsNone(_parse_price(""))
        self.assertIsNone(_parse_price("cheap"))


class TestPrecompute(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("  New York "), "new-york")

    def test_builds_a_serializable_payload(self):
        import json

        listings = FixtureProvider().search("Lisbon", limit=500)
        payload = build_city({"name": "Lisbon", "country": "PT"}, listings, 10, 1)

        self.assertEqual(payload["slug"], "lisbon")
        self.assertEqual(len(payload["restaurants"]), 10)
        self.assertEqual(payload["restaurants"][0]["rank"], 1)
        self.assertTrue(payload["restaurants"][0]["sources"])
        json.dumps(payload)  # Must survive the trip to the browser.

    def test_ranks_are_sequential(self):
        listings = FixtureProvider().search("Lisbon", limit=500)
        payload = build_city({"name": "Lisbon", "country": "PT"}, listings, 10, 1)
        ranks = [r["rank"] for r in payload["restaurants"]]
        self.assertEqual(ranks, list(range(1, 11)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
