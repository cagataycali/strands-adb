# 🧭 strands-adb — Frontiers

Unexplored territory for turning strands-adb from "phone remote control" into
"phone-native autonomous agent". Each section is independently shippable.

Current state: **v0.3.0**, 67 actions, smart UI + sensors + thermals + comms,
11/11 tests passing on real Pixel 10 Pro (Android 16).

---

## 1. 📡 Live Sensor Streaming

**Problem:** `dumpsys sensorservice` is a point-in-time snapshot. To detect
real-world events (phone picked up, walking, in-pocket, face-down) we need
continuous streams.

**Approach:**
- Push a tiny companion APK that exports a ContentProvider streaming sensor
  events, OR
- Use `adb shell` long-running commands + SensorManager via a stub activity, OR
- Parse `dumpsys sensorservice` at 1Hz in a background thread and emit deltas
  to devduck's event_bus

**Use cases:**
- "Phone just got picked up" → surface pending notifications by voice
- "Phone has been face-down for >5min" → assume DND, defer non-urgent pings
- "Step counter jumped 50 steps" → user is walking, lower notification volume
- "Significant motion + high accel variance + GPS speed" → in a vehicle, switch
  to hands-free mode
- "Ambient light dropped from 500 → 5 lux" → user entered dark room / sleep mode

**New actions:**
- `sensor_subscribe(name="accelerometer", hz=10)` → starts background poll,
  pushes to event_bus topic `phone.sensor.accel`
- `sensor_unsubscribe(name=...)` / `sensor_list_active()`
- `motion_classify()` → returns "still" | "walking" | "vehicle" | "picked_up"

**Complexity:** Medium. Zero APK = polling only. With APK = real streams at
200Hz accelerometer rate.

---

## 2. 🔌 Accessibility Service Companion APK

**Problem:** Android's security model blocks reading SMS bodies, contacts,
clipboard, and *actually sending* messages without being the default handler
OR the Accessibility service. ADB alone can only draft messages.

**Approach:** Ship a minimal signed APK (`devduck-companion.apk`) that:
1. Declares AccessibilityService in manifest
2. On install, user grants accessibility permission once
3. Exposes a local socket (loopback only, ADB-forwarded) where strands-adb can:
   - Query current screen's full text tree (richer than uiautomator)
   - Observe events: notification posted, text typed, focus changed, app opened
   - Send real SMS / WhatsApp / IG DM via input-focus + programmatic send
   - Read clipboard content
4. Package into the pypi distribution; `adb install` via a new action

**Use cases:**
- "Reply to Arron's IG reel with: 🔥" → actually sends
- "When I get any Telegram message from Burak, forward to me on zenoh"
- "Read the text of any notification that appears"
- "Paste this into whatever app is focused"

**New actions:**
- `companion_install()` — adb install bundled APK
- `companion_grant()` — guide user to Settings > Accessibility (screenshot + smart_tap)
- `companion_status()` — is service bound?
- `accessibility_read_screen()` — full text tree (semantic, richer than UIAutomator)
- `accessibility_observe(event_types=["NOTIFICATION_STATE_CHANGED"])` — subscribe to events
- `send_message(app="whatsapp|telegram|instagram|sms", to="...", body="...")` — REAL send

**Complexity:** High. Needs APK build infra (could use Kotlin or even Rhino/JS via
Termux-on-device). But unlocks 10× the power.

---

## 3. 📜 Logcat Event Stream → DevDuck Event Bus

**Problem:** We poll. Android is pushing events constantly via logcat. Free
information we're ignoring.

**Approach:**
- Background thread runs `adb logcat -v threadtime *:W` (warning and up)
- Parser extracts: app crashes, ANR, notification posts, camera opens, package
  installs, low battery, WiFi connect/disconnect
- Structured events pushed to devduck's `event_bus` under topic `phone.log.*`
- Agent can subscribe via existing event_bus pattern (same as telegram/whatsapp)

**Use cases:**
- App crashes → agent investigates logs, suggests fix, notifies user
- New notification posted → agent parses, decides if urgent, forwards via zenoh
- Phone calls coming in → agent mutes current media, screenshots caller ID
- Package installed → log what apps are added for audit
- Low battery → agent closes non-essential apps, dims screen

