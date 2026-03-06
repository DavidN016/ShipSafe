#!/bin/bash
# ShipSafe pre-push hook: sends changed files to /analyze (Chroma) or raw diff to /analyze/diff (test)

API_URL="${SHIPSAFE_API_URL:-http://localhost:8000}"
# For hook testing (accepts raw_diff + file_path): SHIPSAFE_ANALYZE_PATH=/analyze/diff
ENDPOINT="${API_URL}${SHIPSAFE_ANALYZE_PATH:-/analyze}"

while read -r local_ref local_sha remote_ref remote_sha; do
  [[ "$remote_sha" != "0000000000000000000000000000000000000000" ]] || continue

  # Base ref for diff (new branch = empty tree, else remote)
  if [[ "$remote_sha" == "0000000000000000000000000000000000000000" || -z "$remote_sha" ]]; then
    base_sha="4b825dc642cb6eb9a060e54bf8d69288fbee4904"
  else
    base_sha="$remote_sha"
  fi

  if [[ "$ENDPOINT" == *"/analyze/diff" ]]; then
    # Test mode: send raw diff only (no Chroma write)
    diff_content=$(git diff --no-color "$base_sha" "$local_sha")
    [[ -n "$diff_content" ]] || continue
    payload=$(printf '%s' "$diff_content" | python3 -c "import sys, json; print(json.dumps({'raw_diff': sys.stdin.read(), 'file_path': 'git-push-context'}))" 2>/dev/null)
  else
    # Production: send full content of changed files to Chroma via /analyze
    changed_paths=$(git diff --name-only --no-color "$base_sha" "$local_sha")
    [[ -n "$changed_paths" ]] || continue
    repository=$(git remote get-url origin 2>/dev/null || echo "")
    payload=$(echo "$changed_paths" | SHIPSAFE_COMMIT_SHA="$local_sha" SHIPSAFE_REPO="$repository" python3 -c '
import os, sys, json, subprocess
commit_sha = os.environ.get("SHIPSAFE_COMMIT_SHA", "")
repo = os.environ.get("SHIPSAFE_REPO", "")
files = []
for path in sys.stdin:
    path = path.strip()
    if not path:
        continue
    try:
        out = subprocess.run(
            ["git", "show", f"{commit_sha}:{path}"],
            capture_output=True,
            timeout=5,
        )
        if out.returncode != 0:
            continue
        content = out.stdout
        if b"\x00" in content:
            continue
        content = content.decode("utf-8", errors="replace")
        files.append({"path": path, "content": content})
    except Exception:
        continue
print(json.dumps({"commit_sha": commit_sha, "repository": repo, "files": files}))
' 2>/dev/null)
  fi

  # Call FastAPI
  response=$(curl -s -X POST "$ENDPOINT" \
    -H "Content-Type: application/json" \
    -d "$payload")

  # Search for the block trigger
  if echo "$response" | grep -qEi '"vulnerability_found"\s*:\s*true'; then
    echo -e "\033[0;31m❌ ShipSafe: Critical Vulnerability Detected.\033[0m"
    echo -e "Patch available at: http://localhost:3000/scans"
    exit 1
  fi
done

exit 0
