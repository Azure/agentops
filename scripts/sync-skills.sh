#!/usr/bin/env bash
# sync-skills.sh — Copy skills from the single source of truth
# (src/agentops/templates/skills/) to the VS Code extension
# (plugins/agentops/skills/).
#
# Run this after editing any SKILL.md in src/agentops/templates/skills/.
# CI will fail if the two directories diverge.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$REPO_ROOT/src/agentops/templates/skills"
DEST_DIR="$REPO_ROOT/plugins/agentops/skills"

if [ ! -d "$SRC_DIR" ]; then
    echo "ERROR: Source directory not found: $SRC_DIR" >&2
    exit 1
fi

synced=0
for skill_dir in "$SRC_DIR"/*/; do
    skill_name="$(basename "$skill_dir")"
    src_file="$skill_dir/SKILL.md"
    dest_file="$DEST_DIR/$skill_name/SKILL.md"

    if [ ! -f "$src_file" ]; then
        continue
    fi

    mkdir -p "$DEST_DIR/$skill_name"
    cp "$src_file" "$dest_file"
    synced=$((synced + 1))
    echo "  ✔ $skill_name/SKILL.md"
done

echo ""
echo "Synced $synced skill(s) from src/agentops/templates/skills/ → plugins/agentops/skills/"
