#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if ! git diff --quiet || ! git diff --cached --quiet || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
  echo "Build requires a clean checkout so the export passport names the exact source commit." >&2
  echo "Commit the changes or use a clean clone before building the submission image." >&2
  exit 1
fi

export FLOODVALUE_COMMIT="$(git rev-parse --verify HEAD)"
if [[ -n "${FLOODVALUE_GITVERSE_COMMIT:-}" && "$FLOODVALUE_GITVERSE_COMMIT" != "$FLOODVALUE_COMMIT" ]]; then
  echo "FLOODVALUE_GITVERSE_COMMIT does not match the source commit; mirror status will not be asserted." >&2
  unset FLOODVALUE_GITVERSE_COMMIT
fi

# Proof, not a claim: the commit is on GitVerse when a remote-tracking branch of the GitVerse
# repository contains it, as in a clone from GitVerse. A GitHub checkout proves nothing.
unset FLOODVALUE_GITVERSE_STATUS
while read -r remote url _; do
  lower="$(printf '%s' "$url" | tr '[:upper:]' '[:lower:]')"
  if [[ "$lower" == *gitverse.ru*hackrus.experts/kosmo-moskva_misis_zhomarung_cr7mentality_21* ]] \
      && git branch -r --contains "$FLOODVALUE_COMMIT" | grep -q "^[[:space:]]*$remote/"; then
    export FLOODVALUE_GITVERSE_COMMIT="$FLOODVALUE_COMMIT"
    export FLOODVALUE_GITVERSE_STATUS="verified_in_gitverse_remote"
    echo "GitVerse remote $remote contains $FLOODVALUE_COMMIT: the export names it as the GitVerse commit." >&2
    break
  fi
done < <(git remote -v)

if [[ "${CONTAINER_CLI:-}" == "docker" ]]; then
  exec docker compose "$@"
elif [[ "${CONTAINER_CLI:-}" == "podman" ]]; then
  exec podman compose "$@"
elif command -v podman >/dev/null 2>&1; then
  exec podman compose "$@"
elif command -v docker >/dev/null 2>&1; then
  exec docker compose "$@"
else
  echo "Podman Compose or Docker Compose is required." >&2
  exit 127
fi
