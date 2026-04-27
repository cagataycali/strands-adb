# UI Automation

Low-level UI introspection + interaction. If [`smart_tap`](smart-tap.md) isn't doing what you need, drop down to these.

---

## The UIAutomator Tree

Android exposes every on-screen element via `uiautomator dump`. Each node has:

- `text` — visible label
- `resource-id` — developer-assigned ID
- `content-desc` — accessibility label
- `class` — widget type (Button, EditText, ImageView, ...)
- `bounds` — `[x1,y1][x2,y2]` rectangle
- `clickable`, `focusable`, `enabled`, `checked`

## Core Actions

### `ui_dump`

Raw XML of the current screen:

```python
result = adb(action="ui_dump")
# result["content"][0]["text"] = "<hierarchy>...</hierarchy>"
```

Use for debugging when `smart_tap` misses.

### `ui_find`

Find matching nodes without tapping:

```python
adb(action="ui_find", text="Send")
adb(action="ui_find", resource_id="com.whatsapp:id/send")
adb(action="ui_find", content_desc="More options")
adb(action="ui_find", clickable=True)   # all clickable elements
```

Returns list of `{text, bounds, resource_id, content_desc, clickable, ...}`.

### `ui_tap_by`

Tap by compound criteria (what `smart_tap` builds on):

```python
adb(action="ui_tap_by", text="Send", clickable=True)
adb(action="ui_tap_by", resource_id="com.foo:id/bar", index=0)
```

### `ui_wait_for`

Poll until a node appears, return when found or timeout:

```python
adb(action="ui_wait_for", text="Accept", timeout_sec=10)
```

## Gestures

### Simple gestures

```python
adb(action="tap", x=500, y=1500)
adb(action="swipe", x1=500, y1=2000, x2=500, y2=500, duration_ms=300)
adb(action="gesture_long_press", x=500, y=500, duration_ms=800)
```

### Multi-point paths

```python
adb(action="gesture_path", points=[(100, 500), (300, 500), (300, 800)], duration_ms=500)
```

### Pinch

```python
adb(action="gesture_pinch", cx=500, cy=1000, start_radius=200, end_radius=500)
```

### Gesture streams

For complex multi-touch (drawing, zooming, scrolling):

```python
adb(action="gesture_stream", events=[...])
```

## Text Input

```python
adb(action="type_text", text="Hello world")
adb(action="key", key="enter")
adb(action="key", key="KEYCODE_BACK")
```

Key aliases built in: `back`, `home`, `recent`, `enter`, `tab`, `space`, `delete`, `escape`, `volume_up`, `volume_down`, `power`, `menu`, `search`.

## Scrolling

Android exposes no native "scroll" — you swipe:

```python
# Scroll down (content moves up)
adb(action="swipe", x1=500, y1=1800, x2=500, y2=600, duration_ms=250)

# Scroll up
adb(action="swipe", x1=500, y1=600, x2=500, y2=1800, duration_ms=250)

# Horizontal scroll
adb(action="swipe", x1=900, y1=1000, x2=100, y2=1000, duration_ms=250)
```

## Debug Loop

When building automation flows:

```python
# 1. Dump current state
print(adb(action="ui_dump"))

# 2. Take a screenshot to cross-reference
adb(action="screenshot")

# 3. Try a smart_tap
adb(action="smart_tap", text="Next")

# 4. Verify with another screenshot
adb(action="screenshot")
```

## Common Pitfalls

| Pitfall | Fix |
|---------|-----|
| Node exists in screenshot but not in `ui_dump` | Likely WebView / Compose. Use vision + pixel `tap`. |
| Animation mid-motion, dump catches wrong state | `ui_wait_for` or sleep briefly |
| Multiple matches for same text | Use `resource_id` or `index=N` |
| `bounds=[0,0][0,0]` | Element is off-screen — scroll first |
| Dump is stale | Call `ui_dump` fresh, don't cache |

## What's Next

- [**Smart Tap**](smart-tap.md) — the high-level wrapper
- [**Vision**](vision.md) — fall back to pixels when UIAutomator fails
- [**Examples**](../examples/overview.md) — real automation flows
