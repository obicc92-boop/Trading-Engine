import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests must never touch a real venue or a real key, whatever happens to be in the
# environment when they run.
os.environ["HELM_VENUE"] = "synthetic"
os.environ["HELM_MODE"] = "dry"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("BINANCE_API_KEY", None)
os.environ.pop("I_UNDERSTAND_REAL_MONEY", None)

# helm.config calls load_dotenv() on import, which would quietly put back every key popped
# above on any machine with a .env — which is every machine that trades. Disable it before
# anything imports helm.config.
import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *args, **kwargs: False
