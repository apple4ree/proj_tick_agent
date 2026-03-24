# Commit Plan (Post v2-only Migration Hygiene)

This plan only groups the **current working tree** into commit-ready chunks.
It does not introduce new behavior changes.

## Change-Set 1: v2-only runtime transition
Intent:
- Keep only StrategySpec v2 runtime/generation/review/registry/compiler paths.

Include files:
- `src/strategy_block/strategy_compiler/__init__.py`
- `src/strategy_block/strategy_compiler/v2/compiler_v2.py`
- `src/strategy_block/strategy_compiler/v2/runtime_v2.py`
- `src/strategy_block/strategy_compiler/v2/features.py` (new)
- `src/strategy_block/strategy_registry/registry.py`
- `src/strategy_block/strategy_registry/models.py`
- `src/strategy_block/strategy_generation/generator.py`
- `src/strategy_block/strategy_generation/__init__.py`
- `src/strategy_block/strategy_generation/openai_client.py`
- `src/strategy_block/strategy_generation/v2/lowering.py`
- `src/strategy_block/strategy_generation/v2/templates_v2.py`
- `src/strategy_block/strategy_review/__init__.py`
- `src/strategy_block/strategy_review/v2/reviewer_v2.py`
- `src/strategy_block/strategy_review/review_common.py` (new)
- `src/strategy_block/strategy_specs/__init__.py`
- `src/strategy_block/strategy_specs/v2/__init__.py`
- `src/strategy_block/strategy_specs/v2/ast_nodes.py`
- `src/strategy_block/strategy_specs/v2/schema_v2.py`
- `scripts/generate_strategy.py`
- `scripts/review_strategy.py`
- `scripts/backtest.py`
- `scripts/backtest_strategy_universe.py`
- `src/evaluation_orchestration/orchestration/backtest_worker.py`
- `src/evaluation_orchestration/orchestration/generation_worker.py`

Delete in same commit (runtime v1 removal):
- `src/strategy_block/strategy_compiler/compiler.py`
- `src/strategy_block/strategy_specs/schema.py`
- `src/strategy_block/strategy_review/reviewer.py`
- `src/strategy_block/strategy_generation/templates.py`
- `src/strategy_block/strategy_generation/assembler.py`
- `src/strategy_block/strategy_generation/agents.py`
- `src/strategy_block/strategy_generation/agent_schemas.py`
- `src/strategy_block/strategy_generation/pipeline.py`
- `src/strategy_block/strategy_generation/prompt_loader.py`
- `src/strategy_block/strategy_generation/prompts/*`

Commit message draft:
- `refactor(strategy): finalize StrategySpec v2-only runtime and remove v1 code paths`

Notes:
- This is the highest-risk commit; run v2 smoke/help checks after this commit.

---

## Change-Set 2: legacy/archive cleanup
Intent:
- Remove stale archive and legacy strategy assets not used in v2-only path.

Include files:
- `archive/docs/*` deletions shown in status
- `archive/legacy_agents/*` deletions
- `archive/legacy_baselines/*` deletions
- `strategies/micro_price_alpha_v1.0.json` (delete)
- `strategies/spread_mean_reversion_v1.0.json` (delete)
- `strategies/trade_flow_pressure_v1.0.json` (delete)
- `strategies/examples/imbalance_momentum_v1.0.json` (delete)

Commit message draft:
- `chore(cleanup): remove archive/legacy strategy assets after v2-only cutover`

---

## Change-Set 3: tests and examples refresh
Intent:
- Align tests/examples with v2-only assumptions and remove legacy suites.

Include files:
- `tests/test_backtest_script.py`
- `tests/test_backtest_worker.py`
- `tests/test_compiler_v2.py`
- `tests/test_experiment_tracker_metrics.py`
- `tests/test_generation_direct_mode.py`
- `tests/test_registry_v2_integration.py`
- `tests/test_strategy_spec_v2.py`
- `tests/test_v2_execution_hint_integration.py` (new)
- `tests/test_v2_phase3.py` (new)
- `tests/test_v2_position_attr.py` (new)
- `tests/test_v2_stronger_integration.py` (new)
- `tests/test_generation_worker.py` (delete)
- `tests/test_multi_agent_generation.py` (delete)
- `tests/test_strategy_compiler.py` (delete)
- `tests/test_strategy_registry.py` (delete)
- `strategies/examples/README.md` (new)
- `strategies/examples/position_aware_time_exit_momentum_v2.0.json` (new)
- `strategies/examples/stateful_cooldown_momentum_v2.0.json` (new)

