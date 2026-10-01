# UI Redesign Plan — Tauri + React

Status: **planned (not started)**
Decided on: 2026-10-01

## Goal

Replace the PyQt6 desktop UI with a modern web-technology stack while keeping the
Python hardware layer intact. The migration is incremental: the current PyQt6 app
(`app.py` + `ui/pages/`) keeps working until the new UI reaches feature parity.

## Chosen stack

| Layer | Choice | Rationale |
|---|---|---|
| Native shell | **Tauri 2** (not Electron) | ~10 MB bundles, uses Windows WebView2, built-in Python sidecar support |
| Frontend | **React + TypeScript** (Vite) | Largest component ecosystem, best AI-assistant support |
| Styling | **Tailwind CSS + shadcn/ui** | De-facto standard for dashboards, accessible components |
| Live plots | **ECharts** | Fast canvas rendering for streaming flow/waveform data |
| Backend | **FastAPI sidecar** (new `backend/api/`) | Wraps existing modules; hardware logic unchanged |

## Architecture implication

The Python backend runs as a local sidecar process launched by Tauri. The web UI
talks to it over HTTP/WebSocket:

- **WebSocket streams**: flow sensor samples, camera frames (MJPEG or WS frames),
  waveform state, pump activity
- **REST endpoints**: board connect/disconnect, pump commands, session CRUD,
  workspace persistence
- Existing modules (`serial_manager`, `pump_control`, `waveform_engine`,
  `session_manager`, `camera_service`) are wrapped, not rewritten.

## Prerequisites

- Rust toolchain is **not yet installed**: `winget install Rustlang.Rustup`
- Node.js 24.x — already present
- Python 3.9.13 — sufficient for FastAPI

## Phases

### Phase 0 — Cleanup (done 2026-10-01)
- Deleted dead duplicate shell `ui/main_window.py` (superseded by `app.py`)
- Deleted superseded one-off probes (camera/iris `test_*.py`, `bartels_driver_probe_test.py`,
  `bartels_inventory_probe.py`, `capture_raw_flow_test_v18.py`)
- Moved `bartels_flow_test_v1.py` helpers into `backend/flow_capture.py` with its
  regression test ported to `tests/test_flow_capture.py`
- Moved generated experiment data (`captures/`, `diagnostics/`, `iris_*_test/`)
  into `user_data/` per the existing data policy
- Moved root README/notes into `docs/`

### Phase 1 — Backend API layer (FastAPI sidecar)
- Add `backend/api/` FastAPI app wrapping existing modules
- REST: board connect/disconnect, pump commands, sessions CRUD, workspace persistence
- WebSocket: flow sensor stream, camera frames, waveform state
- Testable independently with pytest + httpx before any UI exists

### Phase 2 — Tauri + React scaffold
- Scaffold `app-ui/` (Tauri 2 + Vite + React + TS + Tailwind + shadcn/ui)
- Tauri sidecar config launches the Python API process
- Theme derived from the existing QSS palette/design tokens
- Basic shell: sidebar nav, connection status, empty stubs for all 8 pages
- PyQt6 app remains fully functional in parallel

### Phase 3 — Page-by-page migration
Order by risk, one page at a time, verifying against hardware between pages:

1. Home
2. Pumps
3. Sensors (ECharts live flow plot)
4. Valves (placeholder page)
5. Waveform (ECharts streaming)
6. Camera (MJPEG view + iris/exposure controls)
7. Analytics
8. Workspace (two-panel synchronized view)

### Phase 4 — Retire PyQt6
- Remove `app.py`, `ui/`, `style.qss`, and PyQt6/pyqtgraph from requirements
  once parity is confirmed
