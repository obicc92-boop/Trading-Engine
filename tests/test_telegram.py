"""Notifications and remote control.

Nothing here touches the network. The two properties that matter: the trading loop can
never be blocked or broken by Telegram, and the command surface cannot be used to open a
position or loosen a limit.
"""

import queue
import re

import pytest

from helm.config import Config
from helm.engine import Engine
from helm.models import now_ms
from helm.notify.telegram import ALERT_LEVELS, TelegramNotifier


@pytest.fixture
def notifier():
    bot = TelegramNotifier("test-token", "12345")
    bot.posted = []
    bot._post = lambda text: bot.posted.append(text) or True
    return bot


@pytest.fixture
def engine(tmp_path):
    config = Config()
    config.db_path = str(tmp_path / "tg.db")
    config.venue = "synthetic"
    engine = Engine(config)
    engine.refresh_prices()
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


# -- the command surface is deliberately narrow ------------------------------------------

def test_no_command_can_open_a_position_or_loosen_a_limit(engine):
    """The bot can stop this system. It must not be able to aim it."""
    commands = set(engine._telegram_handlers())
    for forbidden in ("buy", "enter", "trade", "risk", "size", "confirm_entry", "withdraw"):
        assert forbidden not in commands
    assert commands == {
        "status", "positions", "today", "pause", "resume",
        "flatten", "kill", "confirm", "help", "start",
    }


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


def test_a_stale_confirmation_is_refused(engine):
    approve_and_open(engine)
    handlers = engine._telegram_handlers()
    handlers["flatten"]()
    engine._pending_command = ("flatten", now_ms() - 1)      # expired
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


# -- daily digest ------------------------------------------------------------------------

def test_first_boot_sends_no_digest(engine):
    sent = []
    engine.notifier.notify = lambda message, level="info": sent.append(message)
    engine.maybe_send_digest()
    assert sent == []


def test_digest_is_sent_once_per_day(engine):
    sent = []
    engine.notifier.notify = lambda message, level="info": sent.append(message)
    engine.maybe_send_digest()                  # records today, sends nothing
    engine.store.put("last_digest_day", "2020-01-01")

    engine.maybe_send_digest()
    engine.maybe_send_digest()                  # same day again
    assert len(sent) == 1


def test_a_quiet_day_still_reports(engine):
    """Silence is ambiguous — a system that only speaks when it trades looks dead."""
    sent = []
    engine.notifier.notify = lambda message, level="info": sent.append(message)
    engine.store.put("last_digest_day", "2020-01-01")
    engine.maybe_send_digest()
    assert "No trades" in sent[0]
    assert "Equity" in sent[0]


# -- orphan detection --------------------------------------------------------------------

def test_holdings_with_no_plan_are_reported_once(engine):
    """The symptom of a lost database: coins on the exchange with no stop attached."""
    alerts = []
    engine.narrate = lambda message, level="info": alerts.append((level, message))

    engine.broker._balances["BTC"] = 0.002
    engine.reconcile()
    assert len(alerts) == 1
    assert alerts[0][0] == "error"
    assert "NO STOP" in alerts[0][1]

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
    from helm.models import Analysis, Candidate, Verdict

    symbol = "BTCEUR"
    price = engine._prices[symbol]
    analysis = Analysis(symbol=symbol, price=price, regime="TRENDING_UP",
                        indicators={"atr": price * 0.01, "swing_low": price * 0.97,
                                    "ema21": price * 0.99, "rsi": 55.0})
    engine._analysis[symbol] = analysis
    candidate = Candidate(symbol=symbol, setup="TREND_PULLBACK", score=75.0, factors={},
                          price=price, stop=price * 0.96, target=price * 1.12,
                          regime="TRENDING_UP", analysis=analysis, rationale=["test"])
    engine.brain.judge_entry = lambda *a, **k: Verdict(
        go=True, conviction=70, size_multiplier=1.0, stop_override=None,
        thesis="Test.", invalidation="Test.", source="stub")
    engine._consider(candidate, engine.account_state())