**New actions:**
- `log_stream_start(filters=["*:W"], topics=["phone.log"])`
- `log_stream_stop()`
- `log_stream_status()`
- Integrates with `devduck.tools.event_bus` — no new tool surface for consumers

**Complexity:** Low. Just a subprocess.Popen with a background thread reader.
Biggest unlock per line of code.

**Priority pick #1.**

---

## 4. 📺 scrcpy-style Live Mirror → Browser Overlay

**Problem:** Debugging phone automation is blind. User doesn't see what agent
sees until after the fact.

**Approach:**
- `exec-out screencap -p` at 5fps (~200KB PNG each) in background thread
- Push frames to mesh.html via existing agentcore_proxy WebSocket on port 10000
- New mesh.html component: `<android-mirror>` that renders latest frame
- Bonus: overlay last `smart_tap` target as a red dot for debugging

**Use cases:**
- Watch the agent drive your phone in real-time from any laptop with browser
- Debug failed automations by replaying frames
- Remote troubleshooting — friend shows you their phone via your mesh
- Great demo / marketing moment

**New actions:**
- `mirror_start(fps=5, quality=80)` — begins WebSocket push
- `mirror_stop()`
- `mirror_overlay(x, y, label="tapped here")` — draw on next frame

**Complexity:** Medium. Requires WebSocket wiring to mesh + tiny frontend
component. Could piggyback on existing `agentcore_proxy` plumbing.

---

## 5. 📸 Physical World Camera Access

**Problem:** Agent can see the *screen* but not the *environment*. The phone
has a camera. Why not use it?

**Approach:**
- `am start -a android.media.action.STILL_IMAGE_CAMERA`
- Wait for Camera to load (poll `current_app`)
- `input keyevent KEYCODE_CAMERA` to snap
- `content query content://media/external/images/media ORDER BY date_added DESC LIMIT 1`
- `adb pull` the new DCIM file
- Return as Converse API image block (same pattern as screenshot)

