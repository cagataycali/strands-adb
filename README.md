# strands-adb 🤖

`@tool` decorated Android control for Strands agents & DevDuck.

Control any adb-connected Android device (phone/tablet/emulator) via a single
declarative tool. Built for remote agentic control — let your agent text people,
check notifications, launch apps, take screenshots, drive the UI, all while
you're away.

## Install

```bash
pip install strands-adb
# or from source
pip install -e .
```

Requires `adb` on PATH (`brew install android-platform-tools`).

## Quickstart

```python
from strands import Agent
from strands_adb import adb

agent = Agent(tools=[adb])
agent("take a screenshot of my phone and describe what's on screen")
```

## DevDuck

```bash
export DEVDUCK_TOOLS="strands_adb:adb;strands_tools:shell"
devduck "open whatsapp and read the last message from mom"
```

## Capabilities

- **Device**: list, select, info, battery, wake, unlock
- **Shell**: run any `adb shell` command safely
- **UI**: tap, swipe, type, key events, back/home/recent
- **Screen**: screenshot (returns image), screen record, dump UI hierarchy
- **Apps**: list, launch, kill, install, uninstall, clear data
- **Files**: push, pull, ls
- **Intents**: open URL, send SMS draft, share text
- **Notifications**: dump, dismiss
- **Logs**: logcat filtered

## Safety

- Dry-run mode for destructive ops
- Allowlist/denylist for package operations
- No plaintext PIN/password storage
