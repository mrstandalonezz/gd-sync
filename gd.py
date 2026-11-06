#!/usr/bin/env python3
"""gd - push/pull files and folders between this Mac and a Google Drive folder (like git push/pull).

  gd pull NAME [to DIR] [--force] [--delete] [-n]    Drive origin/NAME  ->  DIR/NAME   (DIR defaults to workspace)
  gd push NAME [from DIR] [--force] [--delete] [-n]  DIR/NAME  ->  Drive origin/NAME
  gd push ~/some/path                                explicit local path -> origin/<basename>
  gd ls [SUB]                                        list the origin folder
  gd root [PATH|auto]                                show / set the Drive root folder on this Mac (default: auto-detect My Drive)
  gd remote [PATH]                                   show / set origin (a folder inside the root)
  gd workspace [DIR]                                 show / set default local folder (default ~/Desktop)
  gd config                                          show settings

Uses the Google Drive for desktop folder (no login needed here). Nothing is deleted unless --delete.
A file that is NEWER at the destination is skipped unless --force (so you never lose newer work).
Config: ~/.config/gd/config.json
"""
import glob, json, os, shlex, shutil, subprocess, sys

CFG_DIR = os.path.expanduser("~/.config/gd")
CFG = os.path.join(CFG_DIR, "config.json")
DEFAULTS = {"origin": "", "workspace": "~/Desktop", "mount": "", "confirm_overwrite": True,
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
        if ss.st_size == ds.st_size and abs(ss.st_mtime - ds.st_mtime) <= TOL:
            p["same"].append(rel)
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
    return len(todo)


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
        src, dst = os.path.join(origin, name), os.path.join(ws, name)
    else:
        local = os.path.abspath(os.path.expanduser(name)) if explicit else os.path.join(ws, name)
        base = os.path.basename(local.rstrip("/"))
        src, dst = local, os.path.join(origin, base)
        name = base
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
    if "-n" in flags or "--dry-run" in flags:
        text = f"[dry run] {name}: {summary(p, force, 0)}"
        print(where)
        for k in ("new", "update", "conflict", "delete"):
            for r in p[k]:
                print(f"  {k:9} {r or os.path.basename(src)}")
        print(text)
        return text
    risky = len(p["update"]) + len(p["delete"]) + (len(p["conflict"]) if force else 0)
    if cfg["confirm_overwrite"] and ui and risky:
        if not confirm(f"{kind} '{name}' will overwrite/delete {risky} existing file(s) at the destination.\n\n{where}"):
            notify("gd " + kind, "Cancelled")
            return "cancelled"
    applied = apply_plan(p, force)
    for rel in p["delete"]:
        os.remove(os.path.join(dst, rel))
    text = f"{kind} {name}: {summary(p, force, applied)}"
    print(where + "\n" + text)
    if ui:
        notify("✅ gd " + kind if not p["conflict"] or force else "⚠️ gd " + kind, text)
    return text


# ---------- listing ----------
def list_dir(path):
    try:
        return sorted(e for e in os.listdir(path) if not e.startswith(".") or e == ".config")
    except OSError:
        return []


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

    if cmd in ("pull", "push") and len(words) > 1:
        rest = q[len(cmd):].strip()
        d, flt = parse_dir(rest, "to" if cmd == "pull" else "from")
        ws = workspace(cfg, d)
        listing = list_dir(os.path.join(origin_dir(cfg))) if cmd == "pull" else list_dir(ws)
        for name in listing:
            if flt.lower() in name.lower():
                arrow = f"Drive:{cfg['origin'] or 'My Drive'}/{name}  →  {ws}/{name}" if cmd == "pull" \
                    else f"{ws}/{name}  →  Drive:{cfg['origin'] or 'My Drive'}/{name}"
                item(name, arrow + "   (⌥ = force overwrite newer)", f"{cmd}{TAB}{name}{TAB}{d or ''}",
                     mods={"alt": {"arg": f"{cmd}{TAB}{name}{TAB}{d or ''}{TAB}force",
                                   "subtitle": "FORCE: also overwrite files that are newer at the destination"}})
        if not items:
            item("Nothing found", f"No match for '{flt}' in {'Drive origin' if cmd == 'pull' else ws}")
    elif cmd == "ls":
        sub = q[2:].strip()
        for name in list_dir(os.path.join(origin_dir(cfg), sub)):
            item(name, os.path.join(origin_dir(cfg), sub, name), f"open{TAB}{os.path.join(origin_dir(cfg), sub, name)}")
        if not items:
            item("Empty", origin_dir(cfg))
    elif cmd == "root":
        val = q[4:].strip()
        if val:
            item(f"Set Drive root to '{val}'" if val != "auto" else "Reset Drive root to auto-detect",
                 "e.g. ~/Library/CloudStorage/GoogleDrive-you@gmail.com/My Drive", f"root{TAB}{val}")
        else:
            try:
                cur = mount_root(cfg)
            except SystemExit as e:
                cur = str(e)
            item("Root: " + cur, "Type: gd root <folder>  (or: gd root auto)")
    elif cmd == "remote":
        val = q[6:].strip()
        if val:
            item(f"Set Drive origin to '{val}'", "A folder path inside My Drive", f"remote{TAB}{val}")
        else:
            item("Origin: " + (cfg["origin"] or "(My Drive root)"), "Type: gd remote <folder in My Drive>")
    elif cmd == "workspace":
        val = q[9:].strip()
        if val:
            item(f"Set workspace to '{val}'", "Default local folder for pull/push", f"workspace{TAB}{val}")
        else:
            item("Workspace: " + cfg["workspace"], "Type: gd workspace <local folder>")
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
                       ("config", "show settings")):
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
        elif kind == "open":
            subprocess.run(["open", parts[1]])
        elif kind == "edit":
            save(cfg)
            subprocess.run(["open", "-t", CFG])
    except SystemExit as e:
        notify("⚠️ gd failed", str(e))
    except Exception as e:
        notify("⚠️ gd failed", f"{type(e).__name__}: {e}")


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
