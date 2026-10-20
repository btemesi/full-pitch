# Full Pitch

**On TV, you see six players. The match has twenty-two.**

Full Pitch is a live second screen for football fans. Next to the broadcast it shows the whole pitch: every player, the shape of both teams, the runs nobody films. It tells you when a team changes shape, a back three, a deeper block, an all-out press, and if you stepped away for ten minutes, one tap tells you what you missed.

> Built for the Microsoft + Premier League *Inside the Game* hackathon. All data is synthetic and all teams and players are fictional.

## Status

- [x] Synthetic match engine (tracking + events)
- [x] Shape & stats layer, tactical change detection (measured, see below)
- [x] Server-held live clock, MCP server for agents, live replay API
- [x] Web app (desktop + phone), EN / HU: replays with drawn explanations, team and player stats, recaps
- [x] Tests (`pytest`)
- [ ] Agents on Microsoft Foundry (Microsoft Agent Framework)
- [ ] Azure Container Apps deployment

## How it works

```
synthetic match engine ──> live replay (SSE, server-held clock) ──> web app
        │                                                              ▲
        ▼                                                              │
shape & stats layer (plain Python, no LLM) ──> MCP server ──> agents on Foundry
```

- **Numbers never come from a language model.** Python measures; agents read the measurements through MCP and explain them.
- **Nobody sees the future.** Each viewer has a session on the server, and the session's minute only moves as the stream plays (or when the viewer jumps). Every endpoint and every MCP tool answers "as of now"; neither the page nor an agent can choose a later time. This is covered by tests.

## 1. Synthetic match engine

`fullpitch/engine.py` simulates a full match: 22 players and the ball at 5 Hz, plus an event stream: passes (distance, difficulty, pressure), shots (xG, speed, penalties), tackles, interceptions, offsides, fouls, yellow and red cards, substitutions, goals, restarts, treatment stoppages.

Every player has a spot in the formation, and the whole block slides with the ball: long and wide in possession, compact without it, with the line height set by the coach. The nearest defenders press, defenders mark goal-side in the box, attackers run onto the offside line, players jog into position and only sprint when the job is urgent. The ball carrier picks passes by weighing progress, space, lane risk and offside. Fouls in the box give penalties, a second yellow sends the player off (the team plays on with ten), substitutions are made at stoppages, and teams change ends at half-time.

