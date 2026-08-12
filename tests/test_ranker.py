"""Tests for the ranking pipeline.

    python3 -m unittest discover -s tests -v

Note what is being tested here: not "does the code run", but "does the code
behave the way the reasoning said it would". Each test below encodes one claim
we made in a docstring. If a claim is not testable, it was probably not a real
claim.
"""

from __future__ import annotations

import unittest

from reduction.cities import City
from reduction.matching import DisjointSet, cluster_listings, haversine_m, is_match
from reduction.models import PROFILES, Listing, Platform
from reduction.normalize import (
    name_similarity,
    name_tokens,
    normalize_name,
    shrink_rating,
    strip_accents,
)
from reduction.pipeline import rank
from reduction.providers import FixtureProvider
from reduction.scoring import platform_score, score_venue


def listing(platform, pid, name, lat, lon, rating, reviews):
    return Listing(
        platform=platform,
        platform_id=pid,
        name=name,
        lat=lat,
        lon=lon,
        rating=rating,
        review_count=reviews,
    )


class TestNameNormalization(unittest.TestCase):
    def test_strips_accents(self):
        self.assertEqual(strip_accents("Café Adélia"), "Cafe Adelia")
        self.assertEqual(strip_accents("Zürich Straße"), "Zurich Straße")

    def test_normalizes_punctuation_and_case(self):
        self.assertEqual(normalize_name("O'Brien's  BAR & Grill!"), "o brien s bar grill")

    def test_drops_generic_words(self):
        self.assertEqual(name_tokens("Restaurante Bella Lda"), frozenset({"bella"}))

    def test_keeps_boilerplate_when_that_is_all_there_is(self):
        # An empty token set would match every other empty set. Never return one.
        self.assertTrue(name_tokens("The Restaurant"))

    def test_accent_and_suffix_variants_match(self):
        self.assertGreater(name_similarity("Café Adélia", "Cafe Adelia"), 0.9)
        self.assertGreater(
            name_similarity("Taberna do Marquês", "Taberna do Marques Lda"), 0.85
        )

    def test_single_shared_word_is_not_enough(self):
        # "Bella" inside "Bella Vista" must NOT count as containment.
        self.assertLess(name_similarity("Restaurante Bella", "Bella Vista"), 0.7)

    def test_unrelated_names_score_low(self):
        self.assertLess(name_similarity("Quinta das Rosas", "Sushi Norte"), 0.3)


class TestShrinkage(unittest.TestCase):
    def test_no_reviews_falls_back_to_the_prior(self):
        profile = PROFILES[Platform.GOOGLE]
        self.assertAlmostEqual(shrink_rating(5.0, 0, profile), profile.prior_mean)

    def test_thin_evidence_is_pulled_toward_average(self):
        profile = PROFILES[Platform.GOOGLE]
        self.assertLess(shrink_rating(5.0, 11, profile), 4.35)

    def test_heavy_evidence_survives_intact(self):
        profile = PROFILES[Platform.GOOGLE]
        self.assertGreater(shrink_rating(4.7, 50_000, profile), 4.69)

    def test_a_busy_good_place_beats_a_quiet_perfect_one(self):
        # The single most important behaviour in the whole system.
        quiet = listing(Platform.GOOGLE, "a", "Quiet", 0, 0, 5.0, 11)
        busy = listing(Platform.GOOGLE, "b", "Busy", 0, 0, 4.7, 3120)
        self.assertGreater(platform_score(busy), platform_score(quiet))


class TestGeometry(unittest.TestCase):
    def test_known_distance(self):
        # Lisbon to Madrid is roughly 500km.
        metres = haversine_m(38.7223, -9.1393, 40.4168, -3.7038)
        self.assertTrue(490_000 < metres < 510_000, metres)

    def test_zero_distance(self):
        self.assertAlmostEqual(haversine_m(38.7, -9.1, 38.7, -9.1), 0.0)


class TestDisjointSet(unittest.TestCase):
    def test_transitive_merging(self):
        dsu = DisjointSet(4)
        dsu.union(0, 1)
        dsu.union(1, 2)
        self.assertEqual(dsu.find(0), dsu.find(2))
        self.assertNotEqual(dsu.find(0), dsu.find(3))


class TestMatching(unittest.TestCase):
    def test_same_place_different_platforms_merges(self):
        a = listing(Platform.GOOGLE, "a", "Café Adélia", 38.7120, -9.1395, 4.6, 1120)
        b = listing(Platform.YELP, "b", "Cafe Adelia", 38.71196, -9.13957, 4.1, 180)
        self.assertTrue(is_match(a, b))

    def test_same_platform_never_merges_directly(self):
        a = listing(Platform.GOOGLE, "a", "Tasca do Rio", 38.7145, -9.1372, 4.5, 1400)
        b = listing(Platform.GOOGLE, "b", "Tasca do Rio", 38.71453, -9.13716, 4.3, 40)
        self.assertFalse(is_match(a, b))

    def test_identical_names_far_apart_do_not_merge(self):
        # A chain with two branches across town is two venues, not one.
        a = listing(Platform.GOOGLE, "a", "Cervejaria Silva", 38.7128, -9.1412, 4.4, 2600)
        b = listing(Platform.YELP, "b", "Cervejaria Silva", 38.7400, -9.1700, 3.9, 320)
        self.assertFalse(is_match(a, b))

    def test_neighbours_with_different_names_stay_separate(self):
        a = listing(Platform.GOOGLE, "a", "Bella Vista", 38.71550, -9.14380, 4.5, 720)
        b = listing(Platform.YELP, "b", "Adega do Chiado", 38.71560, -9.14330, 3.6, 95)
        self.assertFalse(is_match(a, b))

    def test_duplicate_google_pins_collapse_to_the_busier_one(self):
        listings = [
            listing(Platform.GOOGLE, "g1", "Tasca do Rio", 38.7145, -9.1372, 4.5, 1400),
            listing(Platform.GOOGLE, "g2", "Tasca do Rio (Baixa)", 38.71453, -9.13716, 4.3, 40),
            listing(Platform.YELP, "y1", "Tasca do Rio", 38.71447, -9.13727, 4.0, 155),
        ]
        venues = cluster_listings(listings)
        self.assertEqual(len(venues), 1)
        google = [x for x in venues[0].listings if x.platform is Platform.GOOGLE]
        self.assertEqual(len(google), 1)
        self.assertEqual(google[0].review_count, 1400)

    def test_listings_without_ratings_are_discarded(self):
        listings = [
            Listing(Platform.GOOGLE, "g", "Ghost", 38.7, -9.1, None, 0),
            Listing(Platform.YELP, "y", "Ghost", 38.7, -9.1, 4.0, 0),
        ]
        self.assertEqual(cluster_listings(listings), [])


