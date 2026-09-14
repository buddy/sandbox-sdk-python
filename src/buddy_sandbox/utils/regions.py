"""API regions and the base URLs they map to."""

from __future__ import annotations

from typing import Final, Literal, get_args

Region = Literal["US", "EU", "AS"]

REGIONS: Final[tuple[Region, ...]] = get_args(Region)

API_URLS: Final[dict[Region, str]] = {
    "US": "https://api.buddy.works",
    "EU": "https://api.eu.buddy.works",
    "AS": "https://api.asia.buddy.works",
}


def get_api_url_from_region(region: Region) -> str:
    """Return the API base URL serving the given region."""
    return API_URLS[region]


def parse_region(value: str | None) -> Region:
    """Normalise a region name, falling back to US when nothing is given."""
    if not value:
        return "US"

    normalized = value.upper().strip()

    if normalized in API_URLS:
        return normalized

    raise ValueError(f'Invalid region: "{value}". Valid regions are: {", ".join(REGIONS)}')
