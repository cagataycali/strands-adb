# API Reference

The full `adb` tool signature. One tool, 90+ actions, dispatched via `action="..."`.

```python
from strands_adb import adb

result = adb(action="screenshot")
```

All actions return:

```python
{
    "status": "success" | "error",
    "content": [{"text": "..."}, {"image": {...}}?],
    # action-specific extras
}
```

---

## Device

### `list_devices`

```python
adb(action="list_devices")
# Returns: {"devices": [{"serial": "...", "state": "device", "model": "..."}]}
```

### `select_device`

```python
adb(action="select_device", serial="59230DLCH0012Z")
```

Sets process-global default serial.

### `device_info`

```python
adb(action="device_info", serial=None)
# Returns model, manufacturer, Android version, SDK, serialno
```

### `battery`

```python
adb(action="battery", serial=None)
```

### `wake`

```python
adb(action="wake", serial=None)
```

### `unlock`

```python
adb(action="unlock", pin="1234", serial=None)
```

PIN is used once, not stored.

---

## Shell

### `shell`

```python
adb(action="shell", command="dumpsys activity top", serial=None, timeout=30)
```

Run arbitrary `adb shell <cmd>`.

---

## UI Input

### `tap`

```python
adb(action="tap", x=500, y=1500, serial=None)
```

### `swipe`

```python
adb(action="swipe", x1=500, y1=2000, x2=500, y2=500, duration_ms=300, serial=None)
```

### `type_text`

```python
adb(action="type_text", text="hello world", serial=None)
```

### `key`

```python
adb(action="key", key="back", serial=None)   # aliases + KEYCODE_*
```

### `back` / `home` / `recent`

```python
adb(action="back")
adb(action="home")
adb(action="recent")
```

### `gesture_long_press`

```python
adb(action="gesture_long_press", x=500, y=500, duration_ms=800, serial=None)
```

### `gesture_path`

```python
adb(action="gesture_path", points=[(100, 500), (300, 500), (300, 800)], duration_ms=500)
```

### `gesture_pinch`

```python
adb(action="gesture_pinch", cx=500, cy=1000, start_radius=200, end_radius=500)
```

### `gesture_stream`

```python
adb(action="gesture_stream", events=[...])
```

---

## Screen

### `screenshot`

```python
adb(action="screenshot",
    output_path=None,           # default: /tmp/adb_screenshot_<ts>.png
    serial=None,
    include_image=True,         # embed Converse image block
    return_base64=False)
```

Returns `{path, size_bytes, content: [text, image]}`.

### `screen_record`

Blocking fixed-duration recording (≤180s Android hard limit).

```python
adb(action="screen_record", duration_sec=30, output_path=None, serial=None)
```

### `screen_record_start`

Start a **non-blocking** background recording. Returns immediately so your
agent can keep acting. Auto-chains segments past Android's 180s cap.

```python
adb(
    action="screen_record_start",
    output_path="/tmp/run.mp4",
    screenrec_bit_rate_mbps=4,
    screenrec_size="720x1600",   # None = native resolution
    screenrec_segment_sec=180,
)
```

### `screen_record_stop`

Stop background recording. Pulls any remaining segment, optionally merges
all segments via `ffmpeg` if present.

```python
result = adb(action="screen_record_stop")
# {
#   "segments":   ["/tmp/run.mp4", "/tmp/run_seg002.mp4", ...],
#   "merged_path": "/tmp/run_merged.mp4",   # if ffmpeg available
#   "duration_sec": 412.3,
#   "total_bytes": 198_234_112,
# }
```

### `screen_record_status`

```python
adb(action="screen_record_status")
# {"running": True, "elapsed_sec": 47.3, "segments": [...], "output_path": "..."}
```

### `screen_frames`

```python
adb(action="screen_frames", duration_sec=10, fps=2, output_dir="/tmp/frames/")
```

### `video_frames`

```python
adb(action="video_frames", device_path="/sdcard/DCIM/Camera/VID.mp4", fps=2)
```

### `ui_dump`

```python
adb(action="ui_dump", serial=None)
```

Returns full UIAutomator XML.

### `ui_find`

```python
adb(action="ui_find",
    text=None, resource_id=None, content_desc=None,
    clickable=None, serial=None)
```

Returns list of matching nodes with bounds + attrs.

### `ui_tap_by`

```python
adb(action="ui_tap_by",
    text=None, resource_id=None, content_desc=None,
    index=0, serial=None)
```

### `ui_wait_for`

```python
adb(action="ui_wait_for",
    text=None, resource_id=None, content_desc=None,
    timeout_sec=10, serial=None)
```

### `smart_tap`

```python
adb(action="smart_tap",
    text=None, resource_id=None, content_desc=None,
    serial=None)
```

High-level semantic tap.

---

## Camera

### `camera_photo`

```python
adb(action="camera_photo",
    facing="back",              # "back" | "front"
    output_path=None,
    auto_pull=True,
    include_image=True,
    return_base64=False,
    timeout_sec=15,
    serial=None)
```

Returns `{path, device_path, size_bytes, content: [text, image]}`.

### `camera_video`

```python
adb(action="camera_video",
    duration_sec=10,
    facing="back",
    output_path=None,
    serial=None)
```

---

## Apps

### `list_packages`

```python
adb(action="list_packages",
    pattern=None,
    third_party=False,
    serial=None)
```

### `launch`

```python
adb(action="launch", package="com.whatsapp", serial=None)
```

### `kill`

```python
adb(action="kill", package="com.foo", serial=None)
```

