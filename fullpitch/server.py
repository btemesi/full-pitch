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
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .analysis import slot_occupancy
from .store import load
from .templates import catch_up_text, change_card, goal_card, recap_text

MATCH_DIR = os.environ.get("FULLPITCH_MATCH", os.path.join(os.path.dirname(__file__), "..", "data", "FP-0001"))
WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")
SESSION_TTL_S = 3 * 3600
MAX_SESSIONS = 2000

Persona = Literal["casual", "analyst", "player"]
Lang = Literal["en", "hu"]


@asynccontextmanager
async def lifespan(_app):
    load(MATCH_DIR)  # warm the match and its analysis before the first viewer arrives
    yield


app = FastAPI(title="Full Pitch", lifespan=lifespan)
SESSIONS: dict[str, dict] = {}


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
    info["formations"] = s.md.meta["formations"]
    info["formation_log"] = s.md.meta["formation_log"]
    info["team_colors"] = {t["short"]: t["color"] for t in s.md.meta["teams"]}
    info["hz"] = s.md.hz
    return info


@app.get("/api/frame")
def frame(sid: str = Query(alias="session")):
    """The single frame at the session's minute (for a paused view)."""
    s, m = store(), now(sid)
    f = s.frame(m)
    md = s.md
    return {"f": f, "c": md.clock_label(f), "p": int(md.poss[f]), "t": round(float(md.clock[f]), 1),
            "m": round(float(md.tmin[f]), 3), "xy": [round(float(v), 1) for v in md.xy[f].ravel()]}


@app.get("/api/state")
def state(sid: str = Query(alias="session")):
    return store().match_state(now(sid))


@app.get("/api/changes")
def changes(sid: str = Query(alias="session")):
    return store().tactical_changes(now(sid))


@app.get("/api/cards")
def cards(sid: str = Query(alias="session"), persona: Persona = "casual", lang: Lang = "en"):
    """On-screen cards up to the live minute: confirmed tactical changes and goals."""
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
            out.append({"kind": "recap", "recap": kind, "team": None, "minute": 45.0 if kind == "ht" else s.length_min,
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

    @app.get("/")
    def index():
        return FileResponse(os.path.join(WEB_DIR, "index.html"))
