# Goal Presets

`run_generate_review_backtest_batch.sh`에서 바로 사용할 수 있는 universe goal 목록 preset 모음.

## Preset Files

- `universe_goals_smoke.txt`: 빠른 smoke/sanity-check 용 2개 goal
- `universe_goals_core.txt`: 일상 batch 탐색용 기본 goal 세트
- `universe_goals_openai.txt`: OpenAI generation/review 계약 통과율을 높이기 위해 실행 제약 문구를 포함한 goal 세트

## Goals File Format

- 한 줄당 goal 1개
- 빈 줄은 무시
- `#`로 시작하는 줄은 주석으로 무시

## Examples

Template/dev smoke:

```bash
./scripts/run_generate_review_backtest_batch.sh \
  --goals-file conf/goals/universe_goals_smoke.txt \
  --start-date 2026-03-13 \
  --end-date 2026-03-13 \
  --profile smoke \
  --backend template \
  --review-mode static
```

OpenAI/prod batch:

```bash
OPENAI_API_KEY=sk-... ./scripts/run_generate_review_backtest_batch.sh \
  --goals-file conf/goals/universe_goals_openai.txt \
  --start-date 2026-03-13 \
  --end-date 2026-03-13 \
  --profile prod \
  --backend openai \
  --review-mode auto-repair
```
