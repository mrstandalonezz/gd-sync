# GD Sync - git-like push/pull with Google Drive

Needs Google Drive for desktop (signed in). Origin = a folder inside My Drive. Workspace = local folder (default ~/Desktop).

    gd remote Books                          # set Drive origin (folder inside My Drive)
    gd workspace ~/Desktop                   # default local folder
    gd pull data-intensive-book-summary      # Drive -> ~/Desktop/data-intensive-book-summary
    gd pull to ~/.config data-intensive-book-summary
    gd push data-intensive-book-summary      # ~/Desktop/... -> Drive
    gd push ~/.config/dev-launcher           # explicit path -> origin/dev-launcher
    gd ls                                    # list origin
    flags: -n dry run, --force overwrite newer files, --delete remove extras at the destination

Push moves (cut/paste): after the copy is verified in Drive the local item goes to the Trash (recoverable), with no popup.
It is kept if Drive had newer files (skipped), with --keep, or with "move_on_push": false in config.json.
Files newer at the destination are skipped unless --force. Nothing is deleted unless --delete.
Excluded by default: .DS_Store, node_modules, __pycache__, .venv (edit "exclude" in ~/.config/gd/config.json).
Alfred: type `gd`, then pull / push / ls ...  (Opt+Enter = force).
Folder hints: `gd pull /` lists the origin root, Tab into a folder (`gd pull /Books/`), Enter pulls the highlighted item.
Works the same for `gd ls`, `gd remote /` (folders in My Drive), `gd push ~/…`, `gd workspace ~/…`, `gd root ~/…`.
A nested pull lands at the workspace by its own name: `gd pull Books/Design` -> ~/Desktop/Design.
Pulled items are tracked (~/.config/gd/links.json, like a git upstream): `gd push Design` sends it back to
Books/Design, not to the origin root. Untracked items push to origin/<name>.
Finder: right-click a file/folder -> Quick Actions -> GD Push / GD Pull (refresh it from origin/<name>,
or if it is inside the Drive origin, pull it to the workspace). Right-click a folder -> GD Pull Here… to pick
items from the origin and pull them into that folder. (If missing: System Settings -> Extensions -> Finder.)
Terminal Tab completion (zsh): `gd pull /<Tab>` lists the origin root, Tab again opens folders
(also `gd ls`, `gd remote`). install.sh adds  eval "$(gd completion zsh)"  to ~/.zshrc.
Install: ./install.sh
