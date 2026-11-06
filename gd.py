#!/usr/bin/env python3
"""gd - push/pull files and folders between this Mac and a Google Drive folder (like git push/pull).

Work on Drive files (workspace):
  gd pull NAME [to DIR] [--force] [-n]        Drive origin/NAME -> DIR/<basename>  (DIR defaults to workspace)
                                              "/" = origin root, e.g. gd pull /resources/Books/test.txt
  gd push NAME [from DIR] [--force] [--keep] [-n]
                                              DIR/NAME -> back to where it was pulled from (else origin/NAME),
                                              then the local copy goes to the Trash (cut/paste, no popup)
  gd push ~/some/path                         explicit local path (same rules)
  flags: -n dry run, --force overwrite newer files, --keep keep the local copy, --delete remove extras at destination

Back up local config files (local stays, Drive keeps an up-to-date copy in mac-backup/<Mac name>/):
  gd backup add PATH...                       register + back up now (to mac-backup/<Mac name>/...)
  gd backup add PATH... to DRIVE_DIR          ...or to your own Drive folder: "to /resources/productivity"
                                              (relative to the Drive root) or a full ~/Library/CloudStorage/... path
  gd backup rm PATH...                        unregister (the Drive copy is kept)
  gd backup ls                                status of each registered item
  gd backup [-q]                              back up changed items now
  gd backup auto on|off                       automatic: on every change + hourly (launchd)
  gd restore [PATH...] [-n] [--force] [--from NAME]
                                              put backups back in place (new Mac); differing local files kept unless --force

Settings:
  gd ls [SUB]                                 list the Drive origin
  gd root [PATH|auto]                         Drive root folder on this Mac (default: auto-detect My Drive)
  gd remote [PATH]                            origin = a folder inside the root
  gd workspace [DIR]                          default local folder (default ~/Desktop)
  gd config                                   show settings
  gd completion zsh                           zsh Tab completion: eval "$(gd completion zsh)"
  gd finder push|pull|pullhere|backup PATH... (used by the Finder Quick Actions)

A file that is NEWER at the destination is skipped unless --force (so you never lose newer work).
Uses the Google Drive for desktop folder (no login here). Config: ~/.config/gd/config.json
"""
import filecmp, glob, json, os, shlex, shutil, subprocess, sys

CFG_DIR = os.path.expanduser("~/.config/gd")
CFG = os.path.join(CFG_DIR, "config.json")
LINKS = os.path.join(CFG_DIR, "links.json")  # local abs path -> origin-relative path it was pulled from / pushed to
DEFAULTS = {"origin": "", "workspace": "~/Desktop", "mount": "", "confirm_overwrite": True, "move_on_push": True,
            "backup": [], "backup_dir": "", "backup_dest": {},
            "exclude": [".DS_Store", "node_modules", "__pycache__", ".venv"]}
TOL = 2  # seconds of mtime tolerance (Drive rounds timestamps)


# ---------- config ----------
def load():
    cfg = dict(DEFAULTS)
    if os.path.exists(CFG):
        with open(CFG) as f:
            cfg.update(json.load(f))
    return cfg


def save(cfg):
    os.makedirs(CFG_DIR, exist_ok=True)
    with open(CFG, "w") as f:
        json.dump(cfg, f, indent=2)


def load_links():
    try:
        with open(LINKS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def remember(local, rel):
    links = load_links()
    links[os.path.abspath(local).rstrip("/")] = rel.strip("/")
    os.makedirs(CFG_DIR, exist_ok=True)
    with open(LINKS, "w") as f:
        json.dump(links, f, indent=2)


def tracked(local):
    """origin-relative path this local item syncs with (where it was pulled from), else its basename."""
    local = os.path.abspath(os.path.expanduser(local)).rstrip("/")
    return load_links().get(local, os.path.basename(local))


def mount_root(cfg):
    if cfg["mount"]:
        return os.path.expanduser(cfg["mount"])
    found = sorted(glob.glob(os.path.expanduser("~/Library/CloudStorage/GoogleDrive-*/My Drive")))
    if not found:
        raise SystemExit("Google Drive for desktop not found. Install it and sign in, "
                         "or set \"mount\" in ~/.config/gd/config.json")
    return found[0]


def set_root(cfg, val):
    if val.strip().lower() == "auto":
        cfg["mount"] = ""
        save(cfg)
        return "Root reset to auto-detect: " + mount_root(cfg)
    path = os.path.abspath(os.path.expanduser(val))
    if not os.path.isdir(path):
        raise SystemExit(f"Folder not found: {path}")
    cfg["mount"] = path
    save(cfg)
    return "Root set to " + path


def origin_dir(cfg):
    return os.path.join(mount_root(cfg), cfg["origin"].strip("/"))


def workspace(cfg, override=None):
    return os.path.abspath(os.path.expanduser(override or cfg["workspace"]))


# ---------- notify / confirm ----------
def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def notify(title, msg):
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification "{esc(msg[:220])}" with title "{esc(title)}"'],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def confirm(msg):
    try:
        r = subprocess.run(["osascript", "-e",
                            f'display dialog "{esc(msg)}" buttons {{"Cancel", "Continue"}} default button "Continue" '
                            f'with icon caution'], capture_output=True, timeout=300)
        return r.returncode == 0
    except Exception:
        return True


# ---------- sync engine ----------
def files_under(root, exclude):
    """relative file paths under root (a dir) with excluded names pruned."""
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in exclude]
        for f in files:
            if f not in exclude:
                out.append(os.path.relpath(os.path.join(base, f), root))
    return out


