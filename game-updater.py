#!/usr/bin/env python3
"""
Game Updater: Build & Deploy for Godot 4, from Linux or Windows.
Supports Itch.io and Steam, plus a cut-down demo (on Steam, or on the same itch page),
and packages the full game for Patreon and SubscribeStar.
"""

import os, sys, json, subprocess, shutil, re, struct, time, argparse, shlex, webbrowser
from datetime import datetime
from itertools import combinations
from pathlib import Path

# --- Configs ---
LOCAL_CFG = "game_config.json"
GODOT_ID = "org.godotengine.Godot"
PRESETS = ["Windows", "Linux"]
DEMO_PRESETS = {p: f"{p} Demo" for p in PRESETS}  # export presets the demo is built from
VERSION_FMT = "%Y.%m.%d.%H"
TEMPLATE = {
    "itch_username": "USER", "itch_project_slug": "SLUG",
    "steam_username": "STEAM_USER", "steam_app_id": "APP", "steam_windows_depot_id": "WIN_DEPOT", "steam_linux_depot_id": "LNX_DEPOT",
    "steam_demo_app_id": "DEMO_APP", "steam_demo_windows_depot_id": "DEMO_WIN_DEPOT", "steam_demo_linux_depot_id": "DEMO_LNX_DEPOT",
}
# Optional keys: not in the template a new project gets, read when present (see the README).
OPTIONAL = ["godot_path", "steamcmd_path", "pre_export", "itch_demo", "patreon_post_url", "subscribestar_post_url", "release_name"]
TEXT_EXTS = (".tscn", ".tres", ".gd", ".gdshader", ".gdshaderinc", ".cfg", ".json", ".godot")
WINDOWS = os.name == "nt"
DEMO_TARGETS = ("Steam Demo", "Itch Demo")
ITCH_CHANNELS = {"Windows": "windows", "Linux": "linux", "Windows Demo": "windows-demo", "Linux Demo": "linux-demo"}

def cfg_value(cfg, key):
    """Usable value for key, or None if it is missing, empty, or still the template placeholder. A value given per
    OS ({"windows": ..., "linux": ...}) is this OS's."""
    val = cfg.get(key)
    if isinstance(val, dict): val = val.get("windows" if WINDOWS else "linux")
    val = str(val or "").strip()
    return None if not val or val == TEMPLATE.get(key) else val

def describe_unset(cfg, keys):
    """Human-readable reason why each of the given keys has no usable value."""
    notes = []
    for k in keys:
        if k in cfg: notes.append(f"'{k}' is " + ("still the template placeholder" if str(cfg[k]).strip() == TEMPLATE.get(k) else "empty"))
        elif "_" + k in cfg: notes.append(f"'{k}' is disabled (found '_{k}')")
        else: notes.append(f"'{k}' is missing")
    return ", ".join(notes)

def resolve_steam(cfg, prefix, preset_names):
    """Deploy settings for the Steam app whose config keys start with prefix, or a string saying why it can't be deployed."""
    depot_keys = {preset_names[p]: f"{prefix}{p.lower()}_depot_id" for p in PRESETS}
    depots = {name: cfg_value(cfg, k) for name, k in depot_keys.items()}
    for k in [prefix + "app_id", "steam_username"]:
        if not cfg_value(cfg, k): return describe_unset(cfg, [k])
    if not any(depots.values()): return "no depots configured: " + describe_unset(cfg, depot_keys.values())
    return {
        "username": cfg_value(cfg, "steam_username"),
        "app_id": cfg_value(cfg, prefix + "app_id"),
        "depots": {name: d for name, d in depots.items() if d},
        "skipped_depots": {name: describe_unset(cfg, [depot_keys[name]]) for name, d in depots.items() if not d},
    }

