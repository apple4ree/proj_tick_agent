"""
reproducibility.py
------------------
Layer 7: 문자열 표현oducibility Management

Ensures that experiments can be reproduced exactly by:
  - Capturing all hyperparameters as a deterministic hash
  - Recording code version (git commit), Python version, library versions
  - Hashing input data to detect dataset changes
  - Setting global random seeds across numpy, random, and optionally torch
  - Saving / loading run configurations

Usage
-----
    mgr = ReproducibilityManager(seed=42)
    config = mgr.capture_config({'lr': 1e-3, 'gamma': 0.99, ...})
    mgr.set_global_seed()
    mgr.save_checkpoint(config, '/path/to/run_config.json')
"""
from __future__ import annotations

import hashlib
import json
import logging
import platform
import random
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RunConfig
# ---------------------------------------------------------------------------

@dataclass
class RunConfig:
    """
    Immutable snapshot of all information needed to reproduce a run.

    속성
    ----------
    seed : int
        Global random seed.
    config_dict : dict
        All hyperparameters and settings.
    data_snapshot : str | None
        SHA-256 hash of the input data (from compute_data_hash).
    code_version : str | None
        Git commit hash at time of run.
    python_version : str
        e.g. '3.11.4'
    library_versions : dict[str, str]
        Package name -> version string for key libraries.
    """
    seed: int
    config_dict: dict
    data_snapshot: str | None = None
    code_version: str | None = None
    python_version: str = field(default_factory=lambda: platform.python_version())
    library_versions: dict[str, str] = field(default_factory=dict)

    @property
    def config_hash(self) -> str:
        """Deterministic SHA-256 hash of config_dict (sorted keys)."""
        serialized = json.dumps(self.config_dict, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "config_dict": self.config_dict,
            "config_hash": self.config_hash,
            "data_snapshot": self.data_snapshot,
            "code_version": self.code_version,
            "python_version": self.python_version,
            "library_versions": self.library_versions,
        }

    def save(self, path: str | Path) -> None:
        """Serialize to JSON."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, default=str)
        logger.debug("RunConfig saved to %s", path)

    @classmethod
    def load(cls, path: str | Path) -> "RunConfig":
        """Deserialize from JSON."""
        path = Path(path)
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return cls(
            seed=data["seed"],
            config_dict=data["config_dict"],
            data_snapshot=data.get("data_snapshot"),
            code_version=data.get("code_version"),
            python_version=data.get("python_version", platform.python_version()),
            library_versions=data.get("library_versions", {}),
        )


# ---------------------------------------------------------------------------
# ReproducibilityManager
# ---------------------------------------------------------------------------

class ReproducibilityManager:
    """
    Manages reproducibility for backtesting runs.

    매개변수
    ----------
    seed : int
        Default global random seed.
    """

    # Libraries to capture versions for
    _TRACKED_LIBRARIES = [
        "numpy",
        "pandas",
        "scipy",
        "torch",
        "gymnasium",
        "sklearn",
        "numba",
    ]

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed

    # ------------------------------------------------------------------
    # 시드 관리
    # ------------------------------------------------------------------

    def set_global_seed(self, seed: int | None = None) -> None:
        """
        Set global random seeds for numpy, Python random, and PyTorch (if available).

        매개변수
        ----------
        seed : int | None
            Seed to use. Defaults to self.seed.
        """
        s = seed if seed is not None else self.seed
        random.seed(s)
        np.random.seed(s)

        try:
            import torch
            torch.manual_seed(s)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(s)
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False
            logger.debug("PyTorch seed set to %d", s)
        except ImportError:
            pass

        logger.debug("Global seed set to %d (numpy, random)", s)

    # ------------------------------------------------------------------
    # 설정 캡처
    # ------------------------------------------------------------------

    def capture_config(
        self,
        config: dict,
        data_df: pd.DataFrame | None = None,
    ) -> RunConfig:
        """
        Build a RunConfig from a hyperparameter dict.

        Captures: code version, Python version, library versions,
        optionally data hash.

        매개변수
        ----------
        config : dict
            Hyperparameter dictionary.
        data_df : pd.DataFrame | None
            If provided, its hash is stored in data_snapshot.

        반환값
        -------
        RunConfig
        """
        code_version = self._get_git_commit()
        lib_versions = self._capture_library_versions()
        data_snapshot = self.compute_data_hash(data_df) if data_df is not None else None

        return RunConfig(
            seed=self.seed,
            config_dict=config,
            data_snapshot=data_snapshot,
            code_version=code_version,
            python_version=platform.python_version(),
            library_versions=lib_versions,
        )

    # ------------------------------------------------------------------
    # 데이터 해시
    # ------------------------------------------------------------------

    @staticmethod
    def compute_data_hash(df: pd.DataFrame) -> str:
        """
        Compute a deterministic SHA-256 hash of a DataFrame's content.

        Uses: shape + column names + a hash of the actual values.

        매개변수
        ----------
        df : pd.DataFrame

        반환값
        -------
        str
            Hex digest of the hash.
        """
        hasher = hashlib.sha256()

        # 형태
        hasher.update(str(df.shape).encode())

        # 컬럼 이름
        hasher.update(str(list(df.columns)).encode())

        # 값 기준 행 단위 해시
        try:
            value_hashes = pd.util.hash_pandas_object(df, index=True)
            hasher.update(value_hashes.values.tobytes())
        except Exception:
            # 대체 경로: head + tail 문자열 표현을 해시한다
            hasher.update(df.head(100).to_string().encode())
            hasher.update(df.tail(100).to_string().encode())

        return hasher.hexdigest()

    # ------------------------------------------------------------------
    # 문자열 표현oducibility verification
    # ------------------------------------------------------------------

    @staticmethod
    def verify_reproducibility(
        run1: RunConfig,
        run2: RunConfig,
    ) -> tuple[bool, list[str]]:
        """
        Compare two RunConfigs and report differences.

        매개변수
        ----------
        run1 : RunConfig
        run2 : RunConfig

        반환값
        -------
        (is_reproducible, list_of_differences)
            is_reproducible = True only if seed, config_hash, and
            data_snapshot all match.
        """
        differences: list[str] = []

        if run1.seed != run2.seed:
            differences.append(f"seed: {run1.seed} != {run2.seed}")

        if run1.config_hash != run2.config_hash:
            differences.append(
                f"config_hash: {run1.config_hash[:8]}... != {run2.config_hash[:8]}..."
            )
            # Report specific config key differences
            for key in set(run1.config_dict) | set(run2.config_dict):
                v1 = run1.config_dict.get(key, "<missing>")
                v2 = run2.config_dict.get(key, "<missing>")
                if v1 != v2:
                    differences.append(f"  config[{key!r}]: {v1!r} != {v2!r}")

        if run1.data_snapshot != run2.data_snapshot:
            differences.append(
                f"data_snapshot: {run1.data_snapshot} != {run2.data_snapshot}"
            )

        if run1.code_version != run2.code_version:
            differences.append(
                f"code_version: {run1.code_version} != {run2.code_version}"
            )

        if run1.python_version != run2.python_version:
            differences.append(
                f"python_version: {run1.python_version} != {run2.python_version}"
            )

        is_reproducible = len(differences) == 0
        return is_reproducible, differences

    # ------------------------------------------------------------------
    # Checkpoint I/O
    # ------------------------------------------------------------------

    def save_checkpoint(self, config: RunConfig, path: str | Path) -> None:
        """Save RunConfig to JSON."""
        config.save(path)

    @staticmethod
    def load_checkpoint(path: str | Path) -> RunConfig:
        """Load RunConfig from JSON."""
        return RunConfig.load(path)

    # ------------------------------------------------------------------
    # 내부 도우미
    # ------------------------------------------------------------------

    @staticmethod
    def _get_git_commit() -> str | None:
        """Return the current HEAD git commit hash, or None if not in a git repo."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return None

    @classmethod
    def _capture_library_versions(cls) -> dict[str, str]:
        """Capture installed versions of key libraries."""
        versions: dict[str, str] = {}
        for lib in cls._TRACKED_LIBRARIES:
            try:
                mod = __import__(lib)
                ver = getattr(mod, "__version__", "unknown")
                versions[lib] = str(ver)
            except ImportError:
                pass
        return versions
