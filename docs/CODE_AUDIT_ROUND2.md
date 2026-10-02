# FluidicStudio Code Audit — Round 2 (post-fix sweep)

Date: 2026-10-01 · Branch `main` · Baseline `1191f78` · Method: 20 parallel read-only
specialist agents over non-overlapping slices, cross-examined, deduplicated, and the
highest-severity claims re-verified by the orchestrator with executable repros.

Round 1 (`CODE_AUDIT.md`) fixed 30 backend defects. Round 2 was asked the opposite
question — *what is still wrong?* — and the answer is: a lot, mostly in the UI layer
and in the lifecycle code that was deliberately left out of scope. One **new critical
physical-safety bug** was found, reproduced, and fixed in this round.

---

## 0. Fixed in round 2

### P{n}ON can be written *after* POFF on disconnect — CRITICAL, reproduced and fixed

**Repro (executable, against a healthy board that ACKs everything):** start a manual
pump and hit Disconnect while the 4-command start transaction is in flight (the
amplitude ack is slow). Before the fix:

```
0.001s  F0=100
0.007s  CS0=0
0.012s  P1V120
0.613s  POFF
0.618s  P1ON        <-- pump ON after the global power-off
close() returned True; start_manual reported success=True, hardware_state='on'
```

The app believed the board was off and healthy while the board was driving the channel.

Cause: `close()` sends POFF without excluding in-flight transactions. Since the ACK
wait happens outside `_command_lock` (round-1 fix 3.4), a pump start could emit its
remaining `P{n}ON` after POFF. `force_release_after_disconnect` then wiped the
ownership the successful start had taken, so nothing even stayed flagged.

Fix: `backend/serial_manager.py` gained a **teardown gate**. `close()` now flips
`_accepting_commands = False` and emits POFF/DFOFF while holding `_command_lock`, so a
sender either completes its current step before the flip or is rejected on the next
one; the internal-write bypass is cleared *before* the lock is released. Post-fix the
same repro shows **no P1ON on the wire at all**, and the transaction correctly reports
`success=False, hardware_state='unknown'`. Regression test:
`tests/test_serial_manager.py::test_pump_start_transaction_cannot_write_on_after_poff`.
Verification: full suite `296 passed / 4 pre-existing failures`.

---

## 0b. Fixed in round 3 (backend-only scope)

Every finding below was re-validated by executable repro before it was accepted, and
several suspected findings were **refuted on inspection** rather than "fixed" — see the
last two bullets. UI-layer findings in sections 1–3 remain unfixed by design. Each fix
carries a regression test that fails against the pre-fix code.

**Serial transport** (`backend/serial_manager.py`)

- Failed write left a phantom ACK entry, so the next command's `OK` was consumed by a
  dead entry and the real command was falsely reported unacknowledged.
- The stale-reply prune could discard an entry whose waiter was still blocked, hanging
  it to timeout even though the board had answered. Entries are now only pruned when
  abandoned or waiter-less.
- The ACK backlog survived `close()`, so a reconnect inherited the previous session and a
  healthy board reported "DFOFF was not acknowledged" forever. Cleared on teardown.
- An unsolicited boot/firmware line popped whatever was at the head of the queue.
  Identification now pops only its own entry.
- `request_firmware_and_wait()` was released by *any* banner, including a stale power-on
  one; it is now correlated like every other command.
- Measurement counting was sensor-agnostic, so a pressure stream satisfied the
  liquid-flow readiness check, and any measurement forced `STREAMING`. Both are now
  per-sensor.
- The `events` queue and the reader's unframed byte buffer were both unbounded — a
  stalled consumer or a device that stopped emitting newlines grew memory without
  limit. Both are bounded, with the loss counted rather than silent.

**Session durability** (`session_manager.py`, `session_runtime.py`)

- `shutil.copy2` truncated the existing `.bak` before writing, so a failed backup copy
  destroyed the last good copy. Backups now rotate atomically.
- A failed target `replace()` discarded the staged payload, leaving primary and backup
  holding the same revision. The failure is now surfaced and no revision is lost.
- The pre-rotation guard checked JSON *syntax*, not loadability, so an unreadable primary
  was rotated over a good backup.
- `build_board_profile` emitted unclamped live values, producing sessions that
  `validate_session` rejected — a captured session could become unsaveable.
