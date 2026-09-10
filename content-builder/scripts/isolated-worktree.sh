#!/bin/sh
# Create a sibling Git worktree for a content-builder specialist.
#
# Overlapping code (shared components, catalogue writers, build scripts)
# must not share the parent's checkout. The parent copies reviewed files
# back; specialists do not merge.
#
# Usage:
#   content-builder/scripts/isolated-worktree.sh lesson-engineer
#   content-builder/scripts/isolated-worktree.sh lesson-engineer mcf3m-m4-l1
#
# Prints the worktree path on stdout.

set -euo pipefail

agent="${1:-}"
label="${2:-}"

if [ -z "$agent" ]; then
  echo "usage: isolated-worktree.sh <agent-name> [lesson-id]" >&2
  exit 2
fi

root="$(git rev-parse --show-toplevel)"
parent_dir="$(dirname "$root")"
safe_agent="$(printf '%s' "$agent" | tr -c 'a-zA-Z0-9._-' '-')"
safe_label="$(printf '%s' "$label" | tr -c 'a-zA-Z0-9._-' '-')"
stamp="$(date +%Y%m%d%H%M%S)"

if [ -n "$safe_label" ]; then
  branch="content-builder/wt-${safe_agent}-${safe_label}-${stamp}"
  dest="${parent_dir}/LLOVES-School-wt-${safe_agent}-${safe_label}"
else
  branch="content-builder/wt-${safe_agent}-${stamp}"
  dest="${parent_dir}/LLOVES-School-wt-${safe_agent}-${stamp}"
fi

if [ -e "$dest" ]; then
  echo "worktree path already exists: $dest" >&2
  exit 1
fi

# New branch from HEAD so uncommitted LMS edits stay in the primary checkout.
git worktree add -b "$branch" "$dest" HEAD
echo "$dest"