def plan(src, dst, exclude, delete=False):
    """Compare src -> dst. Returns dict of lists of relative paths (or '' for a single file)."""
    p = {"new": [], "update": [], "conflict": [], "same": [], "delete": []}
    pairs = {}
    if os.path.isfile(src):
        d = dst if not os.path.isdir(dst) else os.path.join(dst, os.path.basename(src))
        pairs[""] = (src, d)
    else:
        for rel in files_under(src, exclude):
            pairs[rel] = (os.path.join(src, rel), os.path.join(dst, rel))
    for rel, (s, d) in pairs.items():
        if not os.path.exists(d):
            p["new"].append(rel)
            continue
        ss, ds = os.stat(s), os.stat(d)
        close = abs(ss.st_mtime - ds.st_mtime) <= TOL  # Drive rounds timestamps, so allow a little slack...
        if ss.st_size == ds.st_size and close and (ss.st_mtime == ds.st_mtime or filecmp.cmp(s, d, shallow=False)):
            p["same"].append(rel)  # ...but when they are not identical, compare the bytes (fast same-size edits)
        elif close:
            p["update"].append(rel)
        elif ss.st_mtime < ds.st_mtime - TOL:
            p["conflict"].append(rel)  # destination is newer
        else:
            p["update"].append(rel)
    if delete and os.path.isdir(src) and os.path.isdir(dst):
        keep = set(pairs)
        p["delete"] = [r for r in files_under(dst, exclude) if r not in keep]
    p["_pairs"] = pairs
    return p


def apply_plan(p, force=False):
    todo = p["new"] + p["update"] + (p["conflict"] if force else [])
    for rel in todo:
        s, d = p["_pairs"][rel]
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copyfile(s, d)
        st = os.stat(s)
        os.utime(d, (st.st_atime, st.st_mtime))  # keep mtime so the next compare says "same"
        try:
            shutil.copymode(s, d)  # keep permissions (e.g. 600 on ~/.ssh files)
        except OSError:
            pass
    return len(todo)


TRASH_JS = """ObjC.import('Foundation');
function run(argv) {
  var fm = $.NSFileManager.defaultManager, bad = [];
  argv.forEach(function (p) {
    var err = $();
    if (!fm.trashItemAtURLResultingItemURLError($.NSURL.fileURLWithPath(p), null, err)) bad.push(p);
  });
  return bad.join('\\n');
}"""


