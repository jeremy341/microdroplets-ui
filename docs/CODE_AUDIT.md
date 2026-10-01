# FluidicStudio Code Audit — GAUNTLET Report

Date: 2026-10-01 · Branch: `main` · Method: 3-agent reconnaissance swarm + 4 independent
specialist slots (Architect / Implementer / Investigator / Adversarial Reviewer),
cross-examined and verified by the orchestrator against source. Every claim below was
either verified by line-level source inspection or marked with its confidence status.

---

## 0. FIXES APPLIED (2026-10-01, backend-only pass)

All non-UI findings from sections 1–6 below were fixed in the backend, verified by
295 passing tests (full suite; 4 remaining failures are the pre-existing
environment-only and stale-UI-contract ones documented in §5) and a clean ruff run on
all touched files. A second adversarial review round over the full diff found four
regressions in the first implementation; all four were fixed and re-verified.
A third round closed the remaining backend items listed further below (library
reload I/O, tolerant measurement parsing, unconfirmed-OFF reporting) and refreshed
this count from 291 to 295.

Lint status, stated precisely: ruff is clean on every file this pass touched. The
repository still carries pre-existing findings that are out of scope here — four in
untouched backend modules (`analytics/render.py`, `analytics/temporal.py`,
`sensor_stream_capture.py`, `session_runtime.py`) and six in untouched tests. A
repo-wide run also reports ~221 findings, almost all inside `ui/pages/*`, which
have never been part of the lint scope.

| Fix | Where | What changed |
|---|---|---|
| ACK correlation (1.1) | `serial_manager.py` | FIFO pending-command queue; each OK/FAIL reply is attributed to the oldest outstanding command; a reply that arrives after its command timed out is consumed by that command's entry instead of falsely acking the next one. Firmware/boot replies consume the identification entry so later OKs stay in order. |
| ERR isolation (1.1/3.8) | `serial_manager.py` | An `ERR`/`FAIL` line now fails only the FIFO-attributed command; when no command is outstanding it only updates connection state. |
| Reader-death race (1.2) | `serial_manager.py` | Volume integration state (`_last_flow_time`, `_last_flow_value`, `_volume_ul`) is guarded by `_flow_lock`; a concurrent reset can no longer crash the reader thread. |
| Retry keeps volume (1.3) | `serial_manager.py` | The watchdog retry path resets the segment with `keep_total=True`, preserving accumulated volume (matches the `retry_active_stream` invariant). |
| Slow-stream volume (2.3) | `serial_manager.py` | Gap guard is now adaptive: integrate when `delta <= max(2.0 s, 3× median of recent intervals)`; intervals ≤10 s feed the cadence estimate; stalls >10 s are excluded. |
| ACK-wait lock hold (3.4) | `serial_manager.py` | `send_and_wait_for_ack` waits outside `_command_lock`; a slow board can no longer block unrelated senders for the full timeout. |
| close() teardown (3.5) | `serial_manager.py` | `close()` always completes teardown (POFF attempts → stop stream → stop reader/initializer → close port); the return value reports only the POFF fact. `close()` is idempotent on an already-disconnected connection, so the UI's "retry disconnect" flow unblocks on the second attempt. |
| CSV header (2.5) | `csv_logger.py` | Header is written lazily from the first received sample's series; a series discovered mid-session rewrites the header row so its column is labelled. |
| Submit/stop race | `csv_logger.py` | `submit` checks status and enqueues atomically under the same lock `stop()` uses; the worker drains everything accepted before (and immediately after) the sentinel — "accepted" now always means "written". |
| Stop hang + status | `csv_logger.py` | The stop sentinel is enqueued non-blocking (a dead worker with a full queue can no longer hang `stop()`), and the status correctly reaches `"running"`. |
| Bounded queue (3.1) | `csv_logger.py` | Queue capped at 50 000 samples; overflow drops the oldest and counts it in `dropped_samples`. |
| Incomplete cache (1.4) | `result_store.py` | `save_result` refuses incomplete results; `load_cached` rejects a well-formed cached result with `complete=false`. |
| Cache collision (3.11) | `result_store.py` | Result directories include a SHA-1 fingerprint of the resolved video path. |
| Auto-ROI stickiness (2.6) | `analyzer.py` | The analyzer never mutates the user's config; the auto-detected band is carried by `result.roi_px` only, so persisted settings keep full-frame ROI and auto re-detects every run. |
| Silent frame skips (3.12) | `analyzer.py` | The four `except Exception: continue` frame loops now count skipped frames, surfaced as `AnalysisResult.skipped_frames`. |
| Cancel leak (3.13) | `analyzer.py` | `iter_frames()` is wrapped in `contextlib.closing` so a cancelled analysis releases the video handle deterministically. |
| Per-frame reopen (D6) | `analyzer.py` | The source is opened once for the analysis and released in a `finally`. |
| Timing drift (2.4) | `waveform_engine.py` | Steps are scheduled against an absolute deadline; send latency and OS timer overshoot no longer accumulate (regression test: 1000 ms wave completes < 1150 ms with a 5 ms-per-write transport). |
| Stop-during-start (2.1) | `waveform_engine.py` | The runner checks the stop request before the start-up sequence and between each start-up command, so a stop arriving mid-start-up cannot pulse the pump ON. |
| Cycle merge (4.4) | `waveform_engine.py` | Equal adjacent levels are merged across cycle boundaries for the non-Highdriver4 path (and the channel-less path), making step counts consistent across driver types. |
| open() lock scope (3.7) | `camera_service.py` | The whole open sequence holds the same lock as `read()`/`close()`. |
| Verification lock hold (3.18) | `camera_service.py` | The 12×15 ms async-apply retry runs outside the service lock (one `get` per hold), serialized by a dedicated verification lock so overlapping writes cannot cross-contaminate. |
| FFI signatures (3.10) | `dnx64_vendor.py` | `SetVideoProcAmp` declared `(int, long)`; the runtime monkey-patch in `camera_service` removed; `GetVideoDeviceCount` no longer performs an implicit `Init()`. |
| Profile quarantine (3.19) | `camera_profiles.py` | Corrupt or wrong-shape profile JSON is renamed to `.corrupt` instead of being silently discarded. |
| Backup recovery (4.1) | `session_manager.py` | `.bak` recovery immediately rewrites the repaired primary file; `save()` stages the new payload durably *before* rotating the backup, and never rotates a good `.bak` over a corrupt primary. |
| Dead code (4.2) | — | `backend/dnx64_api.py` deleted (zero references). |

