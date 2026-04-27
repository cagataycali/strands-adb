# Apps & Intents

Launch apps, manage packages, and send Android intents.

---

## Launch an App

```python
adb(action="launch", package="com.whatsapp")
adb(action="launch", package="com.spotify.music")
adb(action="launch", package="com.android.settings")
```

The tool calls `am start -n <package>/<MAIN_ACTIVITY>` under the hood.

## What's Currently Open

```python
adb(action="current_app")
# → {"package": "com.whatsapp", "activity": "..."}
```

## List Installed Packages

```python
adb(action="list_packages")                 # all packages
adb(action="list_packages", pattern="whats") # filter
adb(action="list_packages", third_party=True) # skip system apps
```

## Install / Uninstall

```python
adb(action="install", apk_path="/path/to/app.apk")
adb(action="uninstall", package="com.foo.bar")
```

!!! warning "Destructive"
    `uninstall` is permanent. Use allowlists in production.

## Force-Stop / Clear Data

```python
adb(action="kill", package="com.flaky.app")
adb(action="clear_data", package="com.broken.app")
```

## Open a URL

```python
adb(action="open_url", url="https://news.ycombinator.com")
```

Equivalent to `am start -a android.intent.action.VIEW -d <url>`. The OS picks the right handler (browser, Spotify link, whatever).

## Share Text

Trigger the share sheet:

```python
adb(action="share_text", text="Check this out: https://example.com")
```

## Arbitrary Activity

Full control:

```python
adb(action="start_activity",
    action_name="android.intent.action.VIEW",
    data="content://contacts",
    package="com.google.android.contacts")
```

## Agent Recipes

### "Pick up where I left off"

```python
agent("""
what app was I last using? open it again.
""")
# → current_app → launch
```

### "Play something chill on Spotify"

```python
agent("launch spotify and play my chill playlist")
# → launch com.spotify.music → smart_tap "chill" → smart_tap play
```

### "Read my last HN story"

```python
agent("open news.ycombinator.com in my browser")
# → open_url
```

## Common Package Names

| App | Package |
|-----|---------|
| WhatsApp | `com.whatsapp` |
| Spotify | `com.spotify.music` |
| Instagram | `com.instagram.android` |
| Telegram | `org.telegram.messenger` |
| Chrome | `com.android.chrome` |
| Gmail | `com.google.android.gm` |
| Maps | `com.google.android.apps.maps` |
| GoogleCamera | `com.google.android.GoogleCamera` |
| Settings | `com.android.settings` |
| Phone | `com.google.android.dialer` |

Or just ask the agent:

```python
agent("list packages matching 'spot' and launch the music one")
```

## What's Next

- [**Smart Tap**](smart-tap.md) — interact once the app is open
- [**Vision**](vision.md) — see what's on screen after launching
- [**Camera**](camera.md) — drive the camera app specifically