def trash(paths):
    """Move to the Trash via NSFileManager, so iCloud/Drive File Provider folders (e.g. an iCloud Desktop)
    and Finder stay in sync (a plain unlink there leaves Finder ghosts -> error -8058). Returns failed paths."""
    if not paths:
        return []
    try:
        r = subprocess.run(["osascript", "-l", "JavaScript", "-e", TRASH_JS, "--"] + paths,
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return paths
        return [x for x in r.stdout.strip().split("\n") if x]
    except Exception:
        return paths


def remove_local(p, src, force):
    """After a push: move the local copy to the Trash once it is verified in Drive.
    Excluded files (node_modules, .venv, ...) were not pushed, so a folder that has them is only partly trashed."""
    home = os.path.expanduser("~")
    if src in ("/", home) or inside(home, src):
        return "kept local (refusing to remove a home/system folder)"
    if p["conflict"] and not force:
        return "kept local (Drive has newer files)"
    done = p["new"] + p["update"] + p["same"] + (p["conflict"] if force else [])
    verified = [rel for rel in done
                if os.path.isfile(p["_pairs"][rel][1])
                and os.path.getsize(p["_pairs"][rel][1]) == os.path.getsize(p["_pairs"][rel][0])]
    if os.path.isdir(src) and len(verified) == len(done) and set(verified) == set(files_under(src, set())):
        targets = [src]  # everything in the folder is in Drive: trash the folder in one go
    else:
        targets = [p["_pairs"][rel][0] for rel in verified]
    failed = trash(targets)
    if failed:
        return f"kept local: could not move {len(failed)} item(s) to the Trash"
    if os.path.isdir(src):  # tidy folders emptied by a partial trash
        for base, dirs, files in os.walk(src, topdown=False):
            if not os.listdir(base) or os.listdir(base) == [".DS_Store"]:
                shutil.rmtree(base, ignore_errors=True)
    left = os.path.exists(src)
    return f"moved to Drive (local copy in Trash{', excluded files left' if left else ''})"


def summary(p, force, applied):
    parts = [f"{len(p['new'])} new", f"{len(p['update'])} updated"]
    if p["conflict"]:
        parts.append(f"{len(p['conflict'])} " + ("overwritten (newer at destination)" if force
                                               else "skipped (destination newer, use --force)"))
    if p["delete"]:
        parts.append(f"{len(p['delete'])} deleted")
    return ", ".join(parts) + f", {len(p['same'])} unchanged"


def transfer(kind, name, other_dir, flags, ui=False):
    cfg = load()
    origin = origin_dir(cfg)
    ws = workspace(cfg, other_dir)
    explicit = name.startswith(("/", "~", "./", "../"))
    if kind == "pull":
        name = name.strip("/")  # "/Books/x" = origin-relative
        if not name:
            raise SystemExit("gd pull: name a file or folder inside the origin")
        src, dst = os.path.join(origin, name), os.path.join(ws, os.path.basename(name))
    else:
        local = os.path.abspath(os.path.expanduser(name)) if explicit else os.path.join(ws, name)
        local = local.rstrip("/")
        name = tracked(local)  # back to where it was pulled from, else origin/<basename>
        src, dst = local, os.path.join(origin, name)
    if not os.path.exists(src):
        msg = f"Not found: {src}"
        notify("gd " + kind, msg) if ui else None
        raise SystemExit(msg)
    if kind == "push":
        os.makedirs(origin, exist_ok=True)
    exclude = set(cfg["exclude"])
    p = plan(src, dst, exclude, delete="--delete" in flags)
    force = "--force" in flags
    where = f"{src}  ->  {dst}"
    move = kind == "push" and cfg.get("move_on_push", True) and "--keep" not in flags
    if "-n" in flags or "--dry-run" in flags:
        text = f"[dry run] {name}: {summary(p, force, 0)}" + (", then remove the local copy" if move else "")
        print(where)
        for k in ("new", "update", "conflict", "delete"):
            for r in p[k]:
                print(f"  {k:9} {r or os.path.basename(src)}")
        print(text)
        return text
    risky = len(p["update"]) + len(p["delete"]) + (len(p["conflict"]) if force else 0)
    if cfg["confirm_overwrite"] and ui and risky and kind == "pull":  # push never pops up a dialog
        if not confirm(f"{kind} '{name}' will overwrite/delete {risky} existing file(s) at the destination.\n\n{where}"):
            notify("gd " + kind, "Cancelled")
            return "cancelled"
    applied = apply_plan(p, force)
    for rel in p["delete"]:
        os.remove(os.path.join(dst, rel))
    remember(dst if kind == "pull" else src, name)
    text = f"{kind} {name}: {summary(p, force, applied)}"
    if move:
        text += "; " + remove_local(p, src, force)
    print(where + "\n" + text)
    if ui:
        notify("✅ gd " + kind if not p["conflict"] or force else "⚠️ gd " + kind, text)
    return text


# ---------- listing ----------
def list_dir(path, hidden=False):
    try:
        return sorted((e for e in os.listdir(path) if hidden or not e.startswith(".") or e == ".config"),
                      key=str.lower)
    except OSError:
        return []


def browse(typed, base=None, dirs_only=False):
    """Path completion for the Alfred filter.
    base=None: typed is a local path (~/..., /...); otherwise typed is relative to base ("/" = base itself).
    Returns (dirpart, folder, [(completion, full_path, is_dir), ...])."""
    if base is None and typed == "~":
        typed = "~/"
    i = typed.rfind("/")
    dirpart, flt = typed[:i + 1], typed[i + 1:]
    folder = os.path.expanduser(dirpart) if base is None else os.path.join(base, dirpart.strip("/"))
    out = []
    for n in list_dir(folder, hidden=flt.startswith(".")):
        full = os.path.join(folder, n)
        isdir = os.path.isdir(full)
        if (isdir or not dirs_only) and flt.lower() in n.lower():
            out.append((dirpart + n, full, isdir))
    out.sort(key=lambda e: not os.path.basename(e[0]).lower().startswith(flt.lower()))  # prefix matches first
    return dirpart, folder, out


# ---------- Alfred script filter ----------
TAB = "\t"


def alfred(query):
    cfg = load()
    q = query.lstrip()
    words = q.split(" ")
    cmd = words[0].lower() if q else ""
    items = []

    def item(title, sub, arg=None, auto=None, **kw):
        d = {"title": title, "subtitle": sub}
        if arg is not None:
            d["arg"] = arg
        else:
            d["valid"] = False
        if auto:
            d["autocomplete"] = auto
        d.update(kw)
        items.append(d)

    def parse_dir(rest, word):  # "to <dir> filter" / "from <dir> filter"
        if rest.startswith(word + " "):
            tail = rest[len(word) + 1:]
            d, _, flt = tail.partition(" ")
            return d, flt.strip()
        return None, rest.strip()

    def is_local(p):
        return p.startswith(("/", "~"))

    drive = lambda rel: f"Drive:{os.path.join(cfg['origin'] or 'My Drive', rel)}"

    if cmd in ("pull", "push") and len(words) > 1:
        rest = q[len(cmd):].strip()
        word = "to" if cmd == "pull" else "from"
        d, flt = parse_dir(rest, word)
        ws = workspace(cfg, d)
        prefix = f"{cmd} " + (f"{word} {d} " if d else "")
        if cmd == "pull":
            dirpart, folder, entries = browse(flt, origin_dir(cfg))
        else:
            dirpart, folder, entries = browse(flt, None if is_local(flt) else ws)

        def transfer_item(title, rel, isdir):
            rel = rel.rstrip("/")
            base = os.path.basename(rel)
            if cmd == "pull":
                rel = rel.lstrip("/")
                arrow = f"{drive(rel)}  →  {ws}/{base}"
            else:
                src = rel if is_local(rel) else f"{ws}/{rel}"
                arrow = f"{src}  →  {drive(tracked(src))}"
            arg = f"{cmd}{TAB}{rel}{TAB}{d or ''}"
            item(title, arrow + ("   (Tab = open folder, ⌥ = force)" if isdir else "   (⌥ = force overwrite newer)"),
                 arg, auto=prefix + rel + "/" if isdir else None,
                 mods={"alt": {"arg": arg + f"{TAB}force",
                               "subtitle": "FORCE: also overwrite files that are newer at the destination"}})

        if not flt.rpartition("/")[2] and dirpart.strip("/") and dirpart not in ("~/",):
            transfer_item(f"⤓ {dirpart}  (this whole folder)", dirpart, False)
        for comp, full, isdir in entries:
            transfer_item(os.path.basename(comp) + ("/" if isdir else ""), comp, isdir)
        if not entries and not items:
            where = drive(dirpart.strip("/")) if cmd == "pull" else folder
            item("Nothing found", f"No match for '{flt.rpartition('/')[2]}' in {where}")
    elif cmd == "ls":
        sub = q[2:].strip()
        dirpart, folder, entries = browse(sub, origin_dir(cfg))
        for comp, full, isdir in entries:
            item(os.path.basename(comp) + ("/" if isdir else ""),
                 full + ("   (Tab = open folder, Enter = reveal)" if isdir else ""),
                 f"open{TAB}{full}", auto=f"ls {comp}/" if isdir else None)
        if not items:
            item("Empty", folder)
    elif cmd in ("root", "remote", "workspace"):
        val = q[len(cmd):].strip()
        if cmd == "remote":
            try:
                base = mount_root(cfg)
            except SystemExit as e:
                base = None
                item("Drive not found", str(e))
            cur, label = cfg["origin"] or "(My Drive root)", "Drive origin"
        elif cmd == "workspace":
            base, cur, label = None, cfg["workspace"], "workspace"
        else:
            base, label = None, "Drive root"
            try:
                cur = mount_root(cfg)
            except SystemExit as e:
                cur = str(e)
        if not val:
            item(f"{label.capitalize()}: {cur}",
                 {"root": "Type: gd root ~/…  (or: gd root auto)", "remote": "Type: gd remote /  to browse My Drive",
                  "workspace": "Type: gd workspace ~/…  to browse"}[cmd])
        elif val == "auto" and cmd == "root":
            item("Reset Drive root to auto-detect", "Find GoogleDrive-*/My Drive automatically", f"root{TAB}auto")
        else:
            browsable = cmd == "remote" and base or is_local(val)
            entries = browse(val, base if cmd == "remote" else None, dirs_only=True)[2] if browsable else []
            shown = val.strip("/") if cmd == "remote" else val
            item(f"Set {label} to '{shown or '(My Drive root)'}'", "Enter to set", f"{cmd}{TAB}{shown}")
            for comp, full, _ in entries:
                target = comp.strip("/") if cmd == "remote" else comp
                item(os.path.basename(comp) + "/", f"{full}   (Tab = open, Enter = set {label})",
                     f"{cmd}{TAB}{target}", auto=f"{cmd} {comp}/")
    elif cmd == "backup":
        n = len(cfg["backup"])
        item(f"Back up now ({n} item{'s' if n != 1 else ''} registered)",
             "Copy changed config files to Drive. Register more: right-click → GD Backup, or gd backup add PATH",
             f"backup{TAB}")
        for key in cfg["backup"]:
            item(key, "registered for backup")
    elif cmd == "config":
        try:
            mount = mount_root(cfg)
        except SystemExit as e:
            mount = str(e)
        item("Origin: " + (cfg["origin"] or "(My Drive root)"), "gd remote <path> to change")
        item("Workspace: " + cfg["workspace"], "gd workspace <dir> to change")
        item("Drive folder: " + mount, "Enter to open config.json", f"edit{TAB}")
    else:
        for c, sub in (("pull", "Drive → this Mac"), ("push", "this Mac → Drive"), ("ls", "list the Drive origin"),
                       ("root", "set the Drive root folder on this Mac"),
                       ("remote", "set the Drive origin folder"), ("workspace", "set the default local folder"),
                       ("config", "show settings"), ("backup", "back up registered config files to Drive now")):
            if not cmd or c.startswith(cmd):
                item("gd " + c, sub, auto=c + " ")
    print(json.dumps({"items": items}))


def run_ui(arg):
    parts = arg.split(TAB)
    kind = parts[0]
    cfg = load()
    try:
        if kind in ("pull", "push"):
            flags = ["--force"] if len(parts) > 3 and parts[3] == "force" else []
            transfer(kind, parts[1], parts[2] or None, flags, ui=True)
        elif kind == "root":
            notify("gd", set_root(cfg, parts[1]))
        elif kind == "remote":
            cfg["origin"] = parts[1].strip("/")
            save(cfg)
            os.makedirs(origin_dir(cfg), exist_ok=True)
            notify("gd", f"Origin set to '{cfg['origin']}'")
        elif kind == "workspace":
            cfg["workspace"] = parts[1]
            save(cfg)
            notify("gd", f"Workspace set to {parts[1]}")
        elif kind == "backup":
            notify("✅ gd backup", backup_run(cfg, quiet=True) or "Drive not available")
        elif kind == "open":
            subprocess.run(["open", parts[1]])
        elif kind == "edit":
            save(cfg)
            subprocess.run(["open", "-t", CFG])
    except SystemExit as e:
        notify("⚠️ gd failed", str(e))
    except Exception as e:
        notify("⚠️ gd failed", f"{type(e).__name__}: {e}")


# ---------- Finder Quick Actions ----------
def inside(path, folder):
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def choose(prompt, options):
    """macOS list picker (multiple selection). Returns the chosen names, [] if cancelled."""
    lst = ", ".join('"' + esc(o) + '"' for o in options)
    r = subprocess.run(["osascript", "-e", f'set r to choose from list {{{lst}}} with prompt "{esc(prompt)}" '
                        f'with multiple selections allowed', "-e",
                        'if r is false then return ""', "-e",
                        'set AppleScript\'s text item delimiters to (ASCII character 9)', "-e", 'return r as text'],
                       capture_output=True, text=True, timeout=600)
    out = r.stdout.rstrip("\n")
    return out.split(TAB) if out else []


def finder(kind, paths):
    cfg = load()
    try:
        root, origin = mount_root(cfg), origin_dir(cfg)
    except SystemExit as e:
        return notify("⚠️ gd failed", str(e))
    for p in (os.path.abspath(x) for x in paths):
        if kind == "push":
            if inside(p, root):
                notify("gd push", f"{os.path.basename(p)} is already inside Google Drive")
                continue
            run_ui(f"push{TAB}{p}{TAB}")
        elif kind == "pull":
            if inside(p, origin) and not inside(origin, p):  # a Drive item -> pull it to the workspace
                run_ui(f"pull{TAB}{os.path.relpath(p, origin)}{TAB}")
            elif inside(p, root):
                notify("gd pull", f"{os.path.basename(p)} is in Drive but outside the origin '{cfg['origin']}'")
            else:  # a local item -> refresh it from where it was pulled from (else origin/<name>)
                run_ui(f"pull{TAB}{tracked(p)}{TAB}{os.path.dirname(p)}")
        elif kind == "backup":  # register + back up now; the local item stays where it is
            try:
                backup_add(cfg, [p])
                notify("✅ gd backup", backup_run(load(), quiet=True, only=[to_key(p)]) or "done")
            except SystemExit as e:
                notify("⚠️ gd backup failed", str(e))
        elif kind == "pullhere":
            target = p if os.path.isdir(p) else os.path.dirname(p)
            names = list_dir(origin)
            if not names:
                return notify("gd pull", f"Drive origin is empty: {origin}")
            for name in choose(f"Pull from Drive:{cfg['origin'] or 'My Drive'} into {target}", names):
                run_ui(f"pull{TAB}{name}{TAB}{target}")


# ---------- backup (local stays the source of truth, Drive keeps a copy) ----------
BACKUP_LOG = os.path.join(CFG_DIR, "backup.log")
AGENT = "com.gd-sync.backup"
AGENT_PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{AGENT}.plist")
REGISTRY = ".gd-backup.json"  # list of registered items, stored in the Drive backup folder for restore
SECRETS = ("id_rsa", "id_ed25519", "id_ecdsa", ".pem", ".key", ".p12", ".env", "credential", "token", "secret")


def machine_name():
    try:
        name = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True).stdout.strip()
    except Exception:
        name = ""
    import re
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or os.uname().nodename).replace("’", "").replace("'", ""))
    return name.strip("-") or "mac"


