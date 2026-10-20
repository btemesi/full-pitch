"""MCP server exposing the match to the agents.

Every tool needs `at_minute`, the live point of the broadcast. The server never
returns anything after it, so an agent cannot leak the future into live commentary.

Run:  python -m fullpitch.mcp_server              (stdio, for agent frameworks)
      python -m fullpitch.mcp_server --http 8811  (streamable HTTP)
"""

import os
import sys

from mcp.server import MCPServer

from .store import load

MATCH_DIR = os.environ.get("FULLPITCH_MATCH", os.path.join(os.path.dirname(__file__), "..", "data", "FP-0001"))

mcp = MCPServer(
    name="full-pitch",
    instructions=(
        "Live football match data (synthetic). Minutes are continuous match minutes from kick-off "
        "(no half-time gap); responses carry broadcast clock labels like 47' or 45+2'. Always pass "
        "the current live minute as at_minute. Every number you report must come from these tools."
    ),
)


def store():
    return load(MATCH_DIR)


@mcp.tool()
def match_info() -> dict:
    """Teams, squads and starting formations."""
    return store().match_info()


@mcp.tool()
def match_state(at_minute: float) -> dict:
    """Score, clock, recent possession, each team's current shape and the latest notable events."""
    return store().match_state(at_minute)


@mcp.tool()
def tactical_changes(at_minute: float) -> list:
    """Sustained changes in a team's shape detected from movement so far (line height, back three/four,
    pressing, attacking side), with when they started and before/after values."""
    return store().tactical_changes(at_minute)


@mcp.tool()
def shape_timeline(team: str, from_minute: float, to_minute: float, at_minute: float) -> list:
    """Minute-by-minute shape of one team (short code, e.g. RIV) with and without the ball."""
    return store().shape_timeline(team, from_minute, to_minute, at_minute)


@mcp.tool()
def player_report(player_id: str, from_minute: float, to_minute: float, at_minute: float) -> dict:
    """Running (distance, sprints, top speed) and on-ball actions for one player in a window."""
    return store().player_report(player_id, from_minute, to_minute, at_minute)


@mcp.tool()
def catch_up(from_minute: float, to_minute: float, at_minute: float, follow_player: str | None = None) -> dict:
    """Everything that happened in a window: goals, big chances, cards, subs, possession, shots, xG,
    tactical changes, and the followed player's numbers. Use for 'what did I miss?'."""
    return store().catch_up(from_minute, to_minute, at_minute, follow_player)


@mcp.tool()
def key_moments(at_minute: float) -> list:
    """Goals, big chances (xG >= 0.2) and cards so far."""
    return store().key_moments(at_minute)


if __name__ == "__main__":
    if "--http" in sys.argv:
        port = int(sys.argv[sys.argv.index("--http") + 1])
        mcp.run(transport="streamable-http", port=port)
    else:
        mcp.run()
