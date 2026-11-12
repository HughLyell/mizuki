#!/usr/bin/env bash
# Run Mizuki directly from a git checkout.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
export MIZUKI_DATA_DIR="$PWD/data"
exec python3 -m mizuki "$@"
