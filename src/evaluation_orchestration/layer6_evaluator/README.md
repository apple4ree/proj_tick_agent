# layer6_evaluator/ — 평가 메트릭 (Layer 6)

백테스트 결과의 PnL, 리스크, 실행 품질, 턴오버, 성과 귀인 메트릭을 계산한다.

## 핵심 역할

- PnL 원장: 실현/미실현 손익, 비용 분해 (수수료, 세금, slippage, impact)
- 리스크 메트릭: Sharpe, Sortino, Calmar, MDD, VaR, ES
- 실행 품질: IS(Implementation Shortfall), VWAP diff, fill rate, maker ratio
- 턴오버/보유 기간 메트릭
- 성과 귀인 분석
- walk-forward selection scoring (robustness/stability-aware aggregate)

## 대표 파일

| 파일 | 핵심 클래스 | 역할 |
|------|-----------|------|
| `pnl_ledger.py` | `PnLLedger`, `PnLEntry`, `PnLReport` | 체결별 손익 기록, 누적 PnL 시계열, 비용 분해 |
| `risk_metrics.py` | `RiskReport` | Sharpe, MDD, VaR 95/99, ES, skewness, kurtosis |
| `execution_metrics.py` | `ExecutionReport` | IS bps, VWAP diff, fill/cancel rate, latency 통계 |
| `turnover_metrics.py` | — | 턴오버, 보유 기간 통계 |
| `attribution.py` | `AttributionReport` | 성과 귀인 분석 |
| `selection_metrics.py` | `SelectionMetrics`, `SelectionScore` | walk-forward run score (edge - churn/cost/queue/adverse penalties) |

## PnL 비용 계층

```
gross_pnl = realized + unrealized
net_pnl = gross_pnl - commission - tax
slippage = fill_price - arrival_mid (정보 목적)
impact = 추정 temporary market-impact (정보 목적)
```

## 전체 파이프라인에서의 위치

Layer 5(Simulator)의 FillEvent를 받아 메트릭을 계산한다. ReportBuilder(Layer 7)가 이 메트릭들을 모아 최종 리포트를 생성한다.

Walk-forward 검증에서는 `selection_metrics.py`가 `summary.json` + `realism_diagnostics.json` aggregate를 입력으로 deterministic selection score를 계산한다.

## 주의사항

- Attribution report는 config에서 비활성화 가능 (smoke에서 off)
- PnLLedger는 `record_fill()`, `mark_to_market()`, `close_position()` 세 가지 엔트리 타입 지원
- RiskReport의 annualized_vol은 기간 수익률 기반

## 관련 문서

- [../layer7_validation/README.md](../layer7_validation/README.md) — ReportBuilder가 메트릭 조립 + walk-forward harness
- [../../market_simulation/layer5_simulator/README.md](../../market_simulation/layer5_simulator/README.md) — FillEvent 생성
