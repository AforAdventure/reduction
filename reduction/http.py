"""A small, polite HTTP client.

Deliberately stdlib-only — no `requests`. This project has one job that needs
the network, and carrying a dependency tree for it is not worth the cost.

"Polite" here means three specific things, all of which exist because we are a
guest on someone else's server and our quota is finite:

  * we never fire requests faster than a fixed interval,
  * we retry only the failures that are worth retrying, with backoff,
  * we cache responses to disk, because the cheapest API call is the one you
    do not make.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Optional

# Status codes where trying again might actually help. A 401 means your key is
# wrong; retrying a wrong key just wastes everyone's time.
RETRYABLE = frozenset({429, 500, 502, 503, 504})


class HttpError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__("HTTP {}: {}".format(status, message))
        self.status = status


class JsonClient:
    """Fetches JSON, slowly and only once."""

    def __init__(
        self,
        min_interval: float = 0.25,
        max_retries: int = 3,
        timeout: float = 15.0,
        cache_dir: Optional[Path] = None,
        cache_ttl: float = 7 * 24 * 3600,
        user_agent: str = "reduction/0.1",
    ) -> None:
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_ttl = cache_ttl
        self.user_agent = user_agent
        self._last_request = 0.0
        self.calls_made = 0  # Quota is finite; counting it is not optional.
        self.cache_hits = 0

    # -- caching ---------------------------------------------------------

    def _cache_path(self, url: str) -> Optional[Path]:
        if self.cache_dir is None:
            return None
        # The URL contains the API key, so we hash it rather than store it.
        digest = sha256(url.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / "{}.json".format(digest)

    def _read_cache(self, url: str) -> Optional[Any]:
        path = self._cache_path(url)
        if path is None or not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.cache_ttl:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None  # A corrupt cache entry is a miss, not a crash.

    def _write_cache(self, url: str, payload: Any) -> None:
        path = self._cache_path(url)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    # -- fetching --------------------------------------------------------

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.time()

    def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        if params:
            url = "{}?{}".format(url, urllib.parse.urlencode(params))

        cached = self._read_cache(url)
        if cached is not None:
            self.cache_hits += 1
            return cached

        # Auth headers are deliberately NOT part of the cache key. The key
        # identifies who is asking, not what was asked for, and two keys
        # requesting the same URL should hit the same cached answer.
        merged = {"Accept": "application/json", "User-Agent": self.user_agent}
        merged.update(headers or {})
        request = urllib.request.Request(url, headers=merged)

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                self.calls_made += 1
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self._write_cache(url, payload)
                return payload
            except urllib.error.HTTPError as exc:
                last_error = HttpError(exc.code, exc.reason or "")
                if exc.code not in RETRYABLE:
                    raise last_error  # Permanent failure: stop immediately.
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                last_error = exc

            # Exponential backoff: 0.5s, 1s, 2s... Hammering a struggling
            # server at a fixed interval is how you turn a blip into a ban.
            if attempt < self.max_retries - 1:
                time.sleep(0.5 * (2 ** attempt))

        raise last_error if last_error else RuntimeError("request failed")
