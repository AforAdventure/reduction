# Reduction

**Many opinions, simmered down to ten.**

Pick a city, get the top 10 restaurants — ranked by combining ratings from
several review platforms rather than trusting any single one.

The name is the method. A reduction boils a great many ingredients down until
what remains is concentrated and true. This does the same to review data:
thousands of ratings across several platforms, reduced to ten places and one
number you can interrogate.

## Try it

```bash
python3 -m reduction.cli Lisbon --limit 10 --min-platforms 2
```

```bash
python3 -m unittest discover -s tests -t .
```

No API keys needed — it runs against synthetic fixture data in
`data/fixtures/`. That is deliberate: the interesting problems (matching
listings across platforms, making ratings comparable, weighting evidence) are
all solvable offline, and solving them offline means fast, repeatable tests.

## How a ranking is produced

```
listings from N platforms
   │
   ├─ cluster ──▶ decide which listings are the same restaurant   (matching.py)
   ├─ shrink  ──▶ pull thin ratings toward the platform average   (normalize.py)
   ├─ translate ▶ convert each platform's scale to a shared one   (normalize.py)
   ├─ combine ──▶ weight by evidence, discount by disagreement    (scoring.py)
   └─ filter & sort ──▶ top N                                     (pipeline.py)
```

Every stage is a plain function: data in, data out. Nothing touches the
network, so the whole thing is testable without mocks.

## Layout

| File | Job |
| --- | --- |
| `reduction/models.py` | `Listing` vs `Venue`, `Distinction`, and each platform's rating profile |
| `reduction/normalize.py` | Name cleaning, name similarity, Bayesian shrinkage, scale conversion |
| `reduction/matching.py` | Haversine distance, spatial blocking, union-find clustering |
| `reduction/scoring.py` | Evidence weighting, corroboration, disagreement discount |
| `reduction/distinctions.py` | Michelin awards, matched to venues after clustering |
| `reduction/pipeline.py` | Wires the stages together; output formatting |
| `reduction/precompute.py` | Builds the static JSON the site serves |
| `reduction/http.py` | Throttled, retrying, disk-cached JSON client |
| `reduction/providers/` | One module per data source; all satisfy `Provider` |
| `data/fixtures/` | Synthetic city data (fictional venues, invented ratings) |

## Reading the output

```
 1. Quinta das Rosas    61.5
    foursquare=66.5 google=64.8 tripadvisor=63.6 yelp=60.6 | sources: 4 | corroboration x0.83 | spread 2.1
```

- **61.5** — final score. 50 is "exactly average for this city". Each 15
  points is roughly one standard deviation.
- **per-platform** — each source's verdict, already shrunk and translated.
- **corroboration** — confidence multiplier, from source count, source
  quality, and how much the sources agree. It pulls the score *toward* 50; it
  never inflates it.
- **spread** — how far apart the platforms are. High spread is a warning.

## Calibration

`PROFILES` in `models.py` holds each platform's typical mean and spread. The
current values are reasonable starting estimates, **not measurements**. Once
real data flows in, recompute them per city — a 4.4 means something different
in Naples than in Copenhagen.

## Michelin distinctions

Stars and Bib Gourmands are handled separately from ratings, on purpose. A
rating is a noisy crowd measurement that needs shrinking and normalising; a
star is one expert verdict with no review count to weigh. So awards skip the
statistics entirely and act as a small, bounded bonus (★★★ +6 down to Bib
+2.5) plus a badge on the page.

Michelin publishes no API and prohibits scraping the guide, so each city has a
hand-curated file in `data/michelin/`. See the `_howto` block in
`data/michelin/lisbon.json` for the schema. Cities with no file simply get no
awards — that is the normal case, not an error.

## Setup

```bash
cp .env.example .env      # then paste your TripAdvisor key into it
```

Never commit `.env` — it is gitignored. On GitHub the key goes in
**Settings → Secrets and variables → Actions** as `TRIPADVISOR_API_KEY`, where
`.github/workflows/refresh.yml` picks it up. It never enters the repo and
never reaches a browser: the published site is static JSON only.

```bash
python3 -m reduction.precompute --dry-run --per-city 200   # fixtures, no key
python3 -m reduction.precompute --only lisbon              # live, needs key
python3 serve.py                                        # http://localhost:8137
```

## Status

Ranking engine: done and tested (54 tests).
TripAdvisor provider: written, **not yet run against the live API** — verify
the endpoint paths and field names against current docs before trusting it.
Google / Yelp / Foursquare providers: not built. The scoring already models
them; each is one file implementing `Provider`.

## A caveat worth repeating

With only TripAdvisor connected, every venue has exactly one source. The
corroboration machinery is real but idle — it cannot reward agreement between
platforms that aren't there, so `--min-platforms 2` would return nothing at
all. Cross-referencing only starts paying off with the second provider.
