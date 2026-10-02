"""Persistence and cache helpers for Analytics results/settings."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import ANALYZER_VERSION, AnalysisConfig, AnalysisResult


class ResultStore:
    def __init__(self, analytics_dir: Path, settings_path: Path) -> None:
        self.analytics_dir = Path(analytics_dir)
        self.settings_path = Path(settings_path)
        self.analytics_dir.mkdir(parents=True, exist_ok=True)

    def save_settings(self, config: AnalysisConfig) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.settings_path.with_suffix(self.settings_path.suffix + ".tmp")
        temp.write_text(json.dumps(config.to_dict(), indent=2), encoding="utf-8")
        temp.replace(self.settings_path)

    def load_settings(self) -> AnalysisConfig:
        if not self.settings_path.is_file():
            return AnalysisConfig()
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            # Valid JSON is not necessarily a settings object.  ``null``, a list
            # or a bare string parse fine but ``AnalysisConfig.from_dict`` calls
            # ``data.get(...)``, whose ``AttributeError`` would escape page
            # construction instead of degrading to defaults.
            if not isinstance(data, dict):
                return AnalysisConfig()
            return AnalysisConfig.from_dict(data)
        except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
            return AnalysisConfig()

    def result_directory(self, video_path: Path) -> Path:
        safe_name = video_path.stem.replace(" ", "_")
        # Two videos in different folders can share a filename stem; include a
        # fingerprint of the resolved path so their results cannot collide.
        fingerprint = hashlib.sha1(
            str(Path(video_path).resolve()).encode("utf-8")
        ).hexdigest()[:10]
        return self.analytics_dir / f"{safe_name}_{fingerprint}"

    def save_result(self, result: AnalysisResult) -> Path | None:
        if result.complete is False:
            # A cancelled or partial run must never be persisted: later cache
            # hits would silently present it as a complete analysis.
            return None
        directory = self.result_directory(result.metadata.path)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "analysis.json"
        temp = target.with_suffix(".json.tmp")
        temp.write_text(json.dumps(result.to_dict(), separators=(",", ":")), encoding="utf-8")
        temp.replace(target)
        return target

    def load_cached(self, video_path: Path, expected_cache_key: str | None = None) -> AnalysisResult | None:
        target = self.result_directory(video_path) / "analysis.json"
        if not target.is_file():
            return None
        try:
            result = AnalysisResult.from_dict(json.loads(target.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError):
            return None
        if result.analyzer_version != ANALYZER_VERSION:
            return None
        if not result.metadata.path.exists():
            return None
        if result.metadata.path.resolve() != video_path.resolve():
            return None
        if result.complete is False:
            return None
        if expected_cache_key and result.cache_key != expected_cache_key:
            return None
        return result
