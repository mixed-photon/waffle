#!/usr/bin/env bash
# Regenerate every pinned lock file. Core first: backend locks are constrained by it.
set -euo pipefail
cd "$(dirname "$0")/.."
compile() { uv pip compile "$@" --universal --python-version 3.13 --generate-hashes --quiet; }
compile requirements/core.in -o requirements/core.lock
for backend in gdsfactory beamz; do
  compile "third_party/$backend/requirements.in" -c requirements/core.lock \
    -o "third_party/$backend/requirements.lock"
done
