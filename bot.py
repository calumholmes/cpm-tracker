"""
cpm-tracker: a Discord bot that tracks chests opened per minute in Fortnite.
It listens in voice chat and counts every time someone says "chest".
Speech recognition runs locally with Vosk; no audio is saved or sent anywhere.

Run with run.bat (Windows), ./run.sh (macOS/Linux), or:  python bot.py
"""

import asyncio
import io
import json
import logging
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import discord
from discord import app_commands
from discord.ext import voice_recv
from vosk import KaldiRecognizer, Model, SetLogLevel

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATS_PATH = HERE / "stats.json"
MODELS_DIR = HERE / "models"
MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"

DEFAULT_CONFIG = {
    "token": "PASTE_YOUR_BOT_TOKEN_HERE",
    "guild_id": None,
    "trigger_words": ["chest", "chests"],
    "min_confidence": 0.6,
    "drop_window_minutes": 3,
    "drop_min_active_minutes": 4,
    "drop_min_chests": 4,
    "drop_ratio": 0.4,
    "min_best_minutes": 3,
    "board_update_seconds": 10,
    "voice_commands": True,
    "session_gap_minutes": 60,
}

# Voice phrases (each is two words said in a row).
END_GAME = ("end", "game")
START_GAME = ("start", "game")

log = logging.getLogger("cpm")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# --------------------------------------------------------------------------- config / storage

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
        raise SystemExit(f"Created {CONFIG_PATH.name}. Paste your bot token into it, then run again.")
    cfg = {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text())}
    if not cfg["token"] or cfg["token"].startswith("PASTE_"):
        raise SystemExit(f"Put your bot token in {CONFIG_PATH.name} first.")
    cfg["trigger_words"] = [w.lower().strip() for w in cfg["trigger_words"] if w.strip()]
    return cfg


def ensure_model() -> Path:
    path = MODELS_DIR / MODEL_NAME
    if path.exists():
        return path
    MODELS_DIR.mkdir(exist_ok=True)
    zip_path = MODELS_DIR / f"{MODEL_NAME}.zip"
    log.info("Downloading speech model (~40 MB, one time only)...")
    urllib.request.urlretrieve(MODEL_URL, zip_path)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(MODELS_DIR)
    zip_path.unlink()
    return path


class Stats:
    """All-time stats plus a crash-safe copy of the live session, in stats.json."""

    def __init__(self, path: Path):
        self.path = path
        data = json.loads(path.read_text()) if path.exists() else {}
        self.lifetime: dict = data.get("lifetime", {})
        self.games: list = data.get("games", [])  # one summary per finished game, all-time
        for i, g in enumerate(self.games, 1):     # stable game numbers (older files lack them)
            g.setdefault("no", i)
        leftover = data.get("session")
        if leftover:  # bot was closed mid-session: keep what was counted
            self.fold(leftover)
        self.save(None)

    def fold(self, session: dict):
        for uid, p in session.items():
            life = self.lifetime.setdefault(uid, {"name": p["name"], "chests": 0, "seconds": 0.0, "best_cpm": 0.0})
            life["name"] = p["name"]
            life["chests"] += p["chests"]
            life["seconds"] += p["seconds"]
            if p["seconds"] >= CFG["min_best_minutes"] * 60:
                life["best_cpm"] = max(life["best_cpm"], cpm(p["chests"], p["seconds"]))

    def next_game_no(self) -> int:
        return max((g["no"] for g in self.games), default=0) + 1

    def find_game(self, no: int) -> dict | None:
        return next((g for g in self.games if g["no"] == no), None)

    def save(self, session: dict | None):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"lifetime": self.lifetime, "games": self.games, "session": session}, indent=2))
        tmp.replace(self.path)


def cpm(chests: int, seconds: float) -> float:
    return chests / (seconds / 60) if seconds > 0 else 0.0


def fmt_time(seconds: float) -> str:
    s = int(seconds)
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# --------------------------------------------------------------------------- session model

