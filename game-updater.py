#!/usr/bin/env python3
"""
Game Updater: Build & Deploy for Godot 4.
Supports Itch.io and Steam, plus a cut-down Steam demo.
"""

import os, sys, json, subprocess, shutil, re, struct, time, argparse
from datetime import datetime
from itertools import combinations
from pathlib import Path

# --- Configs ---
LOCAL_CFG = "game_config.json"
GODOT_ID = "org.godotengine.Godot"
PRESETS = ["Windows", "Linux"]
DEMO_PRESETS = {p: f"{p} Demo" for p in PRESETS}  # export presets the demo is built from
VERSION_FMT = "%Y.%m.%d.%H"
STEAMCMD_PATH = str(Path.home() / ".steam" / "steamcmd" / "steamcmd.sh")
TEMPLATE = {
    "itch_username": "USER", "itch_project_slug": "SLUG",
    "steam_username": "STEAM_USER", "steam_app_id": "APP", "steam_windows_depot_id": "WIN_DEPOT", "steam_linux_depot_id": "LNX_DEPOT",
    "steam_demo_app_id": "DEMO_APP", "steam_demo_windows_depot_id": "DEMO_WIN_DEPOT", "steam_demo_linux_depot_id": "DEMO_LNX_DEPOT",
}
TEXT_EXTS = (".tscn", ".tres", ".gd", ".gdshader", ".gdshaderinc", ".cfg", ".json", ".godot")

def cfg_value(cfg, key):
    """Usable value for key, or None if it is missing, empty, or still the template placeholder."""
    val = str(cfg.get(key) or "").strip()
    return None if not val or val == TEMPLATE[key] else val

def describe_unset(cfg, keys):
    """Human-readable reason why each of the given keys has no usable value."""
    notes = []
    for k in keys:
        if k in cfg: notes.append(f"'{k}' is " + ("still the template placeholder" if str(cfg[k]).strip() == TEMPLATE[k] else "empty"))
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

    return targets, skipped

def run(cmd, interactive=False):
    print(f"> {(' ').join(cmd)}")
    try:
        subprocess.run(cmd, check=True, stdin=None if not interactive else None)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: {e}"); sys.exit(1)

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
        if f in withheld or not f.endswith(TEXT_EXTS): continue
        text = (root / f).read_text(errors="ignore")
        hits = {r.removeprefix("res://") for r in re.findall(r'res://[^"\'\s\])]+', text)} & withheld
        hits |= {uids[u] for u in re.findall(r'uid://\w+', text) if u in uids}
        if hits: dangling[f] = sorted(hits)
    return dangling

def check_demo(root, presets, demo_names, export_paths, files):
    """Pre-flight for the demo. Returns (errors, withheld) where withheld maps preset -> set of files it keeps out of the demo."""
    errors, withheld = [], {}
    for name in demo_names:
        preset = presets.get(name)
        if not preset or not preset.get("export_path"):
            base = name.removesuffix(" Demo")
            errors.append(f"No export preset named '{name}'. In Godot: Project > Export, duplicate '{base}', name it '{name}', give it its own "
                          f"export path (e.g. builds/demo/{base.lower()}/...), an exclude filter, and the custom feature 'demo'.")
            continue
        patterns = [p.strip() for p in preset.get("exclude_filter", "").split(",") if p.strip()]
        if not patterns:
            errors.append(f"Preset '{name}' has an empty exclude filter, so it would upload the FULL game to the demo app."); continue
        withheld[name] = set()
        for pattern in patterns:
            matched = {f for f in files if filter_regex(pattern).fullmatch(f)}
            if not matched:
                hint = f" Folders need a trailing '/*' (try '{pattern.rstrip('/')}/*')." if (root / pattern.removeprefix("res://")).is_dir() else ""
                errors.append(f"Preset '{name}': exclude filter '{pattern}' matches no files, so Godot would silently ignore it.{hint}")
            withheld[name] |= matched

    # Steam uploads a build's whole folder, so a demo must never share (or sit inside) another build's folder
    folders = {name: Path(p).parent for name, p in export_paths.items()}
    for a, b in combinations(folders, 2):
        if not (a in demo_names or b in demo_names): continue
        if folders[a] == folders[b] or folders[a] in folders[b].parents or folders[b] in folders[a].parents:
            errors.append(f"'{a}' and '{b}' export into overlapping folders ({folders[a]} / {folders[b]}). Steam uploads the whole "
                          f"folder, so one build would be uploaded inside the other. Give each preset its own folder.")
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

