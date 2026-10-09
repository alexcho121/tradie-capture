import httpx
import pytest

from app.geocode import GeocodeError, ReverseGeocoder, readable_name


def test_readable_name_prefers_street_locality_and_australian_state_abbreviation():
    assert readable_name({"address": {
        "house_number": "123", "road": "Example Street", "suburb": "Macquarie Park",
        "state": "New South Wales", "ISO3166-2-lvl4": "AU-NSW",
    }}) == "123 Example Street, Macquarie Park NSW"
    assert readable_name({"address": {"suburb": "Macquarie Park", "state": "New South Wales"}}) == "Macquarie Park NSW"
    assert readable_name({"address": {}}) is None


def test_nominatim_request_is_identified_and_returns_a_compact_label(monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(url=url, **kwargs)
        return httpx.Response(200, json={"address": {"suburb": "Macquarie Park", "state": "New South Wales"}},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr("app.geocode.httpx.get", fake_get)
    geocoder = ReverseGeocoder("https://example.test/reverse", "TradieCapture/0.1", 2)
    result = geocoder.reverse(-33.7, 151.1)
    assert result.display_name == "Macquarie Park NSW"
    assert seen["headers"]["User-Agent"] == "TradieCapture/0.1"
    assert seen["params"]["format"] == "jsonv2" and seen["params"]["addressdetails"] == 1
    assert seen["timeout"] == 2
    monkeypatch.setattr("app.geocode.httpx.get", lambda *_args, **_kwargs: pytest.fail("cached lookup called provider"))
    assert geocoder.reverse(-33.7, 151.1) == result


def test_nominatim_network_failure_is_normalized(monkeypatch):
    monkeypatch.setattr("app.geocode.httpx.get", lambda *_args, **_kwargs: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    with pytest.raises(GeocodeError, match="unavailable"):
        ReverseGeocoder("https://example.test/reverse", "TradieCapture/0.1").reverse(-33.7, 151.1)
