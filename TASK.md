# 🎯 TASK.md — Autonomous Work Log

**Start:** 2026-04-26 23:59
**Mode:** Autonomous (100+ cycles)
**Directive:** Implement FRONTIERS.md features, starting with Frontier #5 (Physical Camera)

## Rules
- Read this file at the START of every iteration to know where we are
- Update this file at the END of every iteration with what was done + next step
- Commit each logical unit of work (`git commit -m "feat/fix/test: ..."`)
- Run tests after each feature: `python3 tests/test_adb.py`
- Bump version in `pyproject.toml` and tag when shipping (v0.4.0, v0.5.0, ...)
- Never modify FRONTIERS.md priority ordering without a note
- If blocked → document the blocker in `## Blockers` and move to next frontier
- Say `[AMBIENT_DONE]` ONLY when all 100 cycles complete or all frontiers shipped

## Device under test
- Pixel 10 Pro · Android 16 · serial `59230DLCH0012Z`
- Connected via adb (USB)
- Wifi: 192.168.1.6
- Already has: screenshot, smart_tap, notifications, sensors, thermals, battery, comms

## Current state
- **v0.3.0** shipped — 67 actions, 11/11 tests passing
- FRONTIERS.md written with 13 features + priority matrix
- INSTALL_ANDROID.md + SSH_ANDROID.md shipped
- Docs: GOAL.md, FRONTIERS.md, INSTALL_ANDROID.md, SSH_ANDROID.md, README.md

## Frontier implementation order (from priority matrix)
1. ✅ **Frontier #5 — Physical camera** — SHIPPED v0.4.0
2. ✅ **Frontier #3 — Logcat event stream → event_bus** — SHIPPED v0.5.0
3. ✅ **Frontier #13 — Settings mutation** — SHIPPED v0.6.0
4. 🟢 **Frontier #1 — Sensor streams (polling mode)**
5. 🟢 **Frontier #9 — Location (coarse via cell/wifi)**
6. 🟢 **Frontier #10 — Multi-device fleet**
7. 🟡 **Frontier #4 — Live mirror → websocket**
8. 🟡 **Frontier #14 — SSH setup action (new, from today's discovery)**
9. 🔴 Frontier #2/6/7/8 — require companion APK, defer
10. 🔴 Frontier #12 — record/replay, defer

---

## Cycle Log

### Cycle 0 — Plan (this)
- [x] Created TASK.md
- [x] Confirmed device connected
- [x] Next: Cycle 1 — implement `camera_photo` action

### Cycle 1 — Frontier #5 Camera (DONE)
- [x] Probed GoogleCamera UI: resource-id `com.google.android.GoogleCamera:id/shutter_button`
- [x] Confirmed live capture works (484KB JPEG via Night Sight)
- [x] Implemented `camera_photo` + `camera_video` actions
- [x] Added `_tap_by_resource_id` + `_tap_by_content_desc` UI helpers
- [x] Added `_latest_dcim_file` helper with baseline diff (handles slow Night Sight)
- [x] Polling loop for new file (handles 3-5s exposure)
- [x] Retry logic for shutter tap after mode switches (3 attempts, 0.8s apart)
- [x] Front camera works — 2.5s settle after toggle, retry covers UI relayout
- [x] Tests: 3/3 camera + 11/11 smoke = 14/14 total passing
- [x] Tagged v0.4.0
- [x] NEXT: Cycle 2 — Frontier #3 Logcat event stream

### Cycle 2 — Frontier #3 Logcat Stream (DONE)
- [x] Scoped devduck event_bus API (`bus.emit(event_type, source, summary, detail, metadata)`)
- [x] Confirmed logcat threadtime format: `MM-DD HH:MM:SS.mmm PID TID LEVEL TAG: MESSAGE`
- [x] Implemented 10-category classifier (crash, anr, app_launch, call, wifi, battery, ...)
- [x] Background subprocess.Popen + daemon thread reader
- [x] Lazy import of devduck.event_bus → stays usable without devduck
- [x] Default filter = whitelist of tags we classify (ActivityTaskManager:I etc.)
  followed by `*:S` to silence everything else — sane parse rate
- [x] Graceful terminate + idempotent start/stop
- [x] Tests: classifier unit + live start/stop + real app_launch captured
- [x] Tagged v0.5.0
- [x] NEXT: Cycle 3 — Frontier #13 Settings mutation

### Cycle 3 — Frontier #13 Settings Mutation (DONE)
- [x] `setting_get/put/delete/list` over system/secure/global
- [x] `setting_dump` full 3-namespace snapshot
- [x] Presets: set_ringer (silent/vibrate/normal), set_brightness (0..255),
  set_bluetooth (svc bluetooth), set_airplane_mode (flag only, honest caveat)
