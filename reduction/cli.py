"""Command line entry point.

    python3 -m reduction.cli Lisbon
    python3 -m reduction.cli Lisbon --limit 5 --min-platforms 2
"""

from __future__ import annotations

import argparse
import sys

from .cities import City, find_city
from .pipeline import DEFAULT_MIN_REVIEWS, format_table, rank
from .providers import FixtureProvider, ProviderError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Rank a city's restaurants by cross-referencing review platforms."
    )
    parser.add_argument("city", help="City name, e.g. Lisbon")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--min-platforms",
        type=int,
        default=1,
        help="Only rank venues seen by at least this many platforms.",
    )
    parser.add_argument(
        "--min-reviews",
        type=int,
        default=DEFAULT_MIN_REVIEWS,
        help="Only rank venues with at least this many total reviews.",
    )
    args = parser.parse_args(argv)

    # Use the configured city if we know it (it carries coordinates), and
    # fall back to a bare name so the CLI still works for anything else.
    city = find_city(args.city) or City(name=args.city)

    provider = FixtureProvider()
    try:
        listings = provider.search(city, limit=500)
    except ProviderError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1

    results = rank(
        listings,
        limit=args.limit,
        min_platforms=args.min_platforms,
        min_total_reviews=args.min_reviews,
        city=city.name,
    )
    print(format_table(results, city=city.label))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