### Round 3 — remaining backend items (also fixed 2026-10-01)

| Fix | Where | What changed |
|---|---|---|
| Library poll I/O (3.2 backend half) | `waveform_library.py` | `load()` compares the file's `(mtime_ns, size)` and skips the disk read + JSON parse when unchanged; `save`/`delete` refresh the stamp so they stay authoritative. Views that poll `library.load()` no longer re-parse the file several times a second. |
| Measurement parse robustness (3.17) | `protocol.py` | Measurement lines now parse against the `<<`-stripped line and accept a trailing **known** unit (`mL/min`, `µL/min`, `mbar`, `ppm`, `raw`, …). A line ending in an unknown word (`V=1.2 extra`) or carrying a second number is still rejected, so the "never guess" rule is preserved. |
| Unconfirmed final OFF (2.1 observability) | `wave_execution.py` | `stop_all()`'s final per-channel OFF retry no longer swallows failure: an unconfirmed or failed OFF sets the channel runtime state to `error` with an explicit message. |
| Documentation drift | `BOARD_COMMUNICATION.md`, `SENSOR_PIPELINE.md`, `PUMP_WAVE_ENGINE.md` | Replaced the now-wrong statements (single pending ACK, "keep connection open and return False", fixed 2 s gap rule, `stop_all` dead-code claim) with the implemented contracts, and recorded the lost-reply limitation where the ACK model is documented. |
| Audit correction | this file (§2.7) | The "capped at 3 attempts" claim is disproved by the call site: the app path retries indefinitely; the real issue is the UI status text. |

**Known limitation (accepted, Option A):** if a board reply is *lost* (not just late),
every subsequent rapid transaction can be mis-attributed until the stale head is
pruned after 5 s of send silence. Pump sequences sent <1 s apart can therefore fail
after a single lost ack (fail-safe direction: false *failures*, not false successes).
A firmware that echoes the command text would remove this class entirely.

**Deferred to the UI workstream:** global excepthook + Sensors save/load guards (1.5),
disconnect/quit GUI-thread blocking (2.2), the app-layer `close()` result handling
(N1 needs app.py:641-647 + :1225 to treat a completed teardown correctly instead of
blocking disconnect), sensor-retry wiring (2.7), per-tick restyle churn (3.3),
Workspace panel visibility gating (3.2), `session_rows` trimming (3.1 UI half),
Pumps `_operation_pending` rebuild guard (3.6), dead `PumpsPage.stop_all` (4.7).

