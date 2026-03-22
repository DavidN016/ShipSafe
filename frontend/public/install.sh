#!/usr/bin/env bash
# ShipSafe — install pre-push hook inside the repo at .shipsafe/hooks/ and set core.hooksPath.
# Usage: curl -fsSL https://shipsafe.dev/install.sh | bash
#
# This writes a tracked path you can commit so teammates get the same hook after clone
# (run this once per clone, or commit .shipsafe/ and re-run only when the hook changes).

set -euo pipefail

RED=$'\033[0;31m'
GRN=$'\033[0;32m'
YLW=$'\033[0;33m'
RST=$'\033[0m'

DEFAULT_API_URL="https://shipsafe.dev"

usage() {
  cat <<'EOF'
ShipSafe install — creates .shipsafe/hooks/pre-push and runs:
  git config core.hooksPath .shipsafe/hooks

  curl -fsSL https://shipsafe.dev/install.sh | bash

Options:
  -h, --help     Show this help
  -u, --url URL  Default API base URL embedded in the hook (default: https://shipsafe.dev)

Environment (install time):
  SHIPSAFE_API_URL   Same as --url

Runtime (each push):
  SHIPSAFE_API_URL   Override API base URL
  SHIPSAFE_TOKEN     Bearer token if the server sets SHIPSAFE_PREPUSH_TOKEN
  SHIPSAFE_SKIP_PREPUSH=1   Bypass once: SHIPSAFE_SKIP_PREPUSH=1 git push
EOF
}

INSTALL_DEFAULT_API="${SHIPSAFE_API_URL:-$DEFAULT_API_URL}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -u|--url)
      INSTALL_DEFAULT_API="${2:?missing URL after --url}"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if ! command -v git >/dev/null 2>&1; then
  echo "${RED}ShipSafe install: git not found.${RST}" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "${RED}ShipSafe install: python3 is required to write the hook.${RST}" >&2
  exit 1
fi

GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "${RED}ShipSafe install: not inside a git repository.${RST}" >&2
  exit 1
}

HOOK_DIR="$GIT_ROOT/.shipsafe/hooks"
PRE_PUSH="$HOOK_DIR/pre-push"
mkdir -p "$HOOK_DIR"

HOOK_TEMPLATE=$(cat <<'TEMPLATE'
#!/usr/bin/env bash
# ShipSafe pre-push — https://shipsafe.dev/install.sh
# Light client: diff → POST /hooks/prepush → allow/block (heavy analysis on server).

set -euo pipefail

API_URL="${SHIPSAFE_API_URL:-__DEFAULT_API__}"
ENDPOINT="${API_URL%/}/hooks/prepush"
TOKEN="${SHIPSAFE_TOKEN:-}"

RED=$'\033[0;31m'
RST=$'\033[0m'

[[ "${SHIPSAFE_SKIP_PREPUSH:-}" != "1" ]] || exit 0

command -v curl >/dev/null 2>&1 || { echo "${RED}ShipSafe: curl is required.${RST}" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "${RED}ShipSafe: python3 is required.${RST}" >&2; exit 1; }

while read -r local_ref local_sha remote_ref remote_sha; do
  if [[ "$local_sha" == "0000000000000000000000000000000000000000" ]]; then
    continue
  fi

  if [[ "$remote_sha" == "0000000000000000000000000000000000000000" || -z "$remote_sha" ]]; then
    base_sha="4b825dc642cb6eb9a060e54bf8d69288fbee4904"
  else
    base_sha="$remote_sha"
  fi

  repo_root=$(git rev-parse --show-toplevel)
  diff_content=$(git -C "$repo_root" diff --no-color "$base_sha" "$local_sha" 2>/dev/null || true)
  [[ -n "$diff_content" ]] || continue

  tmp=$(mktemp)
  printf '%s' "$diff_content" | REPO_ROOT="$repo_root" LOCAL_SHA="$local_sha" python3 - "$tmp" <<'PY'
import json, os, subprocess, sys
out_path = sys.argv[1]
repo = ""
try:
    repo = subprocess.run(
        ["git", "-C", os.environ["REPO_ROOT"], "remote", "get-url", "origin"],
        capture_output=True, text=True, timeout=10,
    ).stdout.strip()
except (KeyError, subprocess.SubprocessError, FileNotFoundError):
    pass
diff = sys.stdin.read()
body = {
    "raw_diff": diff,
    "repository": repo or None,
    "commit_sha": os.environ.get("LOCAL_SHA") or None,
}
open(out_path, "w", encoding="utf-8").write(json.dumps(body))
PY

  if [[ -n "$TOKEN" ]]; then
    RESP="$(curl -sS -w $'\n%{http_code}' -X POST "$ENDPOINT" -H "Content-Type: application/json" -H "Authorization: Bearer ${TOKEN}" --data-binary @"$tmp")"
  else
    RESP="$(curl -sS -w $'\n%{http_code}' -X POST "$ENDPOINT" -H "Content-Type: application/json" --data-binary @"$tmp")"
  fi
  rm -f "$tmp"

  http_body=$(printf '%s' "$RESP" | sed '$d')
  http_code=$(printf '%s' "$RESP" | tail -n1)

  if [[ "$http_code" != "200" ]]; then
    echo "${RED}ShipSafe: API HTTP ${http_code}${RST}" >&2
    echo "$http_body" >&2
    exit 1
  fi

  if ! printf '%s' "$http_body" | python3 -c "import sys, json; d=json.load(sys.stdin); sys.exit(0 if d.get('allow_push') else 1)" 2>/dev/null; then
    reason=$(printf '%s' "$http_body" | python3 -c "import sys, json; print(json.load(sys.stdin).get('reason') or 'blocked')" 2>/dev/null || echo "blocked")
    echo "${RED}ShipSafe: push blocked — ${reason}${RST}" >&2
    printf '%s' "$http_body" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    for r in d.get('results') or []:
        if r.get('auditor_confirmed_vulnerable'):
            fp = r.get('file_path', '')
            n = len(r.get('vulnerabilities') or [])
            print(f'  · {fp} ({n} finding(s))')
except Exception:
    pass
" 2>/dev/null || true
    exit 1
  fi
done

exit 0
TEMPLATE
)

printf '%s' "$HOOK_TEMPLATE" | python3 -c "import sys; d=sys.argv[1]; t=sys.stdin.read(); sys.stdout.write(t.replace('__DEFAULT_API__', d))" "$INSTALL_DEFAULT_API" >"$PRE_PUSH"
chmod +x "$PRE_PUSH"

git -C "$GIT_ROOT" config core.hooksPath .shipsafe/hooks

echo "${GRN}ShipSafe: wrote ${PRE_PUSH}${RST}"
echo "${GRN}ShipSafe: git config core.hooksPath .shipsafe/hooks (this repo)${RST}"
echo "${YLW}Default API: ${INSTALL_DEFAULT_API%/}/hooks/prepush — commit .shipsafe/ to share the hook with your team.${RST}"
echo "Override: export SHIPSAFE_API_URL=https://your-host"
