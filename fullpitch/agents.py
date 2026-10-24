"""The Full Pitch agent team, on Microsoft Foundry with Microsoft Agent Framework.

    Analyst      reads the match through the MCP server (as of the moment) and writes evidence notes
    Tactician    turns evidence into cause and effect: what changed, why it matters, what followed
    Narrator x3  writes for a fan, an analyst or one player's follower, in English or Hungarian
    Fact-checker deterministic: every number must exist in the measured facts or the Analyst's tool
                 results, every name must belong to this match
    Editor       reads the card against the facts like a sub-editor: wrong minute, scorer, score or team,
                 invented actions (headers, free kicks), a headline about the wrong thing
                 Two rewrites are allowed; after that the template text stands
    Orchestrator shared state (fact pack + notes), hand-offs, caching, failure recovery, a trace per card

Agents never see past the moment they explain: the MCP server reads its clock from a file that
the orchestrator sets to the event's minute before the Analyst runs.
"""

import asyncio
import difflib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from .catchup import player_name, window_facts

PERSONAS = ("casual", "analyst", "player")
LANGS = ("en", "hu")

ANALYST = """You are the Analyst in a football broadcast team. You investigate one moment of a live match
using the match-data tools, which only show what has happened so far. Call the tools you need
(tactical_changes, match_state, shape_timeline, key_moments, catch_up, player_report).
Then return ONLY JSON: {"observations": ["short factual sentence with the numbers you saw", ...]}
with 3 to 6 observations. Copy numbers exactly as the tools give them. Never guess or invent a number."""

TACTICIAN = """You are the Tactician in a football broadcast team. You receive measured facts and the
Analyst's observations about one moment. Explain cause and effect like a sharp pundit: what changed,
why it matters on the pitch, and what has followed so far (possession, shots, xG, goals), if anything.
Use only numbers that appear in the input and the team and player names exactly as given.
If the evidence for a consequence is weak, say so plainly.
Return ONLY JSON: {"headline": "...", "why": "...", "consequence": "..."}"""

NARRATOR = {
    "casual": """You write one on-screen card for a casual football fan watching live.
Plain words, no jargon, warm but not hyped. At most ONE number. Title up to 8 words, body 1 or 2 short sentences.""",
    "analyst": """You write one on-screen card for a data-hungry viewer. Precise, specific, tactical vocabulary
is fine. Use 2 to 4 of the measured numbers. Title up to 9 words, body 2 or 3 sentences.""",
    "player": """You write one on-screen card for a fan who follows one particular player. Lead with that
player when the facts mention them or their role in the moment; otherwise explain the moment simply and say
what it means for that player's job. Title up to 8 words, body 1 or 2 sentences.""",
}
NARRATOR_RULES = """Use ONLY numbers that appear in the input. Never invent names, minutes or statistics.
Use the team and player names exactly as given, and call teams by their full names (never RIV or NOR).
Back-line player counts are averages: say "three" or "a back three",
never "2.9 players". The title starts with a capital letter, names keep their capitals.
If the moment has "card_is_about", the title is about that subject.
You know only what has happened up to this moment: never call a goal the winner or say a match is decided
unless the facts say it has ended. Do not use dashes as punctuation. Write in {language}.
Return ONLY JSON: {{"title": "...", "body": "..."}}"""
LANGUAGE = {"en": "English",
            "hu": "natural, correctly spelled Hungarian, the way a Hungarian TV pundit talks (decimal comma, "
                  "team names keep their English form with Hungarian case endings, e.g. 'a Northgate FC-nek')"}

EDITOR = """You are the Editor of a football broadcast. You check one on-screen card against the measured facts.
Flag ONLY real problems: a wrong minute, scorer, assist, score or team; an action that is not in the facts
(for example a header, volley, free kick, save or dribble that the facts do not mention); a headline about
something other than the moment; a claim that contradicts a number. Do not flag style or wording.
Also flag a claim that needs knowledge of the future (calling a goal "the winner", "decisive", "sealed it")
unless the facts show the match has ended, and clear spelling or grammar mistakes in Hungarian.
A clock like "45+2'" is the 2nd minute of first-half stoppage time; "47'" is the 2nd minute of the second half,
which you may call "early in the second half" but never "the second minute".
Window stats are counts inside that window. A goal's own "score_after" is the score it produced.
A pundit's "may have", "helped" or "led to" about a goal that followed a change is fine; flag causation
only when the card states it as certain.
Return ONLY JSON: {"ok": true or false, "problems": ["short description", ...]}"""

