#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# KPatch Tracker — LXD Container Setup
# Ubuntu 24.04 / 26.04 Desktop — run as your normal user
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

CONTAINER="kpatch-tracker"
WORKDIR="/workdir"

echo ""
echo "KPatch Tracker — LXD Setup"
echo ""

# ── Step 1: Install LXD ───────────────────────────────────────────────────────
echo "Step 1 of 5: Install LXD"
if ! command -v lxd &>/dev/null; then
    sudo snap install lxd
fi
sudo lxd init --auto 2>/dev/null || true
if ! groups | grep -q lxd; then
    sudo usermod -aG lxd "$USER"
    echo ""
    echo "  ⚠  You have been added to the 'lxd' group."
    echo "  ⚠  Please LOG OUT and LOG BACK IN, then re-run this script."
    exit 0
fi
echo "  ✓ LXD ready"

# ── Step 2: Create container ─────────────────────────────────────────────────
echo ""
echo "Step 2 of 5: Create container '$CONTAINER'"
if lxc info "$CONTAINER" &>/dev/null; then
    echo "  Container already exists — skipping"
else
    lxc launch ubuntu:24.04 "$CONTAINER"
    echo "  Waiting for network …"
    sleep 8
fi
echo "  ✓ Container running"

# ── Step 3: Python venv + packages ───────────────────────────────────────────
echo ""
echo "Step 3 of 5: Install Python packages"
lxc exec "$CONTAINER" -- apt-get update -qq
lxc exec "$CONTAINER" -- apt-get install -y python3 python3.12-venv -qq
lxc exec "$CONTAINER" -- bash -c \
  'python3 -m venv /workdir/venv && /workdir/venv/bin/pip install requests openai --quiet'
echo "  ✓ Python venv ready at /workdir/venv"

# ── Step 4: Copy project files ────────────────────────────────────────────────
echo ""
echo "Step 4 of 5: Copy project files into container"
lxc exec "$CONTAINER" -- mkdir -p "$WORKDIR"
lxc file push kpatch_tracker.py "$CONTAINER${WORKDIR}/kpatch_tracker.py"
lxc file push agent.py          "$CONTAINER${WORKDIR}/agent.py"
lxc file push CLAUDE.md         "$CONTAINER${WORKDIR}/CLAUDE.md"
echo "  ✓ Files copied to ${WORKDIR} inside the container"

# ── Step 5: API key + model → host dotfile ───────────────────────────────────
DOTFILE="$HOME/.config/kpatch-tracker/env"
echo ""
echo "Step 5 of 5: Configure API key and model"
echo "  Credentials file: $DOTFILE"
echo ""

mkdir -p "$HOME/.config/kpatch-tracker"

EXISTING_KEY=$(grep "^OPENROUTER_API_KEY=" "$DOTFILE" 2>/dev/null | cut -d= -f2)
EXISTING_MODEL=$(grep "^OPENROUTER_MODEL=" "$DOTFILE" 2>/dev/null | cut -d= -f2)

if [[ -n "$EXISTING_KEY" && -n "$EXISTING_MODEL" ]]; then
    KEY_DISPLAY="sk-or-...${EXISTING_KEY: -6}"
    echo "  Dotfile already configured — skipping prompts."
    echo ""
    echo "    OPENROUTER_API_KEY = $KEY_DISPLAY"
    echo "    OPENROUTER_MODEL   = $EXISTING_MODEL"
    echo ""
    echo "  To change either value: nano $DOTFILE"
    API_KEY="$EXISTING_KEY"
    MODEL_ID="$EXISTING_MODEL"
