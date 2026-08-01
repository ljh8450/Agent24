from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .errors import DomainError

NOTIONISTS_API_VERSION = "10.x"
NOTIONISTS_SOURCE = "https://www.dicebear.com/styles/notionists/"
NOTIONISTS_LICENSE = "CC0-1.0"
NOTIONISTS_SEED = re.compile(r"[a-f0-9]{24}")
MAX_AVATAR_BYTES = 512 * 1024


def notionists_avatar(profile_id: str) -> dict[str, Any]:
    """Return a deterministic, non-identifying avatar descriptor."""
    seed = hashlib.sha256(f"persona-restorer:notionists:{profile_id}".encode()).hexdigest()[:24]
    return {
        "style": "notionists",
        "seed": seed,
        "url": f"/api/avatars/notionists/{seed}.svg",
        "remote_url": f"https://api.dicebear.com/{NOTIONISTS_API_VERSION}/notionists/svg?seed={seed}",
        "source": NOTIONISTS_SOURCE,
        "license": NOTIONISTS_LICENSE,
        "tag": "decorative_synthetic",
        "alt": f"{profile_id}의 장식용 Notionists 합성 아바타",
    }


def _fallback_svg(seed: str) -> bytes:
    """Always-available neutral profile art when the upstream asset is unavailable."""
    accent = f"#{seed[:6]}"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96" role="img">
<rect width="96" height="96" rx="18" fill="#f3efe7"/>
<path d="M18 91c2-24 14-36 30-36s28 12 30 36" fill="{accent}" opacity=".22"/>
<circle cx="48" cy="36" r="19" fill="#fffaf0" stroke="#171717" stroke-width="3"/>
<path d="M30 35c2-15 10-23 20-23 12 0 20 10 20 24-8-2-14-7-18-14-4 8-11 13-22 13Z" fill="#171717"/>
<path d="M40 38h2m12 0h2M42 47c4 3 8 3 12 0" fill="none" stroke="#171717" stroke-linecap="round" stroke-width="2.5"/>
<path d="M22 92c3-21 12-31 26-31s23 10 26 31" fill="#fffaf0" stroke="#171717" stroke-width="3"/>
</svg>""".encode()


def notionists_svg(root: Path, seed: str) -> bytes:
    """Load a same-origin cached avatar, falling back to an embedded SVG."""
    if not NOTIONISTS_SEED.fullmatch(seed):
        raise DomainError("INVALID_AVATAR_SEED", "아바타 seed 형식이 올바르지 않습니다.")

    cache_file = root / "data" / "avatar-cache" / f"notionists-{seed}.svg"
    if cache_file.is_file():
        cached = cache_file.read_bytes()
        if cached and len(cached) <= MAX_AVATAR_BYTES and b"<svg" in cached[:1024]:
            return cached

    request = urllib.request.Request(
        f"https://api.dicebear.com/{NOTIONISTS_API_VERSION}/notionists/svg?seed={seed}",
        headers={"Accept": "image/svg+xml", "User-Agent": "E2PAgent/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:  # noqa: S310 - fixed trusted host
            content = response.read(MAX_AVATAR_BYTES + 1)
        if len(content) > MAX_AVATAR_BYTES or b"<svg" not in content[:1024]:
            raise ValueError("invalid SVG response")
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(content)
        return content
    except (OSError, TimeoutError, ValueError, urllib.error.URLError):
        return _fallback_svg(seed)
