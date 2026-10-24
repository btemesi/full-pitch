"""Live replay server: plays a synthetic match as if it were a live feed.

The live clock lives on the server. Each viewer gets a session; the session's
minute moves forward only as the stream delivers the match (or when the viewer
seeks). Every data endpoint answers "as of the session's minute", whatever the
client asks for, so neither the page nor an agent can read ahead.

  POST /api/session?from=0             new viewing session at a minute -> {session, minute}
  POST /api/session/{id}/seek?to=..    jump (the demo lets you skip around the replay)
  GET  /api/stream?session=..&speed=8  Server-Sent Events: frames (5 Hz), events, detected changes
  GET  /api/clock?session=..           the session's live minute (used by the MCP server)
  GET  /api/frame|state|cards|changes|lineup|player|catchup?session=..

Run: uvicorn fullpitch.server:app --port 8010
"""

import asyncio
import hashlib
import json
import os
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .analysis import slot_occupancy
from .catchup import player_name, score_at
from .insights import frames_payload
from .store import load
from .templates import WORDS, catch_up_text, change_card, goal_card, recap_text

MATCH_DIR = os.environ.get("FULLPITCH_MATCH", os.path.join(os.path.dirname(__file__), "..", "data", "FP-0001"))
WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")
SESSION_TTL_S = 3 * 3600
MAX_SESSIONS = 2000

Persona = Literal["casual", "analyst", "player"]
Lang = Literal["en", "hu"]


def _load_env(path):
    """Minimal .env reader (KEY=value lines); real environment variables win."""
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v)


_load_env(os.path.join(os.path.dirname(__file__), "..", ".env"))
TEAM = None  # the agent team; None means template text only
SPEECH = None  # spoken commentary; None means silent


def _speech_endpoint():
    # the same AI Services resource as the Foundry project: <name>.services.ai.azure.com -> <name>.cognitiveservices.azure.com
    explicit = os.environ.get("FULLPITCH_SPEECH_ENDPOINT")
    if explicit:
        return explicit
    project = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
    host = project.split("//")[-1].split("/")[0]
    return f"https://{host.split('.')[0]}.cognitiveservices.azure.com" if host.endswith(".services.ai.azure.com") else None


@asynccontextmanager
async def lifespan(_app):
    global TEAM, SPEECH
    if _speech_endpoint() and os.environ.get("FULLPITCH_SPEECH", "1") != "0":
        try:
            from .speech import Speech
            SPEECH = Speech(_speech_endpoint(), os.path.join(MATCH_DIR, "speech"))
        except Exception as exc:  # noqa: BLE001 - the page works without a voice
            print(f"[speech] disabled: {type(exc).__name__}: {exc}")
            SPEECH = None
    store_ = load(MATCH_DIR)  # warm the match and its analysis before the first viewer arrives
    if os.environ.get("FOUNDRY_PROJECT_ENDPOINT") and os.environ.get("FULLPITCH_AGENTS", "1") != "0":
        try:
            from .agents import AgentTeam
            TEAM = AgentTeam(store_, cache_path=os.path.join(MATCH_DIR, "agent_cache.json"))
            await TEAM.start()
        except Exception as exc:  # noqa: BLE001 - without agents the app still works on templates
            print(f"[agents] disabled: {type(exc).__name__}: {exc}")
            TEAM = None
    yield
    if TEAM:
        await TEAM.stop()


app = FastAPI(title="Full Pitch", lifespan=lifespan)
SESSIONS: dict[str, dict] = {}
CATCHUP_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
CATCHUP_INFLIGHT: dict[tuple, asyncio.Task] = {}
CATCHUP_CACHE_MAX = 300


def store():
    return load(MATCH_DIR)


def _clamp_minute(m):
    return max(0.0, min(float(m), store().length_min))


def _prune():
    now = time.monotonic()
    for sid in [k for k, v in SESSIONS.items() if now - v["seen"] > SESSION_TTL_S]:
        SESSIONS.pop(sid, None)
    while len(SESSIONS) > MAX_SESSIONS:
        SESSIONS.pop(min(SESSIONS, key=lambda k: SESSIONS[k]["seen"]))


def session(sid: str) -> dict:
    s = SESSIONS.get(sid)
    if s is None:
        raise HTTPException(404, "unknown or expired session")
    s["seen"] = time.monotonic()
    return s


def now(sid: str) -> float:
    return session(sid)["minute"]