class Player:
    def __init__(self, uid: int, name: str, slot: int):
        self.uid, self.name, self.slot = uid, name, slot  # slot = fixed chart colour
        self.chests = 0
        self.acc = 0.0           # active seconds banked
        self.seg: float | None = None  # monotonic time the clock started running
        self.recent: list[float] = []  # active-seconds stamp of each chest
        self.in_drop = False
        # current game
        self.g_chests = 0        # session chests when this game began
        self.g_active = 0.0      # session active seconds when this game began
        self.g_stamps: list[float] = []  # seconds since game start for each chest

    def new_game(self, now: float):
        self.g_chests, self.g_active, self.g_stamps = self.chests, self.active(now), []

    def active(self, now: float) -> float:
        return self.acc + (now - self.seg if self.seg is not None else 0.0)

    def run(self, now: float):
        if self.seg is None:
            self.seg = now

    def halt(self, now: float):
        if self.seg is not None:
            self.acc += now - self.seg
            self.seg = None

    def add(self, now: float, n: int = 1, game_t: float | None = None):
        a = self.active(now)
        for _ in range(n):
            self.chests += 1
            self.recent.append(a)
            if game_t is not None:
                self.g_stamps.append(game_t)
        self.recent = self.recent[-100:]

    def undo(self):
        if self.chests > self.g_chests:
            self.chests -= 1
            if self.recent:
                self.recent.pop()
            if self.g_stamps:
                self.g_stamps.pop()

    def drop(self, now: float, avg: float, session_chests: int):
        """(recent_cpm, avg_cpm) if this player's pace has dropped below their session average, else None."""
        if self.seg is None:
            return None
        a = self.active(now)  # since the bot started listening: the rolling window needs this much history
        if a < CFG["drop_min_active_minutes"] * 60 or session_chests < CFG["drop_min_chests"]:
            return None
        window = CFG["drop_window_minutes"] * 60
        rolling = sum(1 for t in self.recent if t > a - window) / (window / 60)
        return (rolling, avg) if rolling < avg * CFG["drop_ratio"] else None

    def snapshot(self, now: float) -> dict:
        return {"name": self.name, "chests": self.chests, "seconds": self.active(now)}


class Session:
    def __init__(self, voice: voice_recv.VoiceRecvClient, text: discord.abc.Messageable):
        self.voice = voice
        self.text = text
        self.players: dict[int, Player] = {}
        self.paused = False
        self.board: discord.Message | None = None
        self.started = time.monotonic()
        # Two different ids:
        #  conn_id: this /start…/stop listening run (its Player data is folded into lifetime at /stop)
        #  play_id: the *session*, i.e. a run of games less than SESSION_GAP apart; survives /stop and restarts
        self.conn_id = f"{time.time():.0f}"
        self.play_id = ""
        self.game_no = 0                 # game number within the session
        self.game_start: float | None = None  # monotonic; None = between games
        self.game_start_ts = 0.0         # wall clock, for the session-gap rule
        self.last_voice_cmd = 0.0
        self._enter_game()               # /start begins a game straight away
        self.game_start = time.monotonic()

    def _enter_game(self):
        """A game is starting: continue the last session if its last game ended < SESSION_GAP ago."""
        now_ts = time.time()
        last = STATS.games[-1] if STATS.games else None
        if last and last.get("play") and last.get("end_ts") and now_ts - last["end_ts"] < SESSION_GAP:
            self.play_id = last["play"]
            self.game_no = max(g.get("session_game", 0) for g in STATS.games if g.get("play") == self.play_id) + 1
        else:
            self.play_id = f"{now_ts:.0f}"
            self.game_no = 1
        self.game_start_ts = now_ts

    @property
    def channel(self) -> discord.VoiceChannel:
        return self.voice.channel

    @property
    def in_game(self) -> bool:
        return self.game_start is not None

    def game_t(self) -> float | None:
        return time.monotonic() - self.game_start if self.in_game else None

    def player(self, member: discord.abc.User) -> Player:
        p = self.players.get(member.id)
        if p is None:
            p = self.players[member.id] = Player(member.id, member.display_name, len(self.players))
            p.new_game(time.monotonic())
        p.name = member.display_name
        return p

    def begin_game(self):
        self.sync_clocks()
        now = time.monotonic()
        self._enter_game()
        self.game_start = now
        self.paused = False
        for p in self.players.values():
            p.new_game(now)
        self.sync_clocks()

    def finish_game(self) -> dict:
        """Stop the clock and return this game's results for the chart."""
        now = time.monotonic()
        duration = now - self.game_start
        self.game_start = None
        self.paused = True
        self.sync_clocks()
        stamps, chests, player_secs, per_player = [], 0, 0.0, {}
        for p in self.players.values():
            ch, secs = p.chests - p.g_chests, p.active(now) - p.g_active
            if ch or secs >= 30:
                per_player[str(p.uid)] = {"name": p.name, "chests": ch, "seconds": round(secs, 1)}
                chests += ch
                player_secs += secs
                stamps += p.g_stamps
        return {"game": self.game_no, "start_ts": self.game_start_ts, "end_ts": time.time(),
                "duration": duration, "stamps": sorted(stamps),
                "chests": chests, "player_seconds": player_secs, "players": len(per_player),
                "per_player": per_player, "cpm": cpm(chests, player_secs)}

    def session_totals(self) -> dict[str, dict]:
        """Per-player chests/seconds for the current session: its finished games plus the game in progress."""
        rows: dict[str, dict] = {}

        def add(uid, name, ch, secs):
            r = rows.setdefault(uid, {"name": name, "chests": 0, "seconds": 0.0})
            r["name"], r["chests"], r["seconds"] = name, r["chests"] + ch, r["seconds"] + secs

        for g in STATS.games:
            if g.get("play") == self.play_id:
                for uid, pp in g.get("per_player", {}).items():
                    add(uid, pp["name"], pp["chests"], pp["seconds"])
        now = time.monotonic()
        for p in self.players.values():
            if self.in_game:
                add(str(p.uid), p.name, p.chests - p.g_chests, p.active(now) - p.g_active)
            else:
                add(str(p.uid), p.name, 0, 0.0)  # keep everyone in voice on the board
        return rows

    def player_drop(self, p: "Player", rows: dict, now: float):
        r = rows.get(str(p.uid))
        if not r:
            return None
        return p.drop(now, cpm(r["chests"], r["seconds"]), r["chests"])

    def remove_game_time(self, uid: int, chests: int, seconds: float):
        """Take a deleted game's chests and time back off a player in this listening run."""
        p = self.players.get(uid)
        if p is None:
            return False
        chests = min(chests, p.chests)
        p.chests -= chests
        p.g_chests -= chests
        p.acc -= seconds
        p.g_active -= seconds
        return True

    def sync_clocks(self):
        """Clock runs for everyone in the voice channel while not paused."""
        now = time.monotonic()
        present = {m.id for m in self.channel.members if not m.bot}
        for m in self.channel.members:
            if not m.bot:
                self.player(m)
        for p in self.players.values():
            if not self.paused and p.uid in present:
                p.run(now)
            else:
                p.halt(now)

    def snapshot(self) -> dict:
        now = time.monotonic()
        return {str(uid): p.snapshot(now) for uid, p in self.players.items()}


