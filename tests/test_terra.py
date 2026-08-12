"""Tests for the Terra provider.

Written against the published response shape, with a fake HTTP client. When
you finally run this against the live API and something is different, add the
real payload here as a fixture first, then fix the code — that way the bug can
never come back silently.
"""

from __future__ import annotations

import unittest

from reduction.cities import City
from reduction.http import HttpError
from reduction.models import Platform
from reduction.providers.base import ProviderError
from reduction.providers.terra import TerraProvider, _pick_name, _pick_url

LISBON = City("Lisbon", "PT", 38.7139, -9.1394, 2.5)
NOWHERE = City("Nowhere", "XX")  # No coordinates.


def row(loc_id, name, lat, lon, rating, count, primary=True):
    return {
        "location": {
            "id": loc_id,
            "names": [{"value": name, "language": "en", "primary": primary}],
            "coordinates": {"latitude": lat, "longitude": lon},
            "overall_rating": {"rating": rating, "count": count},
            "addresses": [{"formatted": "{} Street, Lisboa".format(loc_id)}],
            "urls": {"web": "https://example.invalid/{}".format(loc_id)},
        },
        "distance_kilometers": 0.4,
    }


class FakeClient:
    """Serves paginated Terra responses and records the requests made."""

    def __init__(self, pages, fail_on_page=None, status=500):
        self.pages = pages
        self.fail_on_page = fail_on_page
        self.status = status
        self.calls = []

    def get(self, url, params=None, headers=None):
        page = int((params or {}).get("page", 1))
        self.calls.append({"params": params or {}, "headers": headers or {}})
        if self.fail_on_page == page:
            raise HttpError(self.status, "forced")
        rows = self.pages[page - 1] if page <= len(self.pages) else []
        return {
            "data": rows,
            "pagination": {
                "page": page,
                "size": 20,
                "total_pages": len(self.pages),
                "total_elements": sum(len(p) for p in self.pages),
            },
        }


class TestTerraRequest(unittest.TestCase):
    def test_key_travels_in_a_header_not_the_url(self):
        # Terra authenticates by header. A key in the query string would end
        # up in server logs and in our own on-disk cache filenames.
        client = FakeClient([[row(1, "A", 38.71, -9.14, 4.5, 900)]])
        TerraProvider(api_key="secret", client=client).search(LISBON)
        call = client.calls[0]
        self.assertEqual(call["headers"].get("X-API-Key"), "secret")
        self.assertNotIn("secret", str(call["params"]))

    def test_sends_coordinates_radius_and_category(self):
        client = FakeClient([[row(1, "A", 38.71, -9.14, 4.5, 900)]])
        TerraProvider(api_key="k", client=client).search(LISBON)
        params = client.calls[0]["params"]
        self.assertEqual(params["lat"], 38.7139)
        self.assertEqual(params["lon"], -9.1394)
        self.assertEqual(params["radius"], 2.5)
        self.assertEqual(params["unit"], "KM")
        self.assertEqual(params["category"], "RESTAURANT")

    def test_a_city_without_coordinates_fails_loudly(self):
        client = FakeClient([[]])
        with self.assertRaises(ProviderError) as ctx:
            TerraProvider(api_key="k", client=client).search(NOWHERE)
        self.assertIn("lat/lon", str(ctx.exception))
        self.assertEqual(client.calls, [])  # Never even asked.

    def test_requires_a_key(self):
        with self.assertRaises(ProviderError):
            TerraProvider(api_key="", client=FakeClient([[]]))


