"""Audit the cards the agents already wrote: apply the current house style and run the current Editor
over every cached card. Cards the Editor rejects are removed, so the next pregenerate rewrites them.

Usage: python scripts/reverify.py            (then: python scripts/pregenerate.py)
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fullpitch.server import MATCH_DIR, _load_env  # noqa: E402

_load_env(str(ROOT / ".env"))
from fullpitch.agents import AgentTeam, expand_codes, polish  # noqa: E402
from fullpitch.store import load  # noqa: E402


async def main():
    s = load(MATCH_DIR)
    team = AgentTeam(s, cache_path=os.path.join(MATCH_DIR, "agent_cache.json"))
    teams = {t["short"]: t["name"] for t in s.md.meta["teams"]}
    keys = [k for k, v in team.cache.items() if not k.endswith("|explain") and v.get("source") == "agent"]
    for k in keys:
        v, lang = team.cache[k], k.split("|")[2]
        v["title"] = polish(expand_codes(v["title"], teams), lang, team.names, title=True)
        v["body"] = polish(expand_codes(v["body"], teams), lang, team.names)
    gate = asyncio.Semaphore(3)

    async def one(k):
        async with gate:
            return k, await team.reverify(k)

    rejected = 0
    for k, problems in await asyncio.gather(*(one(k) for k in keys)):
        if problems:
            rejected += 1
            print(f"REJECT {k}: {team.cache[k]['title']}\n    " + "\n    ".join(problems), flush=True)
            del team.cache[k]
            # a Hungarian card is a translation of its English card: it goes with it
            team.cache.pop(k.replace("|en|", "|hu|"), None) if "|en|" in k else None
    team._save()
    print(f"{rejected}/{len(keys)} cards rejected by the Editor")


if __name__ == "__main__":
    asyncio.run(main())
