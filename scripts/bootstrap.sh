#!/usr/bin/env bash
# Safe first-run setup. Does not invoke external agents, scan home, or enable services.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python}"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 11), "Zenith requires Python >= 3.11"'
"$PYTHON" -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/zenith init
.venv/bin/zenith doctor
.venv/bin/python -m unittest discover -s tests -v
printf '\nReady. Next: open this directory with Claude Code and use prompts/CLAUDE_IMPLEMENTATION_EN.md\n'
