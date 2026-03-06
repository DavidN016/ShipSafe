#!/bin/bash
# ShipSafe pre-push hook: optimized for JSON consistency and UX

API_URL="${SHIPSAFE_API_URL:-http://localhost:8000}"
# For hook testing (accepts raw_diff + file_path): SHIPSAFE_ANALYZE_PATH=/analyze/diff
ENDPOINT="${API_URL}${SHIPSAFE_ANALYZE_PATH:-/analyze}"

while read -r local_ref local_sha remote_ref remote_sha; do
  [[ "$remote_sha" != "0000000000000000000000000000000000000000" ]] || continue

  # Get the diff (Handling new branches with empty tree hash)
  if [[ "$remote_sha" == "0000000000000000000000000000000000000000" || -z "$remote_sha" ]]; then
    diff_content=$(git diff --no-color 4b825dc642cb6eb9a060e54bf8d69288fbee4904 "$local_sha")
  else
    diff_content=$(git diff --no-color "$remote_sha" "$local_sha")
  fi

  [[ -n "$diff_content" ]] || continue

  # Build JSON payload - Match the key 'raw_diff' from our agents.md
  payload=$(printf '%s' "$diff_content" | python3 -c "import sys, json; print(json.dumps({'raw_diff': sys.stdin.read(), 'file_path': 'git-push-context'}))" 2>/dev/null)

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