# ---------------------------------------------------------------- sessions
@app.post("/api/session")
def new_session(start: float = Query(0.0, alias="from")):
    _prune()
    sid = uuid.uuid4().hex
    SESSIONS[sid] = {"minute": _clamp_minute(start), "seen": time.monotonic(), "stream": 0}
    return {"session": sid, "minute": SESSIONS[sid]["minute"]}


@app.post("/api/session/{sid}/seek")
def seek(sid: str, to: float):
    s = session(sid)
    s["minute"] = _clamp_minute(to)
    s["stream"] += 1  # any running stream for this session stops
    return {"session": sid, "minute": s["minute"]}


@app.get("/api/clock")
def clock(sid: str = Query(alias="session")):
    return {"minute": now(sid), "clock": store().clock(now(sid))}


# ---------------------------------------------------------------- data, always "as of now"
@app.get("/api/match")
def match():
    s = store()
    info = s.match_info()
    info["team_colors"] = {t["short"]: t["color"] for t in s.md.meta["teams"]}
    info["hz"] = s.md.hz
    return info


@app.get("/api/frame")
def frame(sid: str = Query(alias="session")):
    """The single frame at the session's minute (for a paused view)."""
    s, m = store(), now(sid)
    f = s.frame(m)
    md = s.md
    return {"f": f, "c": md.clock_label(f), "p": int(md.poss[f]), "t": round(float(md.clock[f]), 1), "per": int(md.period[f]),
            "m": round(float(md.tmin[f]), 3), "xy": [round(float(v), 1) for v in md.xy[f].ravel()]}


@app.get("/api/state")
def state(sid: str = Query(alias="session")):
    return store().match_state(now(sid))


@app.get("/api/changes")
def changes(sid: str = Query(alias="session")):
    return store().tactical_changes(now(sid))


@app.get("/api/cards")
async def cards(sid: str = Query(alias="session"), persona: Persona = "casual", lang: Lang = "en",
                follow: str | None = None):
    """On-screen cards up to the live minute: confirmed tactical changes, goals, red cards, recaps.
    The template text shows at once; the agents' version replaces it as soon as it is written."""
    out = _template_cards(sid, persona, lang)
    if TEAM:
        fol = follow if persona == "player" else None
        if fol and fol not in {p["id"] for p in store().md.meta["players"]}:
            raise HTTPException(404, "unknown player")
        for card in out:
            if card["kind"] not in ("change", "goal", "recap"):
                continue
            key = TEAM.event_key(card)
            hit = TEAM.lookup(card, persona, lang, fol)
            writing = hit is None and TEAM.ensure(card, persona, lang, fol)
            if hit or writing:
                card["agent_key"] = key  # only cards the agents wrote, tried, or are writing have a trace
            if hit and hit.get("source") == "agent":
                card.update(title=hit["title"], body=hit["body"], source="agent")
                exp = TEAM.cache.get(f"{card['agent_key']}|explain")
                if card["kind"] == "change" and exp:  # the start minute the agents wrote about
                    card["subtitle"] = WORDS[lang]["since"].format(clock=exp["facts"]["since"])
            elif writing:
                card["pending"] = True
    if SPEECH:  # the agents' prose can be read out (template cards are shorthand), and only cards sent here
        for card in out:
            if card.get("source") == "agent":
                card["speech"] = SPEECH.register(f"{card['title']}. {card['body']}", lang)
    return out


@app.get("/api/trace")
def trace(key: str, sid: str = Query(alias="session"), persona: Persona = "casual", lang: Lang = "en",
          follow: str | None = None):
    """How a card was written: each agent's step, tools used, time, and the fact-check.
    Only for cards the session can already see: a trace holds the card's text."""
    if not TEAM:
        raise HTTPException(404, "agents are off")
    if key not in {TEAM.event_key(c) for c in _template_cards(sid, persona, lang)}:
        raise HTTPException(404, "no such card yet")
    hit = TEAM.cache.get(f"{key}|{persona}|{lang}|{follow or ''}")
    if not hit:
        raise HTTPException(404, "not written yet")
    return {"key": key, "source": hit.get("source"), "steps": hit.get("trace", []), "models": _models()}


