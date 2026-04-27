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
1. 🟢 **Frontier #5 — Physical camera** ← START HERE
2. 🟢 **Frontier #3 — Logcat event stream → event_bus**
3. 🟢 **Frontier #13 — Settings mutation**
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
- [ ] Next: Cycle 1 — implement `camera_photo` action

---

## Acceptance criteria per feature

### Frontier #5 — Physical Camera
- [ ] `adb(action="camera_photo", facing="back", auto_pull=True)` opens Camera app
- [ ] Auto-snaps via KEYCODE_CAMERA
- [ ] Pulls latest DCIM file via adb pull
- [ ] Returns Converse API image block (same pattern as screenshot)
- [ ] `facing="front"` supported via intent extra or tap
- [ ] `camera_video(duration_sec=5)` records + pulls mp4
- [ ] Test: `tests/test_camera.py` — takes a real photo, verifies file exists + image block
- [ ] Tagged v0.4.0

### Frontier #3 — Logcat Stream
- [ ] `log_stream_start(filters=["*:W"])` launches background thread
- [ ] Parser extracts structured events (crash, notification, battery)
- [ ] Pushes to devduck `event_bus` under `phone.log.*`
- [ ] `log_stream_stop()` cleanly kills subprocess
- [ ] Test: triggers a notification, verifies event appears on bus
- [ ] Tagged v0.5.0

### Frontier #13 — Settings Mutation
- [ ] `setting_get(namespace, key)` / `setting_put(...)`
- [ ] Presets: `airplane_mode(on)`, `bluetooth(on)`, `brightness(0-255)`, `ringer(mode)`
- [ ] Guard dangerous ops with confirmation
- [ ] Tests
- [ ] Tagged v0.6.0

---

## Blockers
*(none yet)*

---

## Version tags shipped
- v0.1.0 — scaffold
- v0.2.0 — screenshot → image block
- v0.3.0 — smart UI + sensors + thermals + comms

## Stats
- Cycles completed: 0 / 100
- Frontiers shipped: 0 / 13
- Actions in tool: 67
