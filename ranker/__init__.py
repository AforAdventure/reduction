"""Cross-referenced restaurant rankings.

The package name is a placeholder until the project is named.
"""

from .models import Listing, Platform, ScoredVenue, Venue
from .pipeline import format_table, rank

__all__ = [
    "Listing",
    "Platform",
    "ScoredVenue",
    "Venue",
    "rank",
    "format_table",
]

__version__ = "0.1.0"