def resolve_targets(cfg):
    """Returns (targets, skipped): platform -> deploy settings, and platform -> reason it can't be deployed."""
    targets, skipped = {}, {}

    itch_keys = ["itch_username", "itch_project_slug"]
    unset = [k for k in itch_keys if not cfg_value(cfg, k)]
    if unset: skipped["Itch"] = describe_unset(cfg, unset)
    else: targets["Itch"] = {"target": "/".join(cfg_value(cfg, k) for k in itch_keys)}

    for name, prefix, preset_names in [("Steam", "steam_", {p: p for p in PRESETS}), ("Steam Demo", "steam_demo_", DEMO_PRESETS)]:
        steam = resolve_steam(cfg, prefix, preset_names)
        if isinstance(steam, dict): targets[name] = steam
        # A game with no demo keys at all simply has no demo; that's not worth a warning
        elif name == "Steam" or any(k.lstrip("_").startswith(prefix) for k in cfg): skipped[name] = steam

    # The demo on the same itch page as the full game, on channels of its own
    if "Itch" in targets and cfg.get("itch_demo"): targets["Itch Demo"] = {"target": targets["Itch"]["target"]}

    # Patreon and SubscribeStar take the full game as zips, posted by hand: neither has an API to upload with
    for name, key in [("Patreon", "patreon_post_url"), ("SubscribeStar", "subscribestar_post_url")]:
        if cfg_value(cfg, key): targets[name] = {"url": cfg_value(cfg, key)}
        elif any(k.lstrip("_") == key for k in cfg): skipped[name] = describe_unset(cfg, [key])

    return targets, skipped

def godot_command(cfg):
    """How to run Godot here: godot_path, else $GODOT, else flatpak on Linux, else godot on PATH. None if none."""
    path = cfg_value(cfg, "godot_path") or os.environ.get("GODOT")
    if path: return [path]
    if not WINDOWS and shutil.which("flatpak"): return ["flatpak", "run", GODOT_ID]
    for name in ["godot", "godot4"]:
        if shutil.which(name): return [shutil.which(name)]
    return None

def steamcmd_path(cfg):
    path = cfg_value(cfg, "steamcmd_path")
    if path: return path
    if WINDOWS: return shutil.which("steamcmd") or r"C:\steamcmd\steamcmd.exe"
    return str(Path.home() / ".steam" / "steamcmd" / "steamcmd.sh")

def run(cmd, interactive=False):
    print(f"> {(' ').join(cmd)}")
    try:
        subprocess.run(cmd, check=True, stdin=None if not interactive else None)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: {e}"); sys.exit(1)

def run_pre_export(cfg, godot_cmd, root):
    """The project's own step before anything is read (bbd writes its export presets' filters here)."""
    cmd = cfg.get("pre_export")
    if not cmd: return
    parts = cmd if isinstance(cmd, list) else shlex.split(cmd, posix=not WINDOWS)
    full = []
    for part in parts:
        if part == "{godot}":
            if not godot_cmd: print("ERROR: pre_export uses {godot} but Godot wasn't found (set godot_path)."); sys.exit(1)
            full += godot_cmd
        else: full.append(part)
    print("\n--- PRE-EXPORT ---")
    print(f"> {' '.join(full)}")
    if subprocess.run(full, cwd=root).returncode != 0:
        print("ERROR: pre_export failed (above), so nothing was built or uploaded."); sys.exit(1)

def read_presets(root):
    """Export presets from export_presets.cfg as {name: {setting: value}}."""
    cfg_path = root / "export_presets.cfg"
    if not cfg_path.exists():
        print(f"WARNING: {cfg_path} not found."); return {}
    presets = {}
    for block in re.findall(r'\[preset\.\d+\]\n(.*?)(?=\n\[|\Z)', cfg_path.read_text(), re.DOTALL):
        settings = dict(re.findall(r'^(\w+)="(.*)"$', block, re.MULTILINE))
        if settings.get("name"): presets[settings["name"]] = settings
    return presets

