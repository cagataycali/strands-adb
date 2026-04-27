# Settings Mutation

Programmatically change device state: brightness, ringer, airplane mode, bluetooth, any setting Android exposes.

---

## High-Level Actions

```python
adb(action="set_brightness", value=128)       # 0-255
adb(action="set_ringer", mode="silent")       # silent | vibrate | normal
adb(action="set_airplane_mode", enabled=True)
adb(action="set_bluetooth", enabled=False)
```

## Low-Level Settings API

Android stores settings in three namespaces:

| Namespace | Purpose |
|-----------|---------|
| `global`  | Device-wide (wifi, airplane, time) |
| `system`  | User prefs (brightness, volume, ringtone) |
| `secure`  | Security-sensitive (location, accessibility) |

### Get / Put / Delete

```python
adb(action="setting_get", namespace="global", key="airplane_mode_on")
adb(action="setting_put", namespace="system", key="screen_brightness", value="128")
adb(action="setting_delete", namespace="secure", key="my_key")
```

### List All in Namespace

```python
adb(action="setting_list", namespace="global")
# → huge list of all global settings
```

### Dump

```python
adb(action="setting_dump", namespace="system")
```

## Common Keys

| Key | Namespace | Values |
|-----|-----------|--------|
| `airplane_mode_on` | global | `0` / `1` |
| `wifi_on` | global | `0` / `1` |
| `bluetooth_on` | global | `0` / `1` |
| `auto_time` | global | `0` / `1` |
| `screen_brightness` | system | `0`–`255` |
| `screen_brightness_mode` | system | `0` manual, `1` auto |
| `screen_off_timeout` | system | milliseconds |
| `vibrate_on` | system | `0` / `1` |
| `notification_sound` | system | URI |
| `location_mode` | secure | `0` off, `3` high accuracy |
| `accessibility_enabled` | secure | `0` / `1` |

## Agent Recipes

### "Going to sleep"

```python
agent("""
airplane mode on, ringer silent, brightness 10%,
screen timeout 30s. I'm going to bed.
""")
```

### "Meeting mode"

```python
agent("ringer to vibrate, do-not-disturb on, keep wifi")
```

### "Travel mode"

```python
agent("enable airplane, then turn wifi back on for gate wifi")
```

### "Dark room adaptation"

```python
agent("""
if ambient light is below 5 lux,
drop brightness to 20 and enable dark theme.
""")
```

## Permission Caveats

Some settings require extra permissions:

- `secure` namespace may need `WRITE_SECURE_SETTINGS` granted via adb:
  ```bash
  adb shell pm grant <pkg> android.permission.WRITE_SECURE_SETTINGS
  ```
- `Do Not Disturb` requires a notification policy exception.
- Default SMS/Dialer changes need user confirmation.

Most toggles in `global` and `system` work out of the box.

## Safety

Settings mutation can soft-brick UX in rare cases (e.g. brightness=0 + auto-mode off = black screen). `strands-adb` ships:

- **Dry-run mode** via `dry_run=True` on destructive actions
- **Reverse snapshots** — `setting_dump` before + apply + diff after

```python
# Snapshot
before = adb(action="setting_dump", namespace="system")

# Mutate
adb(action="set_brightness", value=10)

# Restore later
for key, value in before["settings"].items():
    adb(action="setting_put", namespace="system", key=key, value=value)
```

## What's Next

- [**Sensors**](sensors.md) — read state, then mutate
- [**Accessibility**](accessibility.md) — a11y-specific settings
- [**Safety**](safety.md) — allowlists, dry-run, reverse ops
