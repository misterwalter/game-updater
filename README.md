# Game Updater
Does what it says on the tin, simply and interactively.
Upload Godot games to:
- [Steam](https://store.steampowered.com/)
- [Itch.io](https://itch.io/)

## How To
1. `alias gu="python ~/code/game-updater/game-updater.py"` in the old .bashrc makes life _even_ easier.
2. Navigate to the godot project folder and run `gu` once. It'll create a `game_config.json` for you to fill in (your steam username goes in there as `steam_username`), and once you do that, you can run it again and it'll probably work!
3. Drown in that sweet sweet indie game dev money. 🤑🤑🤑🤑

Only on one store? Delete that platform's keys from `game_config.json`, or prefix them with `_` (e.g. `_itch_username`) to park them for later. The plan shown before the pause tells you exactly which platforms will and won't be updated, and why.

## Dependencies
Latest version is best, but whatever version you have is probably fine. There's nothing crazy here.
1. Python3
2. butler, to upload to itch.io
3. steamCMD

## Outro
Lumo did a lot here, but so did I. Let's call it a collaboration? The future is weird.
Additionally, almost zero effort has been expended on making this run on a variety of setups, as I am a busy person and I don't expect others to find this directly useful. I do recommend that you take a look if you want to learn though! Just be careful with the .vdf files, they are surprisingly finicky. Don't expect it to work right out of the box, but if you try it let me know! I'd be happy to do a little tech support just to learn how others do things.

Future platforms (gog.com, epic, etc) may come online with time, once I expand to those stores as well.

## License is public domain, but attribution is appreciated. I'm just not going to act like it makes any sense for me to chase you down if you don't.