def backup_root(cfg, machine=None):
    rel = f"mac-backup/{machine}" if machine else (cfg["backup_dir"] or f"mac-backup/{machine_name()}")
    return os.path.join(mount_root(cfg), rel)


def to_key(path):
    """~/x/y -> "~/x/y" (home-relative, portable across Macs); other absolute paths stay absolute."""
    path = os.path.abspath(os.path.expanduser(path)).rstrip("/")
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if inside(path, home) else path


def key_paths(cfg, key, machine=None, dests=None):
    """registered key -> (local path, path of its copy in Drive).
    Custom destination (gd backup add X to DIR): <Drive root>/DIR/<basename>.
    Default: mac-backup/<Mac>/ with the home-relative path, e.g. ~/.ssh/config -> mac-backup/<Mac>/.ssh/config."""
    dest = (cfg["backup_dest"] if dests is None else dests).get(key)
    if dest is not None:
        return os.path.expanduser(key), os.path.join(mount_root(cfg), dest, os.path.basename(key))
    rel = key[2:] if key.startswith("~/") else "_root" + key  # /etc/x -> _root/etc/x
    return os.path.expanduser(key), os.path.join(backup_root(cfg, machine), rel)


def drive_dir(cfg, where):
    """'to' folder -> path relative to the Drive root.
    Accepts a full path inside Drive (~/Library/CloudStorage/.../second-brain/x), or '/x' / 'x' relative to the root."""
    root = mount_root(cfg)
    full = os.path.abspath(os.path.expanduser(where))
    top = "/" + where.strip("/").split("/")[0]
    if not (where.startswith("~") or inside(full, root) or os.path.exists(top)):
        full = os.path.join(root, where.strip("/"))  # '/resources/x' = <Drive root>/resources/x
    if not inside(full, root):
        raise SystemExit(f"'{where}' is not inside the Drive root {root}")
    rel = os.path.relpath(os.path.realpath(full), os.path.realpath(root))
    return "" if rel == "." else rel