class TestTerraPagination(unittest.TestCase):
    def test_pages_until_the_limit_is_reached(self):
        pages = [[row(i, "R%d" % i, 38.71, -9.14, 4.5, 500) for i in range(20)],
                 [row(100 + i, "S%d" % i, 38.72, -9.15, 4.4, 400) for i in range(20)]]
        provider = TerraProvider(api_key="k", client=(c := FakeClient(pages)))
        listings = provider.search(LISBON, limit=40)
        self.assertEqual(len(listings), 40)
        self.assertEqual([call["params"]["page"] for call in c.calls], [1, 2])

    def test_stops_early_on_a_short_page(self):
        # Fewer than 20 rows means there is no next page. Asking for one is a
        # wasted call against a finite quota.
        pages = [[row(i, "R%d" % i, 38.71, -9.14, 4.5, 500) for i in range(7)]]
        provider = TerraProvider(api_key="k", client=(c := FakeClient(pages)))
        listings = provider.search(LISBON, limit=40)
        self.assertEqual(len(listings), 7)
        self.assertEqual(len(c.calls), 1)

    def test_never_returns_more_than_the_limit(self):
        pages = [[row(i, "R%d" % i, 38.71, -9.14, 4.5, 500) for i in range(20)]]
        provider = TerraProvider(api_key="k", client=FakeClient(pages))
        self.assertEqual(len(provider.search(LISBON, limit=5)), 5)

    def test_a_failed_later_page_keeps_what_we_have(self):
        pages = [[row(i, "R%d" % i, 38.71, -9.14, 4.5, 500) for i in range(20)],
                 [row(99, "Z", 38.72, -9.15, 4.9, 100)]]
        provider = TerraProvider(api_key="k", client=FakeClient(pages, fail_on_page=2))
        self.assertEqual(len(provider.search(LISBON, limit=40)), 20)

    def test_a_failed_first_page_is_an_error(self):
        provider = TerraProvider(api_key="k", client=FakeClient([[]], fail_on_page=1))
        with self.assertRaises(ProviderError):
            provider.search(LISBON)

    def test_a_rejected_key_stops_everything(self):
        provider = TerraProvider(
            api_key="k", client=FakeClient([[]], fail_on_page=1, status=401)
        )
        with self.assertRaises(ProviderError) as ctx:
            provider.search(LISBON)
        self.assertIn("key rejected", str(ctx.exception))


class TestTerraParsing(unittest.TestCase):
    def _one(self, rows):
        return TerraProvider(api_key="k", client=FakeClient([rows])).search(LISBON)

    def test_maps_nested_fields_onto_a_listing(self):
        listing = self._one([row(42, "Taberna", 38.715, -9.140, 4.5, 1840)])[0]
        self.assertIs(listing.platform, Platform.TRIPADVISOR)
        self.assertEqual(listing.platform_id, "42")
        self.assertEqual(listing.name, "Taberna")
        self.assertAlmostEqual(listing.lat, 38.715)
        self.assertEqual(listing.rating, 4.5)
        self.assertEqual(listing.review_count, 1840)
        self.assertIsNone(listing.price_level)  # Terra has no price field.

    def test_prefers_the_primary_name(self):
        names = [
            {"value": "Lisbonne Taverne", "language": "fr", "primary": False},
            {"value": "Taberna do Marquês", "language": "pt", "primary": True},
        ]
        self.assertEqual(_pick_name(names), "Taberna do Marquês")

    def test_falls_back_when_no_name_is_primary(self):
        names = [{"value": "Only Option", "language": "en", "primary": False}]
        self.assertEqual(_pick_name(names), "Only Option")

    def test_no_usable_name_means_no_listing(self):
        bad = row(1, "X", 38.71, -9.14, 4.5, 100)
        bad["location"]["names"] = []
        self.assertEqual(self._one([bad]), [])

    def test_missing_coordinates_means_no_listing(self):
        bad = row(1, "X", 38.71, -9.14, 4.5, 100)
        bad["location"]["coordinates"] = {}
        self.assertEqual(self._one([bad]), [])

    def test_an_unrated_venue_survives_parsing(self):
        # Terra may list a place with no rating yet. It should arrive intact
        # and be discarded later by clustering, not lost silently here.
        bad = row(1, "New Place", 38.71, -9.14, 4.5, 100)
        bad["location"]["overall_rating"] = {}
        listing = self._one([bad])[0]
        self.assertIsNone(listing.rating)
        self.assertFalse(listing.is_usable)

    def test_malformed_rows_do_not_sink_the_page(self):
        rows = [
            {"not_a_location": True},
            row(2, "Good", 38.71, -9.14, 4.4, 300),
            {"location": "not a dict"},
        ]
        self.assertEqual([x.platform_id for x in self._one(rows)], ["2"])

    def test_url_extraction_tolerates_shape_changes(self):
        self.assertEqual(_pick_url({"web": "https://a.invalid"}), "https://a.invalid")
        self.assertEqual(_pick_url("https://b.invalid"), "https://b.invalid")
        self.assertIsNone(_pick_url({"tracking_id": "abc123"}))
        self.assertIsNone(_pick_url(None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
