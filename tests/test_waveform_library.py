from dataclasses import replace

import pytest

from backend.waveform_engine import WaveformDefinition
from backend.waveform_library import WaveformLibrary


def make_definition(name="Mixing Slow", waveform_id="one"):
    return WaveformDefinition(
        id=waveform_id,
        name=name,
        template="Triangle",
        min_vpp=80,
        max_vpp=180,
        increment_vpp=10,
        step_duration_ms=100,
        cycles=5,
    )


def test_library_round_trip(tmp_path):
    path = tmp_path / "waveforms.json"
    library = WaveformLibrary(path)
    library.save(make_definition())
    loaded = WaveformLibrary(path)
    assert loaded.items == (make_definition(),)


def test_library_updates_by_stable_id(tmp_path):
    library = WaveformLibrary(tmp_path / "waveforms.json")
    original = make_definition()
    library.save(original)
    library.save(replace(original, name="Mixing Fast", max_vpp=200))
    assert len(library.items) == 1
    assert library.items[0].name == "Mixing Fast"
    assert library.items[0].id == "one"


def test_duplicate_names_are_rejected(tmp_path):
    library = WaveformLibrary(tmp_path / "waveforms.json")
    library.save(make_definition())
    with pytest.raises(ValueError):
        library.save(make_definition(waveform_id="two"))


def test_duplicate_creates_new_id_and_unique_name(tmp_path):
    library = WaveformLibrary(tmp_path / "waveforms.json")
    library.save(make_definition())
    copy = library.duplicate("one")
    assert copy.id != "one"
    assert copy.name == "Mixing Slow Copy"
    assert len(library.items) == 2


def test_delete_persists(tmp_path):
    path = tmp_path / "waveforms.json"
    library = WaveformLibrary(path)
    library.save(make_definition())
    assert library.delete("one") is True
    assert WaveformLibrary(path).items == ()


def test_library_loads_pre_cycle_duration_wave_without_changing_legacy_cycle_time(tmp_path):
    import json

    path = tmp_path / "waveforms.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "waveforms": [{
            "id": "legacy",
            "name": "Legacy Triangle",
            "template": "Triangle",
            "min_vpp": 80,
            "max_vpp": 180,
            "increment_vpp": 10,
            "samples_per_cycle": 32,
            "step_duration_ms": 100,
            "cycles": 5
        }]
    }), encoding="utf-8")
    loaded = WaveformLibrary(path).get("legacy")
    assert loaded is not None
    assert loaded.cycle_duration_ms == 2000
    assert loaded.step_duration_ms == 100


def test_reload_is_skipped_while_the_file_is_unchanged(tmp_path, monkeypatch):
    import json

    import backend.waveform_library as library_module

    path = tmp_path / "waveforms.json"
    path.write_text(json.dumps({"schema_version": 1, "waveforms": []}), encoding="utf-8")
    library = WaveformLibrary(path)

    calls = {"count": 0}
    real_loads = json.loads

    def counting_loads(text, *args, **kwargs):
        calls["count"] += 1
        return real_loads(text, *args, **kwargs)

    monkeypatch.setattr(library_module.json, "loads", counting_loads)

    # Views poll load(); an unchanged file must not be re-read or re-parsed.
    for _ in range(50):
        library.load()
    assert calls["count"] == 0

    # An external change (different size/mtime) is picked up.
    path.write_text(json.dumps({
        "schema_version": 1,
        "waveforms": [{
            "id": "added", "name": "Added", "template": "Square",
            "min_vpp": 80, "max_vpp": 180, "increment_vpp": 10,
            "step_duration_ms": 100, "cycle_duration_ms": 200, "cycles": 5,
        }],
    }), encoding="utf-8")
    library.load()
    assert [item.name for item in library.items] == ["Added"]
    assert calls["count"] == 1


def test_save_keeps_the_reload_guard_in_sync(tmp_path):
    library = WaveformLibrary(tmp_path / "waveforms.json")
    library.save(make_definition(name="Stored"))
    # A save is authoritative in memory; the following load() must be a no-op
    # and must not duplicate or drop the stored definition.
    library.load()
    assert [item.name for item in library.items] == ["Stored"]
    library.delete(library.items[0].id)
    library.load()
    assert library.items == ()