def backup_add(cfg, paths, to=None):
    dest = drive_dir(cfg, to) if to else None
    if not cfg["backup_dir"]:
        cfg["backup_dir"] = f"mac-backup/{machine_name()}"  # fixed once, so renaming the Mac keeps the folder
    for p in paths:
        key, full = to_key(p), os.path.abspath(os.path.expanduser(p))
        if not os.path.exists(full):
            print(f"skip (not found): {full}")
            continue
        if inside(full, mount_root(cfg)):
            print(f"skip (already in Drive): {full}")
            continue
        if key not in cfg["backup"]:
            cfg["backup"].append(key)
        if dest is not None:
            cfg["backup_dest"][key] = dest
        else:
            cfg["backup_dest"].pop(key, None)
        warn = any(x in os.path.basename(full).lower() for x in SECRETS)
        print(f"added {key}" + ("   ⚠️ looks like a key/secret: it will be stored as a plain file in your Drive"
                                if warn else ""))
    save(cfg)
    if os.path.exists(AGENT_PLIST):
        backup_auto(cfg, True, quiet=True)  # refresh the watched paths


def backup_rm(cfg, paths):
    for p in paths:
        key = to_key(p)
        if key in cfg["backup"]:
            cfg["backup"].remove(key)
            cfg["backup_dest"].pop(key, None)
            print(f"removed {key} (the copy in Drive is kept)")
        else:
            print(f"not registered: {key}")
    save(cfg)
    if os.path.exists(AGENT_PLIST):
        backup_auto(cfg, True, quiet=True)


