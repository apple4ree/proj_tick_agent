"""
tests/test_strategy_registry.py
-------------------------------
Tests for the extended StrategyRegistry: metadata lifecycle, version-pinned
access, promote/approve flows, and error handling.
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from strategy_block.strategy_specs.schema import StrategySpec, SignalRule, PositionRule, ExitRule
from strategy_block.strategy_registry.models import StrategyMetadata, StrategyStatus, VALID_TRANSITIONS
from strategy_block.strategy_registry.registry import StrategyRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_spec(name: str = "test_strat", version: str = "1.0") -> StrategySpec:
    return StrategySpec(
        name=name,
        version=version,
        description="unit-test strategy",
        signal_rules=[
            SignalRule(
                feature="order_imbalance",
                operator=">",
                threshold=0.3,
                score_contribution=1.0,
                description="OI bullish",
            ),
        ],
        position_rule=PositionRule(max_position=100, sizing_mode="fixed", fixed_size=10),
        exit_rules=[
            ExitRule(exit_type="stop_loss", threshold_bps=10.0),
        ],
    )


@pytest.fixture()
def registry(tmp_path: Path) -> StrategyRegistry:
    return StrategyRegistry(tmp_path / "strategies")


# ---------------------------------------------------------------------------
# Metadata save / load
# ---------------------------------------------------------------------------

class TestMetadataSaveLoad:
    def test_save_creates_meta_file(self, registry: StrategyRegistry) -> None:
        spec = _make_spec()
        registry.save_spec(spec, generation_backend="gpt-4o", generation_mode="multi_agent")

        meta_path = registry._meta_path("test_strat", "1.0")
        assert meta_path.exists()

        meta = StrategyMetadata.load(meta_path)
        assert meta.strategy_id == "test_strat_v1.0"
        assert meta.name == "test_strat"
        assert meta.version == "1.0"
        assert meta.status == StrategyStatus.DRAFT
        assert meta.generation_backend == "gpt-4o"
        assert meta.generation_mode == "multi_agent"
        assert meta.created_at  # non-empty

    def test_get_metadata(self, registry: StrategyRegistry) -> None:
        spec = _make_spec()
        registry.save_spec(spec)
        meta = registry.get_metadata("test_strat", "1.0")
        assert meta.status == StrategyStatus.DRAFT
        assert meta.spec_path.endswith("test_strat_v1.0.json")

    def test_metadata_round_trip(self, tmp_path: Path) -> None:
        meta = StrategyMetadata(
            strategy_id="x_v1.0",
            name="x",
            version="1.0",
            status=StrategyStatus.APPROVED,
            generation_backend="claude",
            trace_path="traces/x.json",
        )
        p = tmp_path / "meta.json"
        meta.save(p)
        loaded = StrategyMetadata.load(p)
        assert loaded.strategy_id == "x_v1.0"
        assert loaded.status == StrategyStatus.APPROVED
        assert loaded.trace_path == "traces/x.json"


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

class TestStatusTransition:
    def test_valid_transitions(self, registry: StrategyRegistry) -> None:
        spec = _make_spec()
        registry.save_spec(spec)

        registry.update_status("test_strat", "1.0", StrategyStatus.REVIEWED)
        m = registry.get_metadata("test_strat", "1.0")
        assert m.status == StrategyStatus.REVIEWED

        registry.update_status("test_strat", "1.0", StrategyStatus.APPROVED)
        m = registry.get_metadata("test_strat", "1.0")
        assert m.status == StrategyStatus.APPROVED

    def test_invalid_transition_raises(self, registry: StrategyRegistry) -> None:
        spec = _make_spec()
        registry.save_spec(spec)

        with pytest.raises(ValueError, match="Cannot transition"):
            registry.update_status("test_strat", "1.0", StrategyStatus.PROMOTED_TO_LIVE)

    def test_archived_is_terminal(self, registry: StrategyRegistry) -> None:
        spec = _make_spec()
        registry.save_spec(spec)
        registry.update_status("test_strat", "1.0", StrategyStatus.REJECTED)
        registry.update_status("test_strat", "1.0", StrategyStatus.ARCHIVED)

        with pytest.raises(ValueError):
            registry.update_status("test_strat", "1.0", StrategyStatus.DRAFT)


# ---------------------------------------------------------------------------
# Approve / Promote flow
# ---------------------------------------------------------------------------

class TestPromoteFlow:
    def test_full_promotion_lifecycle(self, registry: StrategyRegistry) -> None:
        spec = _make_spec()
        registry.save_spec(spec)

        # draft -> reviewed -> approved
        registry.update_status("test_strat", "1.0", StrategyStatus.REVIEWED)
        registry.update_status("test_strat", "1.0", StrategyStatus.APPROVED)

        # promote for backtest
        meta = registry.promote_for_backtest("test_strat", "1.0")
        assert meta.status == StrategyStatus.PROMOTED_TO_BACKTEST
        assert meta.approved_for_backtest is True

        # promote for live
        meta = registry.promote_for_live("test_strat", "1.0")
        assert meta.status == StrategyStatus.PROMOTED_TO_LIVE
        assert meta.approved_for_live is True

    def test_promote_for_backtest_requires_approved(self, registry: StrategyRegistry) -> None:
        spec = _make_spec()
        registry.save_spec(spec)
        # status is DRAFT, cannot promote directly
        with pytest.raises(ValueError, match="Cannot transition"):
            registry.promote_for_backtest("test_strat", "1.0")

    def test_promote_for_live_requires_backtest(self, registry: StrategyRegistry) -> None:
        spec = _make_spec()
        registry.save_spec(spec)
        registry.update_status("test_strat", "1.0", StrategyStatus.REVIEWED)
        registry.update_status("test_strat", "1.0", StrategyStatus.APPROVED)
        # skip backtest, try to go to live
        with pytest.raises(ValueError, match="Cannot transition"):
            registry.promote_for_live("test_strat", "1.0")


# ---------------------------------------------------------------------------
# Nonexistent version error
# ---------------------------------------------------------------------------

class TestNonexistentVersion:
    def test_load_spec_missing_version(self, registry: StrategyRegistry) -> None:
        with pytest.raises(FileNotFoundError):
            registry.load_spec("no_such", "9.9")

    def test_get_metadata_missing(self, registry: StrategyRegistry) -> None:
        with pytest.raises(FileNotFoundError):
            registry.get_metadata("no_such", "1.0")

    def test_resolve_version_missing_name(self, registry: StrategyRegistry) -> None:
        with pytest.raises(FileNotFoundError):
            registry.resolve_version("no_such")


# ---------------------------------------------------------------------------
# latest_approved query
# ---------------------------------------------------------------------------

class TestLatestApproved:
    def test_returns_latest_approved(self, registry: StrategyRegistry) -> None:
        for ver in ("1.0", "2.0", "3.0"):
            spec = _make_spec(version=ver)
            registry.save_spec(spec)

        # approve v1.0 and v2.0 only
        for ver in ("1.0", "2.0"):
            registry.update_status("test_strat", ver, StrategyStatus.REVIEWED)
            registry.update_status("test_strat", ver, StrategyStatus.APPROVED)

        result = registry.latest_approved("test_strat")
        assert result.version == "2.0"

    def test_no_approved_raises(self, registry: StrategyRegistry) -> None:
        spec = _make_spec()
        registry.save_spec(spec)  # remains DRAFT
        with pytest.raises(FileNotFoundError, match="No approved"):
            registry.latest_approved("test_strat")

    def test_promoted_counts_as_approved(self, registry: StrategyRegistry) -> None:
        spec = _make_spec()
        registry.save_spec(spec)
        registry.update_status("test_strat", "1.0", StrategyStatus.REVIEWED)
        registry.update_status("test_strat", "1.0", StrategyStatus.APPROVED)
        registry.promote_for_backtest("test_strat", "1.0")

        result = registry.latest_approved("test_strat")
        assert result.version == "1.0"


# ---------------------------------------------------------------------------
# Version resolution & list
# ---------------------------------------------------------------------------

class TestVersionAndList:
    def test_resolve_version_explicit(self, registry: StrategyRegistry) -> None:
        spec = _make_spec()
        registry.save_spec(spec)
        assert registry.resolve_version("test_strat", "1.0") == "1.0"

    def test_resolve_version_latest(self, registry: StrategyRegistry) -> None:
        for ver in ("1.0", "2.0"):
            registry.save_spec(_make_spec(version=ver))
        assert registry.resolve_version("test_strat") == "2.0"

    def test_list_specs_all(self, registry: StrategyRegistry) -> None:
        registry.save_spec(_make_spec(name="a", version="1.0"))
        registry.save_spec(_make_spec(name="b", version="1.0"))
        items = registry.list_specs()
        assert len(items) == 2

    def test_list_specs_name_filter(self, registry: StrategyRegistry) -> None:
        registry.save_spec(_make_spec(name="a", version="1.0"))
        registry.save_spec(_make_spec(name="b", version="1.0"))
        items = registry.list_specs(name_filter="a")
        assert len(items) == 1
        assert items[0]["name"] == "a"

    def test_list_specs_status_filter(self, registry: StrategyRegistry) -> None:
        registry.save_spec(_make_spec(name="a", version="1.0"))
        registry.save_spec(_make_spec(name="b", version="1.0"))
        registry.update_status("a", "1.0", StrategyStatus.REVIEWED)

        items = registry.list_specs(status_filter=StrategyStatus.REVIEWED)
        assert len(items) == 1
        assert items[0]["name"] == "a"


# ---------------------------------------------------------------------------
# File layout
# ---------------------------------------------------------------------------

class TestFileLayout:
    def test_spec_and_meta_filenames(self, registry: StrategyRegistry) -> None:
        spec = _make_spec(name="my_strat", version="2.1")
        registry.save_spec(spec)

        assert (registry.registry_dir / "my_strat_v2.1.json").exists()
        assert (registry.registry_dir / "my_strat_v2.1.meta.json").exists()

    def test_iter_specs_excludes_meta(self, registry: StrategyRegistry) -> None:
        registry.save_spec(_make_spec())
        specs = list(registry.iter_specs())
        assert len(specs) == 1
        assert specs[0].name == "test_strat"
