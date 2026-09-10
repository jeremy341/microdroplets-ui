from tools.compare_driver_probes import summarize


def probe(label, fingerprint, path):
    return {
        "label": label,
        "fingerprint": fingerprint,
        "path": path,
        "firmware_lines": [],
    }


def test_consistent_repeated_label_is_reported():
    result = summarize([
        probe("highdriver4_only", "4", "a.json"),
        probe("highdriver4_only", "4", "b.json"),
    ])
    info = result["labels"]["highdriver4_only"]
    assert info["consistent"] is True
    assert info["fingerprints"] == ["4"]


def test_same_fingerprint_under_multiple_labels_is_flagged():
    result = summarize([
        probe("config_a", "4D", "a.json"),
        probe("config_b", "4D", "b.json"),
    ])
    assert result["fingerprint_label_conflicts"] == {"4D": ["config_a", "config_b"]}


def test_missing_driver_field_never_becomes_consistent_mapping():
    result = summarize([
        probe("mp_driver_only", None, "a.json"),
        probe("mp_driver_only", "D", "b.json"),
    ])
    info = result["labels"]["mp_driver_only"]
    assert info["consistent"] is False
    assert info["missing_fingerprint_captures"] == 1
