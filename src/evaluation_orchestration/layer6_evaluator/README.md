# layer6_evaluator/ — 평가 메트릭 (Layer 6)

백테스트 결과의 PnL, 리스크, 실행 품질, 턴오버, 성과 귀인 메트릭을 계산한다.

## 대표 파일

| 파일 | 핵심 클래스 | 역할 |
|------|-----------|------|
| `pnl_ledger.py` | `PnLLedger`, `PnLReport` | 체결별 손익 기록, 누적 PnL 시계열, 비용 분해 |
| `risk_metrics.py` | `RiskReport` | Sharpe, Sortino, MDD, VaR, ES, skewness |
| `execution_metrics.py` | `ExecutionReport` | IS bps, VWAP diff, fill/cancel rate, latency 통계 |
| `turnover_metrics.py` | `TurnoverReport` | 턴오버, 보유 기간, IQM, regime별 성과 |
| `attribution.py` | `AttributionReport` | 성과 귀인 분석 |
| `selection_metrics.py` | `SelectionScore` | walk-forward run score (edge - churn/cost/queue/adverse penalties) |

## PnL 비용 계층

```
gross_pnl = realized + unrealized
net_pnl   = gross_pnl - commission - tax
slippage  = fill_price - arrival_mid  (정보 목적)
```

## 성능 주의사항

`execution_metrics.py`와 `turnover_metrics.py`의 timestamp 매핑은 numpy `searchsorted`를 사용한다.
타임스탬프 배열은 루프 진입 전에 `np.array(..., dtype="datetime64[ns]")`로 미리 변환해야 한다.

`TurnoverMetrics.compute_holding_periods()`: positions_history가 클 경우 numpy 벡터화 경로를 사용한다.
`PipelineRunner`는 60틱마다 1회 샘플링해 positions_history 크기를 제한한다.