def backup_run(cfg, quiet=False, only=None):
    """Copy every registered item that changed to Drive. Never touches the local files."""
    try:
        root = backup_root(cfg)
    except SystemExit as e:  # Drive not mounted (e.g. not signed in yet): try again next time
        if not quiet:
            print(e)
        return
    exclude, lines, changed = set(cfg["exclude"]), [], 0
    for key in only or cfg["backup"]:
        local, remote = key_paths(cfg, key)
        if not os.path.exists(local):
            lines.append(f"missing locally: {key}")
            continue
        own_logs = {"backup.log", "finder.log"} if inside(CFG_DIR, local) else set()  # avoid a change->backup loop
        p = plan(local, remote, exclude | own_logs)
        n = len(p["new"]) + len(p["update"]) + len(p["conflict"])
        if n:
            apply_plan(p, force=True)  # this Mac is the source of truth
            changed += n
            lines.append(f"{key}: {n} file(s) backed up")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, REGISTRY), "w") as f:
        json.dump({"machine": machine_name(), "home": os.path.expanduser("~"), "items": cfg["backup"],
                   "dest": cfg["backup_dest"]}, f, indent=2)
    msg = f"backup: {changed} file(s) updated, {len(cfg['backup'])} item(s) registered"
    if changed or not quiet:
        os.makedirs(CFG_DIR, exist_ok=True)
        import time
        with open(BACKUP_LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "".join("\n    " + l for l in lines) + "\n")
    if not quiet:
        print("\n".join(lines + [msg]))
    return msg


