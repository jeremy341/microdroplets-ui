"""Compare labelled driver-detection probe JSON files without guessing a mapping.

This is an offline companion to ``driver_detection_probe.py``. It never opens a
serial port and never communicates with hardware. Give it two or more JSON
captures from known physical configurations and it prints the observed
``Driver:`` fingerprints, flags conflicting captures, and shows which labels
have repeated consistently.

Examples::

    python tools/compare_driver_probes.py diagnostics/driver_probe_*.json
    python tools/compare_driver_probes.py diagnostics/driver_probe_*.json --json-out diagnostics/driver_probe_summary.json

A fingerprint becomes *evidence for a mapping* only when the operator labelled
the physical setup correctly and repeated captures agree. This tool deliberately
does not translate tokens such as ``4D`` into driver names.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from typing import Any


def load_probe(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    observations = data.get("observations") or {}
    return {
        "path": str(path),
        "label": str(data.get("label") or "unlabelled"),
        "fingerprint": observations.get("driver_fingerprint"),
        "firmware_lines": list(data.get("firmware_lines") or []),
    }


def summarize(probes: list[dict[str, Any]]) -> dict[str, Any]:
    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_fingerprint: dict[str, list[str]] = defaultdict(list)

    for probe in probes:
        by_label[probe["label"]].append(probe)
        fingerprint = probe.get("fingerprint")
        if fingerprint is not None:
            by_fingerprint[str(fingerprint)].append(probe["label"])

    labels: dict[str, Any] = {}
    for label, captures in sorted(by_label.items()):
        fingerprints = [capture.get("fingerprint") for capture in captures]
        unique = sorted({str(value) for value in fingerprints if value is not None})
        missing_count = sum(value is None for value in fingerprints)
        labels[label] = {
            "captures": len(captures),
            "fingerprints": unique,
            "missing_fingerprint_captures": missing_count,
            "consistent": len(unique) == 1 and missing_count == 0,
            "files": [capture["path"] for capture in captures],
        }

    conflicts = {
        fingerprint: sorted(set(labels_for_fingerprint))
        for fingerprint, labels_for_fingerprint in by_fingerprint.items()
        if len(set(labels_for_fingerprint)) > 1
    }

    return {
        "schema_version": 1,
        "probe_count": len(probes),
        "labels": labels,
        "fingerprint_label_conflicts": conflicts,
        "interpretation": {
            "mapping_status": "experimental_only",
            "note": (
                "A consistent labelled capture is evidence, not a production mapping. "
                "Repeat each known hardware configuration and resolve any shared fingerprints "
                "before adding a decoder to FluidicStudio."
            ),
        },
    }


def _print_summary(summary: dict[str, Any]) -> None:
    print(f"Compared {summary['probe_count']} probe file(s).\n")
    for label, info in summary["labels"].items():
        fingerprints = ", ".join(info["fingerprints"]) or "<not reported>"
        state = "CONSISTENT" if info["consistent"] else "NEEDS MORE DATA"
        print(f"{label}")
        print(f"  captures:      {info['captures']}")
        print(f"  fingerprints:  {fingerprints}")
        print(f"  result:        {state}")
        if info["missing_fingerprint_captures"]:
            print(f"  missing field: {info['missing_fingerprint_captures']} capture(s)")
        print()

    conflicts = summary["fingerprint_label_conflicts"]
    if conflicts:
        print("Fingerprint conflicts (same token observed under different labels):")
        for fingerprint, labels in sorted(conflicts.items()):
            print(f"  {fingerprint}: {', '.join(labels)}")
        print("Do not create a production mapping until every conflict is explained.\n")
    else:
        print("No cross-label fingerprint conflicts were found in these files.\n")

    print("This tool does not decode fingerprints into driver names automatically.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path, help="Probe JSON files to compare")
    parser.add_argument("--json-out", type=Path, help="Optional path for the comparison summary")
    args = parser.parse_args()

    missing = [path for path in args.files if not path.is_file()]
    if missing:
        for path in missing:
            print(f"Missing probe file: {path}")
        return 2

    try:
        probes = [load_probe(path) for path in args.files]
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"Could not read probe files: {exc}")
        return 3

    summary = summarize(probes)
    _print_summary(summary)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Saved summary: {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