def get_export_paths(root, presets, wanted):
    paths = {}
    for name in wanted:
        path_str = presets.get(name, {}).get("export_path", "")
        if path_str:
            if path_str.startswith("res://"): path_str = path_str[6:]
            paths[name] = str(root / path_str)
        elif name in PRESETS:
            paths[name] = str(root / "builds" / name.lower() / ("game.exe" if name=="Windows" else "game"))
            print(f"  -> FALLBACK for '{name}': {paths[name]}")
    return paths

# --- Demo safety checks ---

def project_files(root):
    """Project-relative paths of everything Godot could export (skips hidden and .gdignore'd folders)."""
    files = []
    for dirpath, dirs, names in os.walk(root):
        if ".gdignore" in names: dirs[:] = []; continue
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        files += [(Path(dirpath) / n).relative_to(root).as_posix() for n in names if not n.endswith((".import", ".uid"))]
    return files

def filter_regex(pattern):
    """Godot's export filter matching: case-insensitive, '*' spans folders, 'res://' is optional, the whole path must match."""
    pattern = pattern.removeprefix("res://")
    return re.compile("".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern), re.IGNORECASE | re.DOTALL)

def find_dangling_refs(root, files, withheld):
    """{shipped file: [withheld files it references]}. Those references break in the demo and leak the withheld names."""
    uids = {}
    for f in withheld:
        for src in [root / f"{f}.uid", root / f"{f}.import"] + ([root / f] if f.endswith((".tscn", ".tres")) else []):
            if src.exists():
                with open(src, errors="ignore") as fh: uid = re.search(r'uid://\w+', fh.read(1024))
                if uid: uids[uid.group(0)] = f

    dangling = {}
    for f in files:
        if f in withheld or f == LOCAL_CFG or not f.endswith(TEXT_EXTS): continue  # our own config is never exported
        text = (root / f).read_text(errors="ignore")
        hits = {r.removeprefix("res://") for r in re.findall(r'res://[^"\'\s\])]+', text)} & withheld
        hits |= {uids[u] for u in re.findall(r'uid://\w+', text) if u in uids}
        if hits: dangling[f] = sorted(hits)
    return dangling

def check_withheld(root, presets, names, demo_names, export_paths, files):
    """Pre-flight for every build that leaves files out (a demo always must). Returns (errors, withheld) where withheld
    maps preset -> set of files it keeps out of its build."""
    errors, withheld = [], {}
    for name in names:
        preset = presets.get(name)
        if name in demo_names and (not preset or not preset.get("export_path")):
            base = name.removesuffix(" Demo")
            errors.append(f"No export preset named '{name}'. In Godot: Project > Export, duplicate '{base}', name it '{name}', give it its own "
                          f"export path (e.g. builds/demo/{base.lower()}/...), an exclude filter, and the custom feature 'demo'.")
            continue
        patterns = [p.strip() for p in (preset or {}).get("exclude_filter", "").split(",") if p.strip()]
        if not patterns:
            if name in demo_names: errors.append(f"Preset '{name}' has an empty exclude filter, so it would upload the FULL game as the demo.")
            continue
        withheld[name] = set()
        for pattern in patterns:
            matched = {f for f in files if filter_regex(pattern).fullmatch(f)}
            if not matched:
                hint = f" Folders need a trailing '/*' (try '{pattern.rstrip('/')}/*')." if (root / pattern.removeprefix("res://")).is_dir() else ""
                errors.append(f"Preset '{name}': exclude filter '{pattern}' matches no files, so Godot would silently ignore it.{hint}")
            withheld[name] |= matched

    # Steam and itch upload a build's whole folder, so a demo must never share (or sit inside) another build's folder
    folders = {name: Path(p).parent for name, p in export_paths.items()}
    for a, b in combinations(folders, 2):
        if not (a in demo_names or b in demo_names): continue
        if folders[a] == folders[b] or folders[a] in folders[b].parents or folders[b] in folders[a].parents:
            errors.append(f"'{a}' and '{b}' export into overlapping folders ({folders[a]} / {folders[b]}). The whole folder is "
                          f"uploaded, so one build would be uploaded inside the other. Give each preset its own folder.")
    return errors, withheld

