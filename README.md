# cpm-tracker

**Track how many chests your squad opens per minute in Fortnite, hands-free, from Discord voice chat.**

cpm-tracker is a Discord bot that sits in your voice channel and listens for the word **"chest"**. Every time
someone says it, that player gets a point. The bot keeps a live leaderboard in your text channel, warns you
when someone's pace drops, and posts a graph after every game.

Speech recognition runs **on your own computer** with [Vosk](https://alphacephei.com/vosk/). No audio is
recorded, saved or sent anywhere.

---

## Features

- 🎙️ **Hands-free counting.** Say "chest" when you open one. Each player is counted separately.
- 📊 **Live leaderboard.** Chests per minute, chest count and play time for everyone in voice, updated every few seconds.
- 🔴 **Pace-drop alerts.** If someone's recent pace falls well below their average, the board turns red and the bot posts an alert.
- 🏁 **End-of-game graph.** Say "end game" to get a chart of the squad's chests over that game, plus a bar chart of every game you've ever played.
- 😬 **Tonight's biggest loser.** After every game, the bot calls out whoever had the lowest chests per minute in that game.
- 🗂️ **Sessions and all-time stats.** Games less than an hour apart count as one session. All-time stats and game history are kept across restarts.
- 🙏 **Thank Claude (optional, off by default).** Say "thank you" or "thanks" and it counts as a thank-you to Claude, the AI
  assistant that helped build this bot. Turning it on adds a thank-you leaderboard, a thanks graph after every game
  and **Tonight's biggest meanie**. See [Thanking Claude](#thanking-claude-optional).
- 🗑️ **Fix mistakes.** `/undo` and `/add` correct the count, and `/deletegame` removes a game that shouldn't count.

---

## What you need

- A computer that stays on while you play (Windows, macOS or Linux). The bot runs there, not in the cloud.
- **Python 3.10 or newer**: [python.org/downloads](https://www.python.org/downloads/). On Windows, tick **"Add python.exe to PATH"** during install.
- **Git**: [git-scm.com/downloads](https://git-scm.com/downloads). It's needed to install one of the dependencies.
- **macOS / Linux only:** the Opus audio library.
  - macOS: `brew install opus`
  - Debian/Ubuntu: `sudo apt install libopus0`
- A Discord server where you have **Manage Server** permission, so you can add the bot.

About 100 MB of disk space is needed: ~40 MB for the speech model plus the Python packages.

---

## Setup (about 10 minutes, one time only)

### 1. Create your Discord bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**. Name it whatever you like, for example `CPM Tracker`.
2. On **General Information**, copy the **Application ID**. You'll need it in step 2.
3. Open **Bot** in the sidebar, click **Reset Token**, and copy the token.
   **Treat the token like a password.** Anyone who has it controls your bot.
4. Optional: on the **Bot** page, switch off **Public Bot** so only you can add it to servers. If Discord shows an error, first go to **Installation** and set **Install Link** to **None**.

You don't need any of the "Privileged Gateway Intents".

### 2. Invite the bot to your server

Open this link after replacing `YOUR_APP_ID` with your Application ID:

```
https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot+applications.commands&permissions=3230720
```

Pick your server and click **Authorize**. That grants View Channels, Send Messages, Embed Links, Read Message
History, Connect and Speak.

### 3. Download cpm-tracker

```bash
git clone <this repository's URL> cpm-tracker
cd cpm-tracker
```

You can also download the ZIP and extract it.

### 4. First run

- **Windows:** double-click `run.bat`
- **macOS / Linux:** run `./run.sh` in a terminal

The first run installs everything, which takes a few minutes. It then creates a `.env` file (a copy of
`.env.example`) and stops.

### 5. Add your token

Open `.env` in any text editor and fill it in:

```ini
DISCORD_TOKEN=paste-your-bot-token-here
DISCORD_GUILD_ID=your-server-id-here
```

No quotes or spaces are needed. `DISCORD_GUILD_ID` is optional but recommended. With it, the slash commands
appear in your server right away instead of after a delay. To find it, turn on **Developer Mode** in Discord
(**User Settings → Advanced**), then right-click your server icon and choose **Copy Server ID**.

You can also set `DISCORD_TOKEN` and `DISCORD_GUILD_ID` as ordinary environment variables instead of using
`.env`, which is handy on a server. Real environment variables take priority over `.env`.

### 6. Start it

Run `run.bat` or `./run.sh` again. When you see `Logged in as … Slash commands synced`, the bot is online.
The first start also downloads the speech model (~40 MB).

Leave that window open while you play. Close it, or press `Ctrl+C`, to stop the bot.

---

## How to use it

1. Join a voice channel with your squad.
2. In any text channel, type **`/start`**. The bot joins your voice channel and posts a live leaderboard in
   that text channel. The first game starts straight away.
3. **Say "chest" every time you open one.** Only say it when you actually open one, because "chest over there"
   counts too.
4. When the match ends, say **"end game"**. The clock stops and the bot posts the game's graph and stats.
5. When the next match starts, say **"start game"**.
6. When you're done for the night, type **`/stop`**.

### Commands

| Command | What it does |
|---|---|
| `/start` | Join your voice channel and start counting. Run it while you're in voice. |
| say **"end game"** or `/endgame` | End the current game and post its graph. |
| say **"start game"** or `/startgame` | Start the next game. |
| `/pause` / `/resume` | Pause the clock mid-game without ending the game, for example when someone goes AFK. |
| `/undo [member]` | Remove one chest that was counted by mistake. Defaults to you. |
| `/add [member]` | Add one chest the bot missed. Defaults to you. |
| `/stats` | All-time stats for every player, plus a squad total. |
| `/games` | List recent games with their numbers. |
| `/thanks` | All-time thank-you leaderboard and Claude's tally. Needs `thank_claude` on. |
| `/deletegame <number>` | Delete a game. You'll be asked to confirm. Its chests and time come off everyone's stats. |
| `/stop` | Stop listening, save stats and leave voice. |

### What the numbers mean

- **Chests/min (per player).** Your chests ÷ the minutes you were playing. Your clock only runs while you're
  in the voice channel and the game isn't paused or over. This is what the live leaderboard, `/stats` and
  pace-drop alerts use.
- **Team chests/min.** All the squad's chests ÷ the length of the game. This is what the end-of-game
  summary and graph use.
- **Session.** A run of games where each game starts less than an hour after the previous one ended. The
  leaderboard's player rows, the "Squad this session" line and game numbers ("Game 3 this session") reset
  when a new session begins. A `/stop` followed by `/start` within the hour continues the same session.
- **Pace drop.** Your chests/min over the last 3 minutes is less than 40% of your session average. It's only
  checked once you've played 4+ minutes and opened 4+ chests. You get one alert each time you drop, not a
  stream of them.
- **Tonight's biggest loser.** The player with the lowest chests/min in the game that just ended. It needs
  at least 2 players who each played a minute or more. The name is a joke and applies to every game.
- **Tonight's biggest meanie** (with `thank_claude` on). The player who thanked Claude the fewest times in
  the game that just ended. It uses the same eligibility rules as the biggest loser, names everyone who's tied, and
  calls out **everyone** if nobody said thank you at all.
- **Best.** A player's best chests/min over a single `/start`…`/stop` run of 3+ minutes.
- **Game numbers.** Every game gets a permanent all-time number (shown on the graph and in `/games`). Numbers
  aren't reused after a deletion.

---

## Configuration

There are two files:

- **`.env`** holds your secrets: `DISCORD_TOKEN` (required) and `DISCORD_GUILD_ID` (optional). See
  [step 5](#5-add-your-token).
- **`config.json`** holds optional settings. The bot works fine without it. To change something, copy
  `config.example.json` to `config.json` and edit the values. You can delete any line you don't want to
  change, and the default is used. Restart the bot after editing.

```bash
cp config.example.json config.json      # Windows: copy config.example.json config.json
```

| Setting | Default | What it does |
|---|---|---|
| `trigger_words` | `["chest", "chests"]` | Words that count as a chest. |
| `min_confidence` | `0.6` | How sure the recogniser must be (0 to 1). Raise it if you get false counts. Lower it if real ones are missed. |
| `voice_commands` | `true` | Set to `false` to turn off "start game" / "end game" and use the slash commands only. |
| `session_gap_minutes` | `60` | A break between games longer than this starts a new session. |
| `drop_window_minutes` | `3` | How far back the pace-drop check looks. |
| `drop_ratio` | `0.4` | A drop is recent pace below this fraction of your average. |
| `drop_min_active_minutes` | `4` | Minutes of play before pace drops are checked. |
| `drop_min_chests` | `4` | Chests needed before pace drops are checked. |
| `min_best_minutes` | `3` | Shortest session that can count as a "best". |
| `board_update_seconds` | `10` | How often the live leaderboard refreshes. |
| `thank_claude` | `false` | Set to `true` to count "thank you"s and "thanks" as thanks to Claude. See below. |

Keys starting with `_`, like `_thank_claude` in the example, are notes. The bot ignores them.

### Thanking Claude (optional)

cpm-tracker was built with help from [Claude](https://claude.ai), Anthropic's AI assistant. To let your squad
say thanks, set `"thank_claude": true` in `config.json` and restart the bot. Then:

- Every **"thank you"** or **"thanks"** said in voice while the bot is listening adds one to Claude's tally in `thanks.md`, a
  small file next to `bot.py`. It holds only the number. If you use Claude Code in this folder, Claude can read it.
- Thank-yous said **during a game** also count for the player who said them. They show up in a "🙏 Thanks to
  Claude" section on the live board and in `/stats`, and in the `/thanks` leaderboard.
- After each game, a second graph shows the squad's thank-yous over that game and the thank-yous per game
  across every game played with the setting on. **😠 Tonight's biggest meanie** goes to whoever thanked
  Claude the least.

It's off by default because the recogniser then listens for "thank", "you" and "thanks" too, and those are easy
to say without meaning it.

---

## Privacy

- **Audio never leaves your computer.** The bot hears each speaker's voice separately, turns it into text
  locally with Vosk and throws the audio away. Nothing is recorded or saved.
- The recogniser only listens for the trigger words plus "start", "end" and "game", and also "thank", "you"
  and "thanks" if `thank_claude` is on. It isn't transcribing your conversations.
- **What is saved** (in `stats.json`, on your computer only): each player's Discord display name and user ID,
  their chest counts, play time and thank-yous, and a summary of each game. `thanks.md` holds only a number.
- **Tell your friends the bot is listening** before you use it.
- `.env` (your token), `config.json` (your settings) and `stats.json` (your friends' data) are listed in
  `.gitignore`, so they won't be committed if you fork this repository. Only the `*.example` templates are
  committed.

---

## Troubleshooting

| Problem | Try this |
|---|---|
| Slash commands don't appear | Set `DISCORD_GUILD_ID` in `.env` and restart. Without it, Discord can take a while to show new commands. |
| `No bot token found` | Paste your token after `DISCORD_TOKEN=` in `.env`, then save it. |
| `config.json isn't valid JSON` | Check for a missing comma or quote, or delete `config.json` to use the defaults. |
| A warning about reading the token from `config.json` | Your setup predates `.env`. Move the token into `.env` as `DISCORD_TOKEN=...`, then delete the `token` and `guild_id` lines from `config.json`. |
| Installing dependencies fails | Make sure **Git** is installed and on your PATH, then run the launcher again. |
| The bot joins voice but never counts anything | Check the bot window for errors. Voice receiving depends on a community package (see below). Updating it often fixes this: delete the `.venv` folder and run the launcher again. |
| Too many false counts | Raise `min_confidence` (for example `0.75`), and make sure game audio isn't leaking into your mics. Use `/undo` to correct counts. |
| Chests are missed | Lower `min_confidence` (for example `0.45`) and say "chest" clearly. Use `/add` to correct counts. |
| "end game" triggers by accident | Set `voice_commands` to `false` and use `/endgame` and `/startgame` instead. |
| macOS/Linux: errors about Opus | Install the Opus library (see [What you need](#what-you-need)). |

---

## Known limitations

- **Voice receive relies on a community package.** Discord encrypts voice calls end to end (the "DAVE"
  protocol), and the official libraries don't yet support *receiving* that audio. cpm-tracker uses the
  [`zacker150/discord-ext-voice-recv`](https://github.com/zacker150/discord-ext-voice-recv) fork, which adds
  that support but describes itself as not yet feature complete. If Discord changes something, voice
  receiving may break until that package is updated.
- **Saying "chest" isn't the same as opening one.** Agree with your squad to say it only when you open a chest.
- **One server at a time per bot instance** is the intended use.
- Deleting a game doesn't recalculate players' "best" figures.
- Game length runs from "start game" to "end game", so a long mid-game `/pause` lowers that game's team rate.

---

## Project files

| File | Purpose |
|---|---|
| `bot.py` | The bot. |
| `requirements.txt` | Python dependencies. |
| `run.bat` / `run.sh` | Launchers that set everything up and start the bot. |
| `.env.example` | Template for `.env`. |
| `config.example.json` | Template for `config.json`, listing every setting with its default. |
| `.env` | Your bot token and server ID. Created on first run and **not committed**. |
| `config.json` | Your optional setting overrides. **Not committed**. |
| `stats.json` | Saved stats. Created automatically and **not committed**. |
| `thanks.md` | Claude's thank-you tally. Created when `thank_claude` is on and **not committed**. |
| `models/` | The downloaded speech model. Not committed. |
