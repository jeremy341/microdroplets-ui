"""Classical/hybrid computer-vision detector for droplets in a fixed channel.

V66 deliberately separates *localisation* from *measurement*:

1. A fast grayscale temporal-background mask finds moving droplet candidates.
2. The original colour frame is compared with a colour background only inside
   a small candidate-centred patch.
3. That local colour residual expands the candidate to the translucent outer
   droplet boundary before geometry is measured along the flow axis.

This avoids the V65 failure mode where the high-contrast internal texture of a
transparent droplet was measured instead of the full droplet envelope.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import cv2
import numpy as np

from .models import AnalysisConfig, Detection
from .video_source import VideoSource


@dataclass(frozen=True)
class DetectorContext:
    roi_px: tuple[int, int, int, int]
    background_gray: np.ndarray
    background_bgr: np.ndarray
    min_area_px: float
    max_area_px: float
    artifact_mask: np.ndarray
    background_edge: np.ndarray
    # Sample frames the background median wanted but could not decode.  A
    # non-zero value means the surviving samples are a biased subset of the
    # recording, so the count must travel with the context instead of being
    # discarded inside ``build_background``.  The requested count travels with it
    # because the skip count only means something as a share of the grid.
    background_skipped_samples: int = 0
    background_requested_samples: int = 0


@dataclass(frozen=True)
class BackgroundEstimate:
    """Temporal-median background plus the decode integrity behind it."""

    background_gray: np.ndarray
    background_bgr: np.ndarray
    requested_samples: int
    skipped_samples: int


def normalized_roi_to_px(roi: tuple[float, float, float, float], width: int, height: int) -> tuple[int, int, int, int]:
    x, y, w, h = roi
    x0 = max(0, min(width - 1, int(round(x * width))))
    y0 = max(0, min(height - 1, int(round(y * height))))
    x1 = max(x0 + 1, min(width, int(round((x + w) * width))))
    y1 = max(y0 + 1, min(height, int(round((y + h) * height))))
    return x0, y0, x1 - x0, y1 - y0


def _read_bgr_roi(source: VideoSource, frame_index: int, roi_px: tuple[int, int, int, int]) -> np.ndarray:
    frame = source.read_frame(frame_index)
    x, y, w, h = roi_px
    return frame[y:y + h, x:x + w]


def build_background(
    source: VideoSource,
    config: AnalysisConfig,
    roi_px: tuple[int, int, int, int],
) -> BackgroundEstimate:
    """Build colour + grayscale temporal-median backgrounds.

    Keeping the colour median is important for translucent droplets: their
    outer envelope can change chroma much more than luminance.

    Sample frames that cannot be decoded are dropped, but never silently: the
    count is returned so the caller can report it.  When the unreadable indices
    cluster at one end of the recording, the surviving samples are a biased
    subset and the resulting median is biased with them.

    Losing *every* sample is a data-quality problem like any other, not a
    programming error: the estimate then carries a well-shaped but empty
    background whose skip count is the whole grid, so the analyzer's background
    gate demotes the run to incomplete instead of the analysis aborting on an
    untyped exception from the middle of the pipeline.  The placeholder is
    deliberately empty rather than a median of something: an all-zero frame makes
    every later detection overlap the artifact mask completely, so no geometry
    derived from it can be presented as a valid measurement.
    """

    frame_count = source.metadata.frame_count
    requested = max(5, min(int(config.background_samples), 61, frame_count))
    indices = np.linspace(0, frame_count - 1, requested, dtype=int)
    samples: list[np.ndarray] = []
    skipped_samples = 0
    for index in indices:
        try:
            roi = _read_bgr_roi(source, int(index), roi_px)
        except Exception:
            skipped_samples += 1
            continue
        samples.append(roi)
    if not samples:
        height = max(1, int(roi_px[3]))
        width = max(1, int(roi_px[2]))
        return BackgroundEstimate(
            background_gray=np.zeros((height, width), dtype=np.uint8),
            background_bgr=np.zeros((height, width, 3), dtype=np.uint8),
            requested_samples=requested,
            skipped_samples=skipped_samples,
        )
    stack = np.stack(samples, axis=0)
    background_bgr = np.median(stack, axis=0).astype(np.uint8)
    background_gray = cv2.cvtColor(background_bgr, cv2.COLOR_BGR2GRAY)
    background_gray = cv2.GaussianBlur(background_gray, (5, 5), 0)
    return BackgroundEstimate(
        background_gray=background_gray,
        background_bgr=background_bgr,
        requested_samples=requested,
        skipped_samples=skipped_samples,
    )


class DropletDetector:
    def __init__(self, context: DetectorContext) -> None:
        self.context = context
        self._kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        self._refine_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._refine_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))

    @classmethod
    def from_video(cls, source: VideoSource, config: AnalysisConfig) -> "DropletDetector":
        roi_px = normalized_roi_to_px(
            config.normalized_roi(), source.metadata.width, source.metadata.height
        )
        background = build_background(source, config, roi_px)
        background_gray = background.background_gray
        background_bgr = background.background_bgr
        _, _, w, h = roi_px
        roi_area = float(w * h)
        min_area = float(config.min_area_px) if config.min_area_px else max(20.0, roi_area * 0.00015)
        max_area = float(config.max_area_px) if config.max_area_px else max(min_area * 3.0, roi_area * 0.45)
        # Static saturated microscope highlights and deep black borders are not
        # useful droplet evidence, but they can attract the local colour
        # refinement.  Keep a conservative mask so later stages can penalise
        # geometry that overlaps those regions without deleting motion seeds.
        raw_gray = cv2.cvtColor(background_bgr, cv2.COLOR_BGR2GRAY)
        bright = raw_gray >= 248
        dark = raw_gray <= 4
        artifact_mask = ((bright | dark).astype(np.uint8) * 255)
        artifact_mask = cv2.dilate(
            artifact_mask,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)),
            iterations=1,
        )
        gx = cv2.Sobel(raw_gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(raw_gray, cv2.CV_32F, 0, 1, ksize=3)
        background_edge = cv2.magnitude(gx, gy)
        return cls(
            DetectorContext(
                roi_px,
                background_gray,
                background_bgr,
                min_area,
                max_area,
                artifact_mask,
                background_edge,
                background.skipped_samples,
                background.requested_samples,
            )
        )

    def _artifact_overlap(self, contour: np.ndarray) -> float:
        x0, y0, w, h = self.context.roi_px
        local = contour.astype(np.int32).copy()
        local[:, 0, 0] -= x0
        local[:, 0, 1] -= y0
        canvas = np.zeros((h, w), dtype=np.uint8)
        cv2.drawContours(canvas, [local], -1, 255, thickness=-1)
        pixels = canvas > 0
        count = int(np.count_nonzero(pixels))
        if count <= 0:
            return 0.0
        overlap = np.count_nonzero((self.context.artifact_mask > 0) & pixels)
        return float(overlap / count)

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Fast motion/background localisation used before local refinement."""

        x0, y0, w, h = self.context.roi_px
        roi = frame[y0:y0 + h, x0:x0 + w]
        if roi.size == 0:
            return []
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        diff = cv2.absdiff(gray, self.context.background_gray)
        otsu_value, _ = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        threshold_value = max(8.0, float(otsu_value))
        _, mask = cv2.threshold(diff, threshold_value, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel_open, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._kernel_close, iterations=2)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections: list[Detection] = []
        border_margin = 2
        for contour_local in contours:
            area = float(cv2.contourArea(contour_local))
            if area < self.context.min_area_px or area > self.context.max_area_px:
                continue
            bx, by, bw, bh = cv2.boundingRect(contour_local)
            if bw < 3 or bh < 3:
                continue
            moments = cv2.moments(contour_local)
            if abs(moments["m00"]) < 1e-9:
                continue
            cx = moments["m10"] / moments["m00"] + x0
            cy = moments["m01"] / moments["m00"] + y0
            contour = contour_local.astype(np.int32).copy()
            contour[:, 0, 0] += x0
            contour[:, 0, 1] += y0
            touches = (
                bx <= border_margin
                or by <= border_margin
                or bx + bw >= w - border_margin
                or by + bh >= h - border_margin
            )
            major_axis = _major_axis_from_contour(contour_local)
            detections.append(
                Detection(
                    contour=contour,
                    centroid=(float(cx), float(cy)),
                    area_px2=area,
                    bbox=(bx + x0, by + y0, bw, bh),
                    touches_roi_edge=touches,
                    major_axis=major_axis,
                    artifact_overlap=self._artifact_overlap(contour),
                )
            )
        detections.sort(key=lambda item: item.centroid[0])
        return detections

    def refine_detection(
        self,
        frame: np.ndarray,
        detection: Detection,
        flow_direction: tuple[float, float],
    ) -> Detection:
        """Expand one motion seed to the translucent outer droplet envelope.

        The refinement is intentionally local.  It therefore costs little on
        long recordings and avoids turning fixed highlights/scratches elsewhere
        in the microscope frame into droplet geometry.
        """

        if detection.length_px is None or detection.width_px is None:
            apply_flow_geometry(detection, flow_direction, 0.0)
        coarse_length = max(8.0, float(detection.length_px or max(detection.bbox[2:])))
        coarse_width = max(5.0, float(detection.width_px or min(detection.bbox[2:])))

        ux, uy = _unit(flow_direction)
        tx, ty = -uy, ux
        half_long = max(28.0, min(190.0, coarse_length * 1.8))
        half_cross = max(16.0, min(90.0, coarse_width * 2.8))
        cx, cy = detection.centroid
        half_x = abs(ux) * half_long + abs(tx) * half_cross
        half_y = abs(uy) * half_long + abs(ty) * half_cross

        roi_x, roi_y, roi_w, roi_h = self.context.roi_px
        xa = max(roi_x, int(math.floor(cx - half_x)))
        xb = min(roi_x + roi_w, int(math.ceil(cx + half_x + 1)))
        ya = max(roi_y, int(math.floor(cy - half_y)))
        yb = min(roi_y + roi_h, int(math.ceil(cy + half_y + 1)))
        if xb - xa < 8 or yb - ya < 8:
            return detection

        patch = frame[ya:yb, xa:xb]
        bg_patch = self.context.background_bgr[
            ya - roi_y:yb - roi_y,
            xa - roi_x:xb - roi_x,
        ]
        if patch.size == 0 or patch.shape != bg_patch.shape:
            return detection

        # CIE-Lab makes the colour shift of the translucent droplet envelope
        # explicit.  Luminance is deliberately down-weighted because V65's
        # grayscale residual already proved too conservative on real videos.
        lab = cv2.cvtColor(patch, cv2.COLOR_BGR2LAB).astype(np.float32)
        bg_lab = cv2.cvtColor(bg_patch, cv2.COLOR_BGR2LAB).astype(np.float32)
        delta = lab - bg_lab
        colour_score = np.sqrt(
            (0.45 * delta[:, :, 0]) ** 2
            + (1.20 * delta[:, :, 1]) ** 2
            + (1.20 * delta[:, :, 2]) ** 2
        )

        # Transparent droplets often have a weak colour residual but a stable
        # refractive edge.  Use the *change* in local edge strength as a second
        # cue rather than trusting either signal alone.  Static channel walls
        # largely cancel because their background edge is subtracted.
        patch_gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        bg_gray = cv2.cvtColor(bg_patch, cv2.COLOR_BGR2GRAY)
        pgx = cv2.Sobel(patch_gray, cv2.CV_32F, 1, 0, ksize=3)
        pgy = cv2.Sobel(patch_gray, cv2.CV_32F, 0, 1, ksize=3)
        bgx = cv2.Sobel(bg_gray, cv2.CV_32F, 1, 0, ksize=3)
        bgy = cv2.Sobel(bg_gray, cv2.CV_32F, 0, 1, ksize=3)
        edge_delta = np.abs(cv2.magnitude(pgx, pgy) - cv2.magnitude(bgx, bgy))
        edge_delta = np.minimum(edge_delta, 40.0)
        # V3.2 returns colour residual to the authoritative segmentation cue.
        # The V3.1 edge bonus occasionally pulled the contour onto glare/internal
        # refraction and made otherwise stable recordings measure worse.  Edge
        # change remains a diagnostic/support signal but no longer expands the
        # segmentation mask by itself.
        score = colour_score

        # Estimate local noise from an outer ring; this adapts to illumination
        # changes while keeping a floor that worked on the supplied microscope
        # recordings.  The upper cap prevents a bright highlight from raising
        # the threshold until only the internal texture remains again.
        ph, pw = score.shape
        outer = np.ones((ph, pw), dtype=bool)
        y1, y2 = int(ph * 0.20), max(int(ph * 0.80), int(ph * 0.20) + 1)
        x1, x2 = int(pw * 0.20), max(int(pw * 0.80), int(pw * 0.20) + 1)
        outer[y1:y2, x1:x2] = False
        noise_values = score[outer]
        noise_median = float(np.median(noise_values)) if noise_values.size else 0.0
        mad = float(np.median(np.abs(noise_values - noise_median))) if noise_values.size else 0.0
        robust_sigma = 1.4826 * mad
        threshold = max(4.5, min(11.0, noise_median + 3.0 * robust_sigma))

        mask = (score >= threshold).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._refine_open, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._refine_close, iterations=2)

        component = self._select_seed_component(mask, detection, xa, ya)
        if component is None:
            return detection
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return detection
        contour = max(contours, key=cv2.contourArea).astype(np.int32)
        contour[:, 0, 0] += xa
        contour[:, 0, 1] += ya
        area = float(cv2.contourArea(contour))
        moments = cv2.moments(contour)
        if area <= 0 or abs(moments["m00"]) < 1e-9:
            return detection

        old = (
            detection.contour,
            detection.centroid,
            detection.area_px2,
            detection.bbox,
            detection.touches_roi_edge,
            detection.major_axis,
            detection.length_px,
            detection.width_px,
            detection.min_s,
            detection.max_s,
            detection.min_t,
            detection.max_t,
            detection.artifact_overlap,
            detection.edge_support,
        )
        detection.contour = contour
        detection.centroid = (
            float(moments["m10"] / moments["m00"]),
            float(moments["m01"] / moments["m00"]),
        )
        detection.area_px2 = area
        detection.bbox = cv2.boundingRect(contour)
        detection.major_axis = _major_axis_from_contour(contour)
        bx, by, bw, bh = detection.bbox
        margin = 2
        detection.touches_roi_edge = (
            bx <= roi_x + margin
            or by <= roi_y + margin
            or bx + bw >= roi_x + roi_w - margin
            or by + bh >= roi_y + roi_h - margin
        )
        apply_flow_geometry(detection, flow_direction, 0.0)
        detection.artifact_overlap = self._artifact_overlap(contour)
        contour_local = contour.astype(np.int32).copy()
        contour_local[:, 0, 0] -= xa
        contour_local[:, 0, 1] -= ya
        edge_canvas = np.zeros(score.shape, dtype=np.uint8)
        cv2.drawContours(edge_canvas, [contour_local], -1, 255, thickness=2)
        edge_pixels = edge_canvas > 0
        if np.any(edge_pixels):
            support = float(np.median(edge_delta[edge_pixels]))
            detection.edge_support = float(max(0.0, min(1.0, support / 20.0)))

        refined_length = float(detection.length_px or 0.0)
        refined_width = float(detection.width_px or 0.0)
        plausible = (
            refined_length >= coarse_length * 0.75
            and refined_length <= coarse_length * 4.2
            and refined_width >= max(3.0, coarse_width * 0.55)
            and refined_width <= max(coarse_width * 8.0, half_cross * 1.9)
            and area <= (xb - xa) * (yb - ya) * 0.78
            and detection.artifact_overlap <= 0.88
        )
        if not plausible:
            (
                detection.contour,
                detection.centroid,
                detection.area_px2,
                detection.bbox,
                detection.touches_roi_edge,
                detection.major_axis,
                detection.length_px,
                detection.width_px,
                detection.min_s,
                detection.max_s,
                detection.min_t,
                detection.max_t,
                detection.artifact_overlap,
                detection.edge_support,
            ) = old
            detection.refined_geometry = False
            return detection

        detection.refined_geometry = True
        # 0..1 quality signal used only by track-level post-processing.
        residual_margin = float(np.median(score[component > 0]) - threshold) if np.any(component > 0) else 0.0
        detection.refinement_score = float(max(0.0, min(1.0, 0.5 + residual_margin / 18.0)))
        return detection

    def _select_seed_component(
        self,
        mask: np.ndarray,
        detection: Detection,
        xa: int,
        ya: int,
    ) -> np.ndarray | None:
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if count <= 1:
            return None
        sx = int(round(detection.centroid[0] - xa))
        sy = int(round(detection.centroid[1] - ya))
        height, width = mask.shape
        min_component_area = max(25.0, detection.area_px2 * 0.45)
        best_id: int | None = None
        best_cost = float("inf")
        for label in range(1, count):
            area = float(stats[label, cv2.CC_STAT_AREA])
            if area < min_component_area:
                continue
            ccx, ccy = centroids[label]
            distance = math.hypot(float(ccx) - sx, float(ccy) - sy)
            contains_seed = 0 <= sx < width and 0 <= sy < height and labels[sy, sx] == label
            # Strongly prefer the component containing the coarse motion seed,
            # then the nearest sufficiently large component if the seed lies in
            # a threshold-created hole.
            cost = distance - (1000.0 if contains_seed else 0.0)
            if cost < best_cost:
                best_cost = cost
                best_id = label
        if best_id is None:
            return None
        return (labels == best_id).astype(np.uint8) * 255