else
    # API key
    if [[ -n "$EXISTING_KEY" ]]; then
        KEY_DISPLAY="sk-or-...${EXISTING_KEY: -6}"
        echo "  API key already set ($KEY_DISPLAY) — press Enter to keep it,"
        echo "  or paste a new one:"
        read -rsp "  OpenRouter API key: " API_KEY
        echo ""
        API_KEY="${API_KEY:-$EXISTING_KEY}"
    else
        echo "  Get a key from: https://openrouter.ai/keys  (starts with sk-or-…)"
        echo ""
        read -rsp "  Paste your OpenRouter API key: " API_KEY
        echo ""
    fi

    if [[ -z "$API_KEY" ]]; then
        echo "  ⚠  No key entered. Add it manually:"
        echo "     echo 'OPENROUTER_API_KEY=sk-or-...' >> $DOTFILE"
    else
        if grep -q "^OPENROUTER_API_KEY=" "$DOTFILE" 2>/dev/null; then
            sed -i "s|^OPENROUTER_API_KEY=.*|OPENROUTER_API_KEY=$API_KEY|" "$DOTFILE"
        else
            echo "OPENROUTER_API_KEY=$API_KEY" >> "$DOTFILE"
        fi
        echo "  ✓ API key saved"
    fi

    # Model — query OpenRouter for available Claude models
    echo ""
    if [[ -z "$API_KEY" ]]; then
        echo "  ⚠  No API key provided — skipping model selection."
        echo "     Set OPENROUTER_MODEL manually in $DOTFILE"
    else
        echo "  Fetching available Claude models from OpenRouter …"
        echo ""

        MODELS=$(curl -s https://openrouter.ai/api/v1/models \
            --header "Authorization: Bearer $API_KEY" \
            | python3 -c "
import json, sys
data = json.load(sys.stdin).get('data', [])
models = [m['id'] for m in data if 'claude' in m['id'].lower() and not m['id'].startswith('~')]
for i, m in enumerate(models, 1):
    print(f'  {i:>2}. {m}')
" 2>/dev/null)

        if [[ -z "$MODELS" ]]; then
            echo "  ⚠  Could not retrieve model list (check your API key)."
            echo "     Using default: anthropic/claude-sonnet-4"
            MODEL_ID="anthropic/claude-sonnet-4"
        else
            echo "$MODELS"
            echo ""

            DEFAULT_NUM=$(echo "$MODELS" | grep -n "claude-sonnet-4" | head -1 | cut -d: -f1 | tr -d ' ')
            DEFAULT_NUM="${DEFAULT_NUM:-1}"
            DEFAULT_ID=$(echo "$MODELS" | sed -n "${DEFAULT_NUM}p" | sed 's/^[[:space:]]*[0-9]*\.[[:space:]]*//')

            echo "  Enter a number to select a model (default: $DEFAULT_NUM — $DEFAULT_ID):"
            read -r MODEL_NUM
            MODEL_NUM="${MODEL_NUM:-$DEFAULT_NUM}"

            MODEL_ID=$(echo "$MODELS" | sed -n "${MODEL_NUM}p" | sed 's/^[[:space:]]*[0-9]*\.[[:space:]]*//')

            if [[ -z "$MODEL_ID" ]]; then
                echo "  ⚠  Invalid selection — using default: $DEFAULT_ID"
                MODEL_ID="$DEFAULT_ID"
            fi
        fi

        if grep -q "^OPENROUTER_MODEL=" "$DOTFILE" 2>/dev/null; then
            sed -i "s|^OPENROUTER_MODEL=.*|OPENROUTER_MODEL=$MODEL_ID|" "$DOTFILE"
        else
            echo "OPENROUTER_MODEL=$MODEL_ID" >> "$DOTFILE"
        fi
        echo "  ✓ Model set to $MODEL_ID"
    fi
fi  # end: dotfile already configured check

# Lock down permissions
chmod 600 "$DOTFILE"
echo "  ✓ $DOTFILE permissions set to 600"

# ── Done ──────────────────────────────────────────────────────────────────────

# Read back actual saved values for display
SAVED_KEY=$(grep "^OPENROUTER_API_KEY=" "$DOTFILE" 2>/dev/null | cut -d= -f2)
SAVED_MODEL=$(grep "^OPENROUTER_MODEL=" "$DOTFILE" 2>/dev/null | cut -d= -f2)

# Mask the key for display — show only last 6 characters
if [[ -n "$SAVED_KEY" ]]; then
    KEY_DISPLAY="sk-or-...${SAVED_KEY: -6}"
else
    KEY_DISPLAY="(not set)"
fi

echo ""
echo "Setup complete."
echo ""
echo "  Credentials file: $DOTFILE"
echo ""
echo "  Current values:"
echo ""
echo "    OPENROUTER_API_KEY = $KEY_DISPLAY"
echo "    OPENROUTER_MODEL   = ${SAVED_MODEL:-(not set)}"
echo ""
echo "  To change either value, edit the file:"
echo "    nano $DOTFILE"
echo ""
echo "  The file must never be committed to git."
echo "  Permissions are set to 600 (readable only by you)."
echo ""
echo "  To run the full agentic workflow:"
echo ""
echo "    lxc exec $CONTAINER \\"
echo "      --env OPENROUTER_API_KEY=\"\$(grep OPENROUTER_API_KEY $DOTFILE | cut -d= -f2)\" \\"
echo "      --env OPENROUTER_MODEL=\"\$(grep OPENROUTER_MODEL $DOTFILE | cut -d= -f2)\" \\"
echo "      -- bash -c 'cd $WORKDIR && /workdir/venv/bin/python3 agent.py'"
echo ""
echo "  To run only the Python collector (no AI):"
echo ""
echo "    lxc exec $CONTAINER -- bash -c \\"
echo "      'cd $WORKDIR && /workdir/venv/bin/python3 kpatch_tracker.py'"
echo ""
echo "  To pull the report to your host machine after a run:"
echo ""
echo "    lxc file pull $CONTAINER${WORKDIR}/kpatch_report.md \\"
echo "      ~/Projects/ai-hackathon/files/kpatch_report.md"
echo ""
echo "  To list available Claude models on your OpenRouter account:"
echo ""
echo "    curl -s https://openrouter.ai/api/v1/models \\"
echo "      --header \"Authorization: Bearer \$(grep OPENROUTER_API_KEY $DOTFILE | cut -d= -f2)\" \\"
echo "      | python3 -c \"import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data'] if 'claude' in m['id'].lower()]\""
echo ""
