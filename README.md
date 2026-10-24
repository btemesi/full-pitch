# Full Pitch

**On TV, you see six players. The match has twenty-two.**

Full Pitch is a live second screen for football fans. Next to the broadcast it shows the whole pitch: every player, the shape of both teams, the runs nobody films. It tells you when a team changes shape, a back three, a deeper block, an all-out press, and if you stepped away for ten minutes, one tap tells you what you missed.

**Live demo:** https://fullpitch.purplehill-778aa3eb.swedencentral.azurecontainerapps.io/?t=50 (the match starts at minute 50; drop `?t=50` to watch from kick-off)

> Built for the Microsoft + Premier League *Inside the Game* hackathon. All data is synthetic and all teams and players are fictional.

## Status

- [x] Synthetic match engine (tracking + events)
- [x] Shape & stats layer, tactical change detection (measured, see below)
- [x] Server-held live clock, MCP server for agents, live replay API
- [x] Web app (desktop + phone), EN / HU: replays with drawn explanations, team and player stats, recaps
- [x] Tests (`pytest`)
- [x] Agents on Microsoft Foundry (Microsoft Agent Framework), with a visible trace for every card
- [x] Azure Container Apps deployment (managed identity to Foundry and Speech, no keys)
- [x] Ask the pitch: questions answered by the agents and drawn on the pitch
- [x] Spoken commentary with Azure AI Speech

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
| Goals per match | 2.5 | 2.93 in 2024-25 ([11v11](https://www.11v11.com/competitions/premier-league/2025/)): ours is a little lower |
| Distance per outfield player | median 10.1 km, max 12.5 km | season leaders around 12 km ([Opta via 90min](https://www.90min.com/posts/5019706-player-who-has-covered-the-most-ground-on-average-per-premier-league-match-this-season-is-revealed)) |
| Top speed | max 34 km/h | record 37.38 km/h ([Premier League](https://www.premierleague.com/news/4138354)) |
| Per team: shots, passes, accuracy | 10, ~510, 82% | not sourced |
| Per team: corners, offsides, subs | 4.5, 1.5, 3.6 | not sourced |

Off the ball, players jog or run hard into position and only sprint for urgent jobs (pressing, marking, chasing the ball, runs in behind); a team opens up over a few seconds after winning the ball rather than instantly.

## 2. Shape & stats layer

`fullpitch/analysis.py`, per minute and per phase (with / without the ball), in each team's own direction of attack:

- defensive line height, and the same height with the ball's position factored out (the coach's instruction)
- players in the back line (back three vs back four), team length and width, players pressing near the ball
- final-third entries by channel (left / centre / right)
- per player: distance, top speed, sprints (over 25.2 km/h for at least 1 s), high-intensity running

**Tactical change detection** compares the 12 minutes before and after each minute (weighted by sample count; a two-proportion test for the attacking side) and keeps a change only if one signal is strong or two agree. Measured against the scenario truth with `scripts/eval_detection.py`, tolerance ±4 minutes. Seeds 1-24 were used while tuning; **seeds 25-60 were never looked at during tuning**:

| | Unseen matches (25-60) | All 60 |
|---|---|---|
| coach decisions found | **69%** (75/108) | 69% (125/180) |
| the all-out press (69') | 35/36 | 58/60 |
| the low block (31') | 19/36 | 31/60 |
| the back three (54') | 23/36 | 38/60 |
| false alarms per match | 0.36 | 0.33 |

Shape "with the ball" is read only from settled possession (5 seconds or more), the way an analyst would.

Live, a change is confirmed a median 7.5 minutes after it starts (middle half: 7 to 10 minutes; a weak signal can take much longer, `scripts/confirm_delay.py`), then dated back ("since 31'"). Once shown, a confirmed change stays on screen.

**Limits, stated plainly:** the detector and the scenario were built by the same team on the same simulator, so these numbers describe this synthetic world, not real matches. The scenario's changes are clear by design.

## 3. MCP server and live replay

- `fullpitch/mcp_server.py`: `match_info`, `match_state`, `tactical_changes`, `shape_timeline`, `player_report`, `catch_up`, `key_moments`. The live minute comes from the replay server's clock (`FULLPITCH_CLOCK_FILE`, written by the agent team for the moment being explained, or `FULLPITCH_CLOCK_URL`), never from the agent.
- `fullpitch/server.py`: FastAPI. `POST /api/session`, `GET /api/stream` (Server-Sent Events: positions, events, detected changes), `/api/cards`, `/api/catchup` ("What did I miss?"), `/api/player`, `/api/lineup`, `/api/frame`, `/api/clock`. Inputs are validated.

## 4. Web app

`web/`, desktop and phone (the pitch stands upright on a phone):

- **Full pitch or broadcast view.** The camera zooms onto the ball like TV ("On TV, you see N players"), then pulls back to all twenty-two. Team shape lines (drawn from positions: players sorted by depth and cut at the largest gaps; the page never receives the coach's formation changes), direction of travel on every player, the ball's path and shadow.
- **Follow a player** by tapping or from the list: name on the pitch, live speed, distance, sprints. You are told when your player is substituted.
- **Insights**, for a fan, an analyst or your player, in English or Hungarian: shape changes, goals, red cards, half-time and full-time recaps (with a transparent player-of-the-match rating). New moments slide in as a broadcast-style lower third.
- **Replays that show the explanation.** Tap a goal or a shape change: the pitch replays that moment and draws what the card claims. For a deeper or higher line, a dashed ghost line shows where the back line would stand on the old instruction and a yellow line where it stands now; for a back three, the back-line players are ringed; for pressing, the players hunting the ball.
- **Stats**: possession, shots, xG, passes and accuracy, possession in the final third, pressing intensity (PPDA), corners, fouls, offsides, cards; a cumulative xG timeline with goals and shape changes marked; a pass network (average positions and passing links) for the whole match, the current half or the last 15 minutes.
- **Player**: goals, assists, xG, pass accuracy, runs in behind the last line (detected from tracking, the movement TV rarely shows), running per 15 minutes with a fatigue read-out, heatmap, pass and shot map.
- **Ask the pitch.** Type a question, or tap one of the suggestions: "Where was everyone at the last goal?", "Where is Northgate attacking from?", "Who has run the most?". The agents look it up in the match data as of now, answer in one to three sentences, and the pitch shows the answer: it replays the moment, rings and names the players and lights up the channel. A question the data cannot answer ("Who is going to win?") gets an honest no, and every answer has the same "How?" trace as the cards.
- **Commentary.** Switch it on and a voice reads out each new moment and every answer, in English or Hungarian (Azure AI Speech neural voices, keyless through Microsoft Entra ID; each line is synthesised once and cached).
- **What did I miss?**: the last 5 to 30 minutes in a few lines.
- **Presentation mode** (`P` or `?present=1`): just the pitch and the scoreboard, for recording.

## 5. Agents on Microsoft Foundry

`fullpitch/agents.py`, built with the **Microsoft Agent Framework** (`agent_framework`), models deployed on **Microsoft Foundry**, match data over **MCP**. Six roles work on every moment (a goal, a shape change, a recap):

| Role | Model | Job |
| --- | --- | --- |
| Analyst | gpt-4.1-mini | Investigates the moment through the MCP tools (`match_state`, `tactical_changes`, `key_moments`, ...). The MCP server reads the clock of the moment being explained, so the Analyst cannot look past it. |
| Tactician | gpt-4.1-mini | Turns the measured facts and the Analyst's notes into cause and effect, without claiming that a change caused what followed. |
| Narrator | gpt-4o | Writes the on-screen card in English for one viewer type: a fan, an analyst, or the fan following one player. |
| Translator | gpt-4o | Turns the checked English card into Hungarian, adding nothing. Both languages carry the same facts. |
| Fact-checker | plain Python | Runs after every agent, the Analyst and the Tactician included. Every number must exist in the measured facts or the Analyst's tool results, every minute must be a clock of this moment, every capitalised name must belong to this match (it caught invented clubs such as "Riverstone", "Riverford" and a real club name), and the language must be the one asked for. |
| Editor | gpt-4o | Checks the card claim by claim against the facts: wrong minute, scorer or score, actions or tactics the data does not show, a headline about the wrong thing, claims that need the future ("the winner" in the 88th minute), Hungarian grammar. |

A card that fails a check goes back with the reasons, up to three drafts. If no draft passes, the template text built straight from the measurements stays on screen, so a viewer never sees an unchecked sentence. The template shows at once in any case; the agents' version replaces it when it is ready (`pending` in `/api/cards`).

**Every card has a "How?" link** (`/api/trace`): each agent's step, the model, the MCP tools it called, seconds, tokens, and what the checks found, including drafts that were sent back. A trace is only served for cards the viewer's session can already see.

For the demo match the cards are written ahead (`python scripts/pregenerate.py`, cached in `data/FP-0001/agent_cache.json`) so the video and the public demo do not depend on rate limits; anything not cached (for example the cards for a player you follow) is written live, capped at 60 agent jobs an hour (`FULLPITCH_AGENT_RUNS_PER_HOUR`) so a public demo cannot drain the model budget. `python scripts/reverify.py` runs the current Editor over every cached card again and drops the ones it rejects; the cached cards were also read by hand. In the run that produced the cache, all 60 cards (10 moments × 3 viewer types × 2 languages) passed: 14 needed more than one draft, the Editor sent back 7 drafts and the Fact-checker 1, and before that the Fact-checker sent back the Tactician twice for invented club names.

**Ask the pitch** (`/api/ask`) runs the same team on a viewer's question: the Analyst investigates with the MCP tools as of the live minute (three of them exist for questions: `positions_at_moment` for where everyone stood at a goal or chance, `attack_channels`, `leaders`), the Narrator answers and says what the pitch should show (player ids, a channel, the moment to replay), and the Fact-checker also rejects player ids that are not in the match, moments after now, and continuous minutes in the text. If no draft passes, the viewer is told the agents could not give a checked answer.

"What did I miss?" is written the same way from the measured facts of the window (`/api/catchup/agent`): Narrator, Fact-checker (it must also mention every goal and shape change in the window), Editor, and for Hungarian the Translator.

## Run it

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt
python scripts/generate.py --seed 3                  # the demo match: RIV 3-2 NOR
uvicorn fullpitch.server:app --port 8010             # open http://localhost:8010/?t=50
python -m pytest                                     # tests
python scripts/pregenerate.py                        # agents write every card of the demo match (needs .env)
python scripts/analyze.py                            # detected changes vs truth for the current match
python scripts/realism_report.py 101 116             # how football-like the engine is
python scripts/eval_detection.py 1 60                # detection accuracy
python scripts/fuzz_api.py 201 206                   # bug hunt: every endpoint, every minute, every player
```

Agents need a Foundry project and `az login` (no keys are stored): put `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_MODEL` (gpt-4.1-mini) and `FOUNDRY_WRITER_MODEL` (gpt-4o) in `.env`. Without them the app runs on the template texts.

Pitch: 105 × 68 m. Home attacks +x in the first half and -x in the second.

## Deploy (Azure Container Apps)

`scripts/deploy.sh v7` builds, pushes and rolls out a new image, and stops at the first failure so the app is never pointed at an image that was not pushed. The first setup:

```bash
docker build -t <registry>.azurecr.io/fullpitch:v1 . && docker push <registry>.azurecr.io/fullpitch:v1
az containerapp create -n fullpitch -g <rg> --environment <env> --image <registry>.azurecr.io/fullpitch:v1   --registry-server <registry>.azurecr.io --registry-identity system --system-assigned   --ingress external --target-port 8000 --min-replicas 1 --max-replicas 1   --env-vars FOUNDRY_PROJECT_ENDPOINT=... FOUNDRY_MODEL=gpt-4.1-mini FOUNDRY_WRITER_MODEL=gpt-4o
```

Then give the app's managed identity the *Foundry User*, *Cognitive Services OpenAI User* and *Cognitive Services Speech User* roles on the Foundry resource. One replica on purpose: viewer sessions and the live clock live in memory.

## License

MIT
