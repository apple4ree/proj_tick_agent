# src/evaluation_orchestration/ — 평가 및 오케스트레이션 (Block 5, Layer 6~7)

백테스트 파이프라인 실행, 메트릭 계산, 리포트 생성, worker 기반 job 오케스트레이션을 담당한다.

## 핵심 역할

- PnL/Risk/Execution/Turnover/Attribution 메트릭 계산
- PipelineRunner로 7-Layer 백테스트 일괄 실행
- BacktestConfig + ComponentFactory로 설정 기반 컴포넌트 조립
- FileQueue 기반 비동기 job 처리 (generation/backtest worker)
- 실험 재현성 관리 (seed, config hash, version tracking)

## 하위 디렉토리

| 디렉토리 | Layer | 역할 |
|----------|-------|------|
| `layer6_evaluator/` | 6 | PnL 원장, 리스크/실행/턴오버/귀인 메트릭 |
| `layer7_validation/` | 7 | PipelineRunner, BacktestConfig, FillSimulator, ReportBuilder |
| `orchestration/` | — | FileQueue, GenerationWorker, BacktestWorker, OrchestrationManager |

## 전체 파이프라인에서의 위치

Market Simulation(Block 4)의 FillEvent를 받아 메트릭을 계산하고, PipelineRunner가 전체 Layer 0~6을 오케스트레이션하여 백테스트를 실행한다.

```
CompiledStrategy + MarketState[]
  → PipelineRunner.run()
  → Signal → Target → Order → Fill → PnL/Metrics
  → ReportBuilder → summary.json + CSV + plots
```

## 현재 제한사항

- Worker는 FileQueue(파일 기반) 사용. 분산 큐(Redis 등)가 아님
- Attribution report는 config에서 비활성화 가능 (smoke 프로필에서 off)

## 관련 문서

- [../../PIPELINE.md](../../PIPELINE.md) — Block 5: Evaluation & Orchestration 상세
- [../../ADR.md](../../ADR.md) — ADR-007(PipelineRunner 분해)
