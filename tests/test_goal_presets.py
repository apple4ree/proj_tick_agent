from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOALS_DIR = PROJECT_ROOT / "conf" / "goals"


def _load_goals(path: Path) -> list[str]:
    goals: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        goals.append(line)
    return goals


def test_goal_presets_exist_and_non_empty() -> None:
    expected = [
        GOALS_DIR / "universe_goals_smoke.txt",
        GOALS_DIR / "universe_goals_core.txt",
        GOALS_DIR / "universe_goals_openai.txt",
    ]
    for path in expected:
        assert path.exists(), f"missing goal preset: {path}"
        assert path.read_text(encoding="utf-8").strip(), f"empty goal preset: {path}"


def test_goal_preset_counts() -> None:
    smoke = _load_goals(GOALS_DIR / "universe_goals_smoke.txt")
    core = _load_goals(GOALS_DIR / "universe_goals_core.txt")
    openai = _load_goals(GOALS_DIR / "universe_goals_openai.txt")

    assert len(smoke) == 2
    assert 5 <= len(core) <= 8
    assert 5 <= len(openai) <= 8


def test_openai_preset_contains_contract_phrases() -> None:
    goals_text = "\n".join(_load_goals(GOALS_DIR / "universe_goals_openai.txt")).lower()
    required_phrases = {
        "explicit execution policy",
        "bounded",
        "conservative",
        "holding horizon",
    }
    for phrase in required_phrases:
        assert phrase in goals_text
