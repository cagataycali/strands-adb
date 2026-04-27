# Accessibility

Android's Accessibility service is the most powerful — and most dangerous — API on the phone. `strands-adb` exposes a safe subset.

---

## List Services

```python
adb(action="accessibility_list")
# → enabled services + all installable services
```

## Toggle a Service

```python
adb(action="accessibility_toggle_service",
    service="com.google.android.accessibility.talkback/.TalkBackService",
    enabled=True)
```

!!! warning "Needs permission"
    Toggling services requires `WRITE_SECURE_SETTINGS`:
    ```bash
    adb shell pm grant <pkg> android.permission.WRITE_SECURE_SETTINGS
    ```

## System Actions

Trigger system-wide accessibility actions:

```python
adb(action="accessibility_system_action", id="home")
adb(action="accessibility_system_action", id="back")
adb(action="accessibility_system_action", id="notifications")
adb(action="accessibility_system_action", id="quick_settings")
```

## Live Captions

```python
adb(action="accessibility_captions", enabled=True)
```

## Screen Magnification

```python
adb(action="accessibility_magnification", enabled=True, scale=2.0)
```

## Font Scale

```python
adb(action="accessibility_font_scale", scale=1.3)   # 130% text size
```

Valid scales: `0.85`, `1.0`, `1.15`, `1.3`, `1.45`, `1.6`, `1.75`, `2.0`.

## Status

Overall accessibility state:

```python
adb(action="accessibility_status")
# → {
#     "enabled_services": [...],
#     "touch_exploration": False,
#     "high_contrast": False,
#     "color_correction": "disabled",
#     "font_scale": 1.0,
#     "magnification_enabled": False,
#   }
```

## Agent Recipes

### "Accessibility mode"

```python
agent("""
enable TalkBack, live captions, and bump font to 1.3x.
My grandma is using the phone today.
""")
```

### "Back off the magnifier"

```python
agent("disable magnification and reset font to 1.0")
```

## Richer Screen Reading — Companion APK

For true accessibility-tree reading (much richer than UIAutomator), you'd need a companion APK that registers as an AccessibilityService. This is tracked in [FRONTIERS.md #2](https://github.com/cagataycali/strands-adb/blob/main/FRONTIERS.md). ADB alone can toggle services but can't read their output without a helper app.

## What's Next

- [**Smart Tap**](smart-tap.md) — alternative to a11y for interaction
- [**Settings**](settings.md) — related a11y settings
- [**Safety**](safety.md) — why a11y is powerful and dangerous
