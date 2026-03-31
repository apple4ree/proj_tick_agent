# strategy_review/ — v2 리뷰 파이프라인

`StrategySpecV2`를 검토하고, 필요 시 제한된 자동 수리(repair)를 수행한다.

## Document Scope

이 문서는 review 계층의 **현재 canonical 동작(Tier 1)** 을 설명한다.
freeze baseline/계약은 Tier 2 문서를 따른다.

- `docs/analysis/benchmark_freeze_protocol.md`
- `docs/analysis/benchmark_freeze_results.md`
- `docs/analysis/benchmark_freeze_baselines.md`

## Core Contract

- **final hard gate는 항상 static reviewer**
- LLM review는 semantic critique (승인권 없음)
- repair는 structured `RepairPlan` + deterministic patcher로만 적용
- 최종 pass/fail은 static re-review 결과

## Layered Components

1. Static review (`v2/reviewer_v2.py`)
- deterministic rule set으로 schema/risk/execution-churn 검증
- pre-layer leakage/liveness lints를 ReviewIssue로 병합 후 hard gate 판정
- `severity=error` 존재 시 `passed=False`

2. LLM review (`v2/llm_reviewer_v2.py`)
- 입력: spec + static review + optional env/feedback context
- 출력: `LLMReviewReport`

3. Repair planning (`v2/repair_planner_v2.py`)
- 출력: 허용 op만 포함하는 `RepairPlan`

4. Deterministic patching (`v2/patcher_v2.py`)
- spec deepcopy 후 허용 op만 적용
- patched spec schema/static re-review 검증

5. Pipeline orchestration (`v2/pipeline_v2.py`)
- static -> llm -> optional repair -> static re-review

## Leakage/Liveness Lint Layer (PR1)

static reviewer 앞단에서 아래 guard를 deterministic하게 실행한다.

- `FeatureTimeGuard`: short-horizon/cooldown/holding 조합의 과도한 반응성 점검
- `LookaheadGuard`: 미래 참조로 해석 가능한 feature/표현 패턴 점검
- `FillAlignmentGuard`: entry path의 execution/position namespace 혼용 점검
- `LatencyFeasibilityGuard`: cadence/latency 대비 실행 불가능한 구조 점검

lint는 reviewer를 대체하지 않고, `ReviewIssue`로 변환되어 hard gate 입력에 병합된다.

## CLI (`scripts/review_strategy.py`)

Public surface:
- positional `spec_path`
- `--mode` (`static|llm-review|auto-repair`)
- `--config`
- `--profile`

동작:
- `static`: static only (artifact 저장 없음)
- `llm-review`: static + llm critique (artifact 자동 저장)
- `auto-repair`: static + llm + repair + static re-review (artifact 자동 저장)

출력 규약:
- `REVIEW_STATUS=PASSED|FAILED`
- `LLM_REVIEW_RUN=true|false`
- `REPAIR_APPLIED=true|false`

## Review Artifacts

`llm-review` / `auto-repair` 모드 기본 경로:
`<spec_dir>/<spec_stem>_review_artifacts`

- `static_review.json`
- `llm_review.json`
- `repair_plan.json`
- `repaired_spec.json`
- `final_static_review.json`

## Environment-Aware Deterministic Gate

static reviewer는 optional `backtest_environment`를 받아 tick 기반 파라미터를 wall-clock으로 해석한다.

주요 반영:
- `canonical_tick_interval_ms`
- `market_data_delay_ms`, `decision_compute_ms`, `effective_delay_ms`
- `latency.order_submit_ms`, `latency.cancel_ms`
- queue/replace semantics context

결과적으로 동일 tick 값이라도 `1s` vs `500ms`, latency/tick 비율에 따라 severity가 달라질 수 있다.

## Feedback-Aware Review/Repair

optional recent feedback source:
- `summary.json`
- `realism_diagnostics.json`

추출기:
- `v2/backtest_feedback.py`

원칙:
- aggregate-only feedback 사용
- raw CSV trace는 prompt에 주입하지 않음
- feedback가 없으면 기존 동작 유지

repair priority는 아래 failure pattern에 따라 재정렬된다.
- `churn_heavy`
- `queue_ineffective`
- `cost_dominated`
- `adverse_selection_dominated`

제약:
- patcher가 지원하는 deterministic op 범위 밖 수정 금지
- final hard gate ownership은 static reviewer 유지

## Trial/Family Tracking Foundation (PR1~PR3)

- file-based `TrialRegistry`:
  - `create`, `get`, `update_stage`, `reject`, `list_by_family`, `attach_family`
- file-based `LineageTracker`:
  - `link_parent_child`, `ancestors`, `descendants`
- deterministic `FamilyFingerprintBuilder`:
  - motif/side/execution/horizon/regime/feature 축으로 `family_id` 산출
- file-based `FamilyIndex`:
  - `upsert`, `get`, `list_members`, `find_duplicate_or_neighbor`

주의:
- PR3는 family grouping/dedup foundation만 추가한다.
- walk-forward selector/promotion contract에 family 정책을 강제하는 단계는 후속 범위다.

## Known Limitations / Deferred Scope

- full staged replace state machine은 deferred
- deeper queue instrumentation beyond aggregate는 deferred
- feedback loop는 aggregate-only
- live/replay LLM 경로는 runtime/provider 상태에 따라 변동 가능 (mock baseline 권장)

## Freeze Reference

- `outputs/benchmarks/phase4_benchmark_freeze.json`
- `outputs/benchmarks/phase4_benchmark_freeze.md`