### Hardware verification checklist (bench items, do NOT assume from fakes)
- [ ] Confirm the board acks every command (`L0`, `DFON`, `DFOFF`, `P{n}…`) with an
      in-order `OK`, and whether `POFF` can reply late (FIFO attribution relies on order).
- [ ] Confirm whether `L0` acks while the flow stream is disabled (legacy notes say it
      may not; the init sequence depends on it).
- [ ] Measure a real wave cycle vs. `wave_frequency_hz` after the deadline fix.
- [ ] Verify DNX64 `Init()` side effects after removing the implicit re-Init in
      `GetVideoDeviceCount` (flaky-attach behavior should improve or stay equal).
- [ ] Verify `SetVideoProcAmp(int, long)` against the real DNX64.dll (brightness path).
- [ ] Check whether SDK enumeration indices ever diverge from DirectShow indices on a
      two-camera setup (`enumerate_cameras` — audit finding 3.9, deliberately not changed).

---

## Verification status legend

- **VERIFIED** — path traced in source / reproduced by test execution
- **LIKELY** — strong evidence, not conclusively proven (hardware-dependent paths)
- **STALE TEST** — the test encodes an outdated contract; the code is intentional

---

## 1. CRITICAL (physical pump state / data loss / crash)

### 1.1 ACK matching has no reply correlation — any `OK` acks any pending command · VERIFIED
`backend/serial_manager.py:486-495` — the ack branch condition
`self._pending_ack_event is not None and self._pending_ack_command == self.last_command`
is tautological (`_pending_ack_command` is always set to the just-sent command). A late
`OK` from a timed-out command falsely acknowledges the *next* command. Proven by trace:
`send_and_wait_for_ack("DFOFF", timeout=0.02)` times out; its late OK then completes a
pending `L0` wait with success. Pump rollbacks, calibration state, and `hardware_state`
are all built on these facts. Related: an unsolicited `ERR` line while any command is
pending marks *that* command failed and flips the whole connection to ERROR
(`serial_manager.py:482-487`).

### 1.2 Reader-thread death race on integration reset · VERIFIED
`backend/serial_manager.py:514-525` checks `_last_flow_time is not None` then uses it,
but `_reset_integration()` / `reset_accumulated_volume()` (`:408-411`, `:550-554`) set
both to `None` from other threads (stop_sensor → close, initializer retry). The
check-then-use window allows `None - float` → `TypeError` inside the reader loop's
`except BaseException` (`:459`) → reader dies permanently; stream dead until manual
reconnect. `_volume_ul +=` is also a non-atomic read-modify-write.