- `apply_board_profile` raised `KeyError` on an out-of-range `driver_index`.

**CSV logger** (`csv_logger.py`)

- `stop()` discarded a queued sample to make room for its sentinel without counting it,
  so the loss was invisible in `dropped_samples`.
- A backward clock step collapsed the interval export (50 samples → 1 row).
- Two sensors of the same type on one board shared a column and could not be attributed.
- A worker restart could resurrect `running` after `stop()` had already drained.

**Wave** (`waveform_library.py`, `wave_execution.py`)

- `WaveformLibrary.load()` cleared the in-memory library and pinned the mtime stamp
  *before* the read succeeded, so one transient failure blanked it permanently and the
  next `save()` would have deleted every stored waveform. It now commits on success.
- An orphaned runner's `finally`-OFF was routed by channel number to the *next* wave on
  that channel, switching a channel off mid-wave and releasing its ownership. Routing is
  now identity-checked.
- `start()` keyed executions by the raw channel argument while ownership normalised with
  `int()`, so `start(channel="2")` registered under a key nothing else could find.

**Camera** (`camera_service.py`, `camera_profiles.py`)

- **ABBA deadlock** between `_lock` and `_property_verify_lock` — reproducible; two
  threads taking the locks in opposite order wedged permanently, and the app could then
  only be killed via Task Manager. One canonical order is now declared and enforced by an
  AST test.
- Exposure verification held `_lock` across a ~440 ms retry/readback loop, blocking
  `close()`; measured `close()` during verification dropped from 257 ms to 11 ms.
- `close()`/`shutdown()` were unbounded (~56.5 s worst case). Now bounded and observable.
- `is_open` itself blocked ~4.8 s behind a wedged owner.
- Profile saves were neither durable nor temp-file-safe, and used an inconsistent device
  key (`str()` in most methods, raw in others), splitting one device's calibration across
  two keys.
- A single wrong-shape record inside an otherwise valid profile (`width: null`,
  `baseline_fps: "abc"`, `fps_samples: "30"`, a non-numeric `version`) crashed the store
  during camera startup.

**Workspace persistence** (`persistence.py`)

- `load_workspace_settings` raised an uncaught `AttributeError` on valid-JSON non-dict
  payloads, taking down app startup.
- Corrupt or wrong-schema settings silently wiped the layout and every per-preset ratio.
  The rejected file is now quarantined as `.corrupt` before falling back to defaults.
- Writes were neither durable nor temp-file-safe.

**Analytics** (`analyzer.py`, `detector.py`, `result_store.py`, `video_source.py`)

- A truncated video was reported `complete=True` and cached with a wrong droplet count.
- `load_settings` raised an uncaught `AttributeError` on a non-dict payload.
- `build_background` silently dropped undecodable samples, biasing the median background;
  it now counts them, and a too-degraded background demotes the run to incomplete.
- `_estimate_flow_direction` fabricated `(1.0, 0.0)` — "flow is left-to-right" — when it
  had zero evidence. It now returns unknown, which is visible in the result and blocks
  caching. `ANALYZER_VERSION` was bumped so results cached by the fabricating code are
  not replayed.
- The same counted-but-ungated hole was closed for channel-ROI, flow, and event-geometry
  frame reads.
- Total background sample loss raised a bare `RuntimeError` instead of returning a
  demoted result.

**Pump control** (`pump_control.py`)

- `start_manual` claimed channel ownership *before* validating its arguments, so a purely
  client-side rejection (e.g. a channel that does not belong to the driver) permanently
  claimed the channel and reported `hardware_state="unknown"` — which the UI renders as
  pump ON-unknown. Validation now precedes the claim.

**Refuted on inspection — deliberately not "fixed"**

- `start_manual`/`force_release` raising `ChannelOwnershipError` where a result was
  expected: **does not reproduce.** `start_manual` already converts contention into a
  result, and no `force_release` method exists on the service.
- The sha-1 result-cache orphaning and the `split_ratio` "no-op" are real but were left
  alone: the former is a one-time migration, the latter is an intentional documented
  design (`validated()` deliberately resets to 0.5).

Verification: full suite **390 passed / 4 pre-existing failures**, deterministic across
repeated runs including `-p no:randomly`. pyflakes 8 → 7 findings vs the baseline (no new
findings). The 4 failures are unchanged: two assert a gitignored `vendor/dnx64/DNX64.dll`
exists, two are stale Sensors-page UI contracts.