def _template_cards(sid, persona, lang):
    s, at = store(), now(sid)
    names = {t["short"]: t["name"] for t in s.md.meta["teams"]}
    out = []
    for c in s.tactical_changes(at):
        card = change_card(c, names, persona, lang)
        card["minute"] = c["since_minute"]
        card["confirmed_minute"] = c["confirmed_minute"]
        out.append(card)
    for kind in ("ht", "ft"):
        r = s.recap(kind, at)
        if r:
            t = recap_text(r, lang)
            out.append({"kind": "recap", "recap": kind, "team": None, "minute": s.ht_minute if kind == "ht" else s.length_min,
                        "subtitle": r["clock"], "title": t["title"], "body": "\n".join(t["lines"][1:]), "source": "template"})
    for e in s.key_moments(at):
        if e["type"] == "goal":
            card = goal_card(e, lang)
            card["minute"] = e["minute"]
            out.append(card)
        elif e["type"] == "card" and e.get("card") == "red":
            out.append({"kind": "red", "team": e["team"], "minute": e["minute"], "subtitle": e["clock"],
                        "title": ("Red card · " if lang == "en" else "Piros lap · ") + e["team"],
                        "body": e.get("player", ""), "source": "template"})
    return out


@app.get("/api/player")
def player(id: str, sid: str = Query(alias="session")):
    """A player's numbers from kick-off to the live minute."""
    s = store()
    if id not in {p["id"] for p in s.md.meta["players"]}:
        raise HTTPException(404, "unknown player")
    at = now(sid)
    return s.player_report(id, 0, at, at)


@app.get("/api/lineup")
def lineup(sid: str = Query(alias="session")):
    """Who is on the pitch in each tracking slot at the live minute (and who has been sent off)."""
    s = store()
    f = s.frame(now(sid))
    occ = slot_occupancy(s.md)
    players = {p["id"]: p for p in s.md.meta["players"]}
    out = {}
    for k, short in enumerate(s.md.shorts):
        team = []
        for slot, spans in enumerate(occ[k]):
            pid = next(p for p, a, b in spans if a <= f < b or (b == s.md.F and f >= a))
            p = players[pid]
            team.append({"id": pid, "name": p["name"], "number": p["number"],
                         "on_pitch": bool(s.md.active[f, k * 11 + slot])})
        out[short] = team
    return out


@app.get("/api/catchup/agent")
async def catchup_agent(sid: str = Query(alias="session"), away: float = Query(10, ge=1, le=120),
                        follow: str | None = None, persona: Persona = "casual", lang: Lang = "en"):
    """'What did I miss?' written by the Narrator agent and fact-checked; 404 if agents are off or fail."""
    if not TEAM:
        raise HTTPException(404, "agents are off")
    s, at = store(), now(sid)
    if follow and follow not in {p["id"] for p in s.md.meta["players"]}:
        raise HTTPException(404, "unknown player")
    # whole minutes: viewers asking within the same minute share one summary
    at = float(int(at))
    start = max(0.0, at - round(away))
    key = (start, at, follow, persona, lang)
    if key in CATCHUP_CACHE:
        CATCHUP_CACHE.move_to_end(key)
        return CATCHUP_CACHE[key]
    if key not in CATCHUP_INFLIGHT:
        if not TEAM.take_run():
            raise HTTPException(503, "agent budget for this hour is used up")
        facts = s.catch_up(start, at, at_minute=at, follow_player=follow)
        CATCHUP_INFLIGHT[key] = asyncio.create_task(_write_catchup(key, facts, persona, lang))
    res = await asyncio.shield(CATCHUP_INFLIGHT[key])
    if not res:
        raise HTTPException(503, "agents could not write a checked summary")
    return res


async def _write_catchup(key, facts, persona, lang):
    try:
        res = await asyncio.wait_for(TEAM.catch_up(facts, persona, lang), timeout=60)
    except Exception:  # noqa: BLE001
        res = None
    finally:
        CATCHUP_INFLIGHT.pop(key, None)
    if res:
        # the window line stays the measured one; the agents write the bullets
        res = {**res, "lines": [catch_up_text(facts, persona, lang)["lines"][0], *res["lines"]]}
        CATCHUP_CACHE[key] = res
        while len(CATCHUP_CACHE) > CATCHUP_CACHE_MAX:
            CATCHUP_CACHE.popitem(last=False)
    return res


