#!/usr/bin/env bash
# Reproducible test invocation. Uses the project venv when present (editable install from
# scripts/bootstrap.sh); otherwise runs straight from the source tree with no installation.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  PY="${PYTHON:-python}"
  export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
fi
exec "$PY" -m unittest discover -s tests -v "$@"