---

## 1. Critical — physical state, crashes, silent data loss (UI layer, unfixed)

These are outside round 1's backend-only scope and remain open.

| # | Finding | Evidence | Agent status |
|---|---|---|---|
| C1 | **"Disconnect blocked / board remains connected" is shown after the port is already closed.** `app.py:641-647` still treats `close()==False` as "still connected"; the board is left in `connected_boards` with a dead port, and the promised retry sends zero bytes and force-releases ownership. | `app.py:641-647`, `serial_manager.py:661-665` | VERIFIED (3 agents) |
| C2 | **Starting a wave on one channel retunes a sibling channel of the same driver.** `pump_start_commands` emits `F<d>=`/`CS<d>=` for the whole group with no group-idle guard; the UI guard is per-channel only. | `protocol.py:223-231`, `Waveform.py:738-772` | VERIFIED |
| C3 | **ABBA deadlock between `_lock` and `_property_verify_lock`** freezes the GUI permanently; reachable via two `CameraWorker`s (no guard on start). | `camera_service.py:742/748` vs `941`, `Camera.py:1423` | VERIFIED (reproduced) |
| C4 | **`start_manual` claims ownership before validating the sequence** — a rejected request leaves the channel owned forever with a false "unknown" state. | `pump_control.py:70` vs `76-86` | VERIFIED |
| C5 | **Camera ABBA + orphaned worker → the app can only be killed via Task Manager.** Stop calls `close()` with no timeout while the worker holds the lock. | `Camera.py:1478-1481`, `camera_service.py:1088` | VERIFIED |
| C6 | **Any unhandled exception in a Qt slot/`QThread.run` aborts the process (`0xC0000409`).** `_apply_pending_properties` and the Workspace wave panel's `library.load()` are unguarded. | `Camera.py:709-744`, `Workspace.py:1407` | VERIFIED (reproduced) |
| C7 | **Droplet counting silently wrong (3 tracker defects):** velocity fuses two non-independent estimators (15× error); no backward/identity gate lets a stale track swallow the next droplet; a dropout longer than the stitch window double-counts one droplet as two. | `tracker.py:834-847`, `:141-196`, `:357` | VERIFIED (synthetic repros) |
| C8 | **`temporal.py` merges genuinely distinct droplets <150 ms apart** → undercount; contradicts the analyzer's own stated intent. | `temporal.py:130-133` | VERIFIED |

## 2. High — data loss, wrong results, UI lockup (unfixed)

- **Analytics:** `_update_graph` `UnboundLocalError` on any zero-measurement result (crash);
  cancelling orphans a worker whose stale result overwrites a *different* video's ROI and
  calibration; opening a resolution-incompatible video silently wipes the user's calibration;
  a cancelled run is presented/exported as complete (`Analytics.py:1070`, `:1003`, `:777`, `:978`).
- **Sensors:** `logger.submit()` return value discarded → CSV overflow drops samples silently;
  detection state never invalidated on teardown → a dead board plots as live; `"Signal paused"`
  masks the specific error status; hardcoded "Liquid-flow" for every sensor
  (`Sensors.py:1953`, `:1642-1644`, `:1621`).
- **CSV/session:** `stop()` can discard a queued sample uncounted; a backward clock step
  collapses the export; `shutil.copy2` failure destroys the last-good `.bak`
  (`csv_logger.py:135-145`, `session_manager.py:534`).
- **Wave:** a transient stall collapses the remaining amplitude staircase into zero-dwell
  holds (absolute-deadline interaction); `WaveformLibrary.load()` permanently blanks the
  library on a transient read failure → next `save()` deletes all saved waves
  (`waveform_engine.py:617-619`, `waveform_library.py:137-149`).
- **Camera:** recorder error never detaches the recorder → Record button dead for the
  session; `stop_camera` leaves controls enabled and moves a stale slider after close.
- **Workspace:** panel teardown orphans `_operation_pending` → pumps page bricked;
  worker-thread reads of QWidget state; shared `_operation_pending` clobbered across pages.
- **Workspace persistence:** `load_workspace_settings` `AttributeError` on valid-JSON
  non-dict crashes app startup; `set_split_ratio` is a silent no-op (`validated()` force-resets
  to 0.5); corrupt settings silently wipe the layout.