# --------------------------------------------------------------------------- speech

def pcm48_stereo_to_16k_mono(pcm: bytes) -> bytes:
    a = np.frombuffer(pcm, dtype=np.int16)
    a = a[: len(a) // 6 * 6]  # 3 stereo frames (6 samples) -> 1 mono sample
    if not len(a):
        return b""
    return a.reshape(-1, 6).mean(axis=1).astype(np.int16).tobytes()


class ChestListener(voice_recv.AudioSink):
    """Feeds each speaker's audio to their own Vosk recognizer. Runs on a voice thread."""

    IDLE_FLUSH = 0.7  # seconds of no packets = end of what they were saying

    def __init__(self, model: Model, on_hit, on_command):
        super().__init__()
        self.model = model
        self.on_hit = on_hit          # called (member, count) from this thread
        self.on_command = on_command  # called (member, "start" | "end") from this thread
        vocab = list(CFG["trigger_words"])
        if CFG["voice_commands"]:
            vocab += sorted(set(END_GAME + START_GAME) - set(vocab))
        self.grammar = json.dumps(vocab + ["[unk]"])
        self.recs: dict[int, KaldiRecognizer] = {}
        self.last: dict[int, tuple[float, discord.abc.User]] = {}
        self.lock = threading.Lock()
        self.alive = True
        threading.Thread(target=self._flusher, daemon=True).start()

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data):
        if user is None or getattr(user, "bot", False) or not data.pcm:
            return
        audio = pcm48_stereo_to_16k_mono(data.pcm)
        if not audio:
            return
        with self.lock:
            rec = self.recs.get(user.id)
            if rec is None:
                rec = self.recs[user.id] = KaldiRecognizer(self.model, 16000, self.grammar)
                rec.SetWords(True)
            self.last[user.id] = (time.monotonic(), user)
            if rec.AcceptWaveform(audio):
                self._handle(user, rec.Result())

    def _flusher(self):
        while self.alive:
            time.sleep(0.25)
            now = time.monotonic()
            with self.lock:
                for uid, (t, user) in list(self.last.items()):
                    if now - t > self.IDLE_FLUSH:
                        del self.last[uid]
                        self._handle(user, self.recs[uid].FinalResult())
                        self.recs[uid].Reset()

    def _handle(self, user, result_json: str):
        try:
            words = json.loads(result_json).get("result", [])
        except ValueError:
            return
        heard = [w.get("word") for w in words if w.get("conf", 0) >= CFG["min_confidence"]]
        try:
            if CFG["voice_commands"]:
                for a, b in zip(heard, heard[1:]):
                    if (a, b) == END_GAME:
                        self.on_command(user, "end")
                    elif (a, b) == START_GAME:
                        self.on_command(user, "start")
            n = sum(1 for w in heard if w in CFG["trigger_words"])
            if n:
                self.on_hit(user, n)
        except Exception:
            log.exception("speech callback failed")

    def cleanup(self):
        self.alive = False


# --------------------------------------------------------------------------- end-of-game chart

SURFACE, INK, INK_2, GRID = "#1a1a19", "#f0efec", "#c3c2b7", "#33332f"
SQUAD = "#3987e5"          # categorical slot 1 (dark step): the squad line, and this game's bar
PAST = "#4a4a46"           # muted: earlier games, so this game stands out
HISTORY_BARS = 30          # how many past games the bottom panel shows