### 1.3 Watchdog recovery wipes accumulated volume mid-session · VERIFIED
`backend/serial_manager.py:311` — after a 5 s sample stall the retry loop re-enters and
calls `_reset_integration()`, zeroing `_volume_ul`, directly contradicting the
documented invariant of `retry_active_stream` (`:394-399`: "a retry must not reset
accumulated volume"). Users lose volume continuity exactly when a recovery happens.

### 1.4 Partial/cancelled Analytics results are cached as complete · VERIFIED
`ui/pages/Analytics.py:978-981` — `_analysis_finished` calls `self._store.save_result`
unconditionally before checking `result.complete`; `backend/analytics/result_store.py:45-61`
`load_cached` never checks `complete`. A cancelled analysis is silently served later
as a complete cached result.

### 1.5 Unhandled exceptions in Qt slots crash the whole app · VERIFIED
No `sys.excepthook` is installed. `Sensors.save_session` uses bare `Path.write_text`
(`Sensors.py:2055`) and `load_session` uses bare `json.loads` + dict access on an
arbitrary file (`Sensors.py:2063`). An unwritable path or malformed session file aborts
the app (PyQt6 unhandled exception in slot → qFatal).

### 1.6 Camera `stop_camera` can destroy a running QThread · VERIFIED (static)
`ui/pages/Camera.py:1474-1493` — `worker.stop()` waits 1500 ms, then `self.worker = None`
without checking `isRunning()`. If a DirectShow `read()` blocks >1.5 s, the parentless
QThread (`Camera.py:585`) is destroyed while running → PyQt abort ("QThread: Destroyed
while thread is still running"). Realistic on USB hiccups.

---

## 2. HIGH

### 2.1 Pump can be left physically ON after disconnect during wave start · LIKELY
`waveform_engine.py:579-586` sends the full startup *before* the first stop check
(`:589`); `app.py:638-641` disconnect path joins `stop_all(2.0)` but a runner can be
inside a ~4 s `send_sequence` holding `_operation_lock`; `connection.close()` proceeds
while commands are in flight — `_write_lock` serializes bytes, not order. Ownership is
force-released (`app.py:657`), erasing the evidence while the pump may stay ON.
Related: stopping a wave within ~200 ms of starting it still executes the ON commands
(VERIFIED logic — `wave_execution.py:166-180`).

### 2.2 GUI-thread blocking on disconnect / stop-all / quit · VERIFIED
`app.py:634-654` (stop_all join ≤2 s + POFF×2×1 s + initializer join 2 s + reader join
1 s ≈ up to ~9 s freeze per board), `Pumps.py` same pattern in `stop_all` (which is
itself dead code — zero callers, see 4.7), and `app.py:1217-1229` at exit (`aboutToQuit`).
Quit on a wedged board can hang for the sum of all ACK timeouts.

### 2.3 Slow sensor streams lose all volume integration · VERIFIED
`backend/serial_manager.py:514-525` — the gap guard `delta_seconds <= 2.0` silently
discards integration for any stream slower than 2 s/sample: live values display, but
`accumulated_volume_ul` stays exactly 0.0 forever. The guard conflates "stream died"
with "legitimately slow sample rate" (no rate-adaptive threshold).

### 2.4 Wave runner timing drift ~20–35% below planned frequency · VERIFIED
`backend/waveform_engine.py:588-604` — each step waits `duration` *after* the send
completes; send latency + Windows timer overshoot compound per step (measured with a
5 ms-per-write fake: planned 1000 ms → actual 1328 ms). No absolute-deadline re-anchoring.

### 2.5 CSV logging: headerless/misaligned CSV when logging starts before detection · VERIFIED
`backend/csv_logger.py:135-137` writes the header once from `series_labels` captured at
construction; `SensorsPage.start_logging` (`Sensors.py:1908-1912`) filters labels by
`metadata["available"]`, which is only True after 2 valid samples. Logging started
before detection produces a headerless CSV; sensors detected later get data columns
with no header rewrite. This is the root cause of the failing
`test_logging_queues_only_new_samples_and_does_not_use_a_log_timer`.

### 2.6 Auto-ROI becomes permanently "sticky" in user settings · VERIFIED
`backend/analytics/analyzer.py:88-94` mutates the working `config.roi = auto_roi`;
`Analytics.py:1009-1012` then persists `result.config` into settings.json. The next
auto-mode run early-returns (`analyzer.py:456-460`) because ROI ≠ full frame — the auto
band is never re-derived. One auto-mode analysis permanently narrows the stored ROI.

### 2.7 Sensor recovery: UI status text is misleading; backend retries are unbounded — LIKELY (partly refuted)
Production uses `initialize_liquid_flow()` with defaults (`monitor_stream=True`,
watchdog enabled — app.py:613-619; the earlier claim that the watchdog was
disabled was refuted: `open_and_start_liquid_flow`/`monitor_stream=False` at
`serial_manager.py:715` is test-only). Correction to the original finding: the
app path passes `max_attempts=None`, so the backend retries the full
acknowledged sequence **indefinitely** (2 s apart) while the port stays open —
it is not capped at 3 attempts. The real remaining defect is presentational:
`retry_active_stream` (`:393-406`) is referenced by zero UI code, and
`Sensors.py:1642-1644` overwrites an informative `Sensor error:` status with a
generic "Signal paused — reconnecting sensor stream…" after 5 s, so the UI can
claim recovery while the board is failing repeatedly. A watchdog retry also
zeroed accumulated volume before the fix in section 0.

### 2.8 Sensor detection state machine hardcodes `liquid_flow` and clobbers status · VERIFIED
`Sensors.py:1610,1614` retry/diagnostic events hardcode `"liquid_flow"`;
`:1621-1625` sets "Liquid-flow sensor connected" for any `sample.sensor_id`;
`:1642-1644` overwrites a more informative `Sensor error:` status with the generic
"Signal paused" after 5 s. Also `:1618-1619` uses one shared per-board sample counter
across all sensors.

---

## 3. MEDIUM

| # | Finding | Status | Evidence |
|---|---|---|---|
| 3.1 | Unbounded memory: `session_rows` appends every sample forever (logging or not); histories unbounded in Pause/Fit mode; csv_logger queue and serial events queue unbounded | VERIFIED | Sensors.py:1698-1709 (only trims histories when `chart.is_live_view`, :1690-1693), csv_logger.py:48, serial_manager.py:92 |
| 3.2 | Workspace wave panel re-reads + re-parses the wave-library JSON from disk every 140 ms and recomputes steps/stats; panels keep polling while the Workspace page is hidden (camera panel: full-res frame → QPixmap per 110 ms tick) | VERIFIED | Workspace.py:325-328, 1353, 1403-1407, 1453-1454, waveform_library.py:119-151 (no mtime check), 1889 |
| 3.3 | Per-tick restyle churn: Pumps unpolish/polish on both controls every 75 ms tick unconditionally; Workspace duplicates at 140 ms | VERIFIED | Pumps.py:111-114, 536-563; Workspace.py:594-599 |
| 3.4 | ACK wait holds `_command_lock` for the full timeout — every cross-thread sender blocks (root cause of freezes; init handshake blocks pump commands) | VERIFIED | serial_manager.py:185-204, 303-318 |
| 3.5 | Failed `close()` (POFF unacked ×2) leaves the stream running, state `closing`, port open, no reconnect path | VERIFIED | serial_manager.py:556-577 |
| 3.6 | `Pumps.create_amplitude_row` resets `channel_data["_operation_pending"] = False` on rebuild — a board switch mid-op clears the guard and permits a second concurrent hardware command; last-finished completion wins | VERIFIED | Pumps.py:473, 893-918 |
| 3.7 | `OpenCVCamera.open()` mutates capture state without holding `_lock` (races with `read()`/`close()` under a zombie worker) | VERIFIED | camera_service.py:500-568 vs 471-476 |
| 3.8 | One failed camera `read()` permanently kills the stream (no retry window) | VERIFIED | Camera.py:822-825 |
| 3.9 | `enumerate_cameras` uses SDK indices as DirectShow indices — wrong device probed when they diverge | LIKELY | camera_service.py:438-442, 395-410 |
| 3.10 | `dnx64_vendor.SetVideoProcAmp` argtypes wrong at declaration; only works because camera_service monkey-patches argtypes at runtime (fragile FFI); `GetVideoDeviceCount` performs an implicit second `Init()` per initialize (likely root cause of flaky-Init behavior) | VERIFIED | dnx64_vendor.py:45, 240-241; camera_service.py:272-275, 282 |
| 3.11 | Analytics cache directory collides by filename stem — two videos with the same stem overwrite each other's stored results | VERIFIED | result_store.py:32-34 (path check in load_cached prevents wrong reuse but not overwrite) |
| 3.12 | Broad `except Exception: continue` frame-skip loops in analyzer swallow all failures silently (corrupt frames, cv2 errors, memory) | VERIFIED | analyzer.py:498, 521, 612, 1050; detector.py:71 |
| 3.13 | `iter_frames` generator abandoned on cancel → video file handle stays locked until GC | VERIFIED | analyzer.py:134-137, video_source.py:97-98 |
| 3.14 | Analytics playback decodes + renders every frame on the UI thread; cancel blocks UI up to 3 s and can apply a stale worker's result to a newly loaded video | VERIFIED | Analytics.py:893-901, 807-812, 1003-1007 |
| 3.15 | Waveform live-frequency writes spawn untracked daemon threads with `except Exception: pass`; failures only surface via the 100 ms poll | VERIFIED | Waveform.py:679-691 |
| 3.16 | Late-ACK aliasing can desync the sensor-init retry state machine (stale OK advances DFOFF→L0→DFON sequence) | LIKELY | serial_manager.py:489-495, 301-318 |
| 3.17 | Unit-suffixed (`V=1.25 mL/min`) and `<<`-prefixed measurement lines are silently dropped by the parser (no telemetry when parsing fails) | LIKELY | protocol.py:110-115, 233, 249-262 |
| 3.18 | Camera: exposure verification holds `self._lock` up to ~440 ms (12×15 ms retries + 80 ms readback pass), blocking stop/close on the same lock | VERIFIED | camera_service.py:731, 748-752, 867-871 |
| 3.19 | Camera profiles: corrupt JSON silently discarded (no log/backup); FPS-profiling machinery is dead production code | VERIFIED | camera_profiles.py:49-58, observe_fps only referenced by tests |

---

## 4. LOW

- **4.1** `.bak` recovery leaves the corrupt primary in place and `save()` copies the corrupt target over the backup before the atomic write (session_manager.py:442-465) — LIKELY.
- **4.2** `dnx64_api.py` is dead code (144 LOC, zero references) — VERIFIED. Candidate for deletion.
- **4.3** `flow_capture.py`/`flow_filter.py`/`diagnostics.py`/`raw_serial_capture.py`/`sensor_stream_capture*.py` are tools/tests-only, not app-path (fine, but note).
- **4.4** Non-Highdriver4 wave steps not merged across cycle boundaries (redundant identical `P{n}V` write per cycle; stats differ per driver type) — VERIFIED, waveform_engine.py:388-392 vs 307-320.
- **4.5** Sensor retry resets detection but keeps stale displayed values (Sensors.py:1676+) — VERIFIED.
- **4.6** 5-bit amplitude quantization checked — **no bug found** (waveform_engine.py:114-149 round-trips correctly).
- **4.7** `PumpsPage.stop_all` is dead code; `docs/PUMP_WAVE_ENGINE.md:307` claims it's the shutdown path — stale doc.
- **4.8** `Workspace` panels mutate the same `_operation_pending`/`enabled`/`hardware_state` channel fields as Pumps — two independent pending-gate state machines over one shared flag (UI-state corruption risk; backend ownership prevents physical double-control).
- **4.9** Analytics worker threads parented but never `deleteLater`'d — minor.
- **4.10** `app.py:1224-1227` silent `except Exception: closed = False` at quit.
- **4.11** Tracker pipeline (stitching, dedup, event recompute, confidence) independently audited — **no defect found**.
- **4.12** `board["firmware"]` two-writer claim **refuted** — both derive from the same parse (`serial_manager.py:476-479` → `protocol.py:244-245`), GUI-thread only, converge to identical strings.

---

## 5. STALE TEST CONTRACTS (product decisions needed)

1. `test_backend_sample_reaches_history_card_and_graph` — expects active sensors to
   auto-plot; code deliberately requires explicit selection (`Sensors.py:1304-1305`,
   `:870`). Both contracts can coexist (auto-plot without auto-select) — needs a
   product call, then fix the test or the code.
2. `test_single_board_allows_two_and_keeps_selected_rows_enabled` — asserts row
   `isEnabled()` False when limited; code deliberately keeps rows enabled and conveys
   the limit via the `selectionLimited` QSS property (`Sensors.py:890-896`).
3. `test_logging_queues_only_new_samples…` — the test's injected metadata lacks
   `available`, but it exposes real bug 2.5. Fix both.

**Environment-only failures** (not bugs): `test_application_paths.py` ×2 — requires the
gitignored proprietary `vendor/dnx64/DNX64.dll` and a machine without a system DNX64
install. `dnx64_api.py` dead code aside, resolver precedence depends on machine state.

## 6. Test-suite observations

- 268 tests / 46 files; good backend coverage overall.
- ~10 test files assert on raw UI source text (substring/AST checks) — a systematic
  flake class that fails on refactors while passing through real behavior changes
  (e.g. `test_camera_performance_architecture.py`, `test_workspace_ux_parity.py`,
  magic-number pinning in `test_camera_exposure_validated_range.py:9-10`).
- Untested: `dnx64_api.py` (dead), `workspace/persistence.py` (real I/O monkeypatched
  away), `analytics/render.py`, `analytics/temporal.py` (no direct tests),
  `driver_frequency_state.py`, `sensor_stream_capture_live.py`.
- No concurrency test covers the reader thread vs `stop_sensor()` race (1.2).

## 7. Recommended fix order

1. ACK correlation token (1.1) — unblocks correctness of everything else on the serial path
2. Reader-thread reset race + volume semantics (1.2, 1.3, 2.3) — one coherent fix around `_last_flow_*`/`_volume_ul` ownership and gap-guard policy
3. Session save/load exception handling + global excepthook (1.5)
4. Camera QThread teardown safety (1.6) + `open()` lock scope (3.7)
5. CSV logger header rewrite (2.5) + submit/stop race (strands accepted samples, csv_logger.py:82-97, VERIFIED)
6. Analytics: skip `save_result` when incomplete (1.4), don't persist auto-ROI (2.6), cache-key hashing (3.11)
7. Wave runner absolute-deadline scheduling (2.4)
8. Performance: Workspace panel visibility gating (3.2), restyle churn (3.3), unbounded buffers (3.1)
9. Delete dead code: `dnx64_api.py`, `PumpsPage.stop_all` (+ doc fix)

Full worker reports archived in this session's task store; this file is the merged,
verified summary.
