# Example: Screen Reader

Continuously describe what's on screen. Perfect for screen-free workflows, accessibility, or monitoring.

---

## What It Does

Every N seconds:

1. `screenshot` the phone
2. Vision model reads what's on screen
3. Speak / notify / log the description
4. Repeat

## Minimal Version

```python
import time
from strands import Agent
from strands_adb import adb

agent = Agent(
    tools=[adb],
    system_prompt="You describe phone screens concisely in 1-2 sentences.",
)

while True:
    print(agent("take a screenshot and describe what's on screen"))
    time.sleep(10)
```

## DevDuck Ambient Version

```bash
export DEVDUCK_TOOLS="strands_adb:adb;strands_tools:shell;devduck.tools:ambient_mode,notify,speak"
export DEVDUCK_AMBIENT_MODE=true
export DEVDUCK_AMBIENT_IDLE_SECONDS=10

devduck "every 10s, screenshot my phone and describe what's on screen, speak it aloud via 'say'"
```

With `DEVDUCK_AMBIENT_MODE`, the loop runs when you're idle. No need to manage threads.

## Continuous + Change Detection

Only announce when the screen meaningfully changed:

```python
from strands import Agent
from strands_adb import adb
import time

agent = Agent(
    tools=[adb],
    system_prompt="""
Compare the new screenshot to the previous description.
If substantially the same, respond exactly: NO_CHANGE
Otherwise describe what's new in 1 sentence.
""",
)

last = ""
while True:
    result = str(agent(f"""
    Previous: {last}
    New screenshot coming. Compare to previous and report.
    """))
    if "NO_CHANGE" not in result:
        print(result)
        last = result
    time.sleep(5)
```

## Accessibility Use Case

For vision-impaired users — pair with `speak`:

```bash
export DEVDUCK_TOOLS="strands_adb:adb;strands_tools:shell,speak"

devduck "every 5s, screenshot and describe any NEW content. speak it aloud."
```

## Performance

- Screenshot via `exec-out screencap`: ~200-300ms
- Vision model inference: 1-3s (Bedrock Claude Sonnet 4) or 500ms (MLX Qwen3-VL on-device)
- Total loop: ~3-4s per cycle → easy at 10s intervals

## Variations

### Record-the-day mode

```python
agent("""
start a screen frame capture at 1 fps for the next hour.
save to /tmp/my_day/.
""")
# → screen_frames action
```

Then process offline:

```python
# Pipeline: frames → strands-cosmos for video understanding
# https://github.com/cagataycali/strands-cosmos
```

### Meeting-mode live captions

```python
agent("""
enable live captions (accessibility), screenshot every 2s,
extract caption text, save to /tmp/meeting.txt.
""")
```

## Full Script

```python
# examples/screen_reader.py
import time, sys
from strands import Agent
from strands_adb import adb

INTERVAL = int(sys.argv[1]) if len(sys.argv) > 1 else 10

agent = Agent(tools=[adb])
print(f"🔍 Screen reader: every {INTERVAL}s")
while True:
    print(agent("screenshot + describe in 1 sentence"))
    time.sleep(INTERVAL)
```

```bash
python examples/screen_reader.py 5
```

## What's Next

- [**Autonomous Agent**](autonomous.md) — full 24/7
- [**Vision guide**](../guide/vision.md) — screenshot internals
- [**DevDuck ambient mode**](../guide/devduck.md) — the right way to run loops
