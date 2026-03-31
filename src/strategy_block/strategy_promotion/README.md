# strategy_promotion/ — Deployment Contract & Promotion Gate

`strategy_promotion`은 실거래 엔진이 아니라 **handoff-ready 후보 산출 계층**이다.

## Scope (PR4)

- walk-forward 결과를 바탕으로 deployment contract 생성
- deterministic promotion gate로 후보 pass/fail 판정
- spec/report/contract를 export bundle로 저장

## Core Modules

- `contract_models.py`: `DeploymentContract`
- `contract_builder.py`: deterministic contract builder
- `promotion_gate.py`: deterministic hard gate (`PromotionDecision`)
- `export_bundle.py`: handoff artifact bundle exporter

## Non-Goals (Deferred)

- live/paper/shadow trading execution
- backtest runtime semantics 변경
- generation/review hard gate semantics 변경

## CLI

- `scripts/promote_candidate.py`

출력:
- `PROMOTION_STATUS=PASSED|FAILED`
- `PROMOTION_BUNDLE=<path>` (passed 시)
- `PROMOTION_REASONS=<compact_summary>`