The scenario (`fullpitch/scenario.py`) gives the coaches three decisions: Northgate drop into a low block (31'), switch to a back three and attack down their left (54'), Riverside push up and press all-out (69'). These are written to `truth.json` and **never streamed**: the analysis must find them from movement alone.

**How football-like is it?** `python scripts/realism_report.py 101 116` simulates 16 fresh matches and checks invariants (score = goals, players on the pitch, no ball faster than a shot, card and substitution rules). All invariants hold. Selected numbers, next to published Premier League figures where we have a source:

| | Full Pitch (16 matches) | Premier League reference |
|---|---|---|
| Ball in play | 58 min | about 57 min in 2024-25 ([The Analyst](https://theanalyst.com/articles/premier-league-ball-in-play-are-we-seeing-less-football-2025-26)) |
| Goals per match | 2.3 | 2.93 in 2024-25 ([11v11](https://www.11v11.com/competitions/premier-league/2025/)): ours is lower |
| Distance per outfield player | median 10.1 km, max 12.9 km | season leaders around 12 km ([Opta via 90min](https://www.90min.com/posts/5019706-player-who-has-covered-the-most-ground-on-average-per-premier-league-match-this-season-is-revealed)) |
| Top speed | max 33 km/h | record 37.38 km/h ([Premier League](https://www.premierleague.com/news/4138354)) |
| Per team: shots, passes, accuracy | 11, ~505, 81% | not sourced |
| Per team: corners, offsides, subs | 5.5, 1.7, 3.5 | not sourced |

## 2. Shape & stats layer

`fullpitch/analysis.py`, per minute and per phase (with / without the ball), in each team's own direction of attack:

- defensive line height, and the same height with the ball's position factored out (the coach's instruction)
- players in the back line (back three vs back four), team length and width, players pressing near the ball
- final-third entries by channel (left / centre / right)
- per player: distance, top speed, sprints (over 25.2 km/h for at least 1 s), high-intensity running

**Tactical change detection** compares the 12 minutes before and after each minute (weighted by sample count; a two-proportion test for the attacking side) and keeps a change only if one signal is strong or two agree. Measured against the scenario truth with `scripts/eval_detection.py`, tolerance ±4 minutes. Seeds 1-24 were used while tuning; **seeds 25-60 were never looked at during tuning**:

| | Unseen matches (25-60) | All 60 |
|---|---|---|
| coach decisions found | **66%** (71/108) | 70% (126/180) |
| the all-out press (69') | 35/36 | 59/60 |
| the low block (31') | 19/36 | 35/60 |
| the back three (54') | 17/36 | 32/60 |
| false alarms per match | 0.42 | 0.37 |

Live, a change is confirmed about 7 to 9 minutes after it starts, then dated back ("since 31'"). Once shown, a confirmed change stays on screen.

**Limits, stated plainly:** the detector and the scenario were built by the same team on the same simulator, so these numbers describe this synthetic world, not real matches. The scenario's changes are clear by design.

## 3. MCP server and live replay

- `fullpitch/mcp_server.py`: `match_info`, `match_state`, `tactical_changes`, `shape_timeline`, `player_report`, `catch_up`, `key_moments`. The live minute comes from the replay server's session clock (`FULLPITCH_CLOCK_URL`), never from the agent.
- `fullpitch/server.py`: FastAPI. `POST /api/session`, `GET /api/stream` (Server-Sent Events: positions, events, detected changes), `/api/cards`, `/api/catchup` ("What did I miss?"), `/api/player`, `/api/lineup`, `/api/frame`, `/api/clock`. Inputs are validated.

## 4. Web app

`web/`, desktop and phone (the pitch stands upright on a phone):

- **Full pitch or broadcast view.** The camera zooms onto the ball like TV ("On TV, you see N players"), then pulls back to all twenty-two. Team shape lines, direction of travel on every player, the ball's path and shadow.
- **Follow a player** by tapping or from the list: name on the pitch, live speed, distance, sprints. You are told when your player is substituted.
- **Insights**, for a fan, an analyst or your player, in English or Hungarian: shape changes, goals, red cards, half-time and full-time recaps (with a transparent player-of-the-match rating). New moments slide in as a broadcast-style lower third.
- **Replays that show the explanation.** Tap a goal or a shape change: the pitch replays that moment and draws what the card claims. For a deeper or higher line, a dashed ghost line shows where the back line would stand on the old instruction and a yellow line where it stands now; for a back three, the back-line players are ringed; for pressing, the players hunting the ball.
- **Stats**: possession, shots, xG, passes and accuracy, possession in the final third, pressing intensity (PPDA), corners, fouls, offsides, cards; a cumulative xG timeline with goals and shape changes marked; a pass network (average positions and passing links) for the whole match, the current half or the last 15 minutes.
- **Player**: goals, assists, xG, pass accuracy, runs in behind the last line (detected from tracking, the movement TV rarely shows), running per 15 minutes with a fatigue read-out, heatmap, pass and shot map.
- **What did I miss?**: the last 5 to 30 minutes in a few lines.
- **Presentation mode** (`P` or `?present=1`): just the pitch and the scoreboard, for recording.

## Run it

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt
python scripts/generate.py --seed 22                 # the demo match: RIV 3-3 NOR
uvicorn fullpitch.server:app --port 8010             # open http://localhost:8010/?t=50
python -m pytest                                     # tests
python scripts/analyze.py                            # detected changes vs truth for the current match
python scripts/realism_report.py 101 116             # how football-like the engine is
python scripts/eval_detection.py 1 60                # detection accuracy
```

Pitch: 105 × 68 m. Home attacks +x in the first half and -x in the second.

## License

MIT
