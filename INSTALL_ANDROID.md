# 🤖 Install DevDuck inside Android (Linux Dev Environment)

Run DevDuck **on the phone itself** using Android 15+'s built-in Linux
Development Environment (Debian-based). No adb needed after setup — the
agent lives and breathes on the device.

This is complementary to `strands-adb` (which controls a phone from outside).
With this, the phone **is** the agent.

---

## 📋 Prerequisites

- **Android 15+ / Pixel 8+** (Linux Terminal is GA on Pixel, rolling out on others)
- **~5 GB free storage** (Debian VM is ~2 GB, deps ~1 GB, headroom)
- **Developer options enabled** → Settings → System → Developer options
- **Linux Development Environment** → Settings → System → Developer options → Linux development environment → **Install** (first run downloads the Debian image)
- Launch the **"Terminal"** app that gets added to your launcher

> **Note:** Linux dev env runs in a VM (pKVM-backed on supported Pixel hardware).
> It has internet + a real Debian userland. You get a full bash + apt ecosystem.

---

## 🚀 One-Shot Install Script

Copy-paste this entire block into the Linux Terminal app on your phone.
It does everything: system deps, pyenv, Python 3.13, pipx, devduck, shell
integration, service autostart.

```bash
#!/usr/bin/env bash
# === DevDuck-on-Android installer ===============================
# Copy-paste into the Android "Terminal" (Linux Dev Env) app.
# Safe to re-run; each step is idempotent.
# ================================================================

set -euo pipefail

echo "🦆 DevDuck-on-Android installer"
echo "================================"

# ---------- 1. System deps --------------------------------------
echo "📦 [1/7] Installing system dependencies..."
sudo apt-get update -y
sudo apt-get install -y --no-install-recommends \
  build-essential git curl wget ca-certificates \
  libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
  libffi-dev liblzma-dev tk-dev uuid-dev \
  pkg-config \
  ffmpeg portaudio19-dev \
  python3-dev python3-venv python3-pip \
  jq

# ---------- 2. pyenv + Python 3.13 ------------------------------
# Android's Debian ships py3.11; devduck wants 3.13.
if ! command -v pyenv >/dev/null 2>&1; then
  echo "🐍 [2/7] Installing pyenv..."
  curl -fsSL https://pyenv.run | bash

  # shell hook
  PYENV_LINES='
# pyenv
export PYENV_ROOT="$HOME/.pyenv"
[[ -d $PYENV_ROOT/bin ]] && export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init - bash)"
'
  grep -q "pyenv init" ~/.bashrc || echo "$PYENV_LINES" >> ~/.bashrc
  export PYENV_ROOT="$HOME/.pyenv"
  export PATH="$PYENV_ROOT/bin:$PATH"
  eval "$(pyenv init - bash)"
else
  echo "🐍 [2/7] pyenv already installed."
  export PYENV_ROOT="$HOME/.pyenv"
  export PATH="$PYENV_ROOT/bin:$PATH"
  eval "$(pyenv init - bash)"
fi

PY_VER="3.13.5"
if ! pyenv versions --bare | grep -q "^${PY_VER}\$"; then
  echo "🐍 Installing Python ${PY_VER} (this takes ~5-8 min on phone)..."
  pyenv install "${PY_VER}"
fi
pyenv global "${PY_VER}"
python3 --version

# ---------- 3. pipx ---------------------------------------------
echo "📥 [3/7] Installing pipx..."
python3 -m pip install --user --upgrade pip pipx
python3 -m pipx ensurepath

# Refresh PATH
export PATH="$HOME/.local/bin:$PATH"
grep -q '.local/bin' ~/.bashrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc

# ---------- 4. DevDuck ------------------------------------------
echo "🦆 [4/7] Installing devduck..."
pipx install devduck --force

# Verify
which devduck
devduck --help >/dev/null && echo "✅ devduck CLI ready."

# ---------- 5. Config directory + env ---------------------------
echo "⚙️  [5/7] Writing default config..."
mkdir -p "$HOME/.config/devduck"
ENV_FILE="$HOME/.config/devduck/env"

if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<'ENVEOF'
# DevDuck environment — edit as needed
# -------------------------------------

# --- Model provider (pick ONE) -------
# Bedrock (recommended, needs AWS creds):
# MODEL_PROVIDER=bedrock
# AWS_BEARER_TOKEN_BEDROCK=your_token
# STRANDS_MODEL_ID=global.anthropic.claude-opus-4-7

# Anthropic:
# MODEL_PROVIDER=anthropic
# ANTHROPIC_API_KEY=sk-ant-...
# STRANDS_MODEL_ID=claude-opus-4-latest

# OpenAI:
# MODEL_PROVIDER=openai
# OPENAI_API_KEY=sk-...
# STRANDS_MODEL_ID=gpt-4o

# Ollama (local, runs on-device — small models only on phone):
# MODEL_PROVIDER=ollama
# STRANDS_MODEL_ID=qwen3:1.7b
# OLLAMA_HOST=http://localhost:11434

# --- Tool surface --------------------
# Slim set for phone (no GUI stuff):
DEVDUCK_TOOLS=devduck.tools:system_prompt,use_github,fetch_github_tool,manage_tools,manage_messages,scheduler,tasks,tunnel,zenoh_peer,notify,identity,openapi,inspect;strands_tools:shell,file_read,file_write

# --- Servers -------------------------
DEVDUCK_ENABLE_WS=true
DEVDUCK_WS_PORT=10001
DEVDUCK_ENABLE_ZENOH=true
DEVDUCK_ENABLE_AGENTCORE_PROXY=false
DEVDUCK_ENABLE_TCP=false
DEVDUCK_ENABLE_MCP=false

# --- Misc ----------------------------
BYPASS_TOOL_CONSENT=true
STRANDS_MAX_TOKENS=32000
STRANDS_TEMPERATURE=1.0
ENVEOF
  echo "✏️  Edit $ENV_FILE to add your API keys."
fi

# ---------- 6. Launcher script ----------------------------------
echo "🚀 [6/7] Creating launcher..."
LAUNCHER="$HOME/.local/bin/duck"
cat > "$LAUNCHER" <<'DUCKEOF'
#!/usr/bin/env bash
# Launch devduck with env file sourced
set -a
[[ -f "$HOME/.config/devduck/env" ]] && source "$HOME/.config/devduck/env"
set +a
exec devduck "$@"
DUCKEOF
chmod +x "$LAUNCHER"

# ---------- 7. Summary ------------------------------------------
echo ""
echo "================================================"
echo "🎉  DevDuck-on-Android installed successfully."
echo "================================================"
echo ""
echo "Next steps:"
echo "  1) Restart your shell:    exec bash"
echo "  2) Add your API key:       nano $ENV_FILE"
echo "  3) Launch devduck:         duck"
echo "  4) Or run a one-shot:      duck 'what is my hostname'"
echo ""
echo "Config: $ENV_FILE"
echo "Logs:   /tmp/devduck/logs/devduck.log"
echo ""
```

