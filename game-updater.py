#!/usr/bin/env python3
"""
Game Updater: Build & Deploy for Godot 4.
Supports Itch.io and Steam.
"""

import os, sys, json, subprocess, shutil, re
from datetime import datetime
from pathlib import Path

# --- Configs ---
LOCAL_CFG = "game_config.json"
GODOT_ID = "org.godotengine.Godot"
PRESETS = ["Windows", "Linux"]
VERSION_FMT = "%Y.%m.%d.%H"
STEAMCMD_PATH = str(Path.home() / ".steam" / "steamcmd" / "steamcmd.sh")
TEMPLATE = {"itch_username": "USER", "itch_project_slug": "SLUG", "steam_username": "STEAM_USER", "steam_app_id": "APP", "steam_windows_depot_id": "WIN_DEPOT", "steam_linux_depot_id": "LNX_DEPOT"}

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

def resolve_targets(cfg):
    """Returns (targets, skipped): platform -> deploy settings, and platform -> reason it can't be deployed."""
    targets, skipped = {}, {}

    itch_keys = ["itch_username", "itch_project_slug"]
    unset = [k for k in itch_keys if not cfg_value(cfg, k)]
    if unset: skipped["Itch"] = describe_unset(cfg, unset)
    else: targets["Itch"] = {"target": "/".join(cfg_value(cfg, k) for k in itch_keys)}

    depot_keys = {p: f"steam_{p.lower()}_depot_id" for p in PRESETS}
    depots = {p: cfg_value(cfg, k) for p, k in depot_keys.items()}
    app_id, username = cfg_value(cfg, "steam_app_id"), cfg_value(cfg, "steam_username")
    if not app_id: skipped["Steam"] = describe_unset(cfg, ["steam_app_id"])
    elif not username: skipped["Steam"] = describe_unset(cfg, ["steam_username"])
    elif not any(depots.values()): skipped["Steam"] = "no depots configured: " + describe_unset(cfg, depot_keys.values())
    else:
        targets["Steam"] = {
            "username": username,
            "app_id": app_id,
            "depots": {p: d for p, d in depots.items() if d},
            "skipped_depots": {p: describe_unset(cfg, [depot_keys[p]]) for p, d in depots.items() if not d},
        }

    return targets, skipped

def run(cmd, interactive=False):
    print(f"> {(' ').join(cmd)}")
    try:
        subprocess.run(cmd, check=True, stdin=None if not interactive else None)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: {e}"); sys.exit(1)

def get_export_paths(root):
    cfg_path = root / "export_presets.cfg"
    if not cfg_path.exists():
        print(f"WARNING: {cfg_path} not found. Using fallbacks.")
        paths = {}
        for p in PRESETS:
            paths[p] = str(root / "builds" / p.lower() / ("game.exe" if p=="Windows" else "game"))
        return paths

    content = cfg_path.read_text()
    paths = {}
    print(f"\n--- DEBUG: Parsing {cfg_path} ---")
    
    pattern = r'\[preset\.(\d+)\](.*?)(?=\n\[|\Z)'
    matches = re.findall(pattern, content, re.DOTALL)
    
    for num, block in matches:
        name_match = re.search(r'name\s*=\s*"([^"]+)"', block)
        path_match = re.search(r'path\s*=\s*"([^"]+)"', block)
        
        if name_match and path_match:
            preset_name = name_match.group(1)
            path_str = path_match.group(1)
            
            if "Windows" in preset_name: target_key = "Windows"
            elif "Linux" in preset_name: target_key = "Linux"
            else: continue
            
            if path_str.startswith("res://"): path_str = path_str[6:]
            full_path = root / path_str
            paths[target_key] = str(full_path)
            print(f"  -> Matched '{preset_name}' -> {target_key}: {full_path}")
        else:
            print(f"  -> Warning: Preset {num} missing name/path.")

    # Fallbacks
    for p in PRESETS:
        if p not in paths:
            paths[p] = str(root / "builds" / p.lower() / ("game.exe" if p=="Windows" else "game"))
            print(f"  -> FALLBACK for '{p}': {paths[p]}")
            
    return paths