### `install`

```python
adb(action="install", apk_path="/path/app.apk", serial=None)
```

### `uninstall`

```python
adb(action="uninstall", package="com.foo", serial=None)
```

### `clear_data`

```python
adb(action="clear_data", package="com.foo", serial=None)
```

### `current_app`

```python
adb(action="current_app", serial=None)
# Returns {"package": "...", "activity": "..."}
```

---

## Files

### `push`

```python
adb(action="push", local="/host/path", remote="/sdcard/x", serial=None)
```

### `pull`

```python
adb(action="pull", remote="/sdcard/x", local="/host/path", serial=None)
```

### `ls`

```python
adb(action="ls", remote="/sdcard/DCIM/", serial=None)
```

---

## Intents

### `open_url`

```python
adb(action="open_url", url="https://example.com", serial=None)
```

### `share_text`

```python
adb(action="share_text", text="Check this", serial=None)
```

### `start_activity`

```python
adb(action="start_activity",
    action_name="android.intent.action.VIEW",
    data=None, package=None, component=None,
    extras=None, serial=None)
```

---

## Sensors

### `sensors`

```python
adb(action="sensors", serial=None)
# Returns structured accelerometer, gyro, light, proximity, pressure, ...
```

### `thermals`

```python
adb(action="thermals", serial=None)
```

### `wifi_info`

```python
adb(action="wifi_info", serial=None)
```

---

## Settings

### `setting_get`

```python
adb(action="setting_get",
    namespace="global",       # "global" | "system" | "secure"
    key="airplane_mode_on",
    serial=None)
```

### `setting_put`

```python
adb(action="setting_put",
    namespace="system", key="screen_brightness", value="128",
    dry_run=False, serial=None)
```

### `setting_delete`

```python
adb(action="setting_delete", namespace="secure", key="...", serial=None)
```

### `setting_list`

```python
adb(action="setting_list", namespace="global", serial=None)
```

### `setting_dump`

```python
adb(action="setting_dump", namespace="system", serial=None)
```

### `set_ringer`

```python
adb(action="set_ringer", mode="silent", serial=None)  # silent | vibrate | normal
```

### `set_brightness`

```python
adb(action="set_brightness", value=128, serial=None)  # 0-255
```

### `set_bluetooth`

```python
adb(action="set_bluetooth", enabled=True, serial=None)
```

### `set_airplane_mode`

```python
adb(action="set_airplane_mode", enabled=True, serial=None)
```

---

## Logs / Notifications

### `logcat`

```python
adb(action="logcat", filter=None, lines=200, serial=None)
```

### `log_stream_start`

```python
adb(action="log_stream_start",
    filter="NotificationManagerService",
    topic="phone.notifications",
    serial=None)
```

### `log_stream_stop`

```python
adb(action="log_stream_stop")
```

### `log_stream_status`

```python
adb(action="log_stream_status")
```

### `notifications`

```python
adb(action="notifications", serial=None)
```

Raw dumpsys output.

### `notifications_parsed`

```python
adb(action="notifications_parsed", serial=None)
# Returns [{app, title, text, time}, ...]
```

### `dismiss_notifications`

```python
adb(action="dismiss_notifications", serial=None)
```

---

## Accessibility

### `accessibility_list`

```python
adb(action="accessibility_list", serial=None)
```

### `accessibility_toggle_service`

```python
adb(action="accessibility_toggle_service",
    service="com.google.android.accessibility.talkback/.TalkBackService",
    enabled=True, serial=None)
```

### `accessibility_system_action`

```python
adb(action="accessibility_system_action", id="home", serial=None)
# ids: home, back, notifications, quick_settings, ...
```

### `accessibility_captions`

```python
adb(action="accessibility_captions", enabled=True, serial=None)
```

### `accessibility_magnification`

```python
adb(action="accessibility_magnification", enabled=True, scale=2.0, serial=None)
```

### `accessibility_font_scale`

```python
adb(action="accessibility_font_scale", scale=1.3, serial=None)
```

### `accessibility_status`

```python
adb(action="accessibility_status", serial=None)
```

---

## Comms

### `dial`

```python
adb(action="dial", phone="+1234567890", call=False, serial=None)
# call=True auto-places the call, call=False just opens dialer
```

### `sms_compose`

```python
adb(action="sms_compose", phone="+1234567890", body="hi", serial=None)
```

### `media_control`

```python
adb(action="media_control", action="play", serial=None)
# actions: play | pause | next | previous | stop | play_pause
```

### `volume`

```python
adb(action="volume", action="up", serial=None)
# actions: up | down | mute
```

---

## Environment

| Var | Default | Purpose |
|-----|---------|---------|
| `ADB_BIN` | `adb` (PATH) | adb binary path |
| `ADB_SERIAL` | none | default device serial |

## Response Shape

```python
{
    "status": "success" | "error",
    "content": [                       # Converse API content blocks
        {"text": "human-readable summary"},
        {"image": {"format": "png" | "jpeg",
                   "source": {"bytes": b"..."}}},
    ],

    # action-specific fields (examples):
    "path": "/tmp/shot.png",
    "size_bytes": 284512,
    "devices": [...],
    "info": {...},
    "settings": {...},
    "events": [...],
}
```

## See Also

- [**Actions Overview**](guide/actions.md) — grouped intro
- [**Architecture**](architecture.md) — how the dispatch works
- [**GitHub source**](https://github.com/cagataycali/strands-adb/blob/main/strands_adb/adb_tool.py)
