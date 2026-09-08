#!/usr/bin/env bash
# Triage a branch against a base ref: size per area, code vs test files, config and
# secret-suspect files. Read-only. Usage: triage.sh [base-ref] (default origin/master)
set -euo pipefail

base_ref="${1:-origin/master}"

if ! git merge-base HEAD "$base_ref" >/dev/null 2>&1; then
  echo "no merge base with $base_ref (shallow clone?) — try: git fetch --deepen=300 origin <base> <head>" >&2
  exit 1
fi

echo "== range: $base_ref...HEAD"
echo "commits: $(git log --oneline "$base_ref..HEAD" | wc -l | tr -d ' ')"
git diff --stat "$base_ref...HEAD" | tail -1

echo
echo "== lines changed per top-level area"
git diff --numstat "$base_ref...HEAD" \
  | awk '{ split($3, parts, "/"); area = parts[1]; if (parts[2] != "" && parts[1] != "") area = parts[1] "/" parts[2]; changed[area] += $1 + $2 } END { for (a in changed) printf "%8d  %s\n", changed[a], a }' \
  | sort -rn

echo
echo "== code files (non-test) changed"
git diff --name-status "$base_ref...HEAD" \
  | grep -viE '(^|/)(test|tests|__tests__|spec)(/|$)|\.test\.|\.spec\.|_test\.|Test\.java$' \
  | grep -iE '\.(py|java|kt|ts|tsx|js|jsx|go|rs|rb|cs)$' || true

echo
echo "== test files changed: $(git diff --name-only "$base_ref...HEAD" | grep -ciE '(^|/)(test|tests|__tests__|spec)(/|$)|\.test\.|\.spec\.|_test\.|Test\.java$' || true)"

echo
echo "== config / dependency / secret-suspect files (read every added line)"
git diff --name-only "$base_ref...HEAD" \
  | grep -iE '\.(properties|ya?ml|env|toml|lock|json|ini|cfg)$|Dockerfile|compose|requirements' || echo "(none)"

echo
echo "== removed symbols worth grepping for residues (deleted files)"
git diff --name-status "$base_ref...HEAD" | awk '$1 == "D" { print $2 }' || true
