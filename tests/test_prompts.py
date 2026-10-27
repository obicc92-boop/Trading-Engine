"""The decision request has to be one the API accepts.

A failed decision call is a NO-GO by design, so a request the API rejects outright does not
crash anything: it quietly declines every candidate, forever. That happened. The schemas put
`minimum`/`maximum` on numeric fields, which structured outputs reject with a 400, and no
trade could ever be approved. Nothing here calls the API; `doctor` checks the live request
for free with count_tokens.
"""

import pytest

from helm import doctrine
from helm.brain import prompts
from helm.brain.claude import Brain
from helm.config import Config
from helm.engine import Engine
from tests.test_engine import make_candidate

# Numeric constraints structured outputs refuse. A schema carrying any of them is a 400.
UNSUPPORTED = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}


def _keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _keys(item)


@pytest.mark.parametrize("schema", [prompts.ENTRY_SCHEMA, prompts.EXIT_SCHEMA], ids=["entry", "exit"])
def test_schemas_carry_no_numeric_bounds_the_api_rejects(schema):
    assert not UNSUPPORTED & set(_keys(schema))


def test_the_bounds_are_enforced_in_code_instead(tmp_path):
    """With the bounds out of the schema, an out-of-range answer must still be clamped."""
    config = Config()
    config.db_path = str(tmp_path / "brain.db")
    config.venue = "synthetic"
    engine = Engine(config)
    engine.refresh_prices()
    candidate = make_candidate(engine)

    config.anthropic_key = "test-key"                # enabled, but _ask never reaches the API
    brain = Brain(config)
    answer = {"go": True, "conviction": 250, "size_multiplier": 3.0, "tighten_stop_to": None,
              "thesis": "t", "invalidation": "i", "key_risk": ""}
    brain._ask = lambda brief, schema, effort=None: (answer, {})

    verdict = brain.judge_entry(candidate, engine.account_state(), [], [])
    assert verdict.go
    assert verdict.conviction == 100
    assert verdict.size_multiplier == doctrine.MAX_SIZE_MULTIPLIER
