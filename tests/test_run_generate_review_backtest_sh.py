from __future__ import annotations

import os
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WRAPPER_SOURCE = PROJECT_ROOT / "scripts" / "run_generate_review_backtest.sh"


DEFAULT_GENERATION_SCRIPT = '''
from pathlib import Path
import os
import sys

spec_path = Path(os.environ["TEST_SPEC_PATH"])
spec_path.parent.mkdir(parents=True, exist_ok=True)
spec_path.write_text('{"spec_format":"v2","name":"generated","version":"2.0"}\\n', encoding="utf-8")
print("generation stub start")
print("generation args:", " ".join(sys.argv[1:]))
print(f"GENERATED_SPEC={spec_path}")
'''

DEFAULT_REVIEW_SCRIPT = '''
from pathlib import Path
import os
import sys

mode = "static"
if "--mode" in sys.argv:
    mode = sys.argv[sys.argv.index("--mode") + 1]

artifact_dir = Path(os.environ["TEST_ARTIFACT_DIR"])
if mode in {"llm-review", "auto-repair"}:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    print(f"ARTIFACT_DIR={artifact_dir}")
if mode == "auto-repair" and os.environ.get("TEST_WRITE_REPAIRED_SPEC") == "1":
    repaired_spec = artifact_dir / "repaired_spec.json"
    repaired_spec.write_text('{"spec_format":"v2","name":"repaired","version":"2.0"}\\n', encoding="utf-8")
print(f"review-mode={mode}")
print(f"REVIEW_STATUS={os.environ.get('TEST_REVIEW_STATUS', 'PASSED')}")
raise SystemExit(int(os.environ.get("TEST_REVIEW_EXIT", "0")))
'''

DEFAULT_BACKTEST_SCRIPT = '''
import argparse
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--spec", required=True)
parser.add_argument("--symbol")
parser.add_argument("--start-date")
parser.add_argument("--end-date")
parser.add_argument("--profile")
parser.add_argument("--config")
args = parser.parse_args()

record_path = Path(os.environ["TEST_BACKTEST_RECORD"])
record_path.parent.mkdir(parents=True, exist_ok=True)
record_path.write_text(args.spec, encoding="utf-8")
run_dir = Path(os.environ["TEST_BACKTEST_RUN_DIR"])
run_dir.mkdir(parents=True, exist_ok=True)
print(f"backtest spec: {args.spec}")
print(f"Saved run artifacts: {run_dir}")
raise SystemExit(int(os.environ.get("TEST_BACKTEST_EXIT", "0")))
'''

DEFAULT_UNIVERSE_SCRIPT = '''
import argparse
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--spec", required=True)
parser.add_argument("--start-date")
parser.add_argument("--end-date")
parser.add_argument("--profile")
parser.add_argument("--config")
args = parser.parse_args()

record_path = Path(os.environ["TEST_BACKTEST_RECORD"])
record_path.parent.mkdir(parents=True, exist_ok=True)
record_path.write_text(args.spec, encoding="utf-8")
results_dir = Path(os.environ["TEST_BACKTEST_RUN_DIR"])
results_dir.mkdir(parents=True, exist_ok=True)
print(f"Results: {results_dir}")
raise SystemExit(int(os.environ.get("TEST_BACKTEST_EXIT", "0")))
'''

CONFIG_STUB = '''
from __future__ import annotations

import os


def load_config(config_path=None, profile=None):
    return {
        "generation": {
            "backend": os.environ.get("TEST_CFG_BACKEND", "template"),
            "mode": os.environ.get("TEST_CFG_MODE", "live"),
        }
    }


def get_generation(cfg):
    gen = dict(cfg.get("generation", {}))
    gen.setdefault("backend", os.environ.get("TEST_CFG_BACKEND", "template"))
    gen.setdefault("mode", os.environ.get("TEST_CFG_MODE", "live"))
    return gen
'''


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")


def _build_project(
    tmp_path: Path,
    *,
    generation_script: str = DEFAULT_GENERATION_SCRIPT,
    review_script: str = DEFAULT_REVIEW_SCRIPT,
    backtest_script: str = DEFAULT_BACKTEST_SCRIPT,
    universe_script: str = DEFAULT_UNIVERSE_SCRIPT,
) -> Path:
    project = tmp_path / "mini_project"
    scripts_dir = project / "scripts"
    src_utils_dir = project / "src" / "utils"
    bin_dir = project / "bin"

    scripts_dir.mkdir(parents=True, exist_ok=True)
    src_utils_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)

    _write(scripts_dir / "run_generate_review_backtest.sh", WRAPPER_SOURCE.read_text(encoding="utf-8"))
    (scripts_dir / "run_generate_review_backtest.sh").chmod(0o755)
    _write(scripts_dir / "generate_strategy.py", generation_script)
    _write(scripts_dir / "review_strategy.py", review_script)
    _write(scripts_dir / "backtest.py", backtest_script)
    _write(scripts_dir / "backtest_strategy_universe.py", universe_script)
    _write(src_utils_dir / "__init__.py", "")
    _write(src_utils_dir / "config.py", CONFIG_STUB)
    _write(
        bin_dir / "python",
        f"#!/usr/bin/env bash\nexec {shlex.quote(sys.executable)} \"$@\"\n",
    )
    (bin_dir / "python").chmod(0o755)

    return project