**Use cases:**
- "What does the room look like right now?" (phone is home, you're at work)
- "Is my cat on the couch?" (cheap home camera)
- "Take a picture of my whiteboard" (meeting assistant)
- "Read the text on the box in front of the camera" (remote OCR)
- With gyro: agent knows phone orientation, can ask you to rotate to get
  the right angle

**New actions:**
- `camera_photo(facing="back|front", auto_pull=True)` → returns image block
- `camera_video(duration_sec=5, facing="back")` → returns mp4
- `camera_stream(fps=2)` — like mirror but for environment, not screen

**Complexity:** Medium. Camera API via intent is well-documented. Permissions
handled by Camera app itself.

**Priority pick #2 for the "phone sitting alone" GOAL.md use case.**

---

## 6. 🗣️ Voice I/O

**Problem:** Text is fast for me, voice is fast for you. Phone has both.

**Approach:**
- Speech OUT: `am start -a android.speech.tts.engine.CHECK_TTS_DATA` + TTS
  intent, OR use the companion APK's TextToSpeech API
- Speech IN: Phone has hotword detection already. Trigger via intent and pull
  resulting transcript
- OR: Use `media.audio_flinger` to record raw audio, push to local Whisper

**Use cases:**
- "Tell my phone to say 'dinner is ready' on the speaker"
- "When Mom calls, answer and say 'he's in a meeting, call back in 1 hour'"
- Phone becomes a voice intercom between rooms via zenoh peers

**New actions:**
- `tts_say(text="...", voice="en-us")` 
- `stt_listen(timeout_sec=10)` → returns transcript
- `hotword_start(keyword="hey phone")` — companion APK needed

**Complexity:** Medium (TTS intent) to High (full STT pipeline).

---

## 7. 📞 Call Control (Receive / Answer / Reject)

**Problem:** `dial` can place calls. But receiving is the real superpower.

**Approach:**
- `dumpsys telephony.registry` shows call state in real-time — poll or watch
- `input keyevent KEYCODE_CALL` answers, `KEYCODE_ENDCALL` hangs up
- Companion APK with InCallService permission for programmatic answer + audio
  routing

**Use cases:**
- "Answer Mom's call, put her on speaker, I'll be right there"
- "Reject all unknown numbers automatically during meetings"
- "Log every incoming call with caller + duration to a spreadsheet"
- Agentic voicemail: phone rings → agent answers → transcribes via STT →
  summarizes → sends to your zenoh mesh

**New actions:**
- `call_state()` — idle | ringing | in_call, plus caller info
- `call_answer()` / `call_reject()` / `call_end()`
- `call_watch()` — subscribe to state changes on event_bus

**Complexity:** High for full control; Low for state monitoring.

---

## 8. 🔔 Notification Actions (Not Just Reading)

**Problem:** We READ notifications. But Android notifications have buttons
("Reply", "Mark as Read", "Snooze"). We can't tap them.

**Approach:**
- `dumpsys notification --noredact` already shows `actions=N` per notification
- Companion APK's NotificationListenerService lets us:
  - Invoke action PendingIntents directly
  - Dismiss notifications
  - Mute specific channels programmatically

**Use cases:**
- "Mark all Gmail newsletters as read"
- "Reply to IG DMs from anyone marked 'friend' in contacts with a preset message"
- "Dismiss all shopping notifications automatically"

**New actions:**
- `notification_action(notification_key="...", action_index=0)`
- `notification_dismiss(notification_key="...")`
- `notification_reply(notification_key="...", text="...")`
- `notification_watch()` — event_bus stream of posted/removed

**Complexity:** Medium, needs companion APK.

---

## 9. 🗺️ Location Awareness

**Problem:** Phone knows where it is. Agent doesn't.

**Approach:**
- `dumpsys location` has last-known fix (when permissions allow)
- Companion APK with FINE_LOCATION → real GPS streaming
- Parse nearby WiFi BSSIDs → approximate location via Mozilla Location Service
- Cell tower ID → approximate location via OpenCellID (no permission needed)

**Use cases:**
- "Where is the phone right now?" (left it somewhere)
- "Alert me if phone leaves home radius"
- Geofencing: "Turn on DND when I get to the office"
- Context-aware: "It's at home, battery is low → probably safe to dim"

**New actions:**
- `location_get(accuracy="fine|coarse|cell")` → {lat, lon, accuracy_m, source}
- `location_history()` — from Google Timeline if available
- `geofence_enter(lat, lon, radius_m, event_name)` — emits on event_bus

**Complexity:** Low (coarse via cell/wifi) to Medium (fine via APK).

---

## 10. 🤖 Multi-Device Mesh (Zenoh for Phones)

**Problem:** One DevDuck controls one phone. What if you have 3 phones and 2
tablets lying around?

**Approach:**
- Each phone already shows up as a unique adb serial
- Treat each as a peer in the existing zenoh mesh
- `adb -s <serial>` already supported via `serial` param
- Orchestrator: "sync contacts between all Android devices", "screenshot all
  devices simultaneously for a family photo dashboard"

**Use cases:**
- Turn old phones into always-on cameras / sensors in different rooms
- A/B test app UI across device generations automatically
- Fleet phones for testing (already a thing — but agentic)
- "Dim every screen at bedtime"

**New actions:**
- `fleet_list()` — all connected devices
- `fleet_broadcast(action, **kwargs)` — same command to all
- `fleet_screenshot()` — grid of all devices' screens as one image

**Complexity:** Low (the serial param already works). Biggest lift is UX.

---

## 11. 🔐 Biometric Unlock Assistance

**Problem:** When the screen is locked with PIN/fingerprint/face, we're stuck.

**Approach:**
- Face: can't impersonate, rightly so
- Fingerprint: same
- PIN: `input text <pin>` + `keyevent ENTER` works if we know the PIN
- Pattern: `input swipe` sequence — could work with known pattern coords
- Smart Lock / Trusted Devices: leverage Bluetooth pairing + companion app

**Use cases:**
- User explicitly stores PIN in devduck identity/env, agent can unlock when
  user asks via zenoh from another device
- Trusted-place unlock: phone unlocks when paired Mac is within Bluetooth range

**Complexity:** Low (dangerous — require explicit opt-in + warn heavily).

---

## 12. 📦 App State Hijacking / Automation Recorder

**Problem:** Teaching agent to navigate a new app requires explaining every
click. What if we could record human clicks and replay them semantically?

**Approach:**
- `getevent -l` reads /dev/input/event* streams → raw touch events
- Record a user session → replay via `sendevent` or translate to `input tap`
- With UIAutomator XML snapshots at each step, record becomes
  "tap element with text='Compose' on screen X" — replayable across UI versions

**Use cases:**
- "Show me once how to send money in Venmo, I'll do it for you after"
- Build a library of per-app "recipes" that survive app updates
- Share recipes between users ("how to cancel Amazon order")

**New actions:**
- `record_start()` — begin capturing events
- `record_stop()` → returns semantic recipe
- `record_replay(recipe)`

**Complexity:** High, but extremely valuable.

---

## 13. ⚙️ System Settings Mutation

**Problem:** We read `dumpsys` for settings. What if we change them?

**Approach:** `settings put <namespace> <key> <value>` works for many settings
without root.

**Use cases:**
- "Turn off WiFi automatically when I leave home" (via geofence)
- "Enable airplane mode during meetings"
- "Set screen brightness to match ambient light sensor reading"
- "Toggle developer options"
- "Change default launcher at midnight" (chaos mode)

**New actions:**
- `setting_get(namespace="system|secure|global", key="...")`
- `setting_put(namespace=..., key=..., value=...)`
- Presets: `airplane_mode(on=True)`, `bluetooth(on=True)`, `brightness(0-255)`, `ringer(silent|vibrate|normal)`

**Complexity:** Low. Most settings are reachable without root. Some require
`WRITE_SECURE_SETTINGS` which adb has via `pm grant`.

---

## Priority Matrix

| Feature | Value | Complexity | Ship order |
|---|---|---|---|
| **3. Logcat event stream** | 🔥🔥🔥 | 🟢 low | **1st** |
| **5. Physical camera** | 🔥🔥🔥 | 🟡 med | **2nd** |
| **2. Companion APK** | 🔥🔥🔥🔥 | 🔴 high | **3rd** (unlocks 6, 7, 8) |
| **1. Sensor streams** | 🔥🔥 | 🟡 med | 4th |
| **13. Settings mutation** | 🔥🔥 | 🟢 low | 5th (quick win) |
| **4. Live mirror** | 🔥🔥 | 🟡 med | 6th (great demo) |
| **9. Location** | 🔥🔥 | 🟡 med | 7th |
| **10. Multi-device** | 🔥 | 🟢 low | 8th |
| 12. Record/replay | 🔥🔥🔥 | 🔴 high | later |
| 6. Voice I/O | 🔥🔥 | 🔴 high | later |
| 7. Call control | 🔥🔥 | 🔴 high | needs #2 |
| 8. Notif actions | 🔥🔥 | 🟡 med | needs #2 |
| 11. Biometric unlock | 🔥 | 🟢 low | controversial |

## What we can NEVER do via pure ADB (by design, good)

- Read SMS body text (need default-SMS-app)
- Read contact book (need permission granted via companion APK)
- Read clipboard content from background (Android 10+ blocks)
- Record microphone audio (app permission required)
- Send SMS programmatically (default-SMS-app required)
- Decrypt app data (app sandbox + encryption)
- Modify system files (root required)

These are **features**, not bugs. Companion APK (#2) lifts most of these via
user-granted runtime permissions — the right boundary.

---

## Philosophy

strands-adb is a **closed perception-action loop on a physical device**. The
screenshot-as-image-block unlock (v0.2.0) gave us eyes. The smart UI + sensors
(v0.3.0) gave us dexterity and proprioception. The frontiers above give us:

- Hearing (logcat, notifications)
- Continuous attention (sensor streams)
- External vision (camera)
- Voice (TTS/STT)
- Mobility (multi-device)
- Memory (record/replay)

Eventually, every Android phone becomes a **Strands agent substrate** — a
physical body you can inhabit from anywhere. 📱🤖
