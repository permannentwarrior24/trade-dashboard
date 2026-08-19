#!/bin/bash
cd "$(dirname "$0")"

export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"

# Optional feature dependencies. The web UI can still start without them.
if ! command -v okx &>/dev/null; then
    echo "WARNING: okx CLI not found. Account and market data need: npm install -g @okx_ai/okx-trade-cli"
fi

if ! command -v claude &>/dev/null; then
    echo "WARNING: claude CLI not found. AI reports need: npm install -g @anthropic-ai/claude-code"
fi

# Install Python deps only when needed
if ! python3 -c "import fastapi, uvicorn, httpx, html2text, yfinance" 2>/dev/null; then
    python3 -m pip install -q --user --break-system-packages -r requirements.txt
fi

echo "Starting Trade Dashboard on http://127.0.0.1:8501"
python3 run_dashboard.py "$@"
