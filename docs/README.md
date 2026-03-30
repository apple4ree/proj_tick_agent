# docs/ — Documentation Index

이 디렉토리는 프로젝트 보조 문서와 분석 문서를 관리한다.

핵심 원칙:
- **current canonical behavior**는 Tier 1 문서에서 확인
- **baseline/freeze 계약**은 Tier 2 문서에서 확인
- **historical analysis**는 배경 맥락으로만 사용

## Authoritative Hierarchy

### Tier 1 — Current Canonical Behavior
- `../PIPELINE.md`
- `../scripts/README.md`
- `../src/strategy_block/strategy_generation/README.md`
- `../src/strategy_block/strategy_review/README.md`
- `../src/evaluation_orchestration/layer7_validation/README.md`

### Tier 2 — Freeze / Baseline
- `analysis/benchmark_freeze_protocol.md`
- `analysis/benchmark_freeze_results.md`
- `analysis/benchmark_freeze_baselines.md`
- `../outputs/benchmarks/phase4_benchmark_freeze.json`
- `../outputs/benchmarks/phase4_benchmark_freeze.md`

### Tier 3 — Historical Analysis
- `analysis/*.md` 중 Tier 2 freeze 문서를 제외한 문서
- 과거 실험/해석 기록이며, 현재 동작 계약의 1차 출처는 아님

## Recommended Reading Order

1. `../PIPELINE.md`
2. `../scripts/README.md`
3. generation/review/layer7 README
4. `analysis/benchmark_freeze_protocol.md`
5. `analysis/benchmark_freeze_results.md`
6. `analysis/benchmark_freeze_baselines.md`

## Other Docs in this Directory

- `COMMANDS.md`: 운영 명령어 치트시트
- `backtest_realism_design.md`: realism 설계 배경/의도
- `history/`: 변경 이력성 문서
- `research_proposal_v5.pdf`: 연구 제안서
