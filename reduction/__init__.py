"""Reduction — cross-referenced restaurant rankings.

Named for what it does. A reduction simmers a great many ingredients down
until what remains is concentrated and true; this simmers a great many
opinions down to ten.
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
