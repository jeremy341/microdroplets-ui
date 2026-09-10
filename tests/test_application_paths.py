from pathlib import Path

import backend.application_paths as paths


def test_runtime_data_is_portable_below_project_root():
    assert paths.USER_DATA_DIR == paths.PROJECT_ROOT / "user_data"
    assert paths.CAPTURES_DIR == paths.USER_DATA_DIR / "captures"
    assert paths.SENSOR_LOGS_DIR == paths.USER_DATA_DIR / "sensor_logs"
    assert paths.SESSIONS_DIR == paths.USER_DATA_DIR / "sessions"
    assert paths.DIAGNOSTICS_DIR == paths.USER_DATA_DIR / "diagnostics"
    assert paths.EXPORTS_DIR == paths.USER_DATA_DIR / "exports"
    assert paths.DEFAULT_SENSOR_LOG_PATH == paths.SENSOR_LOGS_DIR / "log_data.csv"
    assert paths.CAMERA_PROFILE_PATH == paths.USER_DATA_DIR / "camera_profiles.json"
    assert paths.WAVEFORM_LIBRARY_PATH == paths.PROJECT_ROOT / "data" / "waveforms.json"
    assert paths.DRIVER_CONFIG_PATH == paths.PROJECT_ROOT / "data" / "driver_config.json"


def test_bundled_dnx64_runtime_is_project_local():
    assert paths.BUNDLED_DNX64_DLL == paths.PROJECT_ROOT / "vendor" / "dnx64" / "DNX64.dll"
    assert paths.BUNDLED_DNX64_DLL.is_file()


def test_dnx64_resolver_prefers_explicit_environment_override(monkeypatch, tmp_path):
    custom = tmp_path / "DNX64.dll"
    monkeypatch.setenv("DNX64_DLL", str(custom))
    assert paths.resolve_dnx64_dll() == custom


def test_dnx64_resolver_uses_bundled_runtime_without_override(monkeypatch):
    monkeypatch.delenv("DNX64_DLL", raising=False)
    assert paths.resolve_dnx64_dll() == paths.BUNDLED_DNX64_DLL


def test_legacy_migration_copies_without_overwrite(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy"
    target = tmp_path / "target"
    legacy.mkdir(); target.mkdir()
    (legacy / "keep.txt").write_text("legacy", encoding="utf-8")
    (legacy / "new.txt").write_text("new", encoding="utf-8")
    (target / "keep.txt").write_text("current", encoding="utf-8")
    paths._copy_directory_contents_if_missing(legacy, target)
    assert (target / "keep.txt").read_text(encoding="utf-8") == "current"
    assert (target / "new.txt").read_text(encoding="utf-8") == "new"