def backup_ls(cfg):
    if not cfg["backup"]:
        print("nothing registered (gd backup add PATH)")
    for key in cfg["backup"]:
        local, remote = key_paths(cfg, key)
        state = "missing locally" if not os.path.exists(local) else \
            "not backed up yet" if not os.path.exists(remote) else \
            "up to date" if not any(plan(local, remote, set(cfg["exclude"]))[k] for k in ("new", "update", "conflict")) \
            else "changed since last backup"
        where = os.path.relpath(os.path.dirname(remote), mount_root(cfg))
        print(f"{key:45} {state:26} -> {where}/")
    print(f"\nDrive folder: {backup_root(cfg)}\nauto backup: {'on' if os.path.exists(AGENT_PLIST) else 'off'}")


def backup_auto(cfg, on, quiet=False):
    import plistlib
    uid = str(os.getuid())
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{AGENT}"], capture_output=True)
    if not on:
        if os.path.exists(AGENT_PLIST):
            os.remove(AGENT_PLIST)
        return print("auto backup off")
    watch = [os.path.expanduser(k) for k in cfg["backup"] if os.path.exists(os.path.expanduser(k))]
    agent = {"Label": AGENT,
             "ProgramArguments": ["/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable,
                                  os.path.realpath(__file__), "backup", "-q"],
             "WatchPaths": watch,      # run when a registered file/folder changes
             "StartInterval": 3600,    # and hourly, to catch changes deep inside folders
             "RunAtLoad": True, "ThrottleInterval": 30, "ProcessType": "Background",
             "StandardErrorPath": BACKUP_LOG}
    os.makedirs(os.path.dirname(AGENT_PLIST), exist_ok=True)
    with open(AGENT_PLIST, "wb") as f:
        plistlib.dump(agent, f)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", AGENT_PLIST], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"launchctl failed: {r.stderr.strip()}")
    if not quiet:
        print(f"auto backup on: on change of {len(watch)} item(s) + hourly. Log: {BACKUP_LOG}")


def restore(cfg, paths, force=False, machine=None, dry=False):
    """Copy backups from Drive back to where they belong. Existing different local files are kept unless --force."""
    root = backup_root(cfg, machine)
    reg = os.path.join(root, REGISTRY)
    if not os.path.exists(reg):
        others = list_dir(os.path.join(mount_root(cfg), "mac-backup"))
        raise SystemExit(f"No backup at {root}" + (f"\nAvailable: {', '.join(others)}  (use --from NAME)" if others else ""))
    with open(reg) as f:
        reg = json.load(f)
    items, dests = reg["items"], reg.get("dest", {})
    keys = [to_key(p) for p in paths] if paths else items
    total = {"new": 0, "same": 0, "kept": 0, "overwritten": 0}
    for key in keys:
        local, remote = key_paths(cfg, key, machine, dests)
        if not os.path.exists(remote):
            print(f"not in backup: {key}")
            continue
        p = plan(remote, local, set())
        differ = p["update"] + p["conflict"]
        if not dry:
            apply_plan({"new": p["new"], "update": differ if force else [], "conflict": [], "_pairs": p["_pairs"]})
        total["new"] += len(p["new"])
        total["same"] += len(p["same"])
        total["overwritten" if force else "kept"] += len(differ)
        print(f"{key}: {len(p['new'])} restored" + (f", {len(differ)} differ locally " +
              ("(overwritten)" if force else "(kept, use --force)") if differ else ""))
        if not dry:  # Drive may not keep unix permissions: make private files private again
            for rel in p["new"] + (differ if force else []):
                d = p["_pairs"][rel][1]
                if "/.ssh/" in d or "/.gnupg/" in d or any(x in os.path.basename(d).lower() for x in SECRETS):
                    os.chmod(d, 0o600)
        if key not in cfg["backup"] and not dry:
            cfg["backup"].append(key)  # keep backing it up on this Mac
            if key in dests:
                cfg["backup_dest"][key] = dests[key]
    if not dry:
        if not cfg["backup_dir"]:
            cfg["backup_dir"] = os.path.relpath(root, mount_root(cfg))
        save(cfg)
    print(("[dry run] " if dry else "") + f"restore: {total['new']} restored, {total['same']} already same, "
          f"{total['kept']} kept (differ), {total['overwritten']} overwritten")