def team_cpm(g: dict) -> float:
    """Whole team's chests ÷ game minutes (not divided by player count)."""
    return cpm(g["chests"], g.get("duration", 0))


def team_totals(games: list) -> tuple[int, float]:
    return sum(g["chests"] for g in games), sum(g.get("duration", 0) for g in games)


def _style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK_2, length=0, labelsize=9)


def make_game_chart(result: dict, games: list) -> bytes:
    """Top: squad's combined chests over this game. Bottom: squad pace for every game, all-time."""
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(9, 8), dpi=130, gridspec_kw={"height_ratios": [1.3, 1]})
    fig.patch.set_facecolor(SURFACE)

    # --- this game, squad combined
    dur_min = max(result["duration"] / 60, 0.1)
    stamps = [t / 60 for t in result["stamps"]]
    n = len(stamps)
    _style(top)
    top.step([0.0] + stamps + [dur_min], [0] + list(range(1, n + 1)) + [n], where="post",
             color=SQUAD, linewidth=2, solid_capstyle="round")
    if stamps:
        top.plot(stamps, range(1, n + 1), "o", ms=5, color=SQUAD, markeredgecolor=SURFACE, markeredgewidth=1.5)
    top.annotate(f"{n} chests", (dur_min, n), xytext=(6, 0), textcoords="offset points",
                 va="center", color=INK, fontsize=10, annotation_clip=False)
    top.set_xlim(0, dur_min)
    top.set_ylim(0, max(n, 1) * 1.12 + 0.5)
    top.yaxis.get_major_locator().set_params(integer=True)
    top.set_xlabel("Minutes into the game", color=INK_2, fontsize=10)
    top.set_ylabel("Squad chests opened", color=INK_2, fontsize=10)
    top.set_title(f"Game {result['game']} this session (#{games[-1]['no']} all-time): squad", loc="left", color=INK, fontsize=15, fontweight="bold", pad=24)
    who = f"{result['players']} player" + ("s" if result["players"] != 1 else "")
    top.text(0, 1.02, f"{fmt_time(result['duration'])} long · {who} · "
                      f"team {team_cpm(result):.2f} chests/min",
             transform=top.transAxes, color=INK_2, fontsize=10)

    # --- every game, all-time
    _style(bottom)
    shown = games[-HISTORY_BARS:]
    xs = list(range(len(shown)))           # bar positions; labelled with the stored game numbers
    labels = [str(g["no"]) for g in shown]  # numbers can have gaps after /deletegame
    vals = [team_cpm(g) for g in shown]
    colors = [PAST] * (len(shown) - 1) + [SQUAD]
    bottom.bar(xs, vals, width=0.8, color=colors, edgecolor=SURFACE, linewidth=2)
    tot_ch, tot_secs = team_totals(games)
    avg = cpm(tot_ch, tot_secs)
    bottom.axhline(avg, color=INK_2, linewidth=1, linestyle=(0, (4, 3)))
    bottom.annotate(f"overall {avg:.2f}", (xs[0] - 0.4, avg), xytext=(0, 4), textcoords="offset points",
                    color=INK_2, fontsize=9, va="bottom")
    bottom.annotate(f"{vals[-1]:.2f}", (xs[-1], vals[-1]), xytext=(0, 3), textcoords="offset points",
                    ha="center", va="bottom", color=INK, fontsize=9, fontweight="bold")
    bottom.set_xlim(xs[0] - 0.6, xs[-1] + 0.6)
    bottom.set_ylim(0, max(vals + [avg, 0.4]) * 1.25)
    step = 1 if len(xs) <= 15 else max(1, len(xs) // 10)
    bottom.set_xticks(xs[::step], labels[::step])
    bottom.set_xlabel("Game # (all-time)", color=INK_2, fontsize=10)
    bottom.set_ylabel("Team chests/min", color=INK_2, fontsize=10)
    bottom.set_title("Overall, every game", loc="left", color=INK, fontsize=13, fontweight="bold", pad=22)
    plural = "game" if len(games) == 1 else "games"
    span = f"last {len(shown)} of {len(games)} games" if len(shown) < len(games) else f"{len(games)} {plural}"
    bottom.text(0, 1.02, f"{span} · {tot_ch} chests · {fmt_time(tot_secs)} of games · "
                         f"#{games[-1]['no']} (this game) highlighted",
                transform=bottom.transAxes, color=INK_2, fontsize=10)

    fig.tight_layout(h_pad=3)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=SURFACE)
    plt.close(fig)
    return buf.getvalue()


LOSER_MIN_SECONDS = 60  # must have played at least this long in the game to be eligible


