"""The JSON API: the auth gate, and the request round trip Home Assistant makes.

The dashboard's HTML routes are deliberately unauthenticated. These are not,
because they are reachable from anywhere on the box, so the gate is the part of
this module most worth a regression test.
"""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings

TOKEN = "test-token-abc123"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A test client whose settings point at a throwaway data dir and token file."""
    token_file = tmp_path / "api_token"
    # Written with a trailing newline on purpose: an editor would, and stripping
    # it is what stops a good token reading as a bad one.
    token_file.write_text(TOKEN + "\n")

    settings = Settings(
        data_dir=Path(tempfile.mkdtemp()), api_token_file=token_file
    )

    from app.web import server

    monkeypatch.setattr(server, "get_settings", lambda: settings)
    server.app.dependency_overrides[server.get_settings] = lambda: settings
    from app.config import get_settings as real_get_settings

    server.app.dependency_overrides[real_get_settings] = lambda: settings
    try:
        yield TestClient(server.app)
    finally:
        server.app.dependency_overrides.clear()


def _auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": TOKEN},  # no scheme
        {"Authorization": "Basic " + TOKEN},
        {"Authorization": "Bearer "},
    ],
)
def test_api_refuses_without_the_right_bearer_token(client, headers):
    assert client.get("/api/requests", headers=headers).status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        f"Bearer {TOKEN}",
        f"Bearer  {TOKEN}",       # two spaces: an iOS Shortcut header built as
                                  # "Bearer " plus a variable
        f"Bearer {TOKEN} ",
        f"Bearer {TOKEN}\n",
        f"bearer {TOKEN}",        # scheme is case-insensitive per RFC 7235
    ],
)
def test_surrounding_whitespace_is_not_part_of_the_token(client, header):
    """Whitespace a paste introduced is invisible, and the refusal blames the token."""
    assert client.get("/api/requests", headers={"Authorization": header}).status_code == 200


def test_a_trailing_newline_in_the_token_file_is_not_part_of_the_token(client):
    """The fixture writes the file with a newline, as any editor would."""
    assert client.get("/api/requests", headers=_auth()).status_code == 200
    # A token that differs by more than whitespace is still refused. Whitespace
    # itself is tolerated on both sides now; see the parametrised test above.
    assert client.get("/api/requests", headers=_auth(TOKEN + "x")).status_code == 401


def test_no_token_file_closes_the_api_rather_than_opening_it(tmp_path, monkeypatch):
    from app.config import get_settings as real_get_settings
    from app.web import server

    settings = Settings(data_dir=Path(tempfile.mkdtemp()))  # no api_token_file
    server.app.dependency_overrides[real_get_settings] = lambda: settings
    try:
        resp = TestClient(server.app).get("/api/requests", headers=_auth())
        assert resp.status_code == 503
    finally:
        server.app.dependency_overrides.clear()


def test_the_html_dashboard_stays_open(client):
    """Not an oversight: the two halves differ on purpose. See app/web/api.py."""
    assert client.get("/").status_code == 200


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


def test_post_request_then_list_it(client):
    resp = client.post(
        "/api/requests",
        json={"text": "oat milk", "source": "reminders", "external_id": "uid-1"},
        headers=_auth(),
    )
    assert resp.status_code == 201 and resp.json()["created"] is True

    listed = client.get("/api/requests", headers=_auth()).json()
    assert listed["count"] == 1
    assert listed["requests"][0]["text"] == "oat milk"
    assert listed["requests"][0]["external_id"] == "uid-1"


def test_reposting_the_same_external_id_is_200_not_201(client):
    """How the Home Assistant automation tells 'sent' from 'sent again'."""
    body = {"text": "oat milk", "external_id": "uid-1"}
    assert client.post("/api/requests", json=body, headers=_auth()).status_code == 201
    again = client.post("/api/requests", json=body, headers=_auth())
    assert again.status_code == 200 and again.json()["created"] is False
    assert client.get("/api/requests", headers=_auth()).json()["count"] == 1


def test_bad_source_is_rejected(client):
    resp = client.post(
        "/api/requests", json={"text": "oat milk", "source": "nope"}, headers=_auth()
    )
    assert resp.status_code == 422


def test_delete_drops_a_request(client):
    rid = client.post(
        "/api/requests", json={"text": "oat milk"}, headers=_auth()
    ).json()["id"]
    assert client.delete(f"/api/requests/{rid}", headers=_auth()).status_code == 200
    assert client.get("/api/requests", headers=_auth()).json()["count"] == 0
    # Dropping it again is a no-op rather than an error; only a request that
    # never existed is a 404.
    assert client.delete(f"/api/requests/{rid}", headers=_auth()).status_code == 200
    assert client.delete("/api/requests/9999", headers=_auth()).status_code == 404


# --------------------------------------------------------------------------- #
# Plan status
# --------------------------------------------------------------------------- #


def test_latest_plan_is_null_before_anything_is_planned(client):
    body = client.get("/api/plan/latest", headers=_auth()).json()
    assert body["plan"] is None and body["job_status"] == "idle"