# ---------- shell completion ----------
ZSH_COMPLETION = r"""
_gd() {
  local cmd=${words[2]} prev=${words[CURRENT-1]}
  if (( CURRENT == 2 )); then
    compadd pull push ls root remote workspace config backup restore completion
    return
  fi
  if [[ $cmd == backup && $prev == to ]]; then
    local -a d; d=("${(@f)$(gd _complete remote "${(Q)PREFIX}" 2>/dev/null)}")
    (( $#d )) && compadd -U -S '' -- $d
    return
  fi
  if [[ $prev == (to|from) ]]; then _files -/; return; fi
  case $cmd in
    pull|ls|remote)
      local -a out dirs files; local line
      out=("${(@f)$(gd _complete $cmd "${(Q)PREFIX}" 2>/dev/null)}")
      for line in $out; do [[ $line == */ ]] && dirs+=$line || { [[ -n $line ]] && files+=$line; }; done
      (( $#dirs ))  && compadd -U -S '' -- $dirs     # folders: no space, keep tabbing
      (( $#files )) && compadd -U -- $files ;;
    push) _files ;;
    backup)
      if (( CURRENT == 3 )); then compadd add rm ls auto
      elif [[ ${words[3]} == auto ]]; then compadd on off
      else _files; fi ;;
    restore) _files ;;
    workspace|root) _files -/ ;;
  esac
}
(( $+functions[compdef] )) || { autoload -Uz compinit && compinit; }
compdef _gd gd
"""


def complete(kind, typed):
    """Candidates for the shell: origin-relative paths for pull/ls, folders only for remote ("/" = origin root)."""
    cfg = load()
    base = mount_root(cfg) if kind == "remote" else origin_dir(cfg)
    for comp, full, isdir in browse(typed, base, dirs_only=kind == "remote")[2]:
        print(comp + ("/" if isdir else ""))


# ---------- CLI ----------
def cli(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd, rest = argv[0], argv[1:]
    flags = [a for a in rest if a.startswith("-") and a != "-"]
    args = [a for a in rest if a not in flags]
    cfg = load()
    if cmd in ("pull", "push"):
        other = None
        for word in ("to", "from"):
            if word in args[:-1] and args.index(word) == 0:
                other = args[1]
                args = args[2:]
                break
            if word in args and args.index(word) > 0:
                i = args.index(word)
                other = args[i + 1]
                args = args[:i] + args[i + 2:]
                break
        if not args:
            raise SystemExit(f"usage: gd {cmd} NAME [{'to' if cmd == 'pull' else 'from'} DIR]")
        transfer(cmd, args[0], other, flags)
    elif cmd == "ls":
        d = os.path.join(origin_dir(cfg), args[0] if args else "")
        for n in list_dir(d):
            print(n + ("/" if os.path.isdir(os.path.join(d, n)) else ""))
    elif cmd == "root":
        if args:
            print(set_root(cfg, " ".join(args)))
        else:
            print("root:", mount_root(cfg))
    elif cmd == "remote":
        if args:
            cfg["origin"] = args[0].strip("/")
            save(cfg)
        print("origin:", cfg["origin"] or "(My Drive root)", "->", origin_dir(cfg))
    elif cmd == "workspace":
        if args:
            cfg["workspace"] = args[0]
            save(cfg)
        print("workspace:", workspace(cfg))
    elif cmd == "config":
        print(json.dumps(cfg, indent=2))
        print("drive folder:", mount_root(cfg))
    elif cmd == "backup":
        sub = args[0] if args else ""
        if sub == "add" and args[1:]:
            paths, to = args[1:], None
            if "to" in paths[:-1]:  # gd backup add A B to resources/productivity
                i = paths.index("to")
                paths, to = paths[:i], " ".join(paths[i + 1:])
            backup_add(cfg, paths, to)
            backup_run(load(), only=[to_key(p) for p in paths if to_key(p) in load()["backup"]])
        elif sub == "rm" and args[1:]:
            backup_rm(cfg, args[1:])
        elif sub in ("ls", "list", "status"):
            backup_ls(cfg)
        elif sub == "auto" and args[1:2] in (["on"], ["off"]):
            backup_auto(cfg, args[1] == "on")
        elif not sub:
            backup_run(cfg, quiet="-q" in flags)
        else:
            raise SystemExit("usage: gd backup [add PATH... | rm PATH... | ls | auto on|off] [-q]")
    elif cmd == "restore":
        machine = None
        if "--from" in rest:
            i = rest.index("--from")
            machine = rest[i + 1] if i + 1 < len(rest) else None
            args = [a for a in args if a != machine]
        restore(cfg, args, force="--force" in flags, machine=machine, dry="-n" in flags)
    elif cmd == "completion":
        print(ZSH_COMPLETION)
    elif cmd == "_complete":
        complete(rest[0], rest[1] if len(rest) > 1 else "")
    elif cmd == "finder":
        if len(rest) < 2 or rest[0] not in ("push", "pull", "pullhere", "backup"):
            raise SystemExit("usage: gd finder push|pull|pullhere|backup PATH...")
        finder(rest[0], rest[1:])
    elif cmd == "alfred":
        alfred(" ".join(args) if args else "")
    elif cmd == "run":
        run_ui(" ".join(rest))
    else:
        raise SystemExit(f"unknown command '{cmd}' (try: gd --help)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "alfred":
        alfred(sys.argv[2] if len(sys.argv) > 2 else "")
    elif len(sys.argv) > 1 and sys.argv[1] == "run":
        run_ui(sys.argv[2] if len(sys.argv) > 2 else "")
    else:
        cli(sys.argv[1:])
