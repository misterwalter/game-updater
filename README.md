# Game Updater
Does what it says on the tin, simply and interactively.
Upload Godot games to:
- [Steam](https://store.steampowered.com/)
- [Itch.io](https://itch.io/)
- [Patreon](https://www.patreon.com/) and [SubscribeStar](https://www.subscribestar.com/) (zipped for you; posted by hand)

## How To
1. `alias gu="python ~/code/game-updater/game-updater.py"` in the old .bashrc makes life _even_ easier.
2. Navigate to the godot project folder and run `gu` once. It'll create a `game_config.json` for you to fill in, and once you do that, you can run it again and it'll probably work!
3. Drown in that sweet sweet indie game dev money. 🤑🤑🤑🤑

Only on one store? Delete that platform's keys from `game_config.json`, or prefix them with `_` (e.g. `_itch_username`) to park them for later. The plan shown before the pause tells you exactly which platforms will and won't be updated, and why.


## Dependencies
Latest version is best, but whatever version you have is probably fine. There's nothing crazy here.
1. Python3
2. butler, to upload to itch.io
3. steamCMD

Future platforms (gog.com, epic, etc) may come online with time, once I expand to those stores as well.

## Steam demos

Got a demo app on Steam? Add `steam_demo_app_id` plus `steam_demo_windows_depot_id` / `steam_demo_linux_depot_id` to `game_config.json`. Then in Godot (Project > Export) duplicate your `Windows` and `Linux` presets as `Windows Demo` and `Linux Demo`, and give each one:

- its own export path in its own folder (e.g. `builds/demo/windows/...`), because Steam uploads the whole folder
- an exclude filter listing what the demo must not contain, e.g. `towns/latergame/*, scenes/secret_boss.tscn`. Folders need the trailing `/*` or Godot silently ignores them
- the custom feature `demo`, so the game can check `OS.has_feature("demo")`

Files left out this way aren't in the demo at all, so they can't be datamined. Everything that *does* ship can be, including the names of anything a shipped scene or script still points at, so the plan lists those for you to clean up. After building, the demo's PCK is opened and checked; if anything withheld is in there, or the demo folder has stray files in it, nothing gets uploaded anywhere.

`--demo-only` and `--no-demo` do what they say.


## Windows or Linux

It runs on either. Godot is found from `godot_path` in `game_config.json`, else `$GODOT`, else flatpak on Linux, else `godot` on PATH. SteamCMD from `steamcmd_path`, else `~/.steam/steamcmd/steamcmd.sh` on Linux and `steamcmd` on PATH on Windows. Either can be given per OS, so one committed config works on both machines:

```json
"godot_path": {"windows": "C:/Program Files (x86)/Steam/steamapps/common/Godot Engine/godot.windows.opt.tools.64.exe", "linux": ""}
```

An empty value falls back to the defaults. Exporting needs Godot's export templates on that machine (Editor > Manage Export Templates).

## A step before exporting

`pre_export` runs in the project before anything is read or built, and stops everything if it fails. Give it as a list (or a string); `{godot}` is the Godot command found above. A project that writes its export presets' filters itself does it here:

```json
"pre_export": ["{godot}", "--headless", "--path", ".", "-s", "res://tools/release_tiers.gd"]
```

## The demo on the same itch page

`"itch_demo": true` builds the `Windows Demo` and `Linux Demo` presets too and pushes them to the same itch page, on the `windows-demo` and `linux-demo` channels, beside the full game's `windows` and `linux`. Once, on itch (Edit game > Uploads), mark the two demo uploads as demos so they can be downloaded without buying. Every build is checked after exporting: any preset with an exclude filter has its PCK opened, and nothing is uploaded anywhere if a withheld file is inside, or a build folder has files the build didn't make (itch, like Steam, gets each build's whole folder).

## Patreon and SubscribeStar

Neither has an API to upload a file or make a post with, so these are by hand, made quick: give `patreon_post_url` and/or `subscribestar_post_url` (the page you post from), and after the other uploads the full game's builds are zipped into `builds/release/` (`<name>-<version>-windows.zip`, `-linux.zip`; `release_name` sets the name, the project's name otherwise) and the pages are opened in your browser for you to attach them to a post.

This script is unapologetically vibe coded and you should steal it. Our time is better spent elsewhere!
