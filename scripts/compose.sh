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