def biggest_loser(result: dict) -> dict | None:
    """'Tonight's biggest loser' (the name's a joke): the lowest chests/min in THIS game. Needs 2+ players."""
    eligible = [p for p in result["per_player"].values() if p["seconds"] >= LOSER_MIN_SECONDS]
    if len(eligible) < 2:
        return None
    worst = min(eligible, key=lambda p: cpm(p["chests"], p["seconds"]))
    return {**worst, "cpm": cpm(worst["chests"], worst["seconds"])}


async def end_game(s: Session, by: str):
    if not s.in_game:
        return
    result = s.finish_game()
    STATS.games.append({
        "no": STATS.next_game_no(), "conn": s.conn_id, "play": s.play_id, "session_game": result["game"],
        "start_ts": round(result["start_ts"]), "end_ts": round(result["end_ts"]),
        "at": time.strftime("%Y-%m-%d %H:%M"),
        "chests": result["chests"], "player_seconds": round(result["player_seconds"], 1),
        "duration": round(result["duration"], 1), "players": result["players"], "cpm": result["cpm"],
        "per_player": result["per_player"],
    })
    STATS.save(s.snapshot())
    games = STATS.games
    tot_ch, tot_secs = team_totals(games)
    try:
        png = await asyncio.to_thread(make_game_chart, result, list(games))
        e = discord.Embed(title=f"🏁 Game {result['game']} over", color=discord.Color.gold())
        e.add_field(name="This game (team)", inline=False,
                    value=f"**{team_cpm(result):.2f} chests/min** · {result['chests']} chests in "
                          f"{fmt_time(result['duration'])}")
        e.add_field(name="Overall across all " + (f"{len(games)} games" if len(games) != 1 else "1 game"),
                    inline=False,
                    value=f"**{cpm(tot_ch, tot_secs):.2f} chests/min** · {tot_ch} chests in "
                          f"{fmt_time(tot_secs)}")
        loser = biggest_loser(result)
        if loser:
            e.add_field(name="😬 Tonight's biggest loser", inline=False,
                        value=f"**{discord.utils.escape_markdown(loser['name'])}** at **{loser['cpm']:.2f}/min** "
                              f"({loser['chests']} chests in {fmt_time(loser['seconds'])})")
        e.set_image(url="attachment://game.png")
        e.set_footer(text=f"Ended by {by} · say “start game” (or /startgame) for the next one")
        await s.text.send(embed=e, file=discord.File(io.BytesIO(png), "game.png"), allowed_mentions=NO_PINGS)
    except Exception as ex:
        log.exception("chart failed")
        await s.text.send(f"🏁 Game {result['game']} over (couldn't draw the graph: {ex})")
    await refresh_board(s)


async def start_game(s: Session, by: str):
    if s.in_game:
        return
    s.begin_game()
    await s.text.send(f"▶️ **Game {s.game_no}** started by {discord.utils.escape_markdown(by)}. Clock's running!",
                      allowed_mentions=NO_PINGS)
    await refresh_board(s)


# --------------------------------------------------------------------------- bot

CFG = load_config()
SetLogLevel(-1)
MODEL = Model(str(ensure_model()))
STATS = Stats(STATS_PATH)

intents = discord.Intents.default()  # includes guilds + voice_states
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
sessions: dict[int, Session] = {}  # guild id -> listening run (/start…/stop)
SESSION_GAP = CFG["session_gap_minutes"] * 60  # games closer together than this form one session
NO_PINGS = discord.AllowedMentions.none()


def overall(session: "Session | None") -> dict:
    """All-time stats per player, including the session in progress (if any)."""
    rows = {uid: dict(r) for uid, r in STATS.lifetime.items()}
    if session:
        for uid, p in session.snapshot().items():
            r = rows.setdefault(uid, {"name": p["name"], "chests": 0, "seconds": 0.0, "best_cpm": 0.0})
            r["name"] = p["name"]
            r["chests"] += p["chests"]
            r["seconds"] += p["seconds"]
            if p["seconds"] >= CFG["min_best_minutes"] * 60:
                r["best_cpm"] = max(r["best_cpm"], cpm(p["chests"], p["seconds"]))
    return rows


def squad_line(rows) -> str:
    """Combined total: everyone's chests ÷ everyone's minutes = the average player's pace."""
    rows = list(rows)
    ch = sum(r["chests"] for r in rows)
    secs = sum(r["seconds"] for r in rows)
    return f"**{ch}** chests · **{cpm(ch, secs):.2f}/min** per player · {fmt_time(secs)} played"


def overall_lines(rows: dict) -> list[str]:
    ranked = sorted(rows.values(), key=lambda r: cpm(r["chests"], r["seconds"]), reverse=True)
    return [f"`{i}.` **{discord.utils.escape_markdown(r['name'])}** {cpm(r['chests'], r['seconds']):.2f}/min avg "
            f"· best {r['best_cpm']:.2f} · {r['chests']} chests · {fmt_time(r['seconds'])}"
            for i, r in enumerate(ranked, 1)]


