"""OpenCV rendering helpers for Analytics review frames."""

from __future__ import annotations

import math

import cv2
import numpy as np

from .models import AnalysisConfig, OverlayDetection


GREEN = (105, 174, 145)  # BGR-ish mint suitable for grayscale imagery.
WHITE = (245, 245, 245)
YELLOW = (72, 190, 225)


def _unit(vector: tuple[float, float]) -> tuple[float, float]:
    norm = math.hypot(vector[0], vector[1])
    if norm <= 1e-9:
        return 1.0, 0.0
    return vector[0] / norm, vector[1] / norm


def _projection_line_segment(
    roi_px: tuple[int, int, int, int],
    flow: tuple[float, float],
    s_value: float,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Return a clipped line perpendicular to flow through the ROI.

    A long line is drawn then clipped against the rectangular ROI.
    """

    x, y, w, h = roi_px
    ux, uy = _unit(flow)
    tx, ty = -uy, ux
    center = np.array([x + w / 2.0, y + h / 2.0], dtype=float)
    center_s = center[0] * ux + center[1] * uy
    point = center + np.array([ux, uy]) * (s_value - center_s)
    extent = math.hypot(w, h) * 2.0
    p1 = point - np.array([tx, ty]) * extent
    p2 = point + np.array([tx, ty]) * extent
    ok, cp1, cp2 = cv2.clipLine((x, y, w, h), tuple(np.round(p1).astype(int)), tuple(np.round(p2).astype(int)))
    if not ok:
        return (x, y), (x, y)
    return cp1, cp2


def render_analysis_frame(
    frame: np.ndarray,
    *,
    roi_px: tuple[int, int, int, int] | None = None,
    flow_direction: tuple[float, float] | None = None,
    line_a_s: float | None = None,
    line_b_s: float | None = None,
    overlays: list[OverlayDetection] | None = None,
    calibration_um_per_px: float | None = None,
) -> np.ndarray:
    image = frame.copy()
    height, width = image.shape[:2]
    if roi_px is not None:
        x, y, w, h = roi_px
        cv2.rectangle(image, (x, y), (x + w, y + h), GREEN, 2, cv2.LINE_AA)
    if roi_px is not None and flow_direction is not None:
        ux, uy = _unit(flow_direction)
        x, y, w, h = roi_px
        origin = (x + 18, max(y + 22, 28))
        end = (int(origin[0] + ux * 62), int(origin[1] + uy * 62))
        cv2.arrowedLine(image, origin, end, WHITE, 2, cv2.LINE_AA, tipLength=0.25)
        cv2.putText(image, "Flow", (origin[0], origin[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1, cv2.LINE_AA)
    if roi_px is not None and flow_direction is not None and line_a_s is not None:
        p1, p2 = _projection_line_segment(roi_px, flow_direction, line_a_s)
        cv2.line(image, p1, p2, YELLOW, 1, cv2.LINE_AA)
        cv2.putText(image, "A", p1, cv2.FONT_HERSHEY_SIMPLEX, 0.45, YELLOW, 1, cv2.LINE_AA)
    if roi_px is not None and flow_direction is not None and line_b_s is not None:
        p1, p2 = _projection_line_segment(roi_px, flow_direction, line_b_s)
        cv2.line(image, p1, p2, GREEN, 1, cv2.LINE_AA)
        cv2.putText(image, "B", p1, cv2.FONT_HERSHEY_SIMPLEX, 0.45, GREEN, 1, cv2.LINE_AA)

    for overlay in overlays or []:
        contour = np.asarray(overlay.contour, dtype=np.int32).reshape(-1, 1, 2)
        if len(contour) >= 2:
            color = GREEN if overlay.valid_candidate else (160, 160, 160)
            cv2.polylines(image, [contour], True, color, 2, cv2.LINE_AA)
        cx, cy = int(round(overlay.centroid[0])), int(round(overlay.centroid[1]))
        cv2.putText(
            image,
            f"#{overlay.droplet_id}",
            (cx + 8, cy - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            GREEN,
            1,
            cv2.LINE_AA,
        )
        if overlay.length_start is not None and overlay.length_end is not None:
            p1 = tuple(int(round(v)) for v in overlay.length_start)
            p2 = tuple(int(round(v)) for v in overlay.length_end)
            cv2.line(image, p1, p2, GREEN, 1, cv2.LINE_AA)
            cv2.circle(image, p1, 3, GREEN, -1, cv2.LINE_AA)
            cv2.circle(image, p2, 3, GREEN, -1, cv2.LINE_AA)

    if calibration_um_per_px and calibration_um_per_px > 0:
        desired_um = 100.0
        bar_px = int(round(desired_um / calibration_um_per_px))
        if bar_px > width * 0.4:
            desired_um = 50.0
            bar_px = int(round(desired_um / calibration_um_per_px))
        if 20 <= bar_px <= width * 0.45:
            x2 = width - 20
            x1 = x2 - bar_px
            y = height - 22
            cv2.line(image, (x1, y), (x2, y), WHITE, 3, cv2.LINE_AA)
            cv2.putText(
                image,
                f"{desired_um:g} um",
                (x1, y - 7),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                WHITE,
                1,
                cv2.LINE_AA,
            )
    return image
