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

Files newer at the destination are skipped unless --force. Nothing is deleted unless --delete.
Excluded by default: .DS_Store, node_modules, __pycache__, .venv (edit "exclude" in ~/.config/gd/config.json).
Alfred: type `gd`, then pull / push / ls ...  (Opt+Enter = force).
Install: ./install.sh