def verify_demo_build(name, export_path, withheld, build_start):
    """Post-build gate: returns a list of reasons this demo build must not be uploaded."""
    problems = []
    folder = Path(export_path).parent
    stale = sorted(f.relative_to(folder).as_posix() for f in folder.rglob("*") if f.is_file() and f.stat().st_mtime < build_start)
    if stale: problems.append(f"{folder} contains files this build didn't produce, which Steam would upload too: {', '.join(stale)}")

    try:
        shipped = {re.sub(r'\.(remap|import)$', '', p) for p in read_pck_paths(export_path)}
    except (ValueError, OSError, struct.error) as e:
        return problems + [f"couldn't read the PCK to check its contents: {e}"]
    leaked = sorted(shipped & withheld)
    if leaked: problems.append(f"{len(leaked)} withheld files are inside the build: {', '.join(leaked[:10])}{' ...' if len(leaked) > 10 else ''}")
    if not problems: print(f"  -> {name}: OK. {len(shipped)} files in the PCK, none of the {len(withheld)} withheld files among them.")
    return problems

# --- Deploy ---

def deploy_steam(label, steam, export_paths, root):
    print(f"\n--- DEPLOYING TO {label.upper()} ---")
    os.chmod(STEAMCMD_PATH, 0o755)

    for preset, reason in steam["skipped_depots"].items():
        print(f"Skipping {preset} depot ({reason}).")

    for preset, depot_id in steam["depots"].items():
        os_name = preset.lower().replace(" ", "_")
        folder = Path(export_paths[preset]).parent
        if not folder.exists():
            print(f"Error: Folder {folder} not found."); continue
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
        cmd_upload = [STEAMCMD_PATH, "+login", steam["username"], f"+run_app_build {vdf_path}", "+quit"]
        print(f"Uploading {os_name}...")
        run(cmd_upload, interactive=True)

        # 2. Assign to Default Branch
        # This command sets the MOST RECENTLY UPLOADED build for this depot to the specified branch
        print(f"Assigning latest build to branch 'public'...")
        cmd_branch = [STEAMCMD_PATH, "+login", steam["username"], "+set_default_branch public", "+quit"]
        run(cmd_branch, interactive=True)

    # Final Step: Open the Steamworks Depots page for you to activate
    print("\n--- FINAL STEP ---")
    print("Uploads complete! The builds are uploaded but NOT yet active.")
    print("Opening Steamworks Depots page to assign the build to the 'public' branch...")

    url = f"https://partner.steamgames.com/apps/builds/{steam['app_id']}/depots"
    try:
        import webbrowser
        webbrowser.open(url)
        print(f"Opened: {url}")
        print("Click 'Set as Default' for the latest build in the list.")
    except Exception as e:
        print(f"Could not open browser automatically: {e}")
        print(f"Go to: {url} manually.")

