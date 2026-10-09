import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

TX = "John asked me to move the bathroom pipe 300 mil to the left on level 2, probably two hours extra."
AUDIO = ("a.webm", b"not-really-audio-but-bytes", "audio/webm;codecs=opus")
PHOTO = ("p.jpg", b"\xff\xd8\xff not-really-a-jpeg", "image/jpeg")


@pytest.fixture
def settings(tmp_path):
    # no keys: STT is "mock" (unavailable), structuring uses the rules extractor
    return Settings(data_dir=tmp_path, stt_provider="mock", extract_provider="rules", rate_limit_per_hour=0)


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


@pytest.fixture
def project(client):
    r = client.post("/projects", json={"name": "Test site", "site_contacts": ["John Smith", "Sarah"]})
    assert r.status_code == 201
    return r.json()


@pytest.fixture
def capture(client, project):
    """A saved capture (audio + photo), not yet structured."""
    r = client.post("/captures", data={"project_id": project["id"]}, files={"audio": AUDIO, "photo": PHOTO})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def structured(client, capture):
    r = client.post(f"/captures/{capture['id']}/extract", json={"transcript": TX})
    assert r.status_code == 200, r.text
    return r.json()
