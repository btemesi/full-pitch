# Full Pitch

**On TV, you see six players. The match has twenty-two.**

Full Pitch gives football fans the view from the stands: the whole pitch, every player, the shape of both teams, and an AI that explains what changed and why it matters. Stepped away for ten minutes? One tap tells you what you missed.

> Built for the Microsoft + Premier League *Inside the Game* hackathon. All data is synthetic and all teams and players are fictional.

## Status

Work in progress. Day 1: synthetic match engine.

## Synthetic match engine

`fullpitch/engine.py` simulates a full match and writes:

| File | Contents |
|---|---|
| `tracking.npz` | 22 players + ball, x/y in metres at 5 Hz, plus match clock, period and possession per frame |
| `events.jsonl` | passes (distance, difficulty, pressure), shots (xG, shot speed), tackles, interceptions, pressure, fouls, cards, subs, goals, restarts |
| `meta.json` | teams, fictional squads, formations, formation changes |
| `truth.json` | the coaches' tactical changes (ground truth, never streamed) |

How it moves: every player has a spot in the formation, and the whole block slides with the ball: long and wide in possession, compact without it, with the back line height set by the coach. The nearest defenders break out to press, defenders mark goal-side in the box, attackers run onto the offside line. The ball carrier picks passes by weighing progress, space and lane risk. Mid-match tactical changes (a low block, a switch to a back three, an all-out press) change those parameters, and the analysis layer has to discover them from movement alone.

```bash
python scripts/generate.py --seed 10   # the demo match: RIV 2-3 NOR
python scripts/preview.py        # writes preview.html, open it in a browser
```

Pitch: 105 × 68 m, home team attacks +x.