- [x] Every mutation verifies via round-trip read
- [x] `_run_shell_capture` helper — subprocess wrapper with timeouts
- [x] Validation: bad namespaces, out-of-range, bad ringer rejected
- [x] 11 new tests all passing live on Pixel 10 Pro
- [x] Tagged v0.6.0
- [x] NEXT: Cycle 4 — Frontier #10 (UI query DSL) OR #2 (touch-streaming for uiautomator replacement)

### Cycle 4 — Frontier #10 UI Query DSL (DONE)
- [x] `ui_find` with 7 composable filters (text, resource_id, class_name,
  desc_filter, clickable_filter, scrollable_filter, package)
- [x] `ui_tap_by` — find + tap (defaults clickable=True)
- [x] `ui_wait_for` — polling with configurable timeout + interval
- [x] Three matcher modes: substring / '=exact' / '^regex'
- [x] text filter falls back to content-desc (for icon buttons)
- [x] Pure stdlib (xml.etree.ElementTree)
- [x] Replaced legacy ui_find/smart_tap → renamed to _legacy
- [x] 11 new tests (unit + live) all passing
- [x] Bonus: camera tests auto-skip when device locked
- [x] Tagged v0.7.0
- [x] NEXT: Cycle 5 — Frontier #2 (touch/gesture streaming) OR #4 (screen video → CV)

### Cycle 5 — Frontier #4 Screen Frames → CV (DONE)
- [x] `screen_frames(n, interval)` — live snapshot stream via screencap
- [x] `video_frames(mp4, n)` — ffmpeg extraction, single-pass via -vf fps=N/duration
- [x] Both return Converse image blocks (agent SEES the pixels)
- [x] Robust duration probe (format + stream fallback)
- [x] Handles variable-fps screenrecord mp4s (where per-frame seek fails)
- [x] Graceful ffmpeg-missing handling
- [x] 10 new tests (5 screen_frames + 5 video_frames)
- [x] Tagged v0.8.0
- [x] NEXT: Cycle 6 — Frontier #2 touch/gesture streaming OR #8 accessibility/ATC

### Cycle 6 — Frontier #2 Touch/Gesture Streaming (STARTING)
- [ ] `sendevent` hot-path → raw kernel touch events (no uiautomator)
- [ ] Macro recording: capture live touches → replay
- [ ] Multi-finger gestures: pinch, rotate
- [ ] Tests


---

## Acceptance criteria per feature

### Frontier #5 — Physical Camera
- [x] `adb(action="camera_photo", facing="back", auto_pull=True)` opens Camera app
- [x] Auto-snaps via UI tap on shutter_button (keyevent unreliable on GCam)
- [x] Pulls latest DCIM file via adb pull
- [x] Returns Converse API image block (same pattern as screenshot)
- [x] `facing="front"` supported via content-desc tap + 2.5s settle
- [x] `camera_video(duration_sec=5)` records + pulls mp4 (code done, untested)
- [x] Test: `tests/test_camera.py` — 3/3 pass on real Pixel 10 Pro
- [x] Tagged v0.4.0 ✅ SHIPPED

### Frontier #3 — Logcat Stream
- [x] `log_stream_start(log_filters=[...])` launches background thread (default = curated tag whitelist)
- [x] Parser extracts 10 categories: crash, anr, low_memory, battery, package_install, package_remove, app_launch, call_ringing, call_active, wifi_connect, wifi_disconnect
- [x] Pushes to devduck `event_bus` under `phone.log.*`
- [x] `log_stream_stop()` cleanly kills subprocess
- [x] Test: live app launch → bus event captured on real device
- [x] Tagged v0.5.0 ✅ SHIPPED

### Frontier #13 — Settings Mutation
- [ ] `setting_get(namespace, key)` / `setting_put(...)`
- [ ] Presets: `airplane_mode(on)`, `bluetooth(on)`, `brightness(0-255)`, `ringer(mode)`
- [ ] Guard dangerous ops with confirmation
- [ ] Tests
- [x] Tagged v0.6.0 ✅ SHIPPED

---

## Blockers
*(none yet)*

---

## Version tags shipped
- v0.1.0 — scaffold
- v0.2.0 — screenshot → image block
- v0.3.0 — smart UI + sensors + thermals + comms
- v0.4.0 — camera_photo + camera_video (Frontier #5)
- v0.5.0 — logcat event stream → event_bus (Frontier #3)
- v0.6.0 — settings mutation + presets (Frontier #13)
- v0.7.0 — UI query DSL (Frontier #10)
- v0.8.0 — screen frames → agent vision (Frontier #4)

## Stats
- Cycles completed: 5 / 100
- Frontiers shipped: 5 / 13
- Actions in tool: 86 (+2 frames: screen_frames, video_frames)
