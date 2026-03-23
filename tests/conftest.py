"""Shared test fixtures and helpers for proj_rl_agent tests."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure src/ is on sys.path so that internal imports like
# ``from data.layer0_data.market_state import …`` resolve correctly even when
# PYTHONPATH is not set externally.
_SRC_ROOT = str(Path(__file__).resolve().parents[1] / "src")
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)