def board_embed(s: Session) -> discord.Embed:
    """Live board. Player rows are for the current session (games < SESSION_GAP apart)."""
    now = time.monotonic()
    totals = s.session_totals()
    ranked = sorted(totals.items(), key=lambda kv: cpm(kv[1]["chests"], kv[1]["seconds"]), reverse=True)
    any_drop = False
    lines = []
    for i, (uid, r) in enumerate(ranked, 1):
        p = s.players.get(int(uid))
        d = s.player_drop(p, totals, now) if p else None
        flag = ""
        if d:
            any_drop = True
            flag = f"  🔴 **pace drop** ({d[0]:.2f}/min last {CFG['drop_window_minutes']} min)"
        elif p and p.seg is None and s.in_game and not s.paused:
            flag = "  _(not in voice)_"
        lines.append(f"`{i}.` **{discord.utils.escape_markdown(r['name'])}** {cpm(r['chests'], r['seconds']):.2f}/min "
                     f"· {r['chests']} chests · {fmt_time(r['seconds'])}{flag}")
    if not s.in_game:
        state = f"⏸️ between games (say “start game”)"
    else:
        state = f"Game {s.game_no} · " + ("⏸️ PAUSED" if s.paused else "🟢 LIVE")
    e = discord.Embed(
        title=f"Chests per minute · {state}",
        description="\n".join(lines) or "Nobody here yet. Say “chest” when you open one!",
        color=discord.Color.red() if any_drop else (discord.Color.orange() if s.paused else discord.Color.gold()),
    )
    if totals:
        e.add_field(name="Squad this session", value=squad_line(totals.values()), inline=False)
    all_time = overall(s)
    if all_time:
        value = squad_line(all_time.values()) + "\n" + "\n".join(overall_lines(all_time)[:10])
        e.add_field(name="Overall (all-time, including this session)", value=value[:1024], inline=False)
    words = ", ".join(f"“{w}”" for w in CFG["trigger_words"])
    e.set_footer(text=f"Listening for {words} in {s.channel.name} · audio stays on this PC, nothing is saved · "
                      f"say “end game” / “start game” · /undo for mistakes")
    return e


async def refresh_board(s: Session):
    try:
        if s.board is None:
            s.board = await s.text.send(embed=board_embed(s), allowed_mentions=NO_PINGS)
        else:
            await s.board.edit(embed=board_embed(s))
    except discord.NotFound:
        s.board = None
    except discord.HTTPException as e:
        log.warning("board update failed: %s", e)


async def check_drops(s: Session):
    now = time.monotonic()
    totals = s.session_totals()
    for p in s.players.values():
        d = s.player_drop(p, totals, now)
        if d and not p.in_drop:
            e = discord.Embed(
                title=f"🔴 Pace drop: {p.name}",
                description=(f"**{d[0]:.2f}/min** over the last {CFG['drop_window_minutes']} min, "
                             f"down from a **{d[1]:.2f}/min** average "
                             f"({(1 - d[0] / d[1]) * 100:.0f}% slower)."),
                color=discord.Color.red())
            try:
                await s.text.send(embed=e, allowed_mentions=NO_PINGS)
            except discord.HTTPException as ex:
                log.warning("drop alert failed: %s", ex)
        p.in_drop = bool(d)


async def board_loop():
    await client.wait_until_ready()
    while not client.is_closed():
        await asyncio.sleep(CFG["board_update_seconds"])
        for s in list(sessions.values()):
            s.sync_clocks()
            await check_drops(s)
            await refresh_board(s)
            STATS.save(s.snapshot())


def end_session(guild_id: int) -> Session | None:
    s = sessions.pop(guild_id, None)
    if s:
        s.sync_clocks()
        STATS.fold(s.snapshot())
        STATS.save(None)
    return s


async def in_session(inter: discord.Interaction) -> Session | None:
    s = sessions.get(inter.guild_id)
    if s is None:
        await inter.response.send_message("No session running. Use `/start` from a voice channel.", ephemeral=True)
    return s


