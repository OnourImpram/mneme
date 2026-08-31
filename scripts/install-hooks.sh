#!/usr/bin/env bash
# Install repository git hooks into .git/hooks.
# Idempotent: re-running overwrites with the current committed versions.
set -euo pipefail
GITROOT=$(git rev-parse --show-toplevel)
cd "$GITROOT"
mkdir -p .git/hooks
for hook in .githooks/*; do
  [ -f "$hook" ] || continue
  name=$(basename "$hook")
  cp "$hook" ".git/hooks/$name"
  chmod +x ".git/hooks/$name"
  echo "installed: .git/hooks/$name"
done
echo "Done. Pre-push leak gate active (uses the committed public pattern list when the private one is absent)."