**Run it:**
```bash
bash <(curl -fsSL https://raw.githubusercontent.com/cagataycali/strands-adb/main/install_android.sh)
```
*(or paste the script inline)*

---

## 🔑 Add your API key

```bash
nano ~/.config/devduck/env
```

Uncomment one provider block and paste your key. Save (Ctrl+O, Enter, Ctrl+X).

---

## 🎬 First Launch

```bash
exec bash          # reload PATH
duck               # interactive REPL
```

Or one-shot:
```bash
duck "what's the linux kernel version? save to /tmp/uname.txt"
```

---

## 🔁 Run as a background service (optional)

DevDuck has a built-in `service` tool that uses systemd. Inside the Linux
Terminal:

```bash
duck 'service(action="install", name="duck-phone",
              startup_prompt="You are the on-device Android agent. Stay alive.",
              env_vars={"ANTHROPIC_API_KEY": "sk-ant-..."})'
```

This creates a user-level systemd unit that:
- Starts DevDuck on terminal-app launch
- Auto-restarts on crash
- Keeps scheduler + telegram listeners alive

Manage:
```bash
duck 'service(action="status", name="duck-phone")'
duck 'service(action="logs",   name="duck-phone", lines=50)'
duck 'service(action="stop",   name="duck-phone")'
duck 'service(action="uninstall", name="duck-phone")'
```

> **Caveat:** the Linux VM only runs while the Terminal app is open (or a
> pinned foreground service holds it). Android will eventually suspend an
> idle VM. For true always-on, use the `tunnel` tool + your laptop as host.

---