@tree.command(description="Join your voice channel and start counting chests")
async def start(inter: discord.Interaction):
    if inter.guild is None:
        return await inter.response.send_message("Use this in a server.", ephemeral=True)
    vs = inter.user.voice
    if not vs or not vs.channel:
        return await inter.response.send_message("Join a voice channel first, then run `/start`.", ephemeral=True)
    if inter.guild_id in sessions:
        return await inter.response.send_message("Already running. Use `/stop` to end it.", ephemeral=True)
    await inter.response.defer()
    try:
        voice = await vs.channel.connect(cls=voice_recv.VoiceRecvClient, self_deaf=False)
    except Exception as e:
        log.exception("voice connect failed")
        return await inter.followup.send(f"Couldn't join voice: {e}")

    s = Session(voice, inter.channel)
    sessions[inter.guild_id] = s
    loop = asyncio.get_running_loop()

    def on_hit(member, n):  # voice thread -> event loop
        def apply():
            if sessions.get(inter.guild_id) is not s or s.paused:
                return
            p = s.player(member)
            if p.seg is None:
                return
            p.add(time.monotonic(), n, s.game_t())
            log.info("%s +%d (total %d)", p.name, n, p.chests)
        loop.call_soon_threadsafe(apply)

    def on_command(member, cmd):  # voice thread -> event loop
        def apply():
            if sessions.get(inter.guild_id) is not s:
                return
            now = time.monotonic()
            if now - s.last_voice_cmd < 5:  # ignore echoes / repeats
                return
            s.last_voice_cmd = now
            log.info("%s said %s game", member.display_name, cmd)
            asyncio.ensure_future(end_game(s, member.display_name) if cmd == "end"
                                  else start_game(s, member.display_name))
        loop.call_soon_threadsafe(apply)

    voice.listen(ChestListener(MODEL, on_hit, on_command),
                 after=lambda exc: exc and log.error("listener stopped: %s", exc))
    s.sync_clocks()
    words = ", ".join(f"**{w}**" for w in CFG["trigger_words"])
    await inter.followup.send(
        f"🎙️ Listening in **{vs.channel.name}** for {words}. "
        + (f"**Game {s.game_no}** of the current session has started"
           if s.game_no > 1 else "**Game 1** of a new session has started")
        + ", and the clock is running for everyone in the channel.\n"
        f"Speech is recognised locally on the host's PC. No audio is recorded or saved.",
        allowed_mentions=NO_PINGS)
    await refresh_board(s)


@tree.command(description="Pause the clock for everyone (between games)")
async def pause(inter: discord.Interaction):
    if s := await in_session(inter):
        s.paused = True
        s.sync_clocks()
        await inter.response.send_message("⏸️ Paused. Chests aren't counted until `/resume`.")
        await refresh_board(s)


@tree.command(description="Resume the clock")
async def resume(inter: discord.Interaction):
    if s := await in_session(inter):
        if not s.in_game:
            await inter.response.send_message("Starting the next game.", ephemeral=True)
            return await start_game(s, inter.user.display_name)
        s.paused = False
        s.sync_clocks()
        await inter.response.send_message("▶️ Resumed. Go loot!")
        await refresh_board(s)


@tree.command(description="End the current game and post its graph (same as saying “end game”)")
async def endgame(inter: discord.Interaction):
    if s := await in_session(inter):
        if not s.in_game:
            return await inter.response.send_message("No game in progress. Say “start game” first.", ephemeral=True)
        await inter.response.send_message("Ending the game…", ephemeral=True)
        await end_game(s, inter.user.display_name)


@tree.command(description="Start the next game (same as saying “start game”)")
async def startgame(inter: discord.Interaction):
    if s := await in_session(inter):
        if s.in_game:
            return await inter.response.send_message("A game is already running.", ephemeral=True)
        await inter.response.send_message("Starting…", ephemeral=True)
        await start_game(s, inter.user.display_name)


@tree.command(description="Remove one chest (a false count)")
@app_commands.describe(member="Whose count to fix (default: you)")
async def undo(inter: discord.Interaction, member: discord.Member | None = None):
    if s := await in_session(inter):
        p = s.player(member or inter.user)
        p.undo()
        await inter.response.send_message(f"↩️ {p.name}: {p.chests} chests", allowed_mentions=NO_PINGS)
        await refresh_board(s)


@tree.command(description="Add one chest the bot missed")
@app_commands.describe(member="Who to credit (default: you)")
async def add(inter: discord.Interaction, member: discord.Member | None = None):
    if s := await in_session(inter):
        p = s.player(member or inter.user)
        p.add(time.monotonic(), 1, s.game_t())
        await inter.response.send_message(f"➕ {p.name}: {p.chests} chests", allowed_mentions=NO_PINGS)
        await refresh_board(s)


@tree.command(description="Stop listening, save stats and leave voice")
async def stop(inter: discord.Interaction):
    s = sessions.get(inter.guild_id)
    if s is None:
        return await inter.response.send_message("No session running.", ephemeral=True)
    await inter.response.defer()
    if s.in_game:  # post the last game's graph before wrapping up
        await end_game(s, inter.user.display_name)
    final = board_embed(s)
    end_session(inter.guild_id)
    final.title = "Chests per minute · session over"
    final.color = discord.Color.dark_gold()
    await s.voice.disconnect(force=True)
    await inter.followup.send(embed=final, allowed_mentions=NO_PINGS)