@app.get("/api/speech/{sid}.mp3")
async def speech(sid: str):
    """A card or answer read out (Azure AI Speech). Only ids the server handed out are spoken."""
    if not SPEECH:
        raise HTTPException(404, "speech is off")
    try:
        audio = await SPEECH.audio(sid)
    except Exception:  # noqa: BLE001
        audio = None
    if audio is None:
        raise HTTPException(404, "no such line, or the voice is resting")
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})


ASK_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
ASK_INFLIGHT: dict[tuple, asyncio.Task] = {}


def _models():
    fast = os.environ.get("FOUNDRY_MODEL", "gpt-4.1-mini")
    writer = os.environ.get("FOUNDRY_WRITER_MODEL", fast)
    return {"Analyst": fast, "Tactician": fast, "Narrator": writer, "Translator": writer, "Editor": writer}


@app.get("/api/ask")
async def ask(sid: str = Query(alias="session"), q: str = Query(min_length=3, max_length=200), lang: Lang = "en"):
    """A viewer's question, answered by the agents from the match up to now, with what to show on the pitch:
    a clip (the moment the answer is about, or the last seconds), players to ring and a channel to light."""
    if not TEAM:
        raise HTTPException(404, "agents are off")
    s, at = store(), float(int(now(sid)))  # whole minutes: the same question within a minute is answered once
    key = (at, " ".join(q.lower().split()), lang)
    if key not in ASK_CACHE:
        if key not in ASK_INFLIGHT:
            if not TEAM.take_run():
                raise HTTPException(503, "agent budget for this hour is used up")
            ASK_INFLIGHT[key] = asyncio.create_task(_answer(key, q, at, lang))
        res = await asyncio.shield(ASK_INFLIGHT[key])
        if res is None:
            raise HTTPException(503, "the agents could not answer just now")
        return res
    ASK_CACHE.move_to_end(key)
    return ASK_CACHE[key]


async def _answer(key, q, at, lang):
    try:
        res = await asyncio.wait_for(TEAM.ask(q, at, lang), timeout=90)
    except Exception:  # noqa: BLE001
        res = None
    finally:
        ASK_INFLIGHT.pop(key, None)
    if res is None:
        return None
    s, md = store(), store().md
    show = res.get("show") or {}
    f_now = s.frame(at)
    replay = show.get("minute") is not None
    if replay:  # the moment the answer is about, a little before and after
        f_mid = s.frame(min(show["minute"], at))
        f_from, f_to = max(0, f_mid - 5 * md.hz), min(f_now, f_mid + 3 * md.hz)
    else:  # about the present: drawn over the live picture, nothing to replay
        f_mid = f_now
    rings = []
    for pid in show.get("players", []):
        slot = s.slot_at(pid, f_mid)
        if slot:
            rings.append({"k": slot[0], "s": slot[1], "name": player_name(md, pid)})
    zone = ({"team_index": md.shorts.index(show["team"]), "channel": show["channel"]}
            if show.get("team") and show.get("channel") else None)
    out = {"question": q, "answer": res.get("answer"), "source": res.get("source"),
           "speech": SPEECH.register(res["answer"], lang) if SPEECH and res.get("answer") else None,
           "steps": res.get("trace", []), "models": _models(),
           "show": {"rings": rings, "zone": zone, "replay": replay,
                    "clock": md.clock_label(f_mid) if res.get("answer") and replay else None},
           "clip": {"frames": frames_payload(md, f_from, max(f_from + 1, f_to)), "score": score_at(md, f_from)}
           if res.get("answer") and replay else None}
    ASK_CACHE[key] = out
    while len(ASK_CACHE) > CATCHUP_CACHE_MAX:
        ASK_CACHE.popitem(last=False)
    return out


@app.get("/api/catchup")
def catchup(sid: str = Query(alias="session"), away: float = Query(10, ge=1, le=120),
            follow: str | None = None, persona: Persona = "casual", lang: Lang = "en"):
    """'What did I miss?' for the last `away` minutes before now."""
    s, at = store(), now(sid)
    if follow and follow not in {p["id"] for p in s.md.meta["players"]}:
        raise HTTPException(404, "unknown player")
    facts = s.catch_up(max(0.0, at - away), at, at_minute=at, follow_player=follow)
    return {"facts": facts, "text": catch_up_text(facts, persona, lang)}


# ---------------------------------------------------------------- deeper numbers, recaps, replays


@app.get("/api/stats")
def stats(sid: str = Query(alias="session")):
    s, at = store(), now(sid)
    return {"teams": s.team_stats(at), "xg": s.xg_timeline(at)}