class TestScoring(unittest.TestCase):
    def _venue(self, listings):
        return cluster_listings(listings)[0]

    def test_disagreement_pulls_the_score_toward_neutral(self):
        agree = self._venue([
            listing(Platform.GOOGLE, "g", "Concord", 38.700, -9.100, 4.7, 900),
            listing(Platform.YELP, "y", "Concord", 38.70004, -9.10004, 4.5, 900),
        ])
        conflict = self._venue([
            listing(Platform.GOOGLE, "g", "Discord", 38.710, -9.110, 4.8, 900),
            listing(Platform.YELP, "y", "Discord", 38.71004, -9.11004, 3.2, 900),
        ])
        self.assertGreater(
            score_venue(agree).corroboration, score_venue(conflict).corroboration
        )

    def test_more_platforms_means_more_confidence(self):
        one = self._venue([
            listing(Platform.GOOGLE, "g", "Solo", 38.700, -9.100, 4.6, 800),
        ])
        two = self._venue([
            listing(Platform.GOOGLE, "g", "Duo", 38.710, -9.110, 4.6, 800),
            listing(Platform.TRIPADVISOR, "t", "Duo", 38.71004, -9.11004, 4.35, 800),
        ])
        self.assertGreater(score_venue(two).corroboration, score_venue(one).corroboration)

    def test_foursquares_ten_point_scale_is_handled(self):
        # 8.9/10 on Foursquare and 4.7/5 on Google are both "clearly good".
        fsq = listing(Platform.FOURSQUARE, "f", "X", 0, 0, 8.9, 430)
        google = listing(Platform.GOOGLE, "g", "X", 0, 0, 4.7, 430)
        self.assertLess(abs(platform_score(fsq) - platform_score(google)), 15.0)

    def test_low_trust_source_is_quieter_even_when_alone(self):
        # Regression: `trust` used to act only as a relative weight, which
        # cancels out when a venue has a single source. A lone Foursquare
        # listing therefore spoke as loudly as a lone Google one.
        fsq = self._venue([listing(Platform.FOURSQUARE, "f", "X", 38.70, -9.10, 9.2, 400)])
        google = self._venue([listing(Platform.GOOGLE, "g", "Y", 38.72, -9.12, 4.8, 400)])
        self.assertLess(score_venue(fsq).corroboration, score_venue(google).corroboration)

    def test_score_stays_inside_the_scale(self):
        for rating, reviews in [(1.0, 5), (5.0, 5), (5.0, 100_000), (1.0, 100_000)]:
            venue = self._venue([listing(Platform.GOOGLE, "g", "X", 0, 0, rating, reviews)])
            self.assertTrue(0.0 <= score_venue(venue).score <= 100.0)


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.listings = FixtureProvider().search(City("Lisbon"), limit=500)

    def test_fixture_loads(self):
        self.assertGreater(len(self.listings), 30)

    def test_returns_at_most_the_limit(self):
        self.assertEqual(len(rank(self.listings, limit=10)), 10)

    def test_results_are_sorted_descending(self):
        scores = [r.score for r in rank(self.listings, limit=10)]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_the_five_star_tourist_trap_does_not_win(self):
        # Casa Vermelha: a perfect 5.0 from eleven reviews on one platform.
        top = rank(self.listings, limit=10)
        self.assertNotIn("Casa Vermelha", [r.venue.display_name for r in top])

    def test_a_single_source_venue_cannot_top_the_chart(self):
        # Mercado do Peixe: 9.2/10 on Foursquare alone, and nowhere else.
        top = rank(self.listings, limit=10)
        self.assertGreater(len(top[0].per_platform), 1, top[0].explain())

    def test_broadly_loved_place_reaches_the_podium(self):
        top3 = [r.venue.display_name for r in rank(self.listings, limit=3)]
        self.assertIn("Quinta das Rosas", top3)

    def test_min_platforms_filters_single_source_venues(self):
        results = rank(self.listings, limit=20, min_platforms=2)
        for result in results:
            self.assertGreaterEqual(len(result.per_platform), 2)

    def test_neighbouring_venues_stay_distinct(self):
        names = [r.venue.display_name for r in rank(self.listings, limit=20)]
        self.assertIn("Bella Vista", names)
        self.assertIn("Adega do Chiado", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
