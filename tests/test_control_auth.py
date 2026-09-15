"""The control API fails open: with `HELM_UI_TOKEN` unset, the guard in `api.py` waves
everything through. That is a deliberate convenience on a laptop and a liquidate button
for strangers on anything with a public domain — which Railway gives every service.

Real money therefore requires a token, in the same way it requires the acknowledgement:
the switch missing means the engine runs dry and says why, rather than trading live with
`POST /api/control/flatten` open to the internet.
"""

import os

import pytest

from helm.config import Config


@pytest.fixture
def env(monkeypatch):
    for name in ("HELM_MODE", "I_UNDERSTAND_REAL_MONEY", "HELM_UI_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_live_requires_a_ui_token(env):
    env.setenv("HELM_MODE", "live")
    env.setenv("I_UNDERSTAND_REAL_MONEY", "true")

    assert Config().live is False, "live with an unguarded control API must fail closed"


def test_live_when_every_switch_agrees(env):
    env.setenv("HELM_MODE", "live")
    env.setenv("I_UNDERSTAND_REAL_MONEY", "true")
    env.setenv("HELM_UI_TOKEN", "a-real-token")

    assert Config().live is True


def test_a_token_alone_does_not_make_it_live(env):
    env.setenv("HELM_MODE", "live")
    env.setenv("HELM_UI_TOKEN", "a-real-token")

    assert Config().live is False, "the acknowledgement is still required"


def test_control_api_is_open_reports_the_gap(env):
    assert Config().control_api_is_open is True

    env.setenv("HELM_UI_TOKEN", "a-real-token")
    assert Config().control_api_is_open is False


def test_whitespace_is_not_a_token(env):
    env.setenv("HELM_MODE", "live")
    env.setenv("I_UNDERSTAND_REAL_MONEY", "true")
    env.setenv("HELM_UI_TOKEN", "   ")

    config = Config()
    assert config.control_api_is_open is True
    assert config.live is False


def test_dry_mode_still_runs_without_a_token(env):
    """A laptop must stay frictionless — this guard is about exposure, not ceremony."""
    config = Config()

    assert config.mode == "dry"
    assert config.live is False
    assert config.control_api_is_open is True
