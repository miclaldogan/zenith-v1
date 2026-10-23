#!/usr/bin/env bash
# Install `zenith` on PATH for everyday use (Arch Linux; bash/zsh/fish).
#   scripts/install.sh            # copies the package into ~/.local/share/zenith/venv (no repo dependency)
#   scripts/install.sh --editable # links the venv to this checkout (developer mode)
# Needs network once to fetch setuptools for the build; never touches ~/.local/share/zenith/zenith.db.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python}"
VENV="${ZENITH_VENV:-$HOME/.local/share/zenith/venv}"
BIN="${ZENITH_BIN_DIR:-$HOME/.local/bin}"
MODE="${1:-}"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 11), "Zenith requires Python >= 3.11"'
mkdir -p "$(dirname "$VENV")" "$BIN"
[ -x "$VENV/bin/python" ] || "$PYTHON" -m venv "$VENV"
if [ "$MODE" = "--editable" ]; then
  "$VENV/bin/python" -m pip install -q --upgrade -e .
else
  "$VENV/bin/python" -m pip install -q --upgrade .
fi
ln -sf "$VENV/bin/zenith" "$BIN/zenith"
# fish completions (harmless elsewhere)
FISH_DIR="$HOME/.config/fish/completions"
if [ -d "$HOME/.config/fish" ]; then
  mkdir -p "$FISH_DIR"
  cp -f scripts/zenith.fish "$FISH_DIR/zenith.fish"
fi
case ":$PATH:" in *":$BIN:"*) ;; *) echo "note: add $BIN to PATH (fish: fish_add_path $BIN)";; esac
"$BIN/zenith" --version
echo "installed: $BIN/zenith -> $VENV  (run: zenith)"
