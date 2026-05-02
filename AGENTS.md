# AGENTS.md — strands-adb

Guidance for AI agents using `strands-adb` to control Android devices.

Tested: Pixel 10 Pro / Android 16 / SDK 36 / v0.19.x

---

## Core principles

1. **One tool, many actions.** Everything dispatches through `adb(action="...", ...)`. Check the docstring for the full action list.
2. **Work in small steps.** Screenshot → find → tap → wait → verify. Don't chain 10 steps blindly.
3. **Verify foreground before tapping.** `foreground_info` tells you what's actually in front. `tap(x,y)` has no safety net — stale coordinates silently launch the wrong app.
4. **Prefer selectors over coordinates.** `tap_element(ui_text_contains=...)` survives UI resizing; `tap(411, 726)` does not.
5. **HTML-unescape UI text.** UI dump returns `&amp;`, `&lt;`, etc. Always unescape before matching or displaying.

---

## Recommended action flow

### Unlock + get to known state
```python
adb(action="is_locked")                       # check state
adb(action="unlock", pin="1071")              # only if locked=True
adb(action="home")                            # known baseline
adb(action="foreground_info")                 # verify launcher is up
```

### Launch an app
```python
adb(action="app_launch", ui_app_name="settings")  # fuzzy resolve
adb(action="wait_for_window", ui_window_package="com.android.settings", ui_timeout=5)
```

### Find + tap an element
```python
# Preferred: text_contains + explicit package scope
adb(action="tap_element",
    ui_text_contains="Network",
    ui_package_sel="com.android.settings",
    ui_clickable=True,
    ui_timeout=5)
```

### Text entry
```python
adb(action="type_into",
    ui_resource_id="com.app:id/search",
    ui_input_text="hello world",
    ui_clear=True)
```

### Screenshots → vision
```python
adb(action="screenshot", output_path="/tmp/x.png")
# returns a Converse image block — you CAN see the pixels.
```

### Screen recording
```python
adb(action="screen_record", duration_sec=5, output_path="/tmp/x.mp4")     # sync, simple
adb(action="screen_record_start", output_path="/tmp/y.mp4")               # background
# ... interact ...
adb(action="screen_record_stop")
```

### Camera
```python
adb(action="camera_photo", facing="back", output_path="/tmp/photo.jpg")   # image block returned
# Note: Pixel shutter sound is region-locked, cannot be suppressed.
```

---

## Selector matrix (v0.19.x — read carefully)

| Selector kwarg              | `find_element` | `find_elements` | `tap_element` | Notes                      |
|-----------------------------|:--------------:|:---------------:|:-------------:|----------------------------|
| `ui_text` (exact)           | ✅             | ✅              | ✅            | Case-sensitive             |
| `ui_text_contains`          | ✅             | ⚠️ unreliable   | ⚠️ unreliable | Use `find_element` then tap-by-coord as fallback |
| `ui_resource_id`            | ✅             | ✅              | ✅            | Prefer this when stable    |
| `ui_resource_id_contains`   | ✅             | ✅              | ✅            |                            |
| `ui_content_desc`           | ✅             | ✅              | ✅            | Best for a11y              |
| `ui_content_desc_contains`  | ✅             | ✅              | ✅            |                            |
| `ui_class_name`             | ✅             | ✅              | ✅            | e.g. `android.widget.Button` |
| `ui_clickable` / `ui_scrollable` / `ui_focusable` / `ui_enabled` / `ui_checked` | ✅ | ✅ | ✅ | Boolean filters            |
| `ui_package_sel`            | ✅             | ✅              | ✅            | Scope by app package       |

**Known limitation (v0.19.x):** `find_elements` and `tap_element` honor `ui_text_contains` inconsistently across some API-36 devices. Workaround: use `find_element` to locate, capture `bounds`, then call `tap(x, y)` on the returned center. Being fixed in v0.20.0.

---

## Timing & waits

Default `ui_timeout=5.0s, ui_poll_interval=0.5s, ui_quiet_ms=500`.

- `wait_for_element(...)` — block until selector matches
- `wait_for_gone(...)` — block until selector disappears
- `wait_for_window(ui_window_package=...)` — block until target app is focused
- `wait_for_idle(ui_quiet_ms=...)` — block until UI tree stops changing

**Pixel 10 Pro / Android 16 tuning:** `wait_for_idle` is aggressive by default because of fluid animations. If you see "polled 3x" timeouts, raise `ui_timeout` to 8s and/or lower `ui_quiet_ms` to 300. Don't block on idle before launching heavy apps — use `wait_for_window` instead.

---

## Common pitfalls

### 1. Stale coordinate taps
```python
# BAD
adb(action="tap", x=411, y=726)           # was valid for Settings, but Settings is backgrounded now
# GOOD
info = adb(action="foreground_info")
assert info["package"] == "com.android.settings"
adb(action="tap_element", ui_text_contains="Network", ui_timeout=5)
```

### 2. HTML-escaped error messages
If you see `no match for (text='Network &amp; internet')`, the target text has `&`, `<`, or `>`. Always pass the plain-text form (`Network & internet`), not the escaped form.

### 3. `screen_record_start` variants
Always pair `screen_record_start` with `screen_record_stop`. If `screen_record_start` errors inside your agent loop but direct invocation works, use the sync `screen_record(duration_sec=...)` variant — same output, no background PID management.

### 4. `ui_dump` timeouts
If you see `uiautomator dump` timeouts on heavy pages, retry once. Default tool timeout is 10s; bump with `timeout=20` on the top-level `adb` call.

---

## Observability actions (safe, read-only)

These never mutate device state — use liberally to orient before acting:

- `device_info`, `battery`, `current_app`, `foreground_info`, `is_locked`
- `list_packages(filter_text=...)`, `list_devices`
- `wifi_status`, `wifi_scan`, `bt_status`, `airplane_mode_get`
- `security_posture`, `security_lock`, `security_biometrics`, `security_vpn`
- `power_status`, `power_thermal`, `power_consumers`, `power_subsystems`
- `sensors_list`, `sensors_recent`, `sensor_get(sensor_query="light")`
- `accessibility_status`, `accessibility_list`
- `notifications_list`, `notifications_stats`
- `media_sessions_list`, `media_now_playing`, `media_volume_get`
- `logcat(filter_text=..., lines=50)`, `ui_dump`, `screenshot`

---

## When something fails

1. `screenshot` → look at the pixels.
2. `foreground_info` → what app is actually in focus?
3. `ui_dump` → raw XML of the current view hierarchy.
4. `logcat(filter_text="<package>", lines=100)` → last 100 log lines for the app.
5. Only THEN retry with adjusted selector / timeout.

---

## Session hygiene

At the end of any agent run that used adb:
```python
adb(action="screen_record_stop")              # idempotent, safe if no recording
adb(action="log_stream_stop")                 # if you started one
adb(action="home")                            # leave device on launcher
adb(action="sleep")                           # optional: turn screen off
```

---

## Versioning

- **v0.19.x** — background screen recording, Android 16 support, 113 action params
- **v0.20.0** (planned) — selector matrix unification, `scroll_to_element`, `perfetto_trace_*`, `app_state`, `a11y_inspect`

Full bug list & roadmap tracked in GitHub Issues.

---

*Document maintained by DevDuck autonomous agent. Last verified: 2026-05-02 (Pixel 10 Pro / Android 16 / SDK 36).*
