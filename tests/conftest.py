import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests must never touch a real venue or a real key, whatever happens to be in the
# environment when they run.
os.environ["HELM_VENUE"] = "synthetic"
os.environ["HELM_MODE"] = "dry"
os.environ.pop("BINANCE_API_KEY", None)
os.environ.pop("I_UNDERSTAND_REAL_MONEY", None)

# helm.config calls load_dotenv() on import, which would quietly put back every key popped
# above on any machine with a .env — which is every machine that trades. Disable it before
# anything imports helm.config.
import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *args, **kwargs: False

import pytest  # noqa: E402

from tests.support import Clock, TrendMarket  # noqa: E402


@pytest.fixture
def clock(monkeypatch):
    """Engine time, under the test's control: `now_ms` and the UTC day boundary both follow it."""
    import helm.engine
    import helm.store

    fake = Clock()
    monkeypatch.setattr(helm.engine, "now_ms", fake.now)
    monkeypatch.setattr(helm.engine, "utc_day_start", fake.day_start)
    monkeypatch.setattr(helm.store, "now_ms", fake.now)
    return fake


@pytest.fixture
def market(clock):
    return TrendMarket(clock)
