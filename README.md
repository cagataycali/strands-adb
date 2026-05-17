<div align="center">
  <img src="docs/strands-adb-logo.svg" alt="Strands ADB" width="160">
  <h1>strands-adb 🤖</h1>
  <p><strong>Give your agent a phone.</strong></p>
  <p>
    <a href="https://pypi.org/project/strands-adb/"><img src="https://img.shields.io/pypi/v/strands-adb.svg" alt="PyPI"></a>
    <a href="https://cagataycali.github.io/strands-adb/"><img src="https://img.shields.io/badge/docs-cagataycali.github.io%2Fstrands--adb-3DDC84" alt="Docs"></a>
    <a href="https://github.com/cagataycali/strands-adb/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License"></a>
  </p>
</div>

`@tool` decorated Android control for [Strands Agents](https://strandsagents.com) & [DevDuck](https://dev.duck.nyc).

Drive any adb-connected Android device (phone / tablet / emulator) from an LLM — let your agent text people, read notifications, launch apps, take screenshots, drive the UI, take physical photos, stream logcat events, mutate settings.

📘 **Full docs:** [cagataycali.github.io/strands-adb](https://cagataycali.github.io/strands-adb/)

---

## Install

```bash
pip install strands-adb
brew install android-platform-tools    # or apt / pacman / winget
```

Enable USB debugging on your phone, plug it in, accept the trust dialog.

```bash
adb devices
# 59230DLCH0012Z  device

[![Awesome Strands Agents](https://img.shields.io/badge/Awesome-Strands%20Agents-00FF77?style=flat-square&logo=data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjkwIiBoZWlnaHQ9IjQ2MyIgdmlld0JveD0iMCAwIDI5MCA0NjMiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxwYXRoIGQ9Ik05Ny4yOTAyIDUyLjc4ODRDODUuMDY3NCA0OS4xNjY3IDcyLjIyMzQgNTYuMTM4OSA2OC42MDE3IDY4LjM2MTZDNjQuOTgwMSA4MC41ODQzIDcxLjk1MjQgOTMuNDI4MyA4NC4xNzQ5IDk3LjA1MDFMMjM1LjExNyAxMzkuNzc1QzI0NS4yMjMgMTQyLjc2OSAyNDYuMzU3IDE1Ni42MjggMjM2Ljg3NCAxNjEuMjI2TDMyLjU0NiAyNjAuMjkxQy0xNC45NDM5IDI4My4zMTYgLTkuMTYxMDcgMzUyLjc0IDQxLjQ4MzUgMzY3LjU5MUwxODkuNTUxIDQxMS4wMDlMMTkwLjEyNSA0MTEuMTY5QzIwMi4xODMgNDE0LjM3NiAyMTQuNjY1IDQwNy4zOTYgMjE4LjE5NiAzOTUuMzU1QzIyMS43ODQgMzgzLjEyMiAyMTQuNzc0IDM3MC4yOTYgMjAyLjU0MSAzNjYuNzA5TDU0LjQ3MzggMzIzLjI5MUM0NC4zNDQ3IDMyMC4zMjEgNDMuMTg3OSAzMDYuNDM2IDUyLjY4NTcgMzAxLjgzMUwyNTcuMDE0IDIwMi43NjZDMzA0LjQzMiAxNzkuNzc2IDI5OC43NTggMTEwLjQ4MyAyNDguMjMzIDk1LjUxMkw5Ny4yOTAyIDUyLjc4ODRaIiBmaWxsPSIjRkZGRkZGIi8+CjxwYXRoIGQ9Ik0yNTkuMTQ3IDAuOTgxODEyQzI3MS4zODkgLTIuNTc0OTggMjg0LjE5NyA0LjQ2NTcxIDI4Ny43NTQgMTYuNzA3NEMyOTEuMzExIDI4Ljk0OTIgMjg0LjI3IDQxLjc1NyAyNzIuMDI4IDQ1LjMxMzhMNzEuMTcyNyAxMDMuNjcxQzQwLjcxNDIgMTEyLjUyMSAzNy4xOTc2IDE1NC4yNjIgNjUuNzQ1OSAxNjguMDgzTDI0MS4zNDMgMjUzLjA5M0MzMDcuODcyIDI4NS4zMDIgMjk5Ljc5NCAzODIuNTQ2IDIyOC44NjIgNDAzLjMzNkwzMC40MDQxIDQ2MS41MDJDMTguMTcwNyA0NjUuMDg4IDUuMzQ3MDggNDU4LjA3OCAxLjc2MTUzIDQ0NS44NDRDLTEuODIzOSA0MzMuNjExIDUuMTg2MzcgNDIwLjc4NyAxNy40MTk3IDQxNy4yMDJMMjE1Ljg3OCAzNTkuMDM1QzI0Ni4yNzcgMzUwLjEyNSAyNDkuNzM5IDMwOC40NDkgMjIxLjIyNiAyOTQuNjQ1TDQ1LjYyOTcgMjA5LjYzNUMtMjAuOTgzNCAxNzcuMzg2IC0xMi43NzcyIDc5Ljk4OTMgNTguMjkyOCA1OS4zNDAyTDI1OS4xNDcgMC45ODE4MTJaIiBmaWxsPSIjRkZGRkZGIi8+Cjwvc3ZnPgo=&logoColor=white)](https://github.com/cagataycali/awesome-strands-agents)
```

## Quickstart

```python
from strands import Agent
from strands_adb import adb

agent = Agent(tools=[adb])
agent("take a screenshot of my phone and describe what's on screen")
```

## DevDuck (1 line)

```bash
export DEVDUCK_TOOLS="strands_adb:adb;strands_tools:shell"
devduck "open whatsapp and read the last message from mom"
```

---

## 👁️ Agent can SEE the screen

`screenshot` returns a proper [Converse API image block](https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html) — the same format as `strands_tools.image_reader`. The agent doesn't just get a file path, it actually receives the pixels and reasons over them:

```python
agent("take a screenshot and tell me what app is open")
# → adb(action="screenshot") returns PNG bytes in Converse image block
# → vision model reads it → "You're on the WhatsApp chat with Mom..."
```

Disable with `include_image=False` if you just want the file path.

## 🎬 Record What The Agent Does

Non-blocking screen recording — start before the agent acts, stop after. Review the video to see what actually happened.

```python
adb(action="screen_record_start", output_path="/tmp/run.mp4")

agent("open whatsapp and reply to mom")   # agent works while recording

result = adb(action="screen_record_stop")
print(result["merged_path"])   # single mp4, auto-stitched past 180s
```

→ [Screen Recording guide](https://cagataycali.github.io/strands-adb/guide/screen-recording/)

## 90+ Actions, One Tool

| Domain | Actions |
|--------|---------|
| **Device** | list, select, info, battery, wake, unlock |
| **UI**     | tap, swipe, type, key, gestures, smart_tap |
| **Screen** | screenshot (image block), screen_record, **screen_record_start/stop** (bg), frames, ui_dump, ui_find |
| **Apps**   | list, launch, kill, install, uninstall, clear_data |
| **Files**  | push, pull, ls |
| **Intents**| open_url, share_text, start_activity |
| **Camera** | camera_photo (image block), camera_video |
| **Sensors**| accelerometer, gyro, light, pressure, step counter |
| **Thermals** | CPU / skin / battery / GPU / modem temps |
| **Settings** | brightness, ringer, airplane, bluetooth, any setting_put |
| **Logs**   | logcat one-shot, log_stream → event_bus, notifications_parsed |
| **A11y**   | enable services, captions, magnification, font scale |
| **Comms**  | dial, sms_compose, media_control, volume |

→ [Full actions guide](https://cagataycali.github.io/strands-adb/guide/actions/)

## Docs

- [**Installation**](https://cagataycali.github.io/strands-adb/getting-started/installation/)
- [**Quickstart**](https://cagataycali.github.io/strands-adb/getting-started/quickstart/)
- [**Connect a Device**](https://cagataycali.github.io/strands-adb/getting-started/connect/) — USB / wireless / SSH
- [**Vision**](https://cagataycali.github.io/strands-adb/guide/vision/) — screenshots as image blocks
- [**Smart Tap**](https://cagataycali.github.io/strands-adb/guide/smart-tap/) — semantic UI automation
- [**Camera**](https://cagataycali.github.io/strands-adb/guide/camera/) — physical photos
- [**Logcat Streaming**](https://cagataycali.github.io/strands-adb/guide/logcat/) — event bus integration
- [**DevDuck Integration**](https://cagataycali.github.io/strands-adb/guide/devduck/)
- [**Safety**](https://cagataycali.github.io/strands-adb/guide/safety/) — production hardening
- [**Examples**](https://cagataycali.github.io/strands-adb/examples/overview/) — WhatsApp, notifications, autonomous
- [**Architecture**](https://cagataycali.github.io/strands-adb/architecture/)
- [**API Reference**](https://cagataycali.github.io/strands-adb/api-reference/)

## Safety

- Dry-run mode for destructive ops
- Allowlist/denylist for package operations
- No plaintext PIN/password storage
- Full audit logging of every adb invocation

→ [Safety guide](https://cagataycali.github.io/strands-adb/guide/safety/)

## License

MIT
