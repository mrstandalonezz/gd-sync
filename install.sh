#!/bin/bash
# Installs the `gd` command for Terminal and imports the Alfred workflow.
set -e
cd "$(dirname "$0")"
chmod +x gd.py
mkdir -p "$HOME/.local/bin"
ln -sf "$PWD/gd.py" "$HOME/.local/bin/gd"
python3 build_workflow.py
LINE='command -v gd >/dev/null && eval "$(gd completion zsh)"   # gd Tab completion'
grep -qF 'gd completion zsh' "$HOME/.zshrc" 2>/dev/null || echo "$LINE" >> "$HOME/.zshrc"
python3 build_quick_actions.py   # Finder right-click: GD Push / GD Pull / GD Pull Here…
/System/Library/CoreServices/pbs -update 2>/dev/null || true
echo 'Terminal: add  export PATH="$HOME/.local/bin:$PATH"  to ~/.zshrc if `gd` is not found.'
open "GD Sync.alfredworkflow"
