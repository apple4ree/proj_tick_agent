# conf/goals/ — Goal Preset 파일

`run_strategy_loop.py`의 `--research-goal` 인자에 사용할 수 있는 goal 목록 모음.

## Preset 파일

| 파일 | 내용 |
|------|------|
| `universe_goals_smoke.txt` | 빠른 smoke 테스트용 2개 goal |
| `universe_goals_core.txt` | 일상 탐색용 기본 goal 세트 |
| `universe_goals_openai.txt` | OpenAI 생성 품질을 높이기 위해 실행 제약 문구를 포함한 goal 세트 |

## 파일 포맷

- 한 줄당 goal 1개
- 빈 줄 무시
- `#`으로 시작하는 줄은 주석

## 사용 예시

```bash
# goal 목록을 순차적으로 실행
while IFS= read -r goal; do
    [[ -z "$goal" || "$goal" == \#* ]] && continue
    PYTHONPATH=src python scripts/run_strategy_loop.py \
        --research-goal "$goal" \
        --symbol 005930 --start-date 20260313 \
        --mode mock --n-iter 3
done < conf/goals/universe_goals_smoke.txt
```
