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
if ! command -v pyenv >/dev/null 2>&1; then
  echo "🐍 [2/7] Installing pyenv..."
  curl -fsSL https://pyenv.run | bash

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

export PATH="$HOME/.local/bin:$PATH"
grep -q '.local/bin' ~/.bashrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc

# ---------- 4. DevDuck ------------------------------------------
echo "🦆 [4/7] Installing devduck..."
pipx install devduck --force

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
# MODEL_PROVIDER=anthropic
# ANTHROPIC_API_KEY=sk-ant-...
# STRANDS_MODEL_ID=claude-opus-4-latest

# --- Tool surface --------------------
DEVDUCK_TOOLS=devduck.tools:system_prompt,use_github,fetch_github_tool,manage_tools,manage_messages,scheduler,tasks,tunnel,zenoh_peer,notify,identity,openapi,inspect;strands_tools:shell,file_read,file_write

# --- Servers -------------------------
DEVDUCK_ENABLE_WS=true
DEVDUCK_WS_PORT=10001
DEVDUCK_ENABLE_ZENOH=true
DEVDUCK_ENABLE_AGENTCORE_PROXY=false
DEVDUCK_ENABLE_TCP=false
DEVDUCK_ENABLE_MCP=false

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
set -a
[[ -f "$HOME/.config/devduck/env" ]] && source "$HOME/.config/devduck/env"
set +a
exec devduck "$@"
DUCKEOF
chmod +x "$LAUNCHER"

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
