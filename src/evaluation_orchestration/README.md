# src/evaluation_orchestration/ — 평가 및 오케스트레이션 (Layer 6~7)

백테스트 파이프라인 실행, 메트릭 계산, 리포트 생성을 담당한다.

## 하위 디렉토리

| 디렉토리 | Layer | 역할 |
|----------|-------|------|
| `layer6_evaluator/` | 6 | PnL 원장, 리스크/실행/턴오버/귀인 메트릭 |
| `layer7_validation/` | 7 | PipelineRunner, BacktestConfig, FillSimulator, ReportBuilder |

## 파이프라인에서의 위치

```
SimpleSpecStrategy + MarketState[]
  → PipelineRunner.run()
  → Signal → Target → Order → Fill → PnL/Metrics
  → ReportBuilder → summary.json + CSV + plots
```
