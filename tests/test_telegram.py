"""Notifications and remote control.

Nothing here touches the network. The two properties that matter: the trading loop can
never be blocked or broken by Telegram, and the command surface cannot be used to buy or
loosen a limit.
"""

import queue
import re

import pytest

from helm import doctrine
from helm.engine import COMMANDS
from helm.notify.telegram import ALERT_LEVELS, TelegramNotifier
from tests.support import UP, build_engine


@pytest.fixture
def notifier():
    bot = TelegramNotifier("test-token", "12345")
    bot.posted = []
    bot._post = lambda text: bot.posted.append(text) or True
    return bot


@pytest.fixture
def engine(tmp_path, market):
    engine = build_engine(str(tmp_path / "tg.db"), market)
    engine.market_ = market
    return engine


# -- configuration -----------------------------------------------------------------------

def test_unconfigured_notifier_is_inert():
    bot = TelegramNotifier("", "")
    assert not bot.enabled
    bot.notify("anything", "entry")        # must not raise
    assert bot.sent == 0


def test_allowed_user_defaults_to_the_chat_id():
    """A private chat is locked to its owner with no extra configuration."""
    assert TelegramNotifier("t", "999").allowed_user_id == "999"
    assert TelegramNotifier("t", "999", allowed_user_id="42").allowed_user_id == "42"


# -- outbound filtering ------------------------------------------------------------------

def test_only_significant_levels_reach_the_phone(notifier):
    """Routine narration must not be forwarded, or the channel becomes ignorable."""
    for level in ("info", "think", "pass"):
        notifier.notify(f"routine {level}", level)
    notifier._drain_once()
    assert notifier.posted == []

    for level in sorted(ALERT_LEVELS):
        notifier.notify(f"important {level}", level)
    for _ in ALERT_LEVELS:
        notifier._drain_once()
    assert len(notifier.posted) == len(ALERT_LEVELS)


def test_message_text_is_escaped_and_capped(notifier):
    notifier.notify("<script>alert(1)</script>", "entry")
    notifier._drain_once()
    assert "<script>" not in notifier.posted[0]
    assert "&lt;script&gt;" in notifier.posted[0]

    # Escaping expands text, so the cap has to be applied after escaping — and the cut
    # must not leave a dangling half-entity, which Telegram rejects as bad markup.
    notifier.notify("&" * 9000, "entry")
    notifier._drain_once()
    trimmed = notifier.posted[1]
    assert len(trimmed) <= notifier.MAX_TEXT + 8
    assert re.search(r"&[a-zA-Z#][a-zA-Z0-9]*$", trimmed.rstrip("…")) is None


# -- the trading loop must never be harmed -----------------------------------------------

def test_notify_never_blocks_when_the_queue_is_full(notifier):
    """A backed-up queue means Telegram is unreachable. Dropping beats stalling a cycle."""
    notifier._queue = queue.Queue(maxsize=2)
    for index in range(50):
        notifier.notify(f"message {index}", "entry")     # must return immediately
    assert notifier.dropped == 48
    assert notifier._queue.qsize() == 2


def test_notify_never_raises_even_when_sending_explodes():
    bot = TelegramNotifier("t", "1")

    def boom(_text):
        raise RuntimeError("telegram is down")

    bot._post = boom
    bot.notify("entry happened", "entry")
    bot._drain_once()                    # the sender thread swallows it
    assert bot.failures >= 1


def test_engine_narration_survives_a_broken_notifier(engine):
    """The single most important property: a dead Telegram cannot stop trading."""
    def boom(*_args, **_kwargs):
        raise RuntimeError("telegram exploded")

    engine.notifier.notify = boom
    with pytest.raises(RuntimeError):
        engine.notifier.notify("x")      # confirms the stub really does raise

    engine.notifier.notify = lambda *a, **k: None
    engine.cycle()                        # a full cycle still completes
    assert engine.snapshot()["cycle"] == 1


# -- inbound authorisation ---------------------------------------------------------------

def _update(text: str, user_id: str = "12345", chat_id: str = "12345") -> dict:
    return {
        "update_id": 1,
        "message": {"text": text, "from": {"id": user_id}, "chat": {"id": chat_id}},
    }


def test_commands_from_a_stranger_are_ignored(notifier):
    called = []
    notifier._handlers = {"status": lambda: called.append(1) or "ok"}
    notifier._handle(_update("/status", user_id="99999", chat_id="99999"))
    assert called == []
    assert notifier.posted == []


def test_commands_from_the_owner_are_handled(notifier):
    notifier._handlers = {"status": lambda: "all good"}
    notifier._handle(_update("/status"))
    assert "all good" in notifier.posted[0]


def test_bot_suffix_and_arguments_are_stripped(notifier):
    notifier._handlers = {"status": lambda: "ok"}
    notifier._handle(_update("/status@HelmBot now please"))
    assert "ok" in notifier.posted[0]


def test_non_commands_are_ignored(notifier):
    notifier._handlers = {"status": lambda: "ok"}
    notifier._handle(_update("just chatting"))
    assert notifier.posted == []


def test_unknown_command_lists_what_exists(notifier):
    notifier._handlers = {"status": lambda: "ok", "today": lambda: "ok"}
    notifier._handle(_update("/withdraw"))
    assert "/status" in notifier.posted[0] and "/today" in notifier.posted[0]


