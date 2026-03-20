#!/bin/bash
# Fix stale GPT-4o session — delete old agent:main:main session
# After deletion, new session will use configured ollama/qwen3.5-nothinker
#
# Run: bash scripts/fix-stale-session.sh

set -e

echo "=== Checking current sessions ==="
openclaw sessions list 2>/dev/null || echo "Cannot list sessions — run manually: openclaw sessions list"

echo ""
echo "=== Deleting stale session agent:main:main ==="
openclaw sessions delete agent:main:main 2>/dev/null || echo "Session not found or already deleted"

echo ""
echo "=== Verifying ==="
openclaw sessions list 2>/dev/null || echo "Done. Verify with: openclaw sessions list"

echo ""
echo "Next session will use configured model (ollama/qwen3.5-nothinker)"
