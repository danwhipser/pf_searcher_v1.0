from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


def resolve_base_dir(entry_file: str | Path) -> Path:
    """Return the app root for source or PyInstaller execution."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(entry_file).resolve().parent


@dataclass(frozen=True)
class RuntimePaths:
    base_dir: Path
    web_dir: Path
    result_dir: Path
    data_dir: Path

    @classmethod
    def from_base_dir(cls, base_dir: str | Path) -> "RuntimePaths":
        root = Path(base_dir).resolve()
        return cls(
            base_dir=root,
            web_dir=root / "web",
            result_dir=root / "result",
            data_dir=root / "data",
        )

    @classmethod
    def from_entry_file(cls, entry_file: str | Path) -> "RuntimePaths":
        return cls.from_base_dir(resolve_base_dir(entry_file))

