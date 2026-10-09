"""Best-effort reverse geocoding, kept separate from evidence capture.

The public Nominatim service is suitable for this low-volume MVP only. Its policy
requires an identifying User-Agent, attribution, and no more than one request per
second. A failed lookup returns no enrichment and never affects the saved capture.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx


ATTRIBUTION = "© OpenStreetMap contributors"


class GeocodeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReadableLocation:
    display_name: str
    provider: str = "nominatim"
    attribution: str = ATTRIBUTION


_AU_STATES = {
    "Australian Capital Territory": "ACT", "New South Wales": "NSW", "Northern Territory": "NT",
    "Queensland": "QLD", "South Australia": "SA", "Tasmania": "TAS", "Victoria": "VIC",
    "Western Australia": "WA",
}


def readable_name(payload: Dict[str, Any]) -> Optional[str]:
    """Build a compact street/suburb/state label from Nominatim's address details."""
    address = payload.get("address")
    if not isinstance(address, dict):
        return None
    locality = next((str(address[k]).strip() for k in (
        "suburb", "neighbourhood", "city_district", "city", "town", "village", "municipality", "county"
    ) if address.get(k)), "")
    state = str(address.get("state") or "").strip()
    iso = str(address.get("ISO3166-2-lvl4") or "").strip()
    state_short = iso.split("-", 1)[1] if iso.startswith("AU-") else _AU_STATES.get(state, state)
    road = str(address.get("road") or "").strip()
    number = str(address.get("house_number") or "").strip()
    street = " ".join(v for v in (number, road) if v)
    parts = [street, locality, state_short]
    label = ", ".join(v for v in parts[:2] if v)
    if state_short:
        label = f"{label} {state_short}" if label else state_short
    return label or None


class ReverseGeocoder:
    def __init__(self, url: str, user_agent: str, timeout_seconds: float = 4.0):
        self.url = url
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._cache: Dict[tuple[float, float], ReadableLocation] = {}

    def reverse(self, latitude: float, longitude: float) -> Optional[ReadableLocation]:
        cache_key = (round(latitude, 4), round(longitude, 4))
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
            wait = 1.0 - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                response = httpx.get(
                    self.url,
                    params={
                        "lat": latitude, "lon": longitude, "format": "jsonv2", "addressdetails": 1,
                        "zoom": 18, "layer": "address", "accept-language": "en",
                    },
                    headers={"User-Agent": self.user_agent},
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise GeocodeError("reverse geocoding unavailable") from exc
            label = readable_name(payload) if isinstance(payload, dict) else None
            result = ReadableLocation(label) if label else None
            if result:
                if len(self._cache) >= 512:
                    self._cache.clear()
                self._cache[cache_key] = result
            return result
