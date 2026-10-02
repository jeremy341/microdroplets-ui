"""Persistent saved-waveform library for FluidicStudio.

Wave definitions are channel-independent and stored separately from normal
session files.  See ``docs/USER_GUIDE.md`` for the schema and execution model.
"""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import uuid

from backend.waveform_engine import WaveformDefinition, validate_definition


SCHEMA_VERSION = 1


class WaveformLibrary:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._items: list[WaveformDefinition] = []
        # (mtime_ns, size) of the file the in-memory items were built from.
        self._loaded_stamp: tuple[int, int] | None = None
        self.load()

    def _file_stamp(self) -> tuple[int, int] | None:
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    @property
    def items(self) -> tuple[WaveformDefinition, ...]:
        return tuple(self._items)

    def get(self, waveform_id: str) -> WaveformDefinition | None:
        return next((item for item in self._items if item.id == waveform_id), None)

    def by_name(self, name: str) -> WaveformDefinition | None:
        target = name.strip().casefold()
        return next(
            (item for item in self._items if item.name.strip().casefold() == target),
            None,
        )

    def new_definition(self, name: str = "Untitled Waveform") -> WaveformDefinition:
        return WaveformDefinition(id=str(uuid.uuid4()), name=name)

    def save(self, definition: WaveformDefinition) -> WaveformDefinition:
        valid, reason = validate_definition(definition)
        if not valid:
            raise ValueError(reason)
        duplicate = self.by_name(definition.name)
        if duplicate is not None and duplicate.id != definition.id:
            raise ValueError(f'A waveform named "{definition.name}" already exists.')

        for index, item in enumerate(self._items):
            if item.id == definition.id:
                self._items[index] = definition
                break
        else:
            self._items.append(definition)
        self._items.sort(key=lambda item: item.name.casefold())
        self._write()
        return definition

    def delete(self, waveform_id: str) -> bool:
        before = len(self._items)
        self._items = [item for item in self._items if item.id != waveform_id]
        changed = len(self._items) != before
        if changed:
            self._write()
        return changed

    def duplicate(self, waveform_id: str) -> WaveformDefinition:
        source = self.get(waveform_id)
        if source is None:
            raise KeyError(waveform_id)
        base = f"{source.name} Copy"
        name = base
        suffix = 2
        while self.by_name(name) is not None:
            name = f"{base} {suffix}"
            suffix += 1
        duplicate = WaveformDefinition(
            id=str(uuid.uuid4()),
            name=name,
            template=source.template,
            min_vpp=source.min_vpp,
            max_vpp=source.max_vpp,
            increment_vpp=source.increment_vpp,
            samples_per_cycle=source.samples_per_cycle,
            step_duration_ms=source.step_duration_ms,
            cycle_duration_ms=source.cycle_duration_ms,
            cycles=source.cycles,
            driver_frequency_hz=source.driver_frequency_hz,
        )
        return self.save(duplicate)

    @staticmethod
    def _legacy_cycle_duration_ms(raw: dict) -> int:
        """Preserve the old cycle timing when loading pre-cycle-duration saves."""

        template = str(raw.get("template", "Triangle"))
        minimum = int(raw.get("min_vpp", 80))
        maximum = int(raw.get("max_vpp", 180))
        increment = max(1, int(raw.get("increment_vpp", 10)))
        step_ms = int(raw.get("step_duration_ms", 100))
        if template == "Sine":
            count = max(4, int(raw.get("samples_per_cycle", 32)))
        elif template == "Square":
            count = 2
        else:
            values = [minimum]
            value = minimum
            while value + increment < maximum:
                value += increment
                values.append(value)
            if values[-1] != maximum:
                values.append(maximum)
            count = len(values) if template == "Sawtooth" else len(values) + max(0, len(values) - 2)
        return max(100, count * step_ms)

    def _parse_waveforms(self, raw_items: list) -> list[WaveformDefinition]:
        """Build definitions from stored records, dropping unusable entries."""

        items: list[WaveformDefinition] = []
        seen_names: set[str] = set()
        for raw in raw_items:
            try:
                definition = WaveformDefinition(
                    id=str(raw["id"]),
                    name=str(raw["name"]),
                    template=str(raw.get("template", "Triangle")),
                    min_vpp=int(raw.get("min_vpp", 80)),
                    max_vpp=int(raw.get("max_vpp", 180)),
                    increment_vpp=int(raw.get("increment_vpp", 10)),
                    samples_per_cycle=int(raw.get("samples_per_cycle", 32)),
                    step_duration_ms=int(raw.get("step_duration_ms", 100)),
                    cycle_duration_ms=int(
                        raw.get("cycle_duration_ms", self._legacy_cycle_duration_ms(raw))
                    ),
                    cycles=int(raw.get("cycles", 5)),
                    driver_frequency_hz=int(raw.get("driver_frequency_hz", 100)),
                )
            except (KeyError, TypeError, ValueError):
                continue
            valid, _ = validate_definition(definition)
            if not valid:
                continue
            name_key = definition.name.strip().casefold()
            if name_key in seen_names:
                continue
            seen_names.add(name_key)
            items.append(definition)
        items.sort(key=lambda item: item.name.casefold())
        return items

    def load(self) -> None:
        """Reload the library only when the file on disk actually changed.

        Views poll this method (the Workspace wave panel refreshes on a timer),
        so an unconditional disk read plus JSON parse on every call is pure
        avoidable I/O on the UI thread. ``save``/``delete`` keep the in-memory
        items authoritative and refresh the stamp after writing.

        The new items and the reload stamp are committed only after a read that
        actually succeeded. A transient read error, corrupt JSON or a foreign
        ``schema_version`` therefore leaves the in-memory library untouched and
        leaves the stamp unpinned, so the next poll retries and a later good
        read can still recover.
        """

        stamp = self._file_stamp()
        if stamp is not None and stamp == self._loaded_stamp:
            return
        if not self.path.exists():
            # The library file really is gone; an empty library is the truth.
            self._items = []
            self._loaded_stamp = stamp
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        if payload.get("schema_version") != SCHEMA_VERSION:
            return
        raw_items = payload.get("waveforms")
        if not isinstance(raw_items, list):
            return
        items = self._parse_waveforms(raw_items)
        self._items = items
        self._loaded_stamp = stamp

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "waveforms": [asdict(item) for item in self._items],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            delete=False,
        ) as handle:
            handle.write(text)
            temp_path = Path(handle.name)
        temp_path.replace(self.path)
        # The in-memory items are the authoritative state after a write; keep
        # the reload guard in sync so the next load() stays a no-op.
        self._loaded_stamp = self._file_stamp()
