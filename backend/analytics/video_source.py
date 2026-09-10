"""OpenCV video input helpers for offline Analytics."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import cv2

from .models import VideoMetadata


SUPPORTED_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".m4v"}


class VideoOpenError(RuntimeError):
    pass


class VideoSource:
    """Small owner around ``cv2.VideoCapture`` with deterministic metadata."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._capture: cv2.VideoCapture | None = None
        self.metadata = self._probe()

    def _new_capture(self) -> cv2.VideoCapture:
        capture = cv2.VideoCapture(str(self.path))
        if not capture.isOpened():
            capture.release()
            raise VideoOpenError(f"Could not open video: {self.path}")
        return capture

    def _probe(self) -> VideoMetadata:
        if not self.path.is_file():
            raise VideoOpenError(f"Video file does not exist: {self.path}")
        capture = self._new_capture()
        try:
            width = int(round(capture.get(cv2.CAP_PROP_FRAME_WIDTH)))
            height = int(round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            frame_count = int(round(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
        finally:
            capture.release()
        if width <= 0 or height <= 0 or frame_count <= 0:
            raise VideoOpenError(f"Video metadata is invalid: {self.path}")
        if not (fps > 0):
            fps = 30.0
        duration = frame_count / fps
        return VideoMetadata(self.path, width, height, fps, frame_count, duration)

    def open(self) -> None:
        self.release()
        self._capture = self._new_capture()

    def release(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def __enter__(self) -> "VideoSource":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def read_frame(self, frame_index: int) -> object:
        frame_index = max(0, min(self.metadata.frame_count - 1, int(frame_index)))
        owned_capture = self._capture is None
        capture = self._new_capture() if owned_capture else self._capture
        assert capture is not None
        try:
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
        finally:
            if owned_capture:
                capture.release()
        if not ok or frame is None:
            raise VideoOpenError(f"Could not decode frame {frame_index} from {self.path.name}")
        return frame

    def iter_frames(self, start: int = 0) -> Iterator[tuple[int, object]]:
        capture = self._new_capture()
        try:
            start = max(0, int(start))
            if start:
                capture.set(cv2.CAP_PROP_POS_FRAMES, start)
            index = start
            while index < self.metadata.frame_count:
                ok, frame = capture.read()
                if not ok or frame is None:
                    break
                yield index, frame
                index += 1
        finally:
            capture.release()


def discover_videos(directory: str | Path) -> list[Path]:
    root = Path(directory)
    if not root.is_dir():
        return []
    candidates = [
        item for item in root.iterdir()
        if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)