def read_pck_paths(export_path):
    """Paths stored in a Godot 4 export's PCK, whether embedded in the binary or sitting next to it as a .pck."""
    export_path = Path(export_path)
    with open(export_path, "rb") as f:
        f.seek(-12, 2); size, magic = struct.unpack("<Q4s", f.read(12))
        start = f.tell() - 12 - size if magic == b"GDPC" else None
    if start is None: export_path, start = export_path.with_suffix(".pck"), 0
    if not export_path.exists(): raise ValueError("no embedded PCK and no .pck file found")

    with open(export_path, "rb") as f:
        f.seek(start); header = f.read(40)
        magic, version, _, _, _, flags = struct.unpack_from("<4s5I", header)
        if magic != b"GDPC": raise ValueError("unrecognised PCK header")
        if flags & 1: raise ValueError("the PCK directory is encrypted")
        f.seek(start + (struct.unpack_from("<Q", header, 32)[0] if version >= 3 else 96))
        paths = []
        for _ in range(struct.unpack("<I", f.read(4))[0]):
            paths.append(f.read(struct.unpack("<I", f.read(4))[0]).rstrip(b"\0").decode().removeprefix("res://"))
            f.seek(36, 1)  # offset, size, md5, flags
    if "project.binary" not in paths: raise ValueError(f"unrecognised PCK layout (format v{version})")
    return paths

def verify_build(name, export_path, withheld, build_start):
    """Post-build gate: returns a list of reasons this build must not be uploaded."""
    problems = []
    folder = Path(export_path).parent
    stale = sorted(f.relative_to(folder).as_posix() for f in folder.rglob("*") if f.is_file() and f.stat().st_mtime < build_start)
    if stale: problems.append(f"{folder} contains files this build didn't produce, which would be uploaded too: {', '.join(stale)}")
    if not withheld:
        if not problems: print(f"  -> {name}: OK (it withholds nothing to check for).")
        return problems

    try:
        shipped = {re.sub(r'\.(remap|import)$', '', p) for p in read_pck_paths(export_path)}
    except (ValueError, OSError, struct.error) as e:
        return problems + [f"couldn't read the PCK to check its contents: {e}"]
    leaked = sorted(shipped & withheld)
    if leaked: problems.append(f"{len(leaked)} withheld files are inside the build: {', '.join(leaked[:10])}{' ...' if len(leaked) > 10 else ''}")
    if not problems: print(f"  -> {name}: OK. {len(shipped)} files in the PCK, none of the {len(withheld)} withheld files among them.")
    return problems

# --- Deploy ---

def package_zips(cfg, root, version, export_paths):
    """The full game's builds as zips in builds/release/, for posting by hand. Returns their paths."""
    name = cfg_value(cfg, "release_name")
    if not name:
        found = re.search(r'config/name\s*=\s*"([^"]*)"', (root / "project.godot").read_text())
        name = found.group(1) if found else root.name
    name = re.sub(r'[^\w.-]+', "-", name).strip("-")
    out_dir = root / "builds" / "release"
    out_dir.mkdir(parents=True, exist_ok=True)
    zips = []
    for preset in PRESETS:
        if preset not in export_paths or not Path(export_paths[preset]).exists(): continue
        base = out_dir / f"{name}-{version}-{preset.lower()}"
        print(f"Zipping {preset} -> {base}.zip")
        zips.append(Path(shutil.make_archive(str(base), "zip", root_dir=Path(export_paths[preset]).parent)))
    return zips

def deploy_by_hand(label, url, zips):
    print(f"\n--- {label.upper()}: POST BY HAND ---")
    print(f"{label} has no API to upload with. Attach these to a new post:")
    for z in zips: print(f"  -> {z} ({z.stat().st_size / 1e6:.0f} MB)")
    try:
        webbrowser.open(url); print(f"Opened: {url}")
    except Exception as e:
        print(f"Could not open a browser ({e}). Go to: {url}")

