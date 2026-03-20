#!/bin/bash
# Setup OpenClaw agents for cargo bot
#
# Architecture:
#   - cargo-manager: Full access (calc, status, admin, onboarding)
#   - cargo-client: Limited access (calc, status/lookup only)
#
# Run: bash scripts/setup-agents.sh

set -e

WORKSPACE_MANAGER="/home/dev-moss/ai-cargo/workspace-cargo"
WORKSPACE_CLIENT="/home/dev-moss/ai-cargo/workspace-cargo-client"
SKILLS_DIR="/home/dev-moss/ai-cargo/skills"

echo "=== Step 1: Delete stale sessions ==="
openclaw sessions delete agent:main:main 2>/dev/null || echo "No stale session found"

echo ""
echo "=== Step 2: Create cargo-manager agent ==="
openclaw agents add cargo-manager \
    --workspace "$WORKSPACE_MANAGER" \
    --model "ollama/qwen3.5-nothinker" \
    --description "Cargo manager bot — full access to all skills" \
    2>/dev/null || echo "Agent cargo-manager already exists or error"

echo ""
echo "=== Step 3: Create cargo-client agent ==="
openclaw agents add cargo-client \
    --workspace "$WORKSPACE_CLIENT" \
    --model "ollama/qwen3.5-nothinker" \
    --description "Cargo client bot — calc and status only" \
    2>/dev/null || echo "Agent cargo-client already exists or error"

echo ""
echo "=== Step 4: Copy skills to agent workspaces ==="

# Manager gets all skills
for skill in calc status admin onboarding; do
    mkdir -p "$WORKSPACE_MANAGER/skills/$skill"
    cp -r "$SKILLS_DIR/$skill/"* "$WORKSPACE_MANAGER/skills/$skill/" 2>/dev/null || true
done

# Also copy common module
mkdir -p "$WORKSPACE_MANAGER/skills/common"
cp -r "$SKILLS_DIR/common/"* "$WORKSPACE_MANAGER/skills/common/" 2>/dev/null || true

# Client gets only calc and status
for skill in calc status; do
    mkdir -p "$WORKSPACE_CLIENT/skills/$skill"
    cp -r "$SKILLS_DIR/$skill/"* "$WORKSPACE_CLIENT/skills/$skill/" 2>/dev/null || true
done

mkdir -p "$WORKSPACE_CLIENT/skills/common"
cp -r "$SKILLS_DIR/common/"* "$WORKSPACE_CLIENT/skills/common/" 2>/dev/null || true

echo "Skills copied."

echo ""
echo "=== Step 5: Configure bindings ==="
echo ""
echo "Add this to your openclaw.json under agents.list:"
echo ""
cat <<'BINDINGS'
{
  "agents": {
    "list": [
      {
        "name": "cargo-manager",
        "bindings": [
          {
            "channel": "telegram",
            "account": "cargo-manager-bot",
            "users": ["5093456686", "291678304"]
          }
        ]
      },
      {
        "name": "cargo-client",
        "bindings": [
          {
            "channel": "telegram",
            "account": "cargo-client-bot"
          }
        ]
      }
    ]
  }
}
BINDINGS

echo ""
echo "=== Done ==="
echo "Run: openclaw agents list --bindings   to verify"
echo "Run: openclaw doctor                    to check config"
