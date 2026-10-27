"""API surface.

Two things matter here: reads return well-formed JSON, and anything that can move money is
POST-only and refuses an unauthenticated caller when a token is configured.
"""

import pytest
from fastapi.testclient import TestClient

from helm.api import create_app
from helm.config import Config
from helm.engine import Engine


@pytest.fixture
def client(tmp_path):
    config = Config()
    config.db_path = str(tmp_path / "api.db")
    config.venue = "synthetic"
    engine = Engine(config)
    engine.cycle()
    # start_engine=False keeps the background trading thread out of the test process.
    app = create_app(engine, start_engine=False)
    with TestClient(app) as test_client:
        test_client.engine = engine
        yield test_client


def test_state_has_the_shape_the_ui_expects(client):
    body = client.get("/api/state").json()
    for key in ("phase", "account", "positions", "coins", "doctrine", "blocks", "config",
                "next_decision_at"):
        assert key in body
    for key in ("equity", "cash", "invested", "quote", "drawdown", "peak_equity"):
        assert key in body["account"]
    assert [row["symbol"] for row in body["coins"]] == ["BTCEUR", "ETHEUR", "SOLEUR", "XRPEUR"]


def test_state_never_reveals_a_secret(client):
    body = client.get("/api/state").text.lower()
    for leak in ("api_key", "secret", "sk-ant", "binance_api"):
        assert leak not in body


def test_read_endpoints_all_return_json(client):
    for path in ("/api/feed", "/api/decisions", "/api/trades", "/api/equity",
                 "/api/stats", "/api/doctrine"):
        response = client.get(path)
        assert response.status_code == 200, path
        response.json()


def test_doctrine_markdown_is_served(client):
    body = client.get("/api/doctrine.md").text
    assert "Doctrine" in body


def test_health_is_plain_and_unauthenticated(client):
    assert client.get("/health").text == "ok"


def test_ui_is_served(client):
    assert "HELM" in client.get("/").text
    assert client.get("/app.js").status_code == 200
    assert client.get("/styles.css").status_code == 200


def test_asset_route_cannot_escape_the_ui_directory(client):
    assert client.get("/../helm/config.js").status_code in (404, 400)


def test_control_endpoints_reject_get(client):
    assert client.get("/api/control/flatten").status_code == 405


def test_pause_and_resume_round_trip(client):
    assert client.post("/api/control/pause").json()["paused"] is True
    assert client.get("/api/state").json()["paused"] is True
    assert client.post("/api/control/resume").json()["paused"] is False


def test_closing_an_unknown_position_is_a_404(client):
    assert client.post("/api/control/close/nope").status_code == 404


def test_token_guards_reads_and_controls(tmp_path):
    config = Config()
    config.db_path = str(tmp_path / "auth.db")
    config.venue = "synthetic"
    config.ui_token = "hunter2"
    app = create_app(Engine(config), start_engine=False)
    with TestClient(app) as client:
        assert client.get("/api/state").status_code == 401
        assert client.post("/api/control/flatten").status_code == 401
        assert client.get("/api/state", headers={"X-Helm-Token": "hunter2"}).status_code == 200
        # The health check stays open so a platform probe still works.
        assert client.get("/health").status_code == 200


def test_websocket_sends_a_state_snapshot_on_connect(client):
    with client.websocket_connect("/ws") as socket:
        first = socket.receive_json()
        assert first["kind"] == "state"
        assert "account" in first["data"]