def deploy_steam(label, steam, export_paths, root, steamcmd):
    print(f"\n--- DEPLOYING TO {label.upper()} ---")
    if not WINDOWS: os.chmod(steamcmd, 0o755)

    for preset, reason in steam["skipped_depots"].items():
        print(f"Skipping {preset} depot ({reason}).")

    for preset, depot_id in steam["depots"].items():
        os_name = preset.lower().replace(" ", "_")
        folder = Path(export_paths[preset]).parent
        if not folder.exists():
            print(f"Error: Folder {folder} not found."); continue
        if not WINDOWS:
            os.chmod(folder, 0o755)
            for item in folder.iterdir():
                if item.is_file(): os.chmod(item, 0o755)

        vdf_path = root / f"steam_build_{os_name}.vdf"
        rel_folder = folder.relative_to(root).as_posix() if folder.is_relative_to(root) else folder.as_posix()

        # Generate VDF
        lines = [
            '"BuildDescription"',
            '{',
            f'    "appid" "{steam["app_id"]}"',
            '    "Depots"',
            '    {',
            f'        "{depot_id}"',
            '        {',
            '            "filemapping"',
            '            {',
            f'                "LocalPath" "{rel_folder}/*"',
            f'                "DepotPath" "."',
            f'                "recursive" "1"',
            '            }',
            '        }',
            '    }',
            '}'
        ]
        vdf_path.write_text('\n'.join(lines) + '\n')
        print(f"Generated VDF: {vdf_path}")

        # 1. Upload the build
        cmd_upload = [steamcmd, "+login", steam["username"], f"+run_app_build {vdf_path}", "+quit"]
        print(f"Uploading {os_name}...")
        run(cmd_upload, interactive=True)

        # 2. Assign to Default Branch
        # This command sets the MOST RECENTLY UPLOADED build for this depot to the specified branch
        print(f"Assigning latest build to branch 'public'...")
        cmd_branch = [steamcmd, "+login", steam["username"], "+set_default_branch public", "+quit"]
        run(cmd_branch, interactive=True)

    # Final Step: Open the Steamworks Depots page for you to activate
    print("\n--- FINAL STEP ---")
    print("Uploads complete! The builds are uploaded but NOT yet active.")
    print("Opening Steamworks Depots page to assign the build to the 'public' branch...")

    url = f"https://partner.steamgames.com/apps/builds/{steam['app_id']}/depots"
    try:
        webbrowser.open(url)
        print(f"Opened: {url}")
        print("Click 'Set as Default' for the latest build in the list.")
    except Exception as e:
        print(f"Could not open browser automatically: {e}")
        print(f"Go to: {url} manually.")

