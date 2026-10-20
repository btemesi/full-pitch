"""Live replay server: plays a synthetic match as if it were a live feed.

  GET /api/match                       teams, squads, formations
  GET /api/stream?from=0&speed=8       Server-Sent Events: frames (5 Hz), events, detected changes
  GET /api/catchup?from=..&to=..       "What did I miss?" facts (narration is added by the agents)
  GET /api/state?at=..                 current match state

Run: uvicorn fullpitch.server:app --port 8000
"""

import asyncio
import json
import os

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .analysis import slot_occupancy
from .store import load
from .templates import catch_up_text, change_card, goal_card

MATCH_DIR = os.environ.get("FULLPITCH_MATCH", os.path.join(os.path.dirname(__file__), "..", "data", "FP-0001"))
WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")

app = FastAPI(title="Full Pitch")


def store():
    return load(MATCH_DIR)


@app.get("/api/match")
def match():
    s = store()
    info = s.match_info()
    info["formations"] = s.md.meta["formations"]
    info["formation_log"] = s.md.meta["formation_log"]
    info["team_colors"] = {t["short"]: t["color"] for t in s.md.meta["teams"]}
    info["hz"] = s.md.hz
    return info


@app.get("/api/state")
def state(at: float):
    return store().match_state(at)


@app.get("/api/catchup")
def catchup(start: float = Query(alias="from"), to: float = Query(...), follow: str | None = None,
            persona: str = "casual", lang: str = "en"):
    facts = store().catch_up(start, to, at_minute=to, follow_player=follow)
    return {"facts": facts, "text": catch_up_text(facts, persona, lang)}


@app.get("/api/changes")
def changes(at: float):
    return store().tactical_changes(at)


@app.get("/api/cards")
def cards(at: float, persona: str = "casual", lang: str = "en"):
    """On-screen cards up to the live minute: detected tactical changes and goals, newest first."""
    s = store()
    names = {t["short"]: t["name"] for t in s.md.meta["teams"]}
    out = []
    for c in s.tactical_changes(at):
        card = change_card(c, names, persona, lang)
        card["minute"] = c["since_minute"]
        card["revealed_at"] = c["since_minute"] + 6  # confirmed once enough minutes are seen
        out.append(card)
    for e in s.key_moments(at):
        if e["type"] == "goal":
            card = goal_card(e, lang)
            card["minute"] = e["minute"]
            out.append(card)
    return out


@app.get("/api/player")
def player(id: str, at: float):
    """A player's numbers from kick-off to the live minute."""
    return store().player_report(id, 0, at, at)


@app.get("/api/lineup")
def lineup(at: float):
    """Who is on the pitch in each tracking slot at the live minute."""
    s = store()
    f = s.frame(at)
    occ = slot_occupancy(s.md)
    players = {p["id"]: p for p in s.md.meta["players"]}
    out = {}
    for k, short in enumerate(s.md.shorts):
        team = []
        for spans in occ[k]:
            pid = next(p for p, a, b in spans if a <= f < b or (b == s.md.F and f >= a))
            p = players[pid]
            team.append({"id": pid, "name": p["name"], "number": p["number"]})
        out[short] = team
    return out


@app.get("/api/stream")
async def stream(start: float = Query(0.0, alias="from"), speed: float = 8.0):
    """Push the match at `speed`x real time. Frames are sent in small batches to keep the stream light."""
    s = store()
    md = s.md
    hz = md.hz
    f = s.frame(start)
    ev_i = next((i for i, e in enumerate(md.events) if e["frame"] >= f), len(md.events))
    batch = max(1, int(round(speed)))           # frames per message
    interval = batch / (hz * speed)             # seconds between messages
    last_change_check = -1

    async def gen():
        nonlocal f, ev_i, last_change_check
        while f < md.F:
            frames = []
            for ff in range(f, min(f + batch, md.F)):
                frames.append({
                    "f": ff, "c": md.clock_label(ff), "p": int(md.poss[ff]), "t": round(float(md.clock[ff]), 1),
                    "m": round(float(md.tmin[ff]), 3),
                    "xy": [round(float(v), 1) for v in md.xy[ff].ravel()],
                })
            yield f"event: frames\ndata: {json.dumps(frames)}\n\n"
            end = frames[-1]["f"]
            while ev_i < len(md.events) and md.events[ev_i]["frame"] <= end:
                e = md.events[ev_i]
                if e["type"] not in ("pressure",):
                    yield f"event: match_event\ndata: {json.dumps(s._event_view(e) | {'frame': e['frame']})}\n\n"
                ev_i += 1
            minute = int(md.tmin[end])
            if minute != last_change_check:
                last_change_check = minute
                ch = s.tactical_changes(md.tmin[end])
                yield f"event: changes\ndata: {json.dumps(ch)}\n\n"
            f = end + 1
            await asyncio.sleep(interval)
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if os.path.isdir(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(WEB_DIR, "index.html"))
