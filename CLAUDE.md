# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**cpm-tracker**: a Python Discord bot that tracks Fortnite **chests opened per minute (CPM)**. It joins a voice channel, recognises speech locally with Vosk, counts "chest", keeps a live leaderboard and posts an end-of-game chart. Everything is in `bot.py`. `README.md` is the user-facing guide, covering setup, commands, config keys and what each number means. Keep it in sync with behaviour changes.

There are no tests, linter or build step.

## Commands
- Run: `run.bat` (Windows) or `./run.sh` (macOS/Linux). Each creates `.venv` on first run, pip-installs `requirements.txt` on every launch, then runs `bot.py`.
- Syntax check: `python -m py_compile bot.py`, then delete the `__pycache__` it leaves behind.
- `bot.py` can't be imported without a valid `config.json`. Module import loads the config, downloads or loads the Vosk model and opens `stats.json`. To test pure functions or classes (`make_game_chart`, `biggest_loser`, `Player`, `Session._enter_game` and so on), extract them with `ast` and run them with stubbed `STATS`/`CFG`/`discord` instead of importing the module.

## Private files (gitignored: never read, print or commit them)
- `config.json` holds the Discord bot **token** and server ID. It's created from `DEFAULT_CONFIG` on first run, and `load_config` merges new defaults into older files.
- `stats.json` holds real people's Discord display names and user IDs, plus lifetime totals, the all-time `games` list and a crash-recovery `session` snapshot.
- `models/` holds the auto-downloaded Vosk model (`vosk-model-small-en-us-0.15`). `.venv/` is the local virtual environment.

Never put real names, IDs, server details or sample stats into committed files, including docs and examples.

## Dependencies (fragile)
Voice receive uses the **`zacker150/discord-ext-voice-recv` fork**, installed from GitHub (so Git is required). It pulls in its own discord.py fork plus `davey` to handle Discord's DAVE end-to-end voice encryption. The upstream `discord-ext-voice-recv` does not support DAVE. If the bot joins voice but never counts anything, suspect this dependency first. On macOS/Linux, the system Opus library is needed for decoding.

## Architecture (all in `bot.py`)
- **Threading boundary:** `ChestListener` (an `AudioSink`) runs on the voice-receive thread. For each speaker it downsamples 48 kHz stereo PCM to 16 kHz mono and feeds a per-user `KaldiRecognizer`. The recogniser is restricted by a grammar to the trigger words plus `start`, `end`, `game` and `[unk]`. Discord stops sending packets when someone goes quiet, so a `_flusher` thread forces `FinalResult()` after 0.7 s of silence. Hits and voice commands cross to the asyncio loop only via `loop.call_soon_threadsafe` (see `on_hit` / `on_command` inside the `/start` command). Never touch `Session` state from the voice thread.
- **Time model:** `Player` uses `time.monotonic()`. The clock is `acc` (banked seconds) plus a running `seg`. `Session.sync_clocks()` is the single place that runs or halts each player's clock: it runs only if not paused and the player is in the voice channel. Call it after any pause, game or voice-state change.
- **Two meanings of "session" (keep them straight):**
  - The `Session` class is a *listening run*, from `/start` to `/stop`, identified by `conn_id`. Its in-memory `Player` data is what gets folded into `STATS.lifetime`.
  - A **session** in the user-facing sense (`play_id`) is a run of games where each starts less than `session_gap_minutes` (default 60) after the previous one ended. It's derived from game records in `Session._enter_game()`, so it survives `/stop` and restarts. Game numbers within a session ("Game 3 this session") use `session_game`, which is max+1 so it stays unique after deletions. The UI says "session", never "tonight". The one exception is the biggest-loser label below.
  - Session-scoped numbers (board player rows, "Squad this session" and pace-drop averages) come from `Session.session_totals()`: the finished games with this `play_id`, plus the in-progress game's `g_*` deltas.
- **Games:** "end game" / `/endgame` calls `Session.finish_game()`, which pauses and returns squad totals. It's followed by `end_game()`, which appends to `STATS.games`, saves, renders the chart and posts it. "start game" calls `begin_game()`, which calls `_enter_game()` and takes per-player `g_*` baselines. `/pause` is a mid-game pause and doesn't end the game.
- **Game records** (`STATS.games`) have a stable all-time `no` (never reused, so numbers have gaps after a deletion), plus `conn`, `play`, `session_game`, `start_ts`/`end_ts` (epoch) and `per_player` chests and seconds. Older records may have only `session` (the old name for the conn id), with no `play`, timestamps or `per_player`. Code must tolerate that. `delete_game()` subtracts a deleted game from the live `Player` if its `conn` matches the running listening run (`Session.remove_game_time`), or from `STATS.lifetime` otherwise. A player's `best_cpm` isn't recalculated after a deletion. The chart labels bars with `no`, not their position.
- **Biggest loser** (`biggest_loser(result)`): labelled "Tonight's biggest loser" (an Impractical Jokers joke). It's the lowest CPM **in that one game** among players with at least `LOSER_MIN_SECONDS` (60) in it, and needs 2+ eligible players.
- **Stats layers:** `STATS.lifetime` holds completed listening runs. `overall(session)` merges the live run into it on the fly for display. `Stats.fold()` commits a run, both on `/stop` and when a leftover `session` snapshot is found at startup. Don't double-fold.
- **Pace-drop rule** (`Player.drop`, called through `Session.player_drop`): over the last `drop_window_minutes` of *active* time, the rate is below `drop_ratio` × the player's session average (from `session_totals`). The rolling window uses in-memory `Player.recent`, so it also needs `drop_min_active_minutes` of time in this listening run, plus `drop_min_chests` in the session. `check_drops` posts a red embed in the text channel where `/start` ran, once each time a player enters the drop state.
- **Discord output:** one live board message is edited every `board_update_seconds` by `board_loop`. All sends use `allowed_mentions=none`. Embed field values are capped at 1024 characters.
- **Two CPM definitions:** per-player CPM (chests ÷ that player's active minutes) is used by the live board, `/stats`, pace drops and the biggest loser. **Team CPM** (total chests ÷ game duration, *not* divided by player count; `team_cpm` / `team_totals`) is used by the end-of-game post, the chart and `/games`.
- **Chart** (`make_game_chart`): matplotlib Agg, rendered in `asyncio.to_thread`. The top panel is the squad's combined cumulative chests for this game. It shows only the squad's combined numbers, with no per-player lines (a design decision). The bottom panel has one bar per all-time game (last 30) with an overall-average reference line. Colours come from a validated dark-surface palette: blue `#3987e5` for the highlighted game, muted grey for past games.