@app.get("/api/passnet")
def passnet(team: str, window: Literal["match", "half", "last15"] = "match", sid: str = Query(alias="session")):
    s, at = store(), now(sid)
    if team not in s.md.shorts:
        raise HTTPException(404, "unknown team")
    if window == "last15":
        start = max(0.0, at - 15)
    elif window == "half":
        f = s.frame(at)
        start = 0.0 if int(s.md.period[f]) == 1 else float(s.md.tmin[int((s.md.period == 2).argmax())])
    else:
        start = 0.0
    return s.pass_network(team, start, at)


@app.get("/api/profile")
def profile(id: str, sid: str = Query(alias="session")):
    s = store()
    if id not in {p["id"] for p in s.md.meta["players"]}:
        raise HTTPException(404, "unknown player")
    return s.player_profile(id, now(sid))


@app.get("/api/recap")
def recap(kind: Literal["ht", "ft"], sid: str = Query(alias="session"), lang: Lang = "en"):
    """Half-time / full-time recap, only once that moment has been reached."""
    r = store().recap(kind, now(sid))
    if r is None:
        raise HTTPException(409, "not reached yet")
    r["text"] = recap_text(r, lang)
    return r


@app.get("/api/replay")
def replay(kind: Literal["goal", "change"], minute: float, team: str | None = None, signal: str | None = None,
           sid: str = Query(alias="session")):
    """Frames around a past moment plus what to draw on top. Never beyond the live minute."""
    s, at = store(), now(sid)
    if minute > at:
        raise HTTPException(409, "that moment hasn't happened yet")
    if kind == "change" and team not in s.md.shorts:
        raise HTTPException(422, "team required for a change replay")
    return s.replay(kind, minute, team, at, signal)


# ---------------------------------------------------------------- live stream
@app.get("/api/stream")
async def stream(sid: str = Query(alias="session"), speed: float = Query(8.0, ge=1, le=64)):
    """Push the match from the session's minute at `speed`x real time; the session clock follows it."""
    sess = session(sid)
    sess["stream"] += 1
    token = sess["stream"]
    s = store()
    md = s.md
    hz = md.hz
    f = s.frame(sess["minute"])
    ev_i = next((i for i, e in enumerate(md.events) if e["frame"] >= f), len(md.events))
    batch = max(1, int(round(speed)))           # frames per message
    interval = batch / (hz * speed)             # seconds between messages

    async def gen():
        nonlocal f, ev_i
        last_minute = -1
        while f < md.F and sess["stream"] == token:
            frames = []
            for ff in range(f, min(f + batch, md.F)):
                frames.append({
                    "f": ff, "c": md.clock_label(ff), "p": int(md.poss[ff]), "t": round(float(md.clock[ff]), 1),
                    "per": int(md.period[ff]),
                    "m": round(float(md.tmin[ff]), 3),
                    "xy": [round(float(v), 1) for v in md.xy[ff].ravel()],
                })
            end = frames[-1]["f"]
            sess["minute"] = float(md.tmin[end])  # the server's live clock
            sess["seen"] = time.monotonic()
            yield f"event: frames\ndata: {json.dumps(frames)}\n\n"
            while ev_i < len(md.events) and md.events[ev_i]["frame"] <= end:
                e = md.events[ev_i]
                if e["type"] not in ("pressure",):
                    yield f"event: match_event\ndata: {json.dumps(s._event_view(e) | {'frame': e['frame']})}\n\n"
                ev_i += 1
            minute = int(md.tmin[end])
            if minute != last_minute:
                last_minute = minute
                yield f"event: changes\ndata: {json.dumps(s.tactical_changes(md.tmin[end]))}\n\n"
            f = end + 1
            await asyncio.sleep(interval)
        if f >= md.F:
            yield "event: end\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if os.path.isdir(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    def _versioned_index():
        # the page names its script and stylesheet by content hash, so a new deploy never runs with a
        # browser's cached copy of the old ones
        html = open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8").read()
        for name in ("app.js", "style.css"):
            digest = hashlib.sha1(open(os.path.join(WEB_DIR, name), "rb").read()).hexdigest()[:10]
            html = html.replace(f"/static/{name}", f"/static/{name}?v={digest}")
        return html

    INDEX_HTML = _versioned_index()

    @app.get("/")
    def index():
        return HTMLResponse(INDEX_HTML, headers={"Cache-Control": "no-cache"})
