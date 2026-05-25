# Game Updater
Does what it says on the tin, simply and interactively.
Upload games to:
- [Steam](https://store.steampowered.com/)
- [Itch.io](https://itch.io/)

## How To
1. Replace the default steam account name in the script with your steam username.
2. `alias gu="python ~/code/game-updater/game-updater.py"` in the old .bashrc makes life _even_ easier.
3. Navigate to the godot project folder and run `gu` once. It'll fill you in on what it needs, and once you do that, you can run it again and it'll probably work!
4. Drown in that sweet sweet indie game dev money. 🤑🤑🤑🤑

## Dependencies
Latest version is best, but whatever verion you have is probably fine.
1. Python3
2. butler, to upload to itch.io
3. steamCMD

## Outro
Lumo did a lot here, but so did I. Let's call it a collaboration? Additionally, almost zero effort has been expended on making this run on a variety of setups, as I am a busy person and I don't expect others to find this directly useful. I do recommend that you take a look if you want to learn though! Just be careful with the .vdf files, they are surprisingly finicky. Don't expect it to work right out of the box, but if you try it let me know! I'd be happy to do a little tech support just to learn how others do things.

Future platforms (gog.com, epic, etc) may come online with time, once I expand to those stores as well.
