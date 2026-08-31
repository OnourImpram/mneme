#!/usr/bin/env bash
# Leak detector for content that must never reach the public mneme codebase.
#
# TWO PATTERN SOURCES, and the difference matters:
#
#   .github/sanitization-patterns.public.txt  -- committed. Describes SHAPES:
#       credential formats, home-directory layouts, consumer mail domains.
#       Carries no personal value, so CI can read it.
#   .github/sanitization-patterns.txt         -- gitignored, maintainer-only.
#       Names the specific private terms. Merged in when present.
#
# The public list is what makes this runnable in CI at all. Before it, the
# script required the private file, which CI never has, so the release pipeline
# ran with no leak gate whatsoever -- the one place a leak becomes permanent.
#
# SCOPE: tracked files only (`git ls-files`). That is exactly what a publish
# ships. Scanning the working tree instead used to flag gitignored local notes,
# which is an alarm nobody can act on: the file is already excluded from every
# artifact. A gate that cries wolf on its own author teaches people to bypass
# it, and a bypassed gate protects nothing.
#
# ONARIM: a match names a file and line. Remove the value from tracked content
# (move it to an ignored path, an env var, or a redacted placeholder) and
# re-run. If the pattern itself is wrong, narrow it in the public list -- an
# instrument's fault is not the subject's fault.
#
# Exit status: 0 if no patterns matched, 1 if any matched.
set -u

PUBLIC_PATTERNS=".github/sanitization-patterns.public.txt"
PRIVATE_PATTERNS=".github/sanitization-patterns.txt"

if [ ! -f "$PUBLIC_PATTERNS" ]; then
  echo "::error::Public patterns file not found: $PUBLIC_PATTERNS"
  exit 1
fi

ALLOW_FILE=".github/sanitization-allow.txt"
ALLOW_VALUES=""
if [ -f "$ALLOW_FILE" ]; then
  ALLOW_VALUES=$(grep -v '^[[:space:]]*#' "$ALLOW_FILE" | grep -v '^[[:space:]]*$' || true)
fi

PATTERNS=$(cat "$PUBLIC_PATTERNS")
PRIVATE_LOADED="no"
if [ -f "$PRIVATE_PATTERNS" ]; then
  PATTERNS="$PATTERNS
$(cat "$PRIVATE_PATTERNS")"
  PRIVATE_LOADED="yes"
fi

# Only file types that carry text a human wrote. Binary and lockfiles produce
# noise, not evidence.
FILES=$(git ls-files -- \
  '*.py' '*.ts' '*.tsx' '*.js' '*.mjs' '*.json' '*.md' '*.yml' '*.yaml' \
  '*.toml' '*.sh' '*.txt' \
  ':!.github/sanitization-patterns.public.txt' \
  ':!.github/sanitization-allow.txt' \
  ':!.github/scripts/sanitize.sh' \
  ':!.githooks/pre-push' 2>/dev/null || true)

if [ -z "$FILES" ]; then
  echo "::error::No tracked files to scan -- is this a git checkout?"
  exit 1
fi

FILE_COUNT=$(printf '%s\n' "$FILES" | wc -l | tr -d ' ')

FAILED=0
PATTERN_COUNT=0
while IFS= read -r pattern || [ -n "$pattern" ]; do
  case "$pattern" in
    ""|"#"*) continue ;;
  esac
  PATTERN_COUNT=$((PATTERN_COUNT + 1))
  MATCH=$(printf '%s\n' "$FILES" | tr '\n' '\0' \
    | xargs -0 grep -nHIiE "$pattern" 2>/dev/null || true)
  if [ -n "$MATCH" ] && [ -n "$ALLOW_VALUES" ]; then
    # Drop lines whose match is a reviewed placeholder. This filters whole
    # lines, so a line carrying an allowlisted example AND a real secret would
    # be missed -- accepted, because a fixture line holds one value.
    MATCH=$(printf '%s\n' "$MATCH" | grep -Fv -f <(printf '%s\n' "$ALLOW_VALUES") || true)
  fi
  if [ -n "$MATCH" ]; then
    echo "::error::Sanitization pattern matched: $pattern"
    echo "$MATCH" | head -20
    FAILED=1
  fi
done <<EOF
$PATTERNS
EOF

if [ "$FAILED" -eq 1 ]; then
  echo "Sanitization gate FAILED. Remove the flagged content from tracked files."
  exit 1
fi
echo "Sanitization gate PASSED ($PATTERN_COUNT patterns, $FILE_COUNT tracked files, private list: $PRIVATE_LOADED)."