## 🌐 Connect phone-DevDuck to your laptop-DevDuck (Zenoh)

Both instances auto-discover each other via Zenoh multicast **on the same
Wi-Fi**. Start phone-side:

```bash
duck
# inside REPL:
🦆 zenoh_peer(action="start")
🦆 zenoh_peer(action="list_peers")
```

You should see your laptop + any other DevDucks on the network. Now
broadcast from laptop:

```
🦆 zenoh_peer(action="broadcast", message="what's your battery level?")
```

The phone-side agent receives it, answers via shell (`cat /sys/class/power_supply/*/capacity` works inside the VM on some devices, or fallback to querying Android via `termux-api` bridge if you have Termux installed alongside).

---

## 🧩 Why this is different from `strands-adb`

| | `strands-adb` (laptop) | DevDuck-on-phone (this doc) |
|---|---|---|
| Where it runs | Your Mac/Linux | Inside Android's Linux VM |
| Access to UI | ✅ via adb (screenshot, tap, swipe) | ❌ (Linux VM can't see Android UI) |
| Access to sensors/camera | ✅ via dumpsys / am intents | ❌ (sandboxed from Android) |
| Compute | Full laptop GPU/CPU | Phone CPU (limited, ~2GB RAM for VM) |
| Network | Home/office wifi | Phone's LTE/wifi — **travels with you** |
| Storage | Laptop disk | Phone-local, persistent across reboots |
| Best for | **Automating the phone** | **Phone as a roaming agent host** |

Use both together: laptop DevDuck drives the phone via adb, phone DevDuck
handles network-adjacent tasks that want to live on the device (scheduled
jobs, offline queues, zenoh rendezvous when you leave home Wi-Fi).

---

## 🛠️ Troubleshooting

**`pip install` fails with "externally-managed-environment"**
→ That's why we use `pipx`. Make sure step 3 ran.

**`pyenv install` crashes mid-build**
→ Phone ran out of RAM. Close other apps, re-run. Debian VM is capped
at ~2 GB on most devices.

**`portaudio19-dev` fails to install**
→ Normal — speech tools aren't critical. Skip with:
```bash
sudo apt-get install -y --no-install-recommends \
  build-essential git curl ... # remove portaudio from your list
```
Then in `DEVDUCK_TOOLS` don't list `listen` or `speech_to_speech`.

**Terminal app closed → DevDuck died**
→ Expected. The Linux VM pauses when the app is backgrounded for too
long. Use Android's "Keep awake while charging" in dev options, or pin
the Terminal in recents.

**Want to SSH in from laptop?**
→ Install `openssh-server` inside the Linux env, then enable port
forwarding in the Terminal app's settings (Settings → port forward).
Now `ssh -p <fwd-port> droid@localhost` from your Mac.

---

## 🎯 What works well on-device

- ✅ Scheduled jobs (`scheduler` tool) — cron-style, survive reboots (via systemd)
- ✅ GitHub automation (`use_github`)
- ✅ API work (`openapi` tool — hit any REST API)
- ✅ Zenoh peer — phone announces itself to your mesh
- ✅ Tunnel — expose a phone service to the internet via Cloudflare
- ✅ Telegram / WhatsApp listeners (if tokens provided)
- ✅ SQLite memory, `identity` tool — per-device personalities

## What to avoid

- ❌ Heavy models (local Ollama > 3B will OOM the VM)
- ❌ `speech_to_speech` (Nova Sonic needs stable audio pipeline — VM shaky)
- ❌ `use_computer` (no display)
- ❌ `browse` (requires Chrome DevTools Protocol — no Chrome in Debian VM by default)
- ❌ Anything that needs adb from inside the VM (the VM isn't on ADB)

---

## 🏁 Quick Reference

```bash
# Install
bash install_android.sh

# Configure
nano ~/.config/devduck/env

# Run interactive
duck

# One-shot
duck "summarize today's GitHub activity"

# Tail logs
tail -f /tmp/devduck/logs/devduck.log

# Update
pipx upgrade devduck

# Uninstall
pipx uninstall devduck
rm -rf ~/.config/devduck ~/.pyenv
```

---

**Result:** your Pixel is now a full DevDuck node. 🦆📱
Part of your zenoh mesh, discoverable from your laptop, persistent across
reboots, and capable of running any DevDuck tool that doesn't need a UI
or adb access.