NUM = re.compile(r"\d+(?:[.,]\d+)?")
WORD_NUMBERS_OK = set(range(0, 12)) | {90}  # counts of players, "90 minutes"


# ------------------------------------------------------------------ helpers
def _json_from(text):
    if not text:
        raise ValueError("empty reply")
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < a:
        raise ValueError("no JSON object in reply")
    return json.loads(text[a:b + 1])


def _numbers_in(obj, out):
    """Every number in a nested structure, including numbers inside strings (e.g. clock labels)."""
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float, np.integer, np.floating)):
        out.append(float(obj))
    elif isinstance(obj, str):
        for m in NUM.findall(obj):
            out.append(float(m.replace(",", ".")))
        try:  # tool results arrive as JSON text
            if obj.strip()[:1] in "{[":
                _numbers_in(json.loads(obj), out)
        except ValueError:
            pass
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers_in(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers_in(v, out)
    return out


def allowed_numbers(*sources):
    """Numbers a text may use: everything measured, plus simple derivations a pundit would make
    (rounding, percentages of shares, differences between before/after pairs)."""
    base = []
    for s in sources:
        _numbers_in(s, base)
    allowed = set()
    for x in base:
        allowed |= {x, round(x), round(x, 1), round(x, 2)}
        if 0 <= x <= 1:
            allowed.add(round(x * 100))
    for s in sources:
        for pair in _pairs(s):
            d = abs(pair[1] - pair[0])
            allowed |= {round(d), round(d, 1)}
    return allowed


def _pairs(obj):
    if isinstance(obj, dict):
        if "before" in obj and "after" in obj and all(isinstance(obj[k], (int, float)) for k in ("before", "after")):
            yield (float(obj["before"]), float(obj["after"]))
        for v in obj.values():
            yield from _pairs(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _pairs(v)


FOOTBALL_PHRASES = ("Player of the Match", "Man of the Match")


def unknown_names(text, names):
    """Capitalised words in the middle of a sentence that are not a known team or player name:
    catches invented clubs and players ("Riverstone")."""
    for phrase in FOOTBALL_PHRASES:
        text = text.replace(phrase, phrase.lower())
    bad = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        words = [w.strip("'’") for w in re.findall(r"[A-Za-zÀ-ÿŐőŰű'’-]+", sentence)]
        for i, w in enumerate(words):
            w = re.sub(r"['’]s$", "", w).split("-")[0]  # Dorsey's, Riverside-nak
            if not w[:1].isupper() or w in names or (w.isupper() and len(w) <= 4):
                continue
            if any(w.startswith(n) and len(w) - len(n) <= 5 and w[len(n):].islower() for n in names if len(n) >= 3):
                continue  # Hungarian case endings: Athleticnak, Northgate-nek
            if w.upper() in names:
                continue
            near_miss = any(len(n) >= 6 and difflib.SequenceMatcher(None, w.lower(), n.lower()).ratio() >= 0.7
                            for n in names)
            if i > 0 or near_miss:  # mid-sentence capital, or a twisted version of a real name anywhere
                bad.append(w)
    return bad


HU_WORDS = re.compile(r"\b(a|az|és|hogy|de|már|még|után|ellen|percben|gól|óta|nem|is)\b", re.I)
HU_VOWEL_NUMBERS = re.compile(r"\b([Aa]) ((?:1|5\d?)\b[.'])")  # egy, öt, ötven...


def wrong_language(text, lang):
    """A Hungarian card that came back in English (or the other way round)."""
    hu_hits = len(HU_WORDS.findall(text)) + len(re.findall(r"[áéíóöőúüű]", text, re.I))
    return hu_hits < 3 if lang == "hu" else hu_hits > 6


def polish(text, lang, names, title=False):
    """Mechanical house style the models keep missing: no dashes as punctuation, hyphen in scores,
    'az' before numbers read with a vowel in Hungarian, sentence-case English titles."""
    text = re.sub(r"(\d)\s*[–—]\s*(\d)", r"\1-\2", text)
    text = re.sub(r"\s+[–—]\s+", ", ", text)
    text = text.replace("—", ", ").replace("–", "-")
    if lang == "hu":
        text = HU_VOWEL_NUMBERS.sub(lambda m: ("Az" if m.group(1) == "A" else "az") + " " + m.group(2), text)
    if title and lang == "en":
        def is_name(w):
            w = re.sub(r"['’]s$", "", w.strip("',.:;!?")).split("-")[0]
            return w in names or w.isupper()
        words = text.split(" ")
        if any(w[:1].isupper() and not is_name(w) for w in words[1:]):  # Title Case: lower all but names
            text = " ".join([words[0]] + [w if is_name(w) else w.lower() for w in words[1:]])
    if title:
        text = text[:1].upper() + text[1:]
    return text


def fact_check(text, allowed):
    """Numbers in the text that are not backed by the facts."""
    bad = []
    for m in NUM.findall(text):
        v = float(m.replace(",", "."))
        if v in WORD_NUMBERS_OK:
            continue
        if not any(abs(v - a) <= max(0.051, 0.01 * abs(a)) for a in allowed):
            bad.append(m)
    return bad


# ------------------------------------------------------------------ orchestrator
class AgentTeam:
    def __init__(self, store, cache_path=None):
        from agent_framework import Agent, MCPStdioTool
        from agent_framework.foundry import FoundryChatClient
        from azure.identity import DefaultAzureCredential

        self.store = store
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache = json.loads(self.cache_path.read_text(encoding="utf-8")) if self.cache_path and self.cache_path.exists() else {}
        self.pending = {}
        self.analyst_lock = asyncio.Lock()
        self.clock_file = os.path.join(tempfile.gettempdir(), f"fullpitch_clock_{os.getpid()}.txt")
        Path(self.clock_file).write_text("0", encoding="utf-8")
        cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
        # fast model for investigation and reasoning, stronger writer for what viewers read
        client = FoundryChatClient(project_endpoint=endpoint, model=os.environ.get("FOUNDRY_MODEL", "gpt-4.1-mini"),
                                   credential=cred)
        writer = FoundryChatClient(project_endpoint=endpoint, credential=cred,
                                   model=os.environ.get("FOUNDRY_WRITER_MODEL", os.environ.get("FOUNDRY_MODEL", "gpt-4.1-mini")))
        self.mcp = MCPStdioTool(name="full_pitch", command=sys.executable, args=["-m", "fullpitch.mcp_server"],
                                env={**os.environ, "FULLPITCH_CLOCK_FILE": self.clock_file,
                                     # the server may run from another folder (uvicorn --app-dir)
                                     "PYTHONPATH": os.pathsep.join(filter(None, [
                                         os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                         os.environ.get("PYTHONPATH")]))},
                                load_prompts=False,
                                description="Live match data, always as of the moment being explained")
        self.analyst = Agent(client=client, name="Analyst", instructions=ANALYST, tools=self.mcp)
        self.tactician = Agent(client=client, name="Tactician", instructions=TACTICIAN)
        self.narrators = {p: Agent(client=writer, name=f"Narrator ({p})", instructions=NARRATOR[p]) for p in PERSONAS}
        self.editor = Agent(client=writer, name="Editor", instructions=EDITOR)
        md = store.md
        self.names = ({w for t in md.meta["teams"] for w in t["name"].split()} | set(md.shorts)
                      | {w for p in md.meta["players"] for w in p["name"].split()} | {"I", "VAR", "xG"})

    async def start(self):
        await self.mcp.__aenter__()

    async def stop(self):
        await self.mcp.__aexit__(None, None, None)

    def _save(self):
        if self.cache_path:
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.cache_path)

    # ---------------------------------------------------------------- events
    @staticmethod
    def event_key(card):
        if card["kind"] == "change":
            return f"change:{card['team']}:{card['confirmed_minute']}"
        if card["kind"] == "goal":
            return f"goal:{card['minute']:.2f}"
        if card["kind"] == "recap":
            return f"recap:{card['recap']}"
        return None

    def fact_pack(self, card):
        """Measured facts for one card, as of the moment it became known."""
        s = self.store
        md = s.md
        if card["kind"] == "change":
            asof = float(card["confirmed_minute"])
            m = float(card["minute"])
            change = next(c for c in s.tactical_changes(asof) if c["team"] == card["team"]
                          and c["confirmed_minute"] == card["confirmed_minute"])
            before = window_facts(md, s.rows, s.frame(max(0, m - 12)), s.frame(m), changes=[])
            after = window_facts(md, s.rows, s.frame(m), s.frame(asof), changes=[])
            return asof, {"type": "shape change", "team": card["team"], "team_name": _team_name(md, card["team"]),
                          "since": change["since"], "signals": change["signals"],
                          "card_is_about": "the shape change; goals since are what followed, not the subject",
                          "score_at_change": after["score_before"], "score_now": after["score_now"],
                          "stats_note": "shots, xG and passes are counts inside each window, not match totals",
                          "before_change": {"window": before["window"], "stats": before["stats"]},
                          "since_change": {"window": after["window"], "stats": after["stats"],
                                           "goals": after["goals"], "big_chances": after["big_chances"]}}
        if card["kind"] == "goal":
            asof = float(card["minute"]) + 0.5
            f_goal = s.frame(float(card["minute"]))
            goal = min((e for e in md.events if e["type"] == "goal"), key=lambda e: abs(e["frame"] - f_goal))
            shot = next(e for e in reversed(md.events) if e["type"] == "shot" and e["frame"] <= goal["frame"])
            build = [e for e in md.events if goal["frame"] - 20 * md.hz <= e["frame"] < goal["frame"]
                     and e["type"] == "pass" and e["team"] == goal["team"] and e["outcome"] == "complete"]
            active = [c for c in s.tactical_changes(asof)]
            return asof, {"type": "goal", "team": goal["team"], "team_name": _team_name(md, goal["team"]),
                          "clock": md.clock_label(goal["frame"]), "scorer": player_name(md, goal["player"]),
                          "assist": player_name(md, goal["assist"]) if goal.get("assist") else None,
                          "score_after": dict(zip(md.shorts, goal["score"])),
                          "shot": {"xg": round(shot["xg"], 2), "distance_m": shot["distance"],
                                   "speed_kmh": shot["shot_speed_kmh"], "penalty": bool(shot.get("penalty"))},
                          "passes_in_the_move": len(build),
                          "shape_changes_so_far": [{"team": c["team"], "since": c["since"],
                                                    "what": [x["what"] for x in c["signals"]]} for c in active]}
        if card["kind"] == "recap":
            asof = 45.0 if card["recap"] == "ht" else s.length_min
            r = s.recap(card["recap"], s.length_min if card["recap"] == "ft" else 60)
            return asof, {"type": "half-time recap" if card["recap"] == "ht" else "full-time recap", **r}
        raise ValueError(card["kind"])

    # ---------------------------------------------------------------- runs
    async def _run(self, agent, prompt, trace, label):
        t = time.time()
        for attempt in range(5):
            try:
                r = await agent.run(prompt)
                break
            except Exception as exc:  # noqa: BLE001
                transient = "429" in str(exc) or "rate limit" in str(exc).lower() or "timeout" in str(exc).lower()
                if not transient or attempt == 4:
                    raise
                await asyncio.sleep(4 * (attempt + 1))
        calls = []
        tool_results = []
        for msg in r.messages:
            for c in msg.contents:
                if getattr(c, "type", None) == "function_call":
                    calls.append(getattr(c, "name", "?"))
                elif getattr(c, "type", None) == "function_result":
                    tool_results.append(getattr(c, "result", None))
        usage = getattr(r, "usage_details", None) or {}
        trace.append({"agent": label, "seconds": round(time.time() - t, 1), "tools": calls,
                      "tokens": usage.get("total_token_count"), "output": (r.text or "")[:600]})
        return r.text, tool_results

    async def explain(self, card):
        """Analyst + Tactician for one event; shared by every persona and language."""
        key = self.event_key(card)
        hit = self.cache.get(f"{key}|explain")
        if hit:
            return hit
        asof, facts = self.fact_pack(card)
        trace = []
        async with self.analyst_lock:  # the clock file is shared, so one investigation at a time
            Path(self.clock_file).write_text(str(asof), encoding="utf-8")
            notes_text, tool_results = await self._run(
                self.analyst, f"Investigate this moment. Facts we already measured:\n{json.dumps(facts, ensure_ascii=False)}",
                trace, "Analyst")
        try:
            notes = _json_from(notes_text)
        except ValueError:
            notes = {"observations": []}
        tac_text, _ = await self._run(
            self.tactician, json.dumps({"facts": facts, "analyst": notes}, ensure_ascii=False), trace, "Tactician")
        tactic = _json_from(tac_text)
        out = {"asof": asof, "facts": facts, "notes": notes, "tactic": tactic,
               "allowed": sorted(allowed_numbers(facts, tool_results)), "trace": trace}
        self.cache[f"{key}|explain"] = out
        self._save()
        return out

    async def narrate(self, card, persona, lang, follow=None):
        key = f"{self.event_key(card)}|{persona}|{lang}|{follow or ''}"
        if key in self.cache:
            return self.cache[key]
        exp = await self.explain(card)
        trace = list(exp["trace"])
        allowed = set(exp["allowed"])
        extra = {}
        if persona == "player" and follow:
            extra = self.store.player_report(follow, 0, exp["asof"], exp["asof"])
            allowed |= allowed_numbers(extra)
        teams = {t["short"]: t["name"] for t in self.store.md.meta["teams"]}
        prompt = json.dumps({"teams": teams, "moment": exp["facts"], "pundit": exp["tactic"],
                             "followed_player": extra or None}, ensure_ascii=False)
        narrator = self.narrators[persona]
        rules = NARRATOR_RULES.format(language=LANGUAGE[lang])
        result = None
        for attempt in range(3):
            text, _ = await self._run(narrator, f"{rules}\n\n{prompt}", trace, narrator.name)
            try:
                card_text = _json_from(text)
                title, body = str(card_text["title"]).strip(), str(card_text["body"]).strip()
                title, body = polish(title, lang, self.names, title=True), polish(body, lang, self.names)
            except (ValueError, KeyError):
                trace.append({"agent": "Fact-checker", "check": "reply was not a card", "passed": False})
                continue
            bad = fact_check(f"{title} {body}", allowed)
            names_bad = unknown_names(body, self.names)
            n_numbers = len([x for x in NUM.findall(f"{title} {body}") if float(x.replace(",", ".")) not in WORD_NUMBERS_OK])
            too_many = persona == "casual" and n_numbers > 2
            lang_bad = wrong_language(f"{title} {body}", lang)
            trace.append({"agent": "Fact-checker", "numbers_checked": len(NUM.findall(f"{title} {body}")),
                          "unsupported": bad, "unknown_names": names_bad, "too_many_numbers": too_many,
                          "wrong_language": lang_bad, "passed": not (bad or names_bad or too_many or lang_bad)})
            editor_problems = []
            if not (bad or names_bad or too_many or lang_bad):
                verdict_text, _ = await self._run(
                    self.editor, json.dumps({"facts": exp["facts"], "card": {"title": title, "body": body},
                                             "language": lang}, ensure_ascii=False), trace, "Editor")
                try:
                    verdict = _json_from(verdict_text)
                    editor_problems = [] if verdict.get("ok") else [str(x) for x in verdict.get("problems", [])][:4]
                except ValueError:
                    editor_problems = []  # an unreadable verdict doesn't block a card that passed the checks
                if not editor_problems:
                    result = {"title": title, "body": body, "source": "agent", "trace": trace}
                    break
            problems = []
            if bad:
                problems.append(f"numbers that are not in the facts: {bad}")
            if names_bad:
                problems.append(f"names that are not in this match: {names_bad}")
            if too_many:
                problems.append("too many numbers for a casual fan")
            if lang_bad:
                problems.append(f"it was not written in {LANGUAGE[lang]}")
            if editor_problems:
                problems.append("these errors found by the editor: " + "; ".join(editor_problems))
            prompt += "\n\nYour previous card had " + "; ".join(problems) + ". Rewrite it."
        if result is None:  # the template stands, and the trace says why
            result = {"source": "template", "trace": trace}
        self.cache[key] = result
        self._save()
        return result

    def lookup(self, card, persona, lang, follow=None):
        return self.cache.get(f"{self.event_key(card)}|{persona}|{lang}|{follow or ''}")

    def ensure(self, card, persona, lang, follow=None):
        """Start writing a card in the background if it isn't written or being written."""
        key = f"{self.event_key(card)}|{persona}|{lang}|{follow or ''}"
        if key in self.cache or key in self.pending:
            return
        task = asyncio.create_task(self._safe(self.narrate(card, persona, lang, follow)))
        self.pending[key] = task
        task.add_done_callback(lambda _t: self.pending.pop(key, None))

    async def _safe(self, coro):
        try:
            return await coro
        except Exception as exc:  # noqa: BLE001 - a failed agent must never break the live page
            print(f"[agents] {type(exc).__name__}: {exc}", file=sys.stderr)
            return None

    # ---------------------------------------------------------------- catch-up (on demand)
    async def catch_up(self, facts, persona, lang):
        """'What did I miss?' written by the Narrator straight from the measured facts, fact-checked."""
        trace = []
        allowed = allowed_numbers(facts)
        narrator = self.narrators[persona]
        rules = NARRATOR_RULES.format(language=LANGUAGE[lang]).replace(
            '{"title": "...", "body": "..."}', '{"lines": ["...", "..."]}')
        prompt = ("The viewer stepped away. Tell them what they missed in 2 to 5 short bullet lines, most important "
                  "first. The screen already shows the time window, so do not repeat it. Cover every goal, red card and "
                  "every entry in shape_changes (what the team changed, in plain words), then the score. Mention "
                  "substitutions only when nothing bigger happened. Facts:\n" + json.dumps(facts, ensure_ascii=False))
        must = [(t, self.store.md.meta["teams"][self.store.md.shorts.index(t)]["name"].split()[0])
                for t in {c["team"] for c in facts.get("shape_changes", [])}]
        must += [(g["scorer"], g["scorer"].split()[-1]) for g in facts.get("goals", [])]
        for _ in range(3):
            text, _r = await self._run(narrator, f"{rules}\n\n{prompt}", trace, narrator.name)
            try:
                lines = [polish(str(x).strip(), lang, self.names) for x in _json_from(text)["lines"] if str(x).strip()]
            except (ValueError, KeyError, TypeError):
                continue
            joined = " ".join(lines)
            bad = fact_check(joined, allowed)
            names_bad = unknown_names(joined, self.names)
            missing = [what for what, word in must if word not in joined]
            lang_bad = wrong_language(joined, lang)
            trace.append({"agent": "Fact-checker", "numbers_checked": len(NUM.findall(joined)), "unsupported": bad,
                          "unknown_names": names_bad, "missing": missing, "wrong_language": lang_bad,
                          "passed": not (bad or names_bad or missing or lang_bad)})
            if not (bad or names_bad or missing or lang_bad) and lines:
                return {"lines": lines, "source": "agent", "trace": trace}
            prompt += (f"\n\nYour previous lines had: numbers not in the facts {bad}, names not in the match "
                       f"{names_bad}, left out {missing}, wrong language {lang_bad}. Rewrite them.")
        return None


def _team_name(md, short):
    return next(t["name"] for t in md.meta["teams"] if t["short"] == short)