Commit message draft:
- `test/examples: drop v1 suites and refresh v2-only coverage`

---

## Change-Set 4: docs/config cleanup
Intent:
- Update operator docs/config comments to v2-only reality.

Include files:
- `README.md`
- `PROJECT.md`
- `ARCHITECTURE.md`
- `conf/EXPERIMENT_PROTOCOL.md`
- `conf/generation.yaml`
- `conf/profiles/smoke.yaml`
- `scripts/run_validation_tiers.sh` (new)

Commit message draft:
- `docs/config: align docs and config comments with v2-only workflow`

Notes:
- `README.md`, `PROJECT.md`, `ARCHITECTURE.md` currently appear as **full-content replacement/removal** in diff.
- Split/restore before committing if this was unintentional.

---

## Change-Set 5: cache cleanup
Intent:
- Keep repository free of tracked runtime cache artifacts.

Include files:
- All `scripts/__pycache__/*.pyc` deletions
- All `tests/__pycache__/*.pyc` deletions
- All `src/**/__pycache__/*.pyc` deletions currently tracked
- `.gitignore` (cache ignore rules check/update)

Commit message draft:
- `chore(hygiene): remove tracked pycache artifacts and enforce ignore rules`

---

## Mixed file inventory

These files include multiple intents and should be handled carefully:

1. `conf/generation.yaml`
- Mixed type: docs/config cleanup + behavior toggles.
- Reason: adds v2 comment plus fallback-control keys (`allow_template_fallback`, `allow_heuristic_fallback`, `fail_on_fallback`) that can affect generation behavior.

2. `conf/backtest_worker.yaml`
- Mixed type: cleanup + runtime semantics.
- Reason: `latencies_ms` value changed (`[0,50,100,500,1000]` -> `[0,100,500]`), which changes experiment coverage.

3. `conf/backtest_core.yaml`
- Mixed type: cleanup + runtime semantics.
- Reason: default date range changed (`2026-03-13` -> `2026-03-17..2026-03-20`).

4. `README.md`, `PROJECT.md`, `ARCHITECTURE.md`
- Mixed type: docs cleanup + high-risk content loss.
- Reason: current diff indicates near-total deletion; treat as separate commit and review manually.

---

## Recommended commit order

1. `chore(hygiene): remove tracked pycache artifacts and enforce ignore rules`
2. `refactor(strategy): finalize StrategySpec v2-only runtime and remove v1 code paths`
3. `test/examples: drop v1 suites and refresh v2-only coverage`
4. `chore(cleanup): remove archive/legacy strategy assets after v2-only cutover`
5. `docs/config: align docs and config comments with v2-only workflow`
6. Optional final split commit for mixed config semantics (`conf/backtest_core.yaml`, `conf/backtest_worker.yaml`, `conf/generation.yaml`) if behavior changes should be isolated.

---

## Pre-commit sanity checklist

- `PYTHONPATH=src python scripts/generate_strategy.py --help`
- `PYTHONPATH=src python scripts/review_strategy.py --help`
- `PYTHONPATH=src python scripts/backtest.py --help`
- `PYTHONPATH=src python scripts/backtest_strategy_universe.py --help`
- `PYTHONPATH=src python scripts/run_backtest_worker.py --help`
- `find . -name __pycache__ -o -name "*.pyc" -o -name .pytest_cache`

---

## Additional file mapping (to cover full `git status`)

Assigned to `v2-only runtime transition`:
- `src/execution_planning/layer4_execution/__init__.py`
- `src/execution_planning/layer4_execution/cancel_replace.py`
- `src/execution_planning/layer4_execution/placement_policy.py`
- `src/evaluation_orchestration/layer7_validation/pipeline_runner.py`

Assigned to `docs/config cleanup`:
- `conf/backtest_core.yaml` (mixed: includes date default semantic change)
- `conf/backtest_worker.yaml` (mixed: includes latency sweep semantic change)
- `src/utils/config.py`

Assigned to `tests and examples refresh`:
- `pytest.ini` (new)

Assigned to `docs/config cleanup` or optional tooling commit:
- `scripts/run_validation_tiers.sh` (new)

Assigned to `cache cleanup`:
- `.gitignore`
- all tracked `*.pyc` deletions under `scripts/`, `src/`, `tests/`

