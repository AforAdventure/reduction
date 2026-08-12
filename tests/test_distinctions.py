"""Tests for Michelin awards.

The stakes are different here, and the tests reflect that. A wrong
cross-platform merge produces a muddled average. A wrongly-awarded star is a
false factual claim on a public page.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from reduction.cities import City
from reduction.distinctions import MichelinIndex
from reduction.matching import cluster_listings
from reduction.models import Distinction, Listing, Platform
from reduction.pipeline import rank
from reduction.providers import FixtureProvider
from reduction.scoring import score_venue


def listing(platform, pid, name, lat, lon, rating, reviews):
    return Listing(platform, pid, name, lat, lon, rating, reviews)


class TestMichelinIndex(unittest.TestCase):
    def test_missing_city_is_not_an_error(self):
        # Most cities will have no file. That is normal, not a failure.
        index = MichelinIndex.for_city("Atlantis")
        self.assertEqual(index.entries, [])

    def test_awards_attach_to_the_right_venue(self):
        listings = FixtureProvider().search(City("Lisbon"), limit=500)
        venues = MichelinIndex.for_city("Lisbon").annotate(cluster_listings(listings))
        awarded = {
            v.display_name: v.distinctions for v in venues if v.distinctions
        }
        self.assertEqual(
            awarded["Quinta das Rosas"],
            [Distinction.TWO_STARS, Distinction.GREEN_STAR],
        )
        self.assertEqual(awarded["Café Adélia"], [Distinction.BIB_GOURMAND])
        self.assertNotIn("Bistro Azul", awarded)

    def test_matches_across_name_spellings(self):
        # The guide says "Taberna do Marquês"; Yelp says "Taberna do Marques".
        # Matching must consider every spelling the venue carries.
        listings = FixtureProvider().search(City("Lisbon"), limit=500)
        venues = MichelinIndex.for_city("Lisbon").annotate(cluster_listings(listings))
        taberna = next(v for v in venues if "Marqu" in v.display_name)
        self.assertEqual(taberna.distinctions, [Distinction.ONE_STAR])

    def test_a_neighbour_does_not_inherit_the_star(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "testville.json").write_text(json.dumps({
                "entries": [
                    {"name": "Starred Place", "lat": 38.7000, "lon": -9.1000,
                     "awards": ["one_star"]}
                ]
            }))
            index = MichelinIndex.for_city("Testville", directory=Path(tmp))
            venues = cluster_listings([
                listing(Platform.GOOGLE, "a", "Sandwich Shop", 38.70005, -9.10005, 4.2, 300),
            ])
            index.annotate(venues)
            self.assertEqual(venues[0].distinctions, [])

    def test_unknown_award_codes_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "testville.json").write_text(json.dumps({
                "entries": [
                    {"name": "Quinta das Rosas", "lat": 38.7, "lon": -9.1,
                     "awards": ["one_star", "four_stars"]}
                ]
            }))
            index = MichelinIndex.for_city("Testville", directory=Path(tmp))
            venues = cluster_listings([
                listing(Platform.GOOGLE, "a", "Quinta das Rosas", 38.7, -9.1, 4.6, 900),
            ])
            index.annotate(venues)
            self.assertEqual(venues[0].distinctions, [Distinction.ONE_STAR])


class TestDistinctionScoring(unittest.TestCase):
    def _venue(self, name, distinctions=()):
        venues = cluster_listings([
            listing(Platform.GOOGLE, "g", name, 38.70, -9.10, 4.6, 900),
            listing(Platform.YELP, "y", name, 38.70004, -9.10004, 4.2, 400),
        ])
        venues[0].distinctions = list(distinctions)
        return venues[0]

    def test_a_star_lifts_the_score(self):
        plain = score_venue(self._venue("Plain"))
        starred = score_venue(self._venue("Starred", [Distinction.ONE_STAR]))
        self.assertAlmostEqual(starred.score - plain.score, 3.0, places=1)

    def test_the_bonus_is_not_diluted_by_confidence(self):
        # A star is a fact, not a crowd estimate, so uncertainty about Yelp
        # must not shrink it. A single-source venue gets the same +3.
        thin = cluster_listings([
            listing(Platform.GOOGLE, "g", "Thin", 38.70, -9.10, 4.6, 900),
        ])
        before = score_venue(thin[0]).score
        thin[0].distinctions = [Distinction.ONE_STAR]
        self.assertAlmostEqual(score_venue(thin[0]).score - before, 3.0, places=1)

    def test_score_is_still_capped_at_100(self):
        venue = self._venue("Perfect", [Distinction.THREE_STARS, Distinction.GREEN_STAR])
        venue.listings = [
            listing(Platform.GOOGLE, "g", "Perfect", 38.70, -9.10, 5.0, 500_000),
        ]
        self.assertLessEqual(score_venue(venue).score, 100.0)

    def test_michelin_nudges_but_does_not_dictate(self):
        # A one-star place that the crowd dislikes must not outrank a
        # genuinely beloved place with no award.
        starred = cluster_listings([
            listing(Platform.GOOGLE, "g", "Cold Fish", 38.70, -9.10, 3.9, 800),
        ])
        starred[0].distinctions = [Distinction.ONE_STAR]
        beloved = cluster_listings([
            listing(Platform.GOOGLE, "g", "Warm Welcome", 38.72, -9.12, 4.8, 3000),
        ])
        self.assertGreater(score_venue(beloved[0]).score, score_venue(starred[0]).score)


class TestPipelineIntegration(unittest.TestCase):
    def test_rank_without_a_city_awards_nothing(self):
        listings = FixtureProvider().search(City("Lisbon"), limit=500)
        results = rank(listings, limit=10)
        self.assertTrue(all(not r.venue.distinctions for r in results))

    def test_rank_with_a_city_applies_awards(self):
        listings = FixtureProvider().search(City("Lisbon"), limit=500)
        results = rank(listings, limit=10, city="Lisbon")
        self.assertTrue(any(r.venue.distinctions for r in results))
        self.assertEqual(results[0].venue.display_name, "Quinta das Rosas")


if __name__ == "__main__":
    unittest.main(verbosity=2)
