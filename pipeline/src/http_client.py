from __future__ import annotations

import logging
import os
import random
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

DEFAULT_FROM_EMAIL = os.environ.get("LADBROKES_FROM_EMAIL", "racing-analytics-bot@turfedge-au.app").strip() or "racing-analytics-bot@turfedge-au.app"
DEFAULT_PARTNER = os.environ.get("LADBROKES_PARTNER", "TurfEdgeAU-PersonalAnalytics").strip() or "TurfEdgeAU-PersonalAnalytics"
DEFAULT_USER_AGENT = (
    "TurfEdgeAU/1.0 (+https://github.com/turfedge-au; Australian Thoroughbred Research Bot; "
    f"contact={DEFAULT_FROM_EMAIL})"
)


class PoliteHttpClient:
    """HTTP client enforcing per-host polite rate limiting (>=3.0s) and exponential backoff."""

    def __init__(
        self,
        min_delay_seconds: float = 3.0,
        max_retries: int = 4,
        base_backoff_seconds: float = 4.0,
        timeout_seconds: float = 20.0,
        from_email: Optional[str] = None,
        partner_name: Optional[str] = None,
    ) -> None:
        self.min_delay_seconds = max(3.0, float(min_delay_seconds))
        self.max_retries = max_retries
        self.base_backoff_seconds = base_backoff_seconds
        self.timeout_seconds = timeout_seconds
        self.from_email = from_email or DEFAULT_FROM_EMAIL
        self.partner_name = partner_name or DEFAULT_PARTNER
        self._last_request_ts: Dict[str, float] = {}
        self.session = requests.Session()

    def _build_headers(self, url: str, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        host = urlparse(url).netloc.lower()
        headers: Dict[str, str] = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        }
        # Ladbrokes Affiliates API guidelines require valid From + descriptive User-Agent (+ X-Partner)
        if "ladbrokes.com.au" in host:
            headers["From"] = self.from_email
            headers["X-Partner"] = self.partner_name
            headers["User-Agent"] = (
                f"TurfEdgeAU-AffiliatesClient/1.0 (Personal Australian Racing Analytics; contact: {self.from_email})"
            )
            headers["Accept"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _wait_for_slot(self, host: str) -> None:
        last_ts = self._last_request_ts.get(host)
        if last_ts is not None:
            elapsed = time.monotonic() - last_ts
            remaining = self.min_delay_seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_ts[host] = time.monotonic()

    def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        allow_404: bool = False,
    ) -> Optional[requests.Response]:
        host = urlparse(url).netloc.lower()
        req_headers = self._build_headers(url, headers)

        for attempt in range(self.max_retries + 1):
            self._wait_for_slot(host)
            try:
                resp = self.session.get(
                    url,
                    params=params,
                    headers=req_headers,
                    timeout=self.timeout_seconds,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt >= self.max_retries:
                    logger.error("HTTP network failure after %d retries for %s: %s", self.max_retries, url, exc)
                    raise
                sleep_s = self.base_backoff_seconds * (2 ** attempt) + random.uniform(0.3, 1.5)
                logger.warning("Network error for %s (%s). Backing off %.1fs...", url, exc, sleep_s)
                time.sleep(sleep_s)
                continue

            if resp.status_code == 404 and allow_404:
                return None

            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt >= self.max_retries:
                    resp.raise_for_status()
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    sleep_s = max(float(retry_after), self.min_delay_seconds)
                else:
                    sleep_s = self.base_backoff_seconds * (2 ** attempt) + random.uniform(0.5, 2.0)
                logger.warning(
                    "HTTP %d from %s (attempt %d/%d). Backing off %.1fs...",
                    resp.status_code,
                    host,
                    attempt + 1,
                    self.max_retries,
                    sleep_s,
                )
                time.sleep(sleep_s)
                continue

            resp.raise_for_status()
            return resp

        return None

    def get_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        allow_404: bool = False,
    ) -> Optional[Dict[str, Any]]:
        resp = self.get(url, params=params, headers=headers, allow_404=allow_404)
        if resp is None:
            return None
        return resp.json()

    def get_text(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        allow_404: bool = False,
    ) -> Optional[str]:
        resp = self.get(url, params=params, headers=headers, allow_404=allow_404)
        if resp is None:
            return None
        return resp.text