def game_line(g: dict) -> str:
    return (f"`#{g['no']}` {g.get('at', '?')} · **{g['chests']}** chests · {team_cpm(g):.2f}/min team · "
            f"{g.get('players', '?')} players · {fmt_time(g.get('duration', 0))}")


def delete_game(no: int, guild_id: int | None) -> dict | None:
    """Remove a game from the history and take its chests/time back off each player."""
    g = STATS.find_game(no)
    if g is None:
        return None
    STATS.games.remove(g)
    conn = g.get("conn", g.get("session"))  # older records stored the listening-run id as "session"
    live = next((s for s in sessions.values() if s.conn_id == conn), None)
    for uid, pp in g.get("per_player", {}).items():
        if live and live.remove_game_time(int(uid), pp["chests"], pp["seconds"]):
            continue  # played during this listening run: not folded into lifetime yet, so fix it live
        life = STATS.lifetime.get(uid)  # earlier night: already folded into all-time totals
        if life:
            life["chests"] = max(0, life["chests"] - pp["chests"])
            life["seconds"] = max(0.0, life["seconds"] - pp["seconds"])
    here = sessions.get(guild_id)
    STATS.save(here.snapshot() if here else None)
    return g


class ConfirmDelete(discord.ui.View):
    def __init__(self, no: int, requester: int):
        super().__init__(timeout=60)
        self.no, self.requester = no, requester

    async def interaction_check(self, inter: discord.Interaction) -> bool:
        if inter.user.id != self.requester:
            await inter.response.send_message("Only the person who asked can confirm this.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Delete it", style=discord.ButtonStyle.danger)
    async def confirm(self, inter: discord.Interaction, button: discord.ui.Button):
        self.stop()
        g = delete_game(self.no, inter.guild_id)
        if g is None:
            return await inter.response.edit_message(content=f"Game #{self.no} is already gone.", view=None)
        note = "" if g.get("per_player") else (
            "\n_This game was saved before per-player tracking, so only the game history changed, "
            "not anyone's totals._")
        await inter.response.edit_message(
            content=f"🗑️ Game **#{self.no}** deleted by {discord.utils.escape_markdown(inter.user.display_name)}. "
                    f"Its chests and time were taken off everyone's stats.{note}",
            view=None, allowed_mentions=NO_PINGS)
        if s := sessions.get(inter.guild_id):
            await refresh_board(s)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, inter: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await inter.response.edit_message(content="Cancelled. Nothing was deleted.", view=None)


@tree.command(description="List recent games with their numbers")
async def games(inter: discord.Interaction):
    recent = STATS.games[-15:][::-1]
    e = discord.Embed(title="Recent games", color=discord.Color.gold(),
                      description="\n".join(game_line(g) for g in recent) or "No games yet.")
    e.set_footer(text=f"{len(STATS.games)} games saved · /deletegame <number> to remove one")
    await inter.response.send_message(embed=e, allowed_mentions=NO_PINGS)


@tree.command(description="Delete a game by its number (see /games)")
@app_commands.describe(number="The game's # from /games or the end-of-game graph")
async def deletegame(inter: discord.Interaction, number: int):
    g = STATS.find_game(number)
    if g is None:
        return await inter.response.send_message(f"There's no game #{number}. Check `/games`.", ephemeral=True)
    await inter.response.send_message(
        f"Delete this game? Its chests and time come off everyone's stats.\n{game_line(g)}",
        view=ConfirmDelete(number, inter.user.id), allowed_mentions=NO_PINGS)


@tree.command(description="Overall all-time stats (includes the session in progress)")
async def stats(inter: discord.Interaction):
    rows = overall(sessions.get(inter.guild_id))
    e = discord.Embed(title="Overall chests per minute",
                      description="\n".join(overall_lines(rows)) or "No stats yet. Run `/start` and go loot!",
                      color=discord.Color.gold())
    if rows:
        e.add_field(name="Squad overall", value=squad_line(rows.values()), inline=False)
    e.set_footer(text=f"Best counts sessions of {CFG['min_best_minutes']}+ minutes.")
    await inter.response.send_message(embed=e, allowed_mentions=NO_PINGS)


@client.event
async def on_voice_state_update(member: discord.Member, before, after):
    s = sessions.get(member.guild.id)
    if s is None:
        return
    if member.id == client.user.id and after.channel is None:  # bot was kicked / disconnected
        end_session(member.guild.id)
        await s.text.send("Bot left voice, so the session is over and stats are saved.")
        return
    s.sync_clocks()


@client.event
async def on_ready():
    if CFG["guild_id"]:
        guild = discord.Object(id=int(CFG["guild_id"]))
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    log.info("Logged in as %s. Slash commands synced.", client.user)


async def main():
    async with client:
        asyncio.create_task(board_loop())
        await client.start(CFG["token"])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        for gid in list(sessions):
            end_session(gid)
        print("Stopped. Stats saved.")