def test_a_refused_poll_backs_off_and_says_why(notifier, caplog):
    """A 409 means another process holds the same token. Telegram answers it instantly, so
    retrying without a pause would hammer the API for as long as that process runs."""
    class Refused:
        def json(self):
            return {"ok": False, "error_code": 409,
                    "description": "Conflict: terminated by other getUpdates request"}

    polls, waits = [], []

    def refused(*args, **kwargs):
        polls.append(1)
        if len(polls) > 3:                   # a loop with no back-off would never stop
            notifier._stop.set()
        return Refused()

    notifier._client.get = refused
    notifier._stop.wait = lambda seconds: waits.append(seconds) or notifier._stop.set()
    with caplog.at_level("WARNING", logger="helm.telegram"):
        notifier._poll()
    assert waits == [10.0]
    assert "Conflict" in caplog.text


# -- the command surface is deliberately narrow ------------------------------------------

def test_no_command_can_buy_or_loosen_a_limit(engine):
    """The bot can stop this system. It must not be able to aim it."""
    commands = set(engine._telegram_handlers())
    for forbidden in ("buy", "enter", "trade", "risk", "size", "confirm_entry", "withdraw"):
        assert forbidden not in commands
    assert commands == {
        "status", "positions", "trades", "pause", "resume",
        "flatten", "kill", "confirm", "help", "start",
    }


def test_the_menu_lists_exactly_the_commands_answered(engine):
    """The menu is registered from the code, so it cannot promise a command that is gone."""
    assert set(COMMANDS) == set(engine._telegram_handlers()) - {"start"}


def test_starting_registers_the_menu(notifier):
    posted = []
    notifier._client.post = lambda url, json=None, **_: posted.append((url, json))
    notifier._client.get = lambda *a, **k: type("R", (), {"json": lambda self: {"result": []}})()
    notifier._poll = lambda: None
    notifier.start({"status": lambda: "ok"}, menu={"status": "How it is going"})
    notifier.stop()
    menus = [body for url, body in posted if url.endswith("/setMyCommands")]
    assert menus == [{"commands": [{"command": "status", "description": "How it is going"}]}]


def test_status_reads_out_every_coin_against_its_average(engine):
    engine.cycle()
    reply = engine._telegram_handlers()["status"]()
    for symbol in doctrine.SYMBOLS:
        assert symbol[:-3] in reply
    assert "Next decision" in reply


# -- destructive commands need confirmation ----------------------------------------------

def test_flatten_asks_before_selling(engine):
    approve_and_open(engine)
    handlers = engine._telegram_handlers()

    reply = handlers["flatten"]()
    assert "/confirm" in reply
    assert len(engine.positions) == 1        # nothing sold yet

    handlers["confirm"]()
    assert engine.positions == []


def test_confirm_does_nothing_on_its_own(engine):
    assert "Nothing waiting" in engine._telegram_handlers()["confirm"]()


def test_a_stale_confirmation_is_refused(engine, clock):
    approve_and_open(engine)
    handlers = engine._telegram_handlers()
    handlers["flatten"]()
    engine._pending_command = ("flatten", clock.now() - 1)   # expired
    assert "expired" in handlers["confirm"]()
    assert len(engine.positions) == 1


def test_flatten_with_nothing_open_does_not_arm(engine):
    handlers = engine._telegram_handlers()
    assert "Nothing to flatten" in handlers["flatten"]()
    assert engine._pending_command is None


def test_kill_flattens_and_halts(engine):
    approve_and_open(engine)
    handlers = engine._telegram_handlers()
    handlers["kill"]()
    handlers["confirm"]()
    assert engine.positions == []
    assert engine.halted


def test_resume_refuses_while_halted(engine):
    engine.halt("testing")
    assert "Still halted" in engine._telegram_handlers()["resume"]()
    assert engine.halted


# -- orphan detection --------------------------------------------------------------------

def test_holdings_with_no_plan_are_reported_once(engine):
    """The symptom of a lost database: coins on the exchange with no record of buying them."""
    alerts = []
    engine.narrate = lambda message, level="info": alerts.append((level, message))

    engine.broker._balances["BTC"] = 0.5          # ~50 EUR
    engine.reconcile()
    assert len(alerts) == 1
    assert alerts[0][0] == "error"
    assert "no record" in alerts[0][1]

    alerts.clear()
    engine.reconcile()
    assert alerts == []                      # warned once, not every cycle


def test_dust_is_not_reported_as_an_orphan(engine):
    alerts = []
    engine.narrate = lambda message, level="info": alerts.append((level, message))
    engine.broker._balances["ETH"] = 0.0000001
    engine.reconcile()
    assert alerts == []


def test_a_managed_position_is_not_an_orphan(engine):
    approve_and_open(engine)
    alerts = []
    engine.narrate = lambda message, level="info": alerts.append((level, message))
    engine.reconcile()
    assert alerts == []


# -- helpers -----------------------------------------------------------------------------

def approve_and_open(engine) -> None:
    """Let the rule buy BTC: its close above the average, and a decision to read it."""
    engine.market_.set_close("BTCEUR", UP)
    engine.cycle()
    assert [position.symbol for position in engine.positions] == ["BTCEUR"]
