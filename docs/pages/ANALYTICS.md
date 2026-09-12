# Analytics Page

Analytics performs offline droplet analysis on recorded video. It does not require a connected Multiboard.


For detector/tracker/event/calibration/cache internals, see [Analytics Engine](../ANALYTICS_ENGINE.md).

## Current workflow

A typical analysis is:

```text
Load video
→ configure ROI / full frame
→ set or estimate flow direction
→ configure calibration if available
→ Analyze
→ inspect overlays, timeline and metrics
→ export CSV
```

Setup is currently drag-oriented: ROI and flow-direction interactions do not
have a complete keyboard alternative.

## What the current pipeline can derive

Depending on video quality and calibration, the result model can contain:

- total droplet events;
- valid geometry count;
- droplet length/geometry;
- velocity;
- spacing;
- generation timing/rate;
- confidence/rejection information;
- rendered overlays.

## Counted event versus valid geometry

This distinction is intentional:

```text
physical event crossed measurement line
              ↓
          counted event

contour/geometry reliable enough?
        ↙ yes       no ↘
valid geometry      counted but geometry rejected
```

Do not collapse these into one “valid droplet” boolean. Event counting and trustworthy geometry solve different problems.

## Calibration

Without calibration:

- length remains pixels;
- velocity remains pixels/second.

A valid `µm/px` scale converts geometry/velocity into physical units. The calibration belongs to a specific optical setup/resolution; nominal microscope magnification alone is not sufficient.

The current calibration model is principally resolution-based. Do not treat a
matching width/height as proof that optics, crop, focus and camera identity are
the same.

## Processing architecture

The backend pipeline is roughly:

```text
VideoSource
→ background / ROI / flow-axis setup
→ DropletDetector + local refinement
→ DropletTracker / stitching
→ temporal crossing association
→ geometry selection and repair
→ velocity / spacing / generation timing
→ AnalysisResult / cache / CSV
```

Primary backend files are under `backend/analytics/`.

## Detector development discipline

The detector was improved from an earlier baseline using real recordings and measured failure modes. Continue that approach:

1. preserve a baseline result;
2. test multiple recordings;
3. inspect overlays and rejection reasons;
4. classify false positives/misses/boundary errors;
5. change the algorithm for a measured failure mode;
6. rerun the benchmark.

Avoid tuning thresholds until one video looks good while silently making the others worse.

## Still images

Standalone still-image Analytics is a future feature, not part of this release.

A future still-image path can reasonably provide geometry if calibration is known. It must not invent temporal quantities such as generation period or true velocity from one frame without an external time reference.

## Files and outputs

Analytics settings/results/cache are stored below:

```text
user_data/analytics/
user_data/analytics_settings.json
```

CSV export is handled by `backend/analytics/csv_export.py`.

Use a unique source filename and inspect cache metadata when results seem stale.
Current cache paths can collide on filename stem, and partial/cancelled results
or truncated videos need conservative interpretation rather than being treated
as complete analyses. Automatic ROI can also change persisted settings without
fully changing cache identity.

## Relevant files

```text
ui/pages/Analytics.py
backend/analytics/analyzer.py
backend/analytics/detector.py
backend/analytics/tracker.py
backend/analytics/temporal.py
backend/analytics/models.py
backend/analytics/render.py
backend/analytics/calibration.py
backend/analytics/result_store.py
backend/analytics/csv_export.py
```

## Relevant tests

```text
test_analytics_backend.py
test_analytics_postprocessing.py
test_analytics_event_geometry.py
```

## Navigation

```text
Shift+6
```
