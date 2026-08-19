#!/bin/bash
cd "$(dirname "$0")"

export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"

# Check dependencies
if ! command -v okx &>/dev/null; then
    echo "ERROR: okx CLI not found. Install: npm install -g @okx_ai/okx-trade-cli"
    exit 1
fi

if ! command -v claude &>/dev/null; then
    echo "ERROR: claude CLI not found. Install: npm install -g @anthropic-ai/claude-code"
    exit 1
fi

# Install Python deps
python3 -m pip install -q --user --break-system-packages -r requirements.txt 2>/dev/null

# Load .env if present
if [ -f .env ]; then
    set -a; source .env; set +a
fi

echo "Starting Trade Dashboard on http://127.0.0.1:8501"
python3 -m uvicorn server:app --host 127.0.0.1 --port 8501 --reload
