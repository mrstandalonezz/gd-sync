#!/bin/bash
# Installs the `gd` command for Terminal and imports the Alfred workflow.
set -e
cd "$(dirname "$0")"
chmod +x gd.py
mkdir -p "$HOME/.local/bin"
ln -sf "$PWD/gd.py" "$HOME/.local/bin/gd"
python3 build_workflow.py
echo 'Terminal: add  export PATH="$HOME/.local/bin:$PATH"  to ~/.zshrc if `gd` is not found.'
open "GD Sync.alfredworkflow"