- **Analytics cache:** sha1 directory orphans every pre-existing on-disk cache; `skipped_frames`
  is computed but consumed by nothing; truncated video cached as `complete=True`.
- **Other:** `driver_config.json` malformed → **app crashes at import before the GUI exists**;
  `start_manual`/`force_release` raise `ChannelOwnershipError` where a result is expected.

## 3. Medium — degraded behaviour, leaks, tooling truthfulness (unfixed)

- GUI-thread blocking: measured **12.0 s** `stop_all` + 2.05 s `close` on one board; camera
  `shutdown()` bounded worst case ~56.5 s; session/hub history and CSV flush races at exit.
- `_measurement_count` is sensor-agnostic (a pressure stream satisfies the liquid-flow
  two-sample check); `events` queue unbounded; reader buffer grows without bound without `\n`.
- Two stale-ACK-sensitivity issues in the FIFO: an unsolicited boot line can pop a live entry;
  a lost reply can cascade failures until the 5 s prune (documented, accepted).
- `_update_graph`/legend/repolish churn; unbounded `session_rows` (≈14 MB/50k rows);
  `_operation_pending` reset on rebuild; `PumpsPage.stop_all` dead code.
- **Tools lie about being read-only:** `tools/dnx64_probe.py` and `camera_backend_smoke_test.py`
  write AE/exposure/resolution; `tools/serial_capture.py` always sends POFF; three tools crash
  on their own documented invocation (`ModuleNotFoundError`); exposure diagnostics write into
  the source tree and never restore auto-exposure.
- **Test suite:** `test_pump_command_architecture.py` asserts on a tuple the product discards;
  `test_workspace_*` lock the `split_ratio=0.5` bug as the contract; ~10 source-string test
  files pass even if the referenced code is commented out; 2 tests assert a gitignored vendor
  DLL exists so a clean clone is structurally red.
- **Docs vs code:** `SOFTWARE_ARCHITECTURE.md` still says "keep connection open and return
  False" and "writes cannot interleave"; `BOARD_COMMUNICATION.md` still says the fixed 2 s gap
  rule; seven docs still claim the Analytics cache/ROI bugs that were fixed; `skipped_frames`
  claimed as "surfaced" but isn't; `CODE_AUDIT.md` count/ruff claims partly unverifiable.

## 4. Refuted / not reported (recorded so they aren't re-hunted)

Several plausible-looking leads were disproven by execution and are **not** defects:
FIFO head attribution under in-order replies with `timeout < STALE_REPLY_SECONDS`; `open()`
re-entrancy (RLock, no nested non-reentrant lock); shared-frame aliasing in the camera
(publish swaps the ref, every consumer copies); `VideoWriter` handle leak; co-visible
droplet stitching (the `gap_frames<=0` guard is correct); `SensorDataHub` concurrent
modify (snapshot-then-invoke avoids it); tracking/velocity unit consistency; no division-by-zero
or NaN in the fusion math; `numpy 2` API removals (none present).

## 5. Verification state

- Full suite after the round-2 fix: **296 passed, 4 failed, 4 subtests passed**.
  The 4 failures are the known pre-existing ones (2 environment: missing gitignored
  `vendor/dnx64/DNX64.dll`; 2 stale UI-contract tests in `test_ui_sensor_integration.py`).
- The critical late-ON-after-POFF bug was reproduced, fixed, and covered by a regression test
  that fails without the fix.

## 6. Recommended fix order (next rounds, all verified with repros)

1. **C1** — teach `app.py` the new `close()` contract (one function, removes the
   "board remains connected" lie and the double-click lockout).
2. **C6 + C5** — wrap every Qt slot/worker entry in a top-level `except`; add a global
   `sys.excepthook`; fix the camera `_camera_opened` worker guard and the ABBA lock order.
3. **C2 + C4** — add the driver-group-idle guard to `start_wave`/`start_manual`; move the
   ownership claim after sequence validation.
4. **C7 + C8** — fix the tracker association/backward gate, the double-estimator velocity,
   the stitch/dropout window, and the temporal merge spacing.
5. **Analytics** crash + calibration-wipe + orphaned-worker class.
6. **CSV/session** durability class (`stop()` discard, backward clock, `copy2` poisoning).
7. **Test suite** — delete the tests that lock in bugs, add real coverage for the round-2
   regressions, and unblock the two vendor-DLL tests so a clean clone is green.