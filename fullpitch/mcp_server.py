"""MCP server exposing the match to the agents.

The agent never chooses the time. The live minute comes from the replay
server's session clock (FULLPITCH_CLOCK_URL), or a fixed minute for offline
use (FULLPITCH_LIVE_MINUTE). Every tool answers as of that minute, so an agent
cannot leak the future into live commentary.

Run:  FULLPITCH_CLOCK_URL=http://localhost:8010/api/clock?session=<id> python -m fullpitch.mcp_server
      FULLPITCH_LIVE_MINUTE=60 python -m fullpitch.mcp_server            (offline, frozen at 60')
      add --http 8811 for streamable HTTP instead of stdio
"""

import json
import os
import sys
import urllib.request

try:  # MCP Python SDK 2.x
    from mcp.server import MCPServer
except ImportError:  # 1.x (what Microsoft Agent Framework currently pins)
    from mcp.server.fastmcp import FastMCP as MCPServer

from .store import load

MATCH_DIR = os.environ.get("FULLPITCH_MATCH", os.path.join(os.path.dirname(__file__), "..", "data", "FP-0001"))

mcp = MCPServer(
    name="full-pitch",
    instructions=(
        "Live football match data (synthetic, fictional teams). Everything is reported as of the live "
        "minute of the broadcast; you cannot see later. Minutes in arguments are continuous match minutes "
        "from kick-off (no half-time gap); responses carry broadcast clock labels like 47' or 45+2'. "
        "Every number you say must come from these tools."
    ),
)


def store():
    return load(MATCH_DIR)


def live_minute() -> float:
    path = os.environ.get("FULLPITCH_CLOCK_FILE")
    if path:  # the orchestrator writes the minute an agent is allowed to see
        with open(path, encoding="utf-8") as f:
            return float(f.read().strip())
    url = os.environ.get("FULLPITCH_CLOCK_URL")
    if url:
        with urllib.request.urlopen(url, timeout=3) as r:
            return float(json.loads(r.read())["minute"])
    fixed = os.environ.get("FULLPITCH_LIVE_MINUTE")
    if fixed is not None:
        return float(fixed)
    raise RuntimeError("No live clock: set FULLPITCH_CLOCK_FILE, FULLPITCH_CLOCK_URL or FULLPITCH_LIVE_MINUTE")


def _with_clock(payload):
    m = live_minute()
    return {"live_minute": round(m, 2), "live_clock": store().clock(m), "data": payload(m)}


@mcp.tool()
def match_info() -> dict:
    """Teams, squads and starting formations."""
    return store().match_info()


@mcp.tool()
def match_state() -> dict:
    """Score, clock, recent possession, each team's current shape and the latest notable events."""
    return _with_clock(lambda m: store().match_state(m))


@mcp.tool()
def tactical_changes() -> dict:
    """Confirmed changes in a team's shape so far (formation, line height, pressing, attacking side),
    with when they started and before/after values."""
    return _with_clock(lambda m: store().tactical_changes(m))


@mcp.tool()
def shape_timeline(team: str, from_minute: float, to_minute: float) -> dict:
    """Minute-by-minute shape of one team (short code, e.g. RIV) with and without the ball."""
    return _with_clock(lambda m: store().shape_timeline(team, from_minute, to_minute, m))


@mcp.tool()
def player_report(player_id: str, from_minute: float = 0.0) -> dict:
    """Running (distance, sprints, top speed) and on-ball actions for one player, from a minute until now."""
    return _with_clock(lambda m: store().player_report(player_id, from_minute, m, m))


@mcp.tool()
def catch_up(minutes_away: float = 10.0, follow_player: str | None = None) -> dict:
    """Everything that happened in the last `minutes_away` minutes: goals, big chances, cards, subs,
    possession, shots, xG, tactical changes, and the followed player's numbers."""
    return _with_clock(lambda m: store().catch_up(max(0.0, m - minutes_away), m, m, follow_player))


@mcp.tool()
def key_moments() -> dict:
    """Goals, big chances (xG >= 0.2), penalties and cards so far."""
    return _with_clock(lambda m: store().key_moments(m))


if __name__ == "__main__":
    if "--http" in sys.argv:
        port = int(sys.argv[sys.argv.index("--http") + 1])
        if hasattr(mcp, "settings"):  # SDK 1.x: host/port live in settings
            mcp.settings.port = port
            mcp.run(transport="streamable-http")
        else:  # SDK 2.x
            mcp.run(transport="streamable-http", port=port)
    else:
        mcp.run()