def _run_wrapper(project: Path, args: list[str], extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{project / 'bin'}:{env.get('PATH', '')}"
    env.setdefault("TEST_CFG_BACKEND", "template")
    env.setdefault("TEST_CFG_MODE", "live")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", "scripts/run_generate_review_backtest.sh", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _env_for(project: Path) -> dict[str, str]:
    return {
        "TEST_SPEC_PATH": str(project / "strategies" / "generated_spec.json"),
        "TEST_ARTIFACT_DIR": str(project / "outputs" / "review_artifacts"),
        "TEST_BACKTEST_RECORD": str(project / "outputs" / "backtest_spec.txt"),
        "TEST_BACKTEST_RUN_DIR": str(project / "outputs" / "backtest_run"),
    }


def test_wrapper_fails_on_malformed_generation_output(tmp_path: Path) -> None:
    project = _build_project(
        tmp_path,
        generation_script='''
print("generation stub start")
print("missing machine parse key on purpose")
''',
    )

    proc = _run_wrapper(
        project,
        [
            "--goal",
            "test goal",
            "--symbol",
            "005930",
            "--start-date",
            "20260313",
        ],
        extra_env=_env_for(project),
    )
    out = proc.stdout + proc.stderr

    assert proc.returncode != 0
    assert "ERROR: Could not parse GENERATED_SPEC from generation output" in out
    assert "ERROR: generation failed" in out
    assert "generation stub start" in out


def test_wrapper_parses_review_status_and_artifact_dir_keys(tmp_path: Path) -> None:
    project = _build_project(
        tmp_path,
        review_script='''
from pathlib import Path
import os

artifact_dir = Path(os.environ["TEST_ARTIFACT_DIR"])
artifact_dir.mkdir(parents=True, exist_ok=True)
print("human-readable status: FAILED (wrapper should ignore this line)")
print(f"ARTIFACT_DIR={artifact_dir}")
print("noise before machine status")
print("REVIEW_STATUS=PASSED")
''',
    )
    env = _env_for(project)

    proc = _run_wrapper(
        project,
        [
            "--goal",
            "test goal",
            "--symbol",
            "005930",
            "--start-date",
            "20260313",
            "--review-mode",
            "llm-review",
        ],
        extra_env=env,
    )
    out = proc.stdout + proc.stderr

    assert proc.returncode == 0, out
    assert "human-readable status: FAILED" in out
    assert f"artifact-dir:   {env['TEST_ARTIFACT_DIR']}" in out
    assert Path(env["TEST_BACKTEST_RECORD"]).read_text(encoding="utf-8") == env["TEST_SPEC_PATH"]


def test_wrapper_auto_repair_uses_repaired_spec_for_backtest(tmp_path: Path) -> None:
    project = _build_project(tmp_path)
    env = _env_for(project)
    env["TEST_WRITE_REPAIRED_SPEC"] = "1"

    proc = _run_wrapper(
        project,
        [
            "--goal",
            "test goal",
            "--symbol",
            "005930",
            "--start-date",
            "20260313",
            "--review-mode",
            "auto-repair",
        ],
        extra_env=env,
    )
    out = proc.stdout + proc.stderr

    assert proc.returncode == 0, out
    repaired_spec = str(Path(env["TEST_ARTIFACT_DIR"]) / "repaired_spec.json")
    assert Path(env["TEST_BACKTEST_RECORD"]).read_text(encoding="utf-8") == repaired_spec
    assert f"backtest-spec:  {repaired_spec}" in out


def test_wrapper_requires_openai_api_key_for_live_mode(tmp_path: Path) -> None:
    project = _build_project(
        tmp_path,
        generation_script='''
from pathlib import Path
import os

Path(os.environ["TEST_GENERATION_MARKER"]).write_text("called", encoding="utf-8")
print("generation should not have been reached")
''',
    )
    env = _env_for(project)
    env["TEST_CFG_MODE"] = "live"
    env["TEST_GENERATION_MARKER"] = str(project / "outputs" / "generation_called.txt")
    env["OPENAI_API_KEY"] = ""

    proc = _run_wrapper(
        project,
        [
            "--goal",
            "test goal",
            "--symbol",
            "005930",
            "--start-date",
            "20260313",
            "--backend",
            "openai",
        ],
        extra_env=env,
    )
    out = proc.stdout + proc.stderr

    assert proc.returncode != 0
    assert "OPENAI_API_KEY" in out
    assert "backend:      openai" in out
    assert "gen-mode:     live" in out
    assert not Path(env["TEST_GENERATION_MARKER"]).exists()


def test_wrapper_warns_when_openai_backend_resolves_to_mock_mode(tmp_path: Path) -> None:
    project = _build_project(tmp_path)
    env = _env_for(project)
    env["TEST_CFG_MODE"] = "mock"

    proc = _run_wrapper(
        project,
        [
            "--goal",
            "test goal",
            "--symbol",
            "005930",
            "--start-date",
            "20260313",
            "--backend",
            "openai",
        ],
        extra_env=env,
    )
    out = proc.stdout + proc.stderr

    assert proc.returncode == 0, out
    assert "resolved generation mode is mock" in out