def main():
    parser = argparse.ArgumentParser(description="Build a Godot 4 game and deploy it to Itch.io and Steam.")
    parser.add_argument("--demo-only", action="store_true", help="only build and upload the Steam demo")
    parser.add_argument("--no-demo", action="store_true", help="leave the Steam demo alone this run")
    parser.add_argument("--skip-demo-check", action="store_true", help="upload the demo even if its contents can't be verified")
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
        flag = "--no-demo" if args.no_demo and name == "Steam Demo" else "--demo-only" if args.demo_only and name != "Steam Demo" else None
        if flag: del targets[name]; skipped[name] = f"you passed {flag}"
    if not targets:
        print(f"ERROR: No deploy targets configured in {LOCAL_CFG}. Nothing to do.")
        for name, reason in skipped.items(): print(f"  -> {name}: {reason}")
        sys.exit(1)

    butler_path = shutil.which("butler")
    if "Itch" in targets and not butler_path:
        print("ERROR: Itch is configured but 'butler' was not found on PATH."); sys.exit(1)
    if any(t.startswith("Steam") for t in targets) and not Path(STEAMCMD_PATH).exists():
        print(f"ERROR: Steam is configured but SteamCMD was not found at {STEAMCMD_PATH}."); sys.exit(1)

    # 2. Build Prep
    version = datetime.now().strftime(VERSION_FMT)
    print(f"Target Version: {version}")

    proj = root / "project.godot"
    if not proj.exists(): print(f"ERROR: {proj} not found. Run this from a Godot project folder."); sys.exit(1)

    full_presets = PRESETS if "Itch" in targets or "Steam" in targets else []
    demo_presets = list(targets["Steam Demo"]["depots"]) if "Steam Demo" in targets else []
    presets = read_presets(root)
    export_paths = get_export_paths(root, presets, full_presets + demo_presets)

    withheld, dangling = {}, {}
    if demo_presets:
        files = project_files(root)
        errors, withheld = check_demo(root, presets, demo_presets, export_paths, files)
        if errors:
            print("\nERROR: The demo can't be built safely:")
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
    if "Itch" in targets: print(f"  -> Itch: {targets['Itch']['target']}")
    for label in ["Steam", "Steam Demo"]:
        if label not in targets: continue
        steam = targets[label]
        print(f"  -> {label}: App {steam['app_id']} ({', '.join(f'{p} depot {d}' for p, d in steam['depots'].items())})")
        for p, reason in steam["skipped_depots"].items(): print(f"     !! {p} depot SKIPPED: {reason}")
    for name in demo_presets:
        print(f"     {name} withholds {len(withheld[name])} of {len(files)} project files")
        if "demo" not in presets[name].get("custom_features", ""):
            print(f"     !! {name} has no 'demo' custom feature, so the game can't tell it's the demo via OS.has_feature(\"demo\")")
        if dangling[name] and dangling[name] == dangling.get(demo_presets[0]) and name != demo_presets[0]:
            print(f"     !! (same {len(dangling[name])} dangling references as {demo_presets[0]})"); continue
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
    godot_cmd = ["flatpak", "run", GODOT_ID]
    for preset, out in export_paths.items():
        run(godot_cmd + ["--headless", "--quit", "--export-release", preset, out])
        if not Path(out).exists(): print(f"FAIL: {out} not created"); sys.exit(1)
    print("Builds complete.")

    # Nothing gets uploaded anywhere until the demo is known to be clean
    if demo_presets:
        print("\n--- VERIFYING DEMO ---")
        problems = {name: verify_demo_build(name, export_paths[name], withheld[name], build_start) for name in demo_presets}
        for name, found in problems.items():
            for problem in found: print(f"  !! {name}: {problem}")
        if any(problems.values()):
            if not args.skip_demo_check: print("ERROR: Refusing to upload anything. Fix the above (or pass --skip-demo-check if you're sure)."); sys.exit(1)
            print("WARNING: Continuing anyway because of --skip-demo-check.")

    # 5. Deploy Itch
    if "Itch" in targets:
        print("\n--- DEPLOYING TO ITCH ---")
        target = targets["Itch"]["target"]

        for preset in PRESETS:
            p = Path(export_paths[preset])
            if p.exists():
                run([butler_path, "push", str(p), f"{target}:{'windows' if preset=='Windows' else 'linux'}", f"--userversion={version}"])
    elif "Itch" in skipped:
        print(f"\n--- SKIPPING ITCH ({skipped['Itch']}) ---")

    # 6. Deploy Steam (full game, then demo)
    for label in ["Steam", "Steam Demo"]:
        if label in targets: deploy_steam(label, targets[label], export_paths, root)
        elif label in skipped: print(f"\n--- SKIPPING {label.upper()} ({skipped[label]}) ---")

    print("\n=== DONE ===")
    print(f"Deployed {version} to: {', '.join(targets)}")
    if skipped: print(f"Skipped: {', '.join(skipped)}")

if __name__ == "__main__": main()
