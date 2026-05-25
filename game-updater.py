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
STEAM_USERNAME = "REPLACE THIS STRING WITH YOUR STEAM USERNAME"
STEAMCMD_PATH = str(Path.home() / ".steam" / "steamcmd" / "steamcmd.sh")

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
        template = {"itch_username": "USER", "itch_project_slug": "SLUG", "steam_app_id": "APP", "steam_windows_depot_id": "WIN_DEPOT", "steam_linux_depot_id": "LNX_DEPOT"}
        cfg_path.write_text(json.dumps(template, indent=2))
        print(f"Created {LOCAL_CFG}. Edit and run again."); sys.exit(0)
    cfg = json.loads(cfg_path.read_text())

    # 2. Build Prep
    version = datetime.now().strftime(VERSION_FMT)
    print(f"Target Version: {version}")
    
    proj = root / "project.godot"
    content = proj.read_text()
    content = re.sub(r'(config/version\s*=\s*)"[^"]*"', r'\1"' + version + '"', content)
    if 'config/version' not in content: content += f'\nconfig/version = "{version}"\n'
    proj.write_text(content)

    export_paths = get_export_paths(root)
    for p in PRESETS: Path(export_paths[p]).parent.mkdir(parents=True, exist_ok=True)

    # 3. Plan
    print("\n" + "="*40)
    print("PLAN")
    print("="*40)
    print(f"Build: {', '.join(PRESETS)}")
    for p in PRESETS: print(f"  -> {p}: {export_paths[p]}")
    if cfg.get("itch_username"): print(f"Itch: {cfg['itch_username']}/{cfg['itch_project_slug']}")
    if cfg.get("steam_app_id"): print(f"Steam: App {cfg['steam_app_id']}")
    print("="*40)
    input("Press Enter to execute...")

    # 4. Build
    print("\n--- BUILDING ---")
    godot_cmd = ["flatpak", "run", GODOT_ID]
    for preset in PRESETS:
        out = export_paths[preset]
        run(godot_cmd + ["--headless", "--quit", "--export-release", preset, out])
        if not Path(out).exists(): print(f"FAIL: {out} not created"); sys.exit(1)
    print("Builds complete.")

    # 5. Deploy Itch
    if cfg.get("itch_username") and cfg.get("itch_project_slug"):
        print("\n--- DEPLOYING TO ITCH ---")
        target = f"{cfg['itch_username']}/{cfg['itch_project_slug']}"
        butler_path = shutil.which("butler")
        if not butler_path: print("ERROR: 'butler' not found"); sys.exit(1)
        
        for preset in PRESETS:
            p = Path(export_paths[preset])
            if p.exists():
                run([butler_path, "push", str(p), f"{target}:{'windows' if preset=='Windows' else 'linux'}", f"--userversion={version}"])

    # 6. Deploy Steam
    if cfg.get("steam_app_id"):
        print("\n--- DEPLOYING TO STEAM ---")
        
        if not Path(STEAMCMD_PATH).exists():
            print(f"ERROR: SteamCMD not found at {STEAMCMD_PATH}."); sys.exit(1)
        os.chmod(STEAMCMD_PATH, 0o755)

        # Define your branches (usually "public" is the default)
        # You can add more if you have beta branches
        branches = ["public"] 
        
        for os_name, depot_id in [("windows", cfg.get("steam_windows_depot_id")), ("linux", cfg.get("steam_linux_depot_id"))]:
            if not depot_id: continue
            
            folder = Path(export_paths[os_name.capitalize()]).parent
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
                f'    "appid" "{cfg["steam_app_id"]}"',
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
            cmd_upload = [STEAMCMD_PATH, "+login", STEAM_USERNAME, f"+run_app_build {vdf_path}", "+quit"]
            print(f"Uploading {os_name}...")
            run(cmd_upload, interactive=True)
            
            # 2. Assign to Default Branch
            # This command sets the MOST RECENTLY UPLOADED build for this depot to the specified branch
            print(f"Assigning latest build to branch 'public'...")
            cmd_branch = [STEAMCMD_PATH, "+login", STEAM_USERNAME, "+set_default_branch public", "+quit"]
            run(cmd_branch, interactive=True)


        # Final Step: Open the Steamworks Depots page for you to activate
        print("\n--- FINAL STEP ---")
        print("Uploads complete! The builds are uploaded but NOT yet active.")
        print("Opening Steamworks Depots page to assign the build to the 'public' branch...")
        
        # Open browser to the specific App's Depots page
        # Replace 4491650 with your App ID if you change it later
        app_id = cfg["steam_app_id"]
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

if __name__ == "__main__": main()