def main():
    parser = argparse.ArgumentParser(description="Build a Godot 4 game, deploy it to Itch.io and Steam, and package it for Patreon and SubscribeStar.")
    parser.add_argument("--demo-only", action="store_true", help="only build and upload the demo")
    parser.add_argument("--no-demo", action="store_true", help="leave the demo alone this run")
    parser.add_argument("--skip-demo-check", action="store_true", help="upload even if a build's contents can't be verified")
    args = parser.parse_args()
    root = Path.cwd()

    # 1. Load Config
    cfg_path = root / LOCAL_CFG
    if not cfg_path.exists():
        cfg_path.write_text(json.dumps(TEMPLATE, indent=2))
        print(f"Created {LOCAL_CFG}. Edit and run again.")
        print("To skip a platform, delete its keys (or prefix them with '_')."); sys.exit(0)
    cfg = json.loads(cfg_path.read_text())

    # Work out where we can deploy, and fail early (before touching the project) if we can't
    targets, skipped = resolve_targets(cfg)
    for name in list(targets):
        flag = "--no-demo" if args.no_demo and name in DEMO_TARGETS else "--demo-only" if args.demo_only and name not in DEMO_TARGETS else None
        if flag: del targets[name]; skipped[name] = f"you passed {flag}"
    if not targets:
        print(f"ERROR: No deploy targets configured in {LOCAL_CFG}. Nothing to do.")
        for name, reason in skipped.items(): print(f"  -> {name}: {reason}")
        sys.exit(1)

    butler_path = shutil.which("butler")
    if any(t.startswith("Itch") for t in targets) and not butler_path:
        print("ERROR: Itch is configured but 'butler' was not found on PATH."); sys.exit(1)
    steamcmd = steamcmd_path(cfg)
    if any(t.startswith("Steam") for t in targets) and not Path(steamcmd).exists():
        print(f"ERROR: Steam is configured but SteamCMD was not found at {steamcmd} (set steamcmd_path)."); sys.exit(1)
    godot_cmd = godot_command(cfg)
    if not godot_cmd:
        print("ERROR: Godot wasn't found. Set godot_path in game_config.json (or $GODOT), or put godot on PATH."); sys.exit(1)

    # 2. Build Prep
    version = datetime.now().strftime(VERSION_FMT)
    print(f"Target Version: {version}")

    proj = root / "project.godot"
    if not proj.exists(): print(f"ERROR: {proj} not found. Run this from a Godot project folder."); sys.exit(1)

    run_pre_export(cfg, godot_cmd, root)

    full_presets = PRESETS if any(t in targets for t in ["Itch", "Steam", "Patreon", "SubscribeStar"]) else []
    demo_presets = list(targets["Steam Demo"]["depots"]) if "Steam Demo" in targets else []
    if "Itch Demo" in targets: demo_presets += [p for p in DEMO_PRESETS.values() if p not in demo_presets]
    presets = read_presets(root)
    export_paths = get_export_paths(root, presets, full_presets + demo_presets)

    files = project_files(root)
    errors, withheld = check_withheld(root, presets, full_presets + demo_presets, demo_presets, export_paths, files)
    if errors:
        print("\nERROR: The builds can't be made safely:")
        for e in errors: print(f"  -> {e}")
        print("Fix the above, or pass --no-demo to deploy everything else."); sys.exit(1)
    dangling = {name: find_dangling_refs(root, files, w) for name, w in withheld.items()}
    for p in export_paths.values(): Path(p).parent.mkdir(parents=True, exist_ok=True)

    # 3. Plan
    print("\n" + "="*40)
    print("PLAN")
    print("="*40)
    print(f"Build: {', '.join(export_paths)}")
    for name, p in export_paths.items(): print(f"  -> {name}: {p}")
    print(f"Deploy: {', '.join(targets)}")
    if "Itch" in targets: print(f"  -> Itch: {targets['Itch']['target']} (windows, linux)")
    if "Itch Demo" in targets:
        print(f"  -> Itch Demo: {targets['Itch Demo']['target']} (windows-demo, linux-demo)")
        print(f"     Once, on itch: Edit game > Uploads, mark the two -demo uploads as demos so they're free on the paid page.")
    for label in ["Patreon", "SubscribeStar"]:
        if label in targets: print(f"  -> {label}: the full game zipped into builds/release/, then {targets[label]['url']} opened to post them")
    for label in ["Steam", "Steam Demo"]:
        if label not in targets: continue
        steam = targets[label]
        print(f"  -> {label}: App {steam['app_id']} ({', '.join(f'{p} depot {d}' for p, d in steam['depots'].items())})")
        for p, reason in steam["skipped_depots"].items(): print(f"     !! {p} depot SKIPPED: {reason}")
    for name in withheld:
        print(f"     {name} withholds {len(withheld[name])} of {len(files)} project files")
        if name in demo_presets and "demo" not in presets[name].get("custom_features", ""):
            print(f"     !! {name} has no 'demo' custom feature, so the game can't tell it's the demo via OS.has_feature(\"demo\")")
        first = next(n for n in withheld if dangling[n] == dangling[name])
        if dangling[name] and first != name:
            print(f"     !! (same {len(dangling[name])} dangling references as {first})"); continue
        if dangling[name]:
            print(f"     !! {len(dangling[name])} shipped files still reference withheld files. Scenes/resources among them will FAIL TO LOAD")
            print(f"        in the demo, scripts need a guard, and every name on the right is visible to dataminers:")
            for f, refs in list(dangling[name].items())[:12]:
                print(f"        {f} -> {refs[0]}" + (f" (+{len(refs) - 1} more)" if len(refs) > 1 else ""))
            if len(dangling[name]) > 12: print(f"        ... and {len(dangling[name]) - 12} more files")
    for name, reason in skipped.items(): print(f"  !! {name}: SKIPPED - {reason}")
    print("="*40)
    if skipped:
        print(f"NOTE: Deploying to {', '.join(targets)} ONLY. {', '.join(skipped)} will NOT be updated this run.")
    try:
        input("Press Enter to execute (Ctrl+C to abort)...")
    except (KeyboardInterrupt, EOFError):
        print("\nAborted. Nothing was built or uploaded."); sys.exit(1)

    # 4. Build
    content = proj.read_text()
    content = re.sub(r'(config/version\s*=\s*)"[^"]*"', r'\1"' + version + '"', content)
    if 'config/version' not in content: content += f'\nconfig/version = "{version}"\n'
    proj.write_text(content)

    print("\n--- BUILDING ---")
    build_start = time.time() - 2  # slack for coarse filesystem timestamps
    for preset, out in export_paths.items():
        run(godot_cmd + ["--headless", "--quit", "--export-release", preset, out])
        if not Path(out).exists():
            print(f"FAIL: {out} not created. No export templates for this Godot? Editor > Manage Export Templates."); sys.exit(1)
    print("Builds complete.")

    # Nothing gets uploaded anywhere until every build is known to be clean
    print("\n--- VERIFYING BUILDS ---")
    problems = {name: verify_build(name, out, withheld.get(name, set()), build_start) for name, out in export_paths.items()}
    for name, found in problems.items():
        for problem in found: print(f"  !! {name}: {problem}")
    if any(problems.values()):
        if not args.skip_demo_check: print("ERROR: Refusing to upload anything. Fix the above (or pass --skip-demo-check if you're sure)."); sys.exit(1)
        print("WARNING: Continuing anyway because of --skip-demo-check.")

    # 5. Deploy Itch: the full game, then the demo on channels of its own on the same page. Each build's whole
    # folder, so one that keeps its .pck beside the executable goes up whole.
    for label in ["Itch", "Itch Demo"]:
        if label in targets:
            print(f"\n--- DEPLOYING TO {label.upper()} ---")
            for preset in (PRESETS if label == "Itch" else list(DEMO_PRESETS.values())):
                if preset in export_paths and Path(export_paths[preset]).exists():
                    folder = Path(export_paths[preset]).parent
                    run([butler_path, "push", str(folder), f"{targets[label]['target']}:{ITCH_CHANNELS[preset]}", f"--userversion={version}"])
        elif label in skipped:
            print(f"\n--- SKIPPING {label.upper()} ({skipped[label]}) ---")

    # 6. Deploy Steam (full game, then demo)
    for label in ["Steam", "Steam Demo"]:
        if label in targets: deploy_steam(label, targets[label], export_paths, root, steamcmd)
        elif label in skipped: print(f"\n--- SKIPPING {label.upper()} ({skipped[label]}) ---")

    # 7. Patreon and SubscribeStar: the full game zipped, and the pages opened to post it
    by_hand = [label for label in ["Patreon", "SubscribeStar"] if label in targets]
    if by_hand:
        zips = package_zips(cfg, root, version, export_paths)
        for label in by_hand: deploy_by_hand(label, targets[label]["url"], zips)

    print("\n=== DONE ===")
    print(f"Deployed {version} to: {', '.join(targets)}")
    if skipped: print(f"Skipped: {', '.join(skipped)}")

if __name__ == "__main__": main()