def main():
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
    if not targets:
        print(f"ERROR: No deploy targets configured in {LOCAL_CFG}. Nothing to do.")
        for name, reason in skipped.items(): print(f"  -> {name}: {reason}")
        sys.exit(1)

    butler_path = shutil.which("butler")
    if "Itch" in targets and not butler_path:
        print("ERROR: Itch is configured but 'butler' was not found on PATH."); sys.exit(1)
    if "Steam" in targets and not Path(STEAMCMD_PATH).exists():
        print(f"ERROR: Steam is configured but SteamCMD was not found at {STEAMCMD_PATH}."); sys.exit(1)

    # 2. Build Prep
    version = datetime.now().strftime(VERSION_FMT)
    print(f"Target Version: {version}")

    proj = root / "project.godot"
    if not proj.exists(): print(f"ERROR: {proj} not found. Run this from a Godot project folder."); sys.exit(1)

    export_paths = get_export_paths(root)
    for p in PRESETS: Path(export_paths[p]).parent.mkdir(parents=True, exist_ok=True)

    # 3. Plan
    print("\n" + "="*40)
    print("PLAN")
    print("="*40)
    print(f"Build: {', '.join(PRESETS)}")
    for p in PRESETS: print(f"  -> {p}: {export_paths[p]}")
    print(f"Deploy: {', '.join(targets)}")
    if "Itch" in targets: print(f"  -> Itch: {targets['Itch']['target']}")
    if "Steam" in targets:
        steam = targets["Steam"]
        print(f"  -> Steam: App {steam['app_id']} ({', '.join(f'{p} depot {d}' for p, d in steam['depots'].items())})")
        for p, reason in steam["skipped_depots"].items(): print(f"     !! {p} depot SKIPPED: {reason}")
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
    godot_cmd = ["flatpak", "run", GODOT_ID]
    for preset in PRESETS:
        out = export_paths[preset]
        run(godot_cmd + ["--headless", "--quit", "--export-release", preset, out])
        if not Path(out).exists(): print(f"FAIL: {out} not created"); sys.exit(1)
    print("Builds complete.")

    # 5. Deploy Itch
    if "Itch" in targets:
        print("\n--- DEPLOYING TO ITCH ---")
        target = targets["Itch"]["target"]

        for preset in PRESETS:
            p = Path(export_paths[preset])
            if p.exists():
                run([butler_path, "push", str(p), f"{target}:{'windows' if preset=='Windows' else 'linux'}", f"--userversion={version}"])
    else:
        print(f"\n--- SKIPPING ITCH ({skipped['Itch']}) ---")

    # 6. Deploy Steam
    if "Steam" not in targets:
        print(f"\n--- SKIPPING STEAM ({skipped['Steam']}) ---")
    else:
        print("\n--- DEPLOYING TO STEAM ---")
        steam = targets["Steam"]
        os.chmod(STEAMCMD_PATH, 0o755)

        # Define your branches (usually "public" is the default)
        # You can add more if you have beta branches
        branches = ["public"] 
        
        for preset, reason in steam["skipped_depots"].items():
            print(f"Skipping {preset} depot ({reason}).")

        for preset, depot_id in steam["depots"].items():
            os_name = preset.lower()
            folder = Path(export_paths[preset]).parent
            if not folder.exists():
                print(f"Error: Folder {folder} not found."); continue
            os.chmod(folder, 0o755)
            for item in folder.iterdir():
                if item.is_file(): os.chmod(item, 0o755)

            vdf_path = root / f"steam_build_{os_name}.vdf"
            rel_folder = f"builds/{os_name}"
            
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
        
        # Open browser to the specific App's Depots page
        # Replace 4491650 with your App ID if you change it later
        app_id = steam["app_id"]
        url = f"https://partner.steamgames.com/apps/builds/{app_id}/depots"
        
        try:
            import webbrowser
            webbrowser.open(url)
            print(f"Opened: {url}")
            print("Click 'Set as Default' for the latest build in the list.")
        except Exception as e:
            print(f"Could not open browser automatically: {e}")
            print(f"Go to: {url} manually.")

    print("\n=== DONE ===")
    print(f"Deployed {version} to: {', '.join(targets)}")
    if skipped: print(f"Skipped: {', '.join(skipped)}")

if __name__ == "__main__": main()