# 🚀 NEW FRONTIERS — Interactive Era

**Status:** The original 13 frontiers from `FRONTIERS.md` are all shipped (`v0.16.0`).
The device is now **unlocked** and fully accessible. This unlocks a **completely
new class** of capabilities: agents that don't just read the device, they *use* it.

This document scopes the second wave of frontiers.

---

## What just unlocked

The user shared the PIN. I wrote a routine that:
1. Wakes the device
2. Dismisses the biometric bouncer (`AlternateBouncerView`)
3. Taps the fingerprint icon to switch to the PIN bouncer
4. Taps digits **1 → 0 → 7 → 1 → Enter**
5. Verifies `deviceLocked=0` in TrustManager

Took about 4 taps total. Now `mCurrentFocus = NexusLauncherActivity` — we're
on the home screen. **This means every app is reachable.**

---

## Frontier #14 — Smart Unlock & Session Lifecycle
Agent-friendly unlock that handles the full state machine.

- `unlock(pin=...)` — wakes, dismisses biometric, enters PIN, verifies
- `unlock_with_fallback(pin_env_var='ADB_DEVICE_PIN')` — fetches PIN
  from env var / keychain / macOS secure enclave
- `lock()` — `input keyevent KEYCODE_POWER` + verify
- `is_locked()` / `is_awake()` / `is_on_ambient()` — fast queries
- `keep_awake(enabled=True)` — set/unset `stay_on_while_plugged_in`
- Handles edge cases: fingerprint retry limit, PIN lockout backoff

## Frontier #15 — UI State Machine & Waits
Reliable UI automation needs "wait for X" primitives, not sleeps.

- `wait_for_window(package, timeout=10)` — blocks until focus matches
- `wait_for_text(text, timeout=10)` — blocks until text appears in dump
- `wait_for_idle(timeout=5)` — no UI changes for 500ms
- `wait_for_gone(selector)` — blocks until element disappears
- `find_element(text=..., resource_id=..., content_desc=...)` —
  returns bounds + clickable state from UIAutomator dump
- `tap_element(...)` — find + tap in one call (handles scroll if needed)

## Frontier #16 — App Launcher & Switcher
High-level app management for an agent workflow.

- `app_launch(name_or_package)` — fuzzy match "gmail" → com.google.android.gm
- `app_kill(package)` → `am force-stop`
- `app_switch_to(package)` — bring to foreground if backgrounded
- `app_recents()` — list of recent apps w/ their last-used timestamp
- `app_clear_data(package)` — factory reset a single app
- `foreground_info()` — { package, activity, top_fragment, is_game }

## Frontier #17 — Scrollable Content Traversal
Most UIs require scrolling. Make that first-class.

- `scroll_until(direction, stop_condition)` — stop when text/element appears
- `scroll_to_top()` / `scroll_to_bottom()`
- `list_items(container_selector)` — enumerate visible list items
- `paginate_list(container, callback)` — scroll through entire list,
  yielding batches
- `fling(direction, velocity)` — for long content

## Frontier #18 — Forms, Inputs & Auto-fill
Typing the PIN was manual. Form filling should be a primitive.

- `fill_form(fields={'username': 'x', 'password': 'y'})` —
  auto-resolves input fields by label/hint
- `tap_and_type(selector, text)` — click + clear + type + dismiss kbd
- `keyboard_dismiss()` — hide soft keyboard
- `autofill(service=?)` — trigger Autofill Framework
- `paste_into(selector)` — use clipboard (already shipped) + tap paste
- `otp_read(timeout=60)` — watch SMS / notifications for incoming OTP,
  extract 4–8 digit code, fill it

## Frontier #19 — Visual Assertions & Regression
Screenshot-based truth.

- `assert_screenshot(ref='home.png', tolerance=0.02)` — pixel diff
- `screenshot_region(x, y, w, h)` — capture specific area
- `ocr(region?)` — tesseract on screenshot, return text + bounding boxes
- `color_at(x, y)` — single-pixel RGBA read
- `visual_diff(before, after)` — heatmap image of changes
- `crop_to_element(selector)` — screenshot just the element bounds

## Frontier #20 — Notifications API
Currently we can only *see* notifications in shade UI. Deepen it.

- `notifications_list()` — structured list of active notifications:
  `[{package, title, text, timestamp, big_text, actions[], is_ongoing}]`
- `notification_tap(notification_id)` — fire the content intent
- `notification_action(id, action_idx)` — trigger a button action
- `notification_dismiss(id)` — swipe away
- `notification_expand(id)` / `collapse(id)`
- `notifications_stream()` — async, yield new notifications as they arrive
- `listener_service()` — install + bind to `NotificationListenerService`
  (this is the serious one: full notification capture in real time)

## Frontier #21 — SMS, Call, Contacts
The original "phone" functions.

- `sms_send(to, body)` — via `am start` + SENDTO intent, or direct if
  we're the default messaging app (we're not, so always tap-to-send)
- `sms_list(thread=?, limit=50)` — read from content://sms/
- `sms_wait_for(pattern, timeout=60)` — block until matching SMS arrives
- `call(number)`, `call_end()`, `call_answer()`
- `contacts_list()` / `contacts_get(query)`
- `contacts_add(name, number, email)`

