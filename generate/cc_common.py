"""Shared helpers for every CC/public-domain track source (Jamendo,
Freesound, Openverse -- see cc_library.py, freesound_source.py,
openverse_source.py). Pulled out so all three sources apply the exact
same license allow-list and the same defensive URL-parsing logic,
instead of three copies that could silently drift apart.
"""
from __future__ import annotations

from urllib.parse import urlparse

ALLOWED_LICENSES = {"CC0", "CC-BY", "public-domain"}
FORBIDDEN_LICENSE_MARKERS = ("NC", "SA", "ND")  # reject if any appear in a license string


def license_from_ccurl(ccurl: str | None) -> str | None:
    """Parses a creativecommons.org license URL into one of
    ALLOWED_LICENSES, or None if it's anything else (including any URL
    this doesn't recognize -- fails safe/rejects rather than guessing).
    Jamendo, Freesound and Openverse all surface a CC URL for a track's
    license (in different fields), so this one parser covers all three.
    """
    if not ccurl:
        return None
    path = urlparse(ccurl).path.lower()
    if "publicdomain/zero" in path:
        return "CC0"
    if "/licenses/by/" in path:  # exactly "by", not "by-nc", "by-sa", "by-nd", etc.
        return "CC-BY"
    return None


def has_forbidden_marker(license_url: str | None) -> bool:
    """Belt-and-braces check: reject if an excluded marker (NC/SA/ND)
    shows up anywhere in the raw license URL string, even if
    license_from_ccurl() didn't already reject it for some other reason."""
    return any(marker in (license_url or "").upper() for marker in FORBIDDEN_LICENSE_MARKERS)
