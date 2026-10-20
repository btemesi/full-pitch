# Full Pitch

**On TV, you see six players. The match has twenty-two.**

Full Pitch is a live second screen for football fans. Next to the broadcast it shows the whole pitch: every player, the shape of both teams, the runs nobody films. An AI explains what changed and why it matters, and if you stepped away for ten minutes, one tap tells you what you missed.

> Built for the Microsoft + Premier League *Inside the Game* hackathon. All data is synthetic and all teams and players are fictional.

## Status

Work in progress.

- [x] Synthetic match engine (tracking + events)
- [x] Shape & stats layer, tactical change detection
- [x] MCP server for agents, live replay server
- [ ] Agents on Microsoft Foundry (Agent Framework)
- [ ] Web app
- [ ] Azure Container Apps deployment

## How it works

```
synthetic match engine ──> live replay (SSE) ──> web app
        │                                          ▲
        ▼                                          │
shape & stats layer (plain Python, no LLM) ──> MCP server ──> agents on Foundry
```

**Numbers never come from the language model.** Python measures; the agents read the measurements through MCP and explain them. Every tool takes the live minute, so an agent cannot see the future.

## 1. Synthetic match engine

`fullpitch/engine.py` simulates a full match: 22 players and the ball at 5 Hz, plus an event stream (passes with distance and difficulty, shots with xG and speed, tackles, interceptions, pressure, fouls, cards, subs, goals, restarts).

Every player has a spot in the formation, and the whole block slides with the ball: long and wide in possession, compact without it, with the line height set by the coach. The nearest defenders press, defenders mark goal-side in the box, attackers run onto the offside line, players jog into position and only sprint when the job is urgent. The ball carrier picks passes by weighing progress, space and lane risk.

The scenario (`fullpitch/scenario.py`) gives the coaches three decisions: Northgate drop into a low block (31'), switch to a back three and attack down their left (54'), Riverside push up and press all-out (69'). These are written to `truth.json` and **never streamed**: the analysis has to find them from movement alone.

Calibration targets per team, checked over many seeds: 8 to 18 shots, median shot distance about 15 m, about 80% pass accuracy, 9 to 14 km run by outfield players, sprinting about 6% of the time.

## 2. Shape & stats layer

`fullpitch/analysis.py`, per minute and per phase (with / without the ball):

- defensive line height, and the same height with the ball's position factored out (the coach's instruction)
- players in the back line (back three vs back four), team length and width, gap between the lines
- players pressing near the ball, final-third entries by channel
- per player: distance, top speed, sprints, high-intensity running

**Tactical change detection** compares the 12 minutes before and after each minute, weighted by sample count, and keeps a change only if one signal is strong or two agree. Measured against the scenario truth (`scripts/eval_detection.py`):

| | |
|---|---|
| coach changes found within ±4 min | 114 of 180 (63%), 60 matches |
| the all-out press (biggest change) | 58 of 60 |
| false alarms per match | 0.5 (plus real reactions to the other team) |

Live, a change is confirmed about 6 minutes after it starts, then dated back ("since 31'"), roughly how long a pundit needs to notice it too.

## 3. MCP server and live replay

- `fullpitch/mcp_server.py`: `match_info`, `match_state`, `tactical_changes`, `shape_timeline`, `player_report`, `catch_up`, `key_moments`. All take `at_minute`.
- `fullpitch/server.py`: FastAPI. `/api/stream` plays the match as Server-Sent Events (positions, events, detected changes), `/api/catchup` returns the facts for "What did I miss?".

## Run it

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
python scripts/generate.py --seed 49 # the demo match
python scripts/analyze.py                     # shape metrics + detected changes vs truth
python scripts/preview.py                     # preview.html: quick look at the tracking
uvicorn fullpitch.server:app --port 8000      # live replay API
python -m fullpitch.mcp_server                # MCP server (stdio)
```

Pitch: 105 × 68 m, home team attacks +x, no change of ends at half-time.
