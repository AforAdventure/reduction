"""Rank a list of cities ahead of time and write static JSON.

This is the design decision that makes the whole project cheap and shippable.

The obvious architecture — user types a city, we call four APIs live — means
a server, exposed API keys or a proxy to hide them, latency on every page
load, and an uncapped bill that a single bored visitor with a loop can run up.

Instead we rank a fixed list of cities on a schedule and commit the results as
plain JSON. GitHub Pages serves it. Page loads are instant, the API bill is
bounded by the number of cities rather than the number of visitors, and there
is no key to leak because there is no key in the browser.

The cost of that choice is honest and worth stating: a fixed city list, and
data that is as fresh as your last run.

    python3 -m reduction.precompute --dry-run
    TRIPADVISOR_API_KEY=... python3 -m reduction.precompute --limit 10
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .models import Listing, ScoredVenue
from .pipeline import rank
from .providers import FixtureProvider, ProviderError
from .providers.tripadvisor import TripAdvisorProvider

ROOT = Path(__file__).resolve().parent.parent
CITIES_FILE = ROOT / "data" / "cities.json"
OUTPUT_DIR = ROOT / "site" / "data"
CACHE_DIR = ROOT / ".cache" / "http"


def slugify(city: str) -> str:
    return city.strip().lower().replace(" ", "-").replace(",", "")


def load_cities(path: Path = CITIES_FILE) -> List[Dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["cities"]


def _serialize(result: ScoredVenue, position: int) -> Dict:
    """Shape one ranked venue for the browser.

    Note what we deliberately include: the per-platform breakdown and the
    confidence figures. The page's whole claim to being better than a listicle
    is that it can show its work, and it can only do that if we ship the work.
    """
    listings = sorted(result.venue.listings, key=lambda x: x.platform.value)
    return {
        "rank": position,
        "name": result.venue.display_name,
        "score": result.score,
        "lat": round(result.venue.lat, 6),
        "lon": round(result.venue.lon, 6),
        "total_reviews": result.venue.total_reviews,
        "corroboration": result.corroboration,
        "distinctions": [
            {"code": d.value, "label": d.label, "short": d.short}
            for d in result.venue.distinctions
        ],
        "distinction_bonus": result.distinction_bonus,
        "disagreement": result.disagreement,
        "sources": [
            {
                "platform": listing.platform.value,
                "rating": listing.rating,
                "scale_max": listing.profile.scale_max,
                "reviews": listing.review_count,
                "normalized": result.per_platform.get(listing.platform),
                "url": listing.url,
            }
            for listing in listings
        ],
        "address": next(
            (x.address for x in listings if x.address), None
        ),
        "price_level": next(
            (x.price_level for x in listings if x.price_level), None
        ),
    }


def build_city(
    city: Dict[str, str],
    listings: List[Listing],
    limit: int,
    min_platforms: int,
    synthetic: bool = False,
) -> Dict:
    results = rank(
        listings,
        limit=limit,
        min_platforms=min_platforms,
        city=city["name"],
    )
    return {
        "city": city["name"],
        "country": city.get("country"),
        "slug": slugify(city["name"]),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "listings_considered": len(listings),
        # Travels with the data so the page can say so out loud. A published
        # ranking of invented restaurants that does not announce itself is
        # indistinguishable from a real one, which is not acceptable.
        "synthetic": synthetic,
        "restaurants": [_serialize(r, i) for i, r in enumerate(results, start=1)],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Precompute city rankings.")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--min-platforms", type=int, default=1)
    parser.add_argument("--per-city", type=int, default=30, help="Venues to fetch.")
    parser.add_argument("--only", help="Slug of a single city to build.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use bundled fixtures instead of the live API. No key required.",
    )
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args(argv)

    cities = load_cities()
    if args.only:
        cities = [c for c in cities if slugify(c["name"]) == args.only]
        if not cities:
            print("error: no city with slug {!r}".format(args.only), file=sys.stderr)
            return 1

    if args.dry_run:
        provider = FixtureProvider()
    else:
        try:
            provider = TripAdvisorProvider(
                client=_cached_client(),
            )
        except ProviderError as exc:
            print("error: {}".format(exc), file=sys.stderr)
            return 1

    args.output.mkdir(parents=True, exist_ok=True)
    index: List[Dict] = []
    started = time.time()

    for city in cities:
        name = city["name"]
        try:
            listings = provider.search(name, limit=args.per_city)
        except ProviderError as exc:
            # A city we cannot fetch is a city we skip. One failure must not
            # abandon the nineteen cities queued behind it.
            print("skip {}: {}".format(name, exc), file=sys.stderr)
            continue

        payload = build_city(
            city, listings, args.limit, args.min_platforms,
            synthetic=args.dry_run,
        )
        if not payload["restaurants"]:
            print("skip {}: nothing ranked".format(name), file=sys.stderr)
            continue

        destination = args.output / "{}.json".format(payload["slug"])
        destination.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        index.append(
            {
                "name": payload["city"],
                "country": payload["country"],
                "slug": payload["slug"],
                "count": len(payload["restaurants"]),
            }
        )
        print("built {:<24} {} restaurants".format(name, len(payload["restaurants"])))

    (args.output / "index.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "synthetic": args.dry_run,
                "cities": sorted(index, key=lambda c: c["name"]),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )

    print(
        "\n{} cities in {:.1f}s".format(len(index), time.time() - started),
        file=sys.stderr,
    )
    if not args.dry_run:
        client = provider.client
        print(
            "api calls: {} (cache hits: {})".format(
                client.calls_made, client.cache_hits
            ),
            file=sys.stderr,
        )
    return 0 if index else 1


def _cached_client():
    from .http import JsonClient

    return JsonClient(cache_dir=CACHE_DIR)


if __name__ == "__main__":
    raise SystemExit(main())