## Frontier #22 — Capture & Playback
Record human actions, replay them as automations.

- `record_session()` — capture taps / swipes / text input as events
- `replay(session_file)` — deterministic playback with wait_for_idle
- `macro_save(name)` / `macro_run(name)` — named recording library
- `record_video(duration)` — screenrecord (already shipped, re-expose
  with higher-level API)
- `record_screen_with_events()` — video + synchronized event log
- `export_session(format='gif'|'mp4'|'html')` — shareable playback

## Frontier #23 — Per-App Deep Automation
High-value apps get "smart" adapters.

- **Gmail**: `gmail.inbox(limit=N)`, `gmail.send(to, subj, body)`,
  `gmail.search(q)`, `gmail.unread_count()`
- **Messages**: `messages.send(thread, text)`, `messages.unread()`
- **Chrome**: `chrome.open(url)`, `chrome.new_tab()`, `chrome.current_url()`,
  `chrome.read_page()` — combine with OCR + screenshot
- **Photos**: `photos.latest()`, `photos.search(q)`, `photos.share(id)`
- **Camera**: `camera.photo()`, `camera.video_start()`, `camera.swap()`
- **Settings**: `settings.toggle_airplane()`, `settings.brightness(N)`

Each adapter: observe the app's UI tree once, memorize stable selectors,
abstract into typed Python methods.

## Frontier #24 — AI Vision + UI Fusion
Combine vision models with raw UI trees for robust automation.

- `describe_screen()` — LLM describes what's on screen (via screenshot)
- `find_element_visual(description)` — "the blue button next to 'Confirm'"
  uses vision model to resolve natural language into coordinates
- `click_by_description(text)` — vision-powered element finder as
  fallback when UIAutomator misses custom-drawn UIs (games, WebViews)
- `read_receipt()` / `read_form()` / `read_table()` — structured
  extraction of common UI patterns
- `explain_action(what_will_happen)` — before-and-after visual preview

## Frontier #25 — Multi-step Planning
The agent needs to *plan* flows, not just execute.

- `plan(goal)` → returns list of steps (LLM reasoning + UI tree)
- `execute_plan(plan, dry_run=True)` — simulate before running
- `checkpoint()` / `rollback()` — nested UI state snapshots
- `on_failure(strategy='retry'|'backtrack'|'ask')` — self-heal flow
- `journal()` — append-only log of all actions taken this session

## Frontier #26 — Safety Rails
Unlocked device = real damage potential. Guard rails needed.

- `require_confirmation(for_action)` — prompt user before destructive ops
- `sandbox_mode(enabled=True)` — simulate actions without executing
- `forbidden_apps = ['Banking', 'Wallet', 'Signal']` — hard-block
  interaction with sensitive apps
- `audit_log()` — tamper-evident log of all issued actions
- `panic_revoke()` — instant `adb kill-server` + lock the device
- `session_timeout(seconds=300)` — auto-lock after idle period

## Frontier #27 — Performance Profiling
Observability for long-running automations.

- `perf_start()` / `perf_stop()` — wrap a code block, return
  flamegraph of CPU + memory + ANR frames
- `frame_stats(package)` — jank detection from gfxinfo
- `memory_snapshot(package)` — detailed PSS / RSS / heap
- `network_trace(package)` — tcpdump-like, per-app
- `method_trace(package, duration_ms)` — Android method tracer

## Frontier #28 — Device Fleet Orchestration
One laptop, many phones.

- `fleet_list()` — all connected devices + metadata
- `fleet_map(fn)` — run function on every device in parallel
- `fleet_record()` — synchronized screen recording across devices
- `fleet_compare(action)` — run same action, diff results
- `device_pool(count=N)` — reserve N devices for a test run

## Frontier #29 — Proactive Agent Monitors
Run in the background, alert on events.

- `monitor_new_notifications()` → callback on every new notification
- `monitor_app_launch()` → callback when app foregrounds
- `monitor_network_change()` → Wi-Fi vs cellular switch
- `monitor_low_battery(threshold=20)` → auto-plug reminder
- `daemon_mode()` — run as a background service, emit events to bus

## Frontier #30 — Natural Language Interface
The final form.

- `agent("send a message to mom saying I'm late")` — full NL → action
- `agent("summarize my inbox")` — chain gmail + summarization
- `agent("turn on WiFi then connect to my home network")` —
  plan + execute + verify
- `agent("what's on my screen?")` — vision + UI tree → description
- `agent("undo the last 3 actions")` — uses journal from #25

---

## Priority Order (my picks)

🥇 **#14 Smart Unlock** — every session needs it. Tiny API, huge value.
🥈 **#15 UI State Machine & Waits** — foundation for all reliable automation.
🥉 **#16 App Launcher** — the ergonomic sugar everyone needs day 1.
   **#20 Notifications API** — notification stream is a key signal.
   **#24 AI Vision + UI Fusion** — where the rubber meets modern LLM roads.
   **#26 Safety Rails** — must land before #30 is safe.
   **#30 Natural Language** — the north star.

Cycle 14+ starts with Frontier #14.
