"""Thread-safe ownership for pump channels.

This module contains no Qt and sends no hardware commands.  It only answers one
question: who is currently allowed to control a given pump channel?

Keeping ownership in the backend prevents the Pumps and Wave pages from both
sending commands to the same physical output at the same time.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import uuid


FREE = "free"
MANUAL = "manual"
WAVEFORM = "waveform"
VALID_OWNERS = frozenset((MANUAL, WAVEFORM))


class ChannelOwnershipError(RuntimeError):
    """Raised when a channel cannot be claimed or released safely."""


@dataclass(frozen=True)
class ChannelOwnership:
    channel: int
    kind: str
    token: str
    label: str = ""


class ChannelOwnershipManager:
    """Own channel reservations for one physical Multiboard.

    A token is returned when a caller successfully claims a channel.  Only the
    holder of that token can release it.  This prevents an old worker finishing
    late from accidentally releasing a newer owner's reservation.
    """

    def __init__(self, channels=range(1, 7)) -> None:
        self._channels = frozenset(int(channel) for channel in channels)
        self._owners: dict[int, ChannelOwnership] = {}
        self._lock = threading.RLock()

    def _validate_channel(self, channel: int) -> int:
        channel = int(channel)
        if channel not in self._channels:
            raise ValueError(f"Unsupported pump channel: CH{channel}")
        return channel

    def claim(self, channel: int, kind: str, *, label: str = "") -> ChannelOwnership:
        channel = self._validate_channel(channel)
        kind = str(kind).strip().lower()
        if kind not in VALID_OWNERS:
            raise ValueError(f"Unsupported channel owner kind: {kind}")
        with self._lock:
            current = self._owners.get(channel)
            if current is not None:
                description = current.label or current.kind
                raise ChannelOwnershipError(
                    f"CH{channel} is already in use by {description}."
                )
            ownership = ChannelOwnership(
                channel=channel,
                kind=kind,
                token=str(uuid.uuid4()),
                label=str(label).strip(),
            )
            self._owners[channel] = ownership
            return ownership

    def get(self, channel: int) -> ChannelOwnership | None:
        channel = self._validate_channel(channel)
        with self._lock:
            return self._owners.get(channel)

    def is_free(self, channel: int) -> bool:
        return self.get(channel) is None

    def release(self, channel: int, token: str) -> bool:
        channel = self._validate_channel(channel)
        with self._lock:
            current = self._owners.get(channel)
            if current is None:
                return False
            if current.token != token:
                raise ChannelOwnershipError(
                    f"CH{channel} ownership changed before it could be released."
                )
            del self._owners[channel]
            return True

    def force_release(self, channel: int) -> bool:
        """Clear one runtime reservation after the board is known disconnected."""

        channel = self._validate_channel(channel)
        with self._lock:
            return self._owners.pop(channel, None) is not None

    def force_release_all(self) -> None:
        """Clear all reservations after a confirmed disconnect/shutdown."""

        with self._lock:
            self._owners.clear()

    def snapshot(self) -> dict[int, ChannelOwnership]:
        with self._lock:
            return dict(self._owners)

    @property
    def all_free(self) -> bool:
        with self._lock:
            return not self._owners