def _unit(vector: tuple[float, float]) -> tuple[float, float]:
    x, y = float(vector[0]), float(vector[1])
    norm = math.hypot(x, y)
    if norm <= 1e-9:
        return 1.0, 0.0
    return x / norm, y / norm


def _major_axis_from_contour(contour: np.ndarray) -> tuple[float, float] | None:
    pts = contour.reshape(-1, 2).astype(np.float64)
    if len(pts) < 3:
        return None
    centered = pts - pts.mean(axis=0, keepdims=True)
    covariance = np.cov(centered.T)
    if covariance.shape != (2, 2):
        return None
    values, vectors = np.linalg.eigh(covariance)
    axis = vectors[:, int(np.argmax(values))]
    norm = float(np.linalg.norm(axis))
    if not math.isfinite(norm) or norm <= 1e-9:
        return None
    axis = axis / norm
    if axis[0] < 0 or (abs(axis[0]) < 1e-9 and axis[1] < 0):
        axis = -axis
    return float(axis[0]), float(axis[1])


def apply_flow_geometry(detection: Detection, flow_direction: tuple[float, float], line_a_s: float) -> Detection:
    """Measure a contour by projection onto the true flow axis."""

    ux, uy = flow_direction
    norm = math.hypot(ux, uy)
    if norm <= 1e-9:
        ux, uy = 1.0, 0.0
    else:
        ux, uy = ux / norm, uy / norm
    tx, ty = -uy, ux
    pts = detection.contour.reshape(-1, 2).astype(np.float64)
    s = pts[:, 0] * ux + pts[:, 1] * uy
    t = pts[:, 0] * tx + pts[:, 1] * ty
    detection.min_s = float(np.min(s))
    detection.max_s = float(np.max(s))
    detection.min_t = float(np.min(t))
    detection.max_t = float(np.max(t))
    detection.length_px = detection.max_s - detection.min_s
    detection.width_px = detection.max_t - detection.min_t
    detection.line_a_intersects = detection.min_s <= line_a_s <= detection.max_s
    return detection
