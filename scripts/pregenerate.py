"""Have the agent team write every card of the current match, for every viewer and language.

The results go to data/<match>/agent_cache.json, which the server serves at once (and keeps
filling live for anything new). Needs FOUNDRY_PROJECT_ENDPOINT / FOUNDRY_MODEL and an Azure login.

Usage: python scripts/pregenerate.py
"""
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fullpitch.server import MATCH_DIR, _load_env  # noqa: E402

_load_env(str(ROOT / ".env"))
from fullpitch.agents import LANGS, PERSONAS, AgentTeam  # noqa: E402
from fullpitch.store import load  # noqa: E402


def all_cards(s):
    end = s.length_min
    cards = [{"kind": "change", "team": c["team"], "confirmed_minute": c["confirmed_minute"], "minute": c["since_minute"]}
             for c in s.tactical_changes(end)]
    cards += [{"kind": "goal", "minute": e["minute"]} for e in s.key_moments(end) if e["type"] == "goal"]
    cards += [{"kind": "recap", "recap": k} for k in ("ht", "ft")]
    return cards


async def main():
    s = load(MATCH_DIR)
    team = AgentTeam(s, cache_path=os.path.join(MATCH_DIR, "agent_cache.json"))
    await team.start()
    t = time.time()
    try:
        cards = all_cards(s)
        for card in cards:  # evidence + tactics once per event (the Analyst works one moment at a time)
            await team.explain(card)
            print(f"explained {team.event_key(card)}  ({time.time() - t:.0f}s)", flush=True)
        sem = asyncio.Semaphore(2)

        async def one(card, persona, lang):
            async with sem:
                r = await team.narrate(card, persona, lang)
                ok = "agent" if r.get("source") == "agent" else "TEMPLATE"
                print(f"  {ok:8s} {team.event_key(card)} {persona}/{lang}: {r.get('title', '')}", flush=True)
                return r.get("source") == "agent"

        results = await asyncio.gather(*(one(c, p, l) for c in cards for p in PERSONAS for l in LANGS))
        print(f"{sum(results)}/{len(results)} cards written by agents in {time.time() - t:.0f}s")
    finally:
        await team.stop()


if __name__ == "__main__":
    asyncio.run(main())
