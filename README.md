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

## 90+ Actions, One Tool

| Domain | Actions |
|--------|---------|
| **Device** | list, select, info, battery, wake, unlock |
| **UI**     | tap, swipe, type, key, gestures, smart_tap |
| **Screen** | screenshot (image block), record, frames, ui_dump, ui_find |
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
