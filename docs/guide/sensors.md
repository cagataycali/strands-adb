# Sensors & Thermals

The phone is a sensor platform: accelerometer, gyro, light, proximity, temperature, battery. All accessible via `adb dumpsys`.

---

## `sensors`

One-shot snapshot of all sensor values:

```python
result = adb(action="sensors")
# → accelerometer (m/s²): x=0.1, y=9.7, z=0.3  (phone is upright)
# → gyroscope (rad/s):    x=0.0, y=0.0, z=0.0  (not rotating)
# → ambient light (lux):  450                    (indoor)
# → proximity (cm):       5                      (nothing nearby)
# → magnetic field:       ...
# → pressure (hPa):       1012                   (sea level-ish)
# → step counter:         14382                  (since last reboot)
```

## `thermals`

CPU, skin, battery, modem temperatures:

```python
adb(action="thermals")
# → CPU:     67.2 °C  (warm, not throttling)
# → Skin:    35.8 °C  (comfortable to hold)
# → Battery: 32.1 °C  (normal)
# → GPU:     58.0 °C
# → Modem:   41.3 °C
```

Great for:
- Detecting throttling before performance degrades
- Warning the user when the phone is overheating
- Monitoring during long video recording / ML inference

## `battery`

```python
adb(action="battery")
# → level: 87%, charging: true, temp: 32.1°C, health: good, USB power source
```

## `wifi_info`

```python
adb(action="wifi_info")
# → SSID: "HomeNet", BSSID: "aa:bb:...", signal: -52 dBm, freq: 5180 MHz
```

## Agent Recipes

### Phone state classification

```python
agent("is my phone face-down on the desk?")
# Agent:
#   sensors → accelerometer z ≈ -9.8 m/s² → face-down
```

### Thermal budgeting

```python
agent("""
check thermals every 30s for 5 minutes.
alert me if CPU > 85°C.
""")
```

### Motion detection

```python
agent("""
take a sensors reading now, wait 10s, take another.
did the phone move?
""")
# compares gyro + accelerometer deltas
```

### Ambient-light-aware UX

```python
agent("""
if ambient light < 10 lux, enable dark mode and
drop brightness to 20%.
""")
# → sensors → set_brightness → setting_put dark mode
```

## Limitations

- `sensors` is a **snapshot**, not a stream. For continuous data, poll at 1 Hz from your agent loop.
- For high-frequency streams (200 Hz accelerometer) you need a companion APK. See [FRONTIERS.md](https://github.com/cagataycali/strands-adb/blob/main/FRONTIERS.md) #1.
- Not all sensors are on all devices. Pixels have barometric pressure; many Samsungs don't.

## What's Next

- [**Settings**](settings.md) — mutate device state based on sensor readings
- [**Logcat**](logcat.md) — stream events for a continuous event loop
- [**DevDuck**](devduck.md) — scheduled polling + event bus
