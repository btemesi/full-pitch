"""The Full Pitch agent team, on Microsoft Foundry with Microsoft Agent Framework.

    Analyst      reads the match through the MCP server (as of the moment) and writes evidence notes
    Tactician    turns evidence into cause and effect: what changed, why it matters, what followed
    Narrator x3  writes for a fan, an analyst or one player's follower, in English
    Translator   turns the checked English card into Hungarian, adding nothing
    Fact-checker deterministic: every number must exist in the measured facts or the Analyst's tool
                 results, every name must belong to this match
    Editor       reads the card against the facts like a sub-editor: wrong minute, scorer, score or team,
                 invented actions (headers, free kicks), a headline about the wrong thing
                 Up to three drafts; after that the template text stands
    Orchestrator shared state (fact pack + notes), hand-offs, caching, failure recovery, a trace per card

Agents never see past the moment they explain: the MCP server reads its clock from a file that
the orchestrator sets to the event's minute before the Analyst runs.
"""

import asyncio
import collections
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
FAIL_COOLDOWN_S = 300
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
The facts never prove cause: when a goal or a number followed a change, say it came after the change or may
have helped, never that the change produced it. If the evidence for a consequence is weak, say so plainly.
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
Describe tactics (pressing, a deep block, a formation, line height) only when the facts measure them.
Never say a change produced what followed ("this change increased possession"); say what happened since:
"Since the change, possession went from 43% to 55%."
You know only what has happened up to this moment: never call a goal the winner or say a match is decided
unless the facts say it has ended. Do not use dashes as punctuation. Write in {language}.
Return ONLY JSON: {{"title": "...", "body": "..."}}"""
LANGUAGE = {"en": "English",
            "hu": "natural, correctly spelled Hungarian, the way a Hungarian TV pundit talks (decimal comma, "
                  "team names keep their English form with Hungarian case endings, e.g. 'a Northgate FC-nek')"}

TRANSLATOR = """You translate one checked on-screen football card from English into Hungarian, the way a
Hungarian TV commentator would say it. Keep every fact, number, minute and name; add nothing and leave nothing out.
Write natural, grammatical Hungarian: decimal comma (30,5), team names keep their English form with Hungarian
case endings ("a Northgate FC-nek", "a Riverside Athletic javára"), the article before minutes ("az 53. percben",
"a 47. percben", "a 66. percben": "az" only before numbers read with a vowel, like 1, 5 and 50 to 59),
suffixes after scores ("2-0-s vezetés", "1-0-ra"). Do not use dashes as punctuation.
Words: "shape change" is "átrendeződés" (never "formációváltás" unless the English says formation),
"defensive line" is "védelmi vonal", "expected goals" or "xG" is "xG" or "várható gólok", "back three" is
"háromvédős hátsó sor", "final third" is "támadóharmad", a "late push" is a "hajrá" (never "nyomás").
Return ONLY JSON: {"title": "...", "body": "..."}"""

EDITOR = """You are the Editor of a football broadcast. You check one on-screen card against the measured facts.
Flag ONLY real problems: a wrong minute, scorer, assist, score or team; an action that is not in the facts
(for example a header, volley, free kick, save or dribble that the facts do not mention); a tactical claim
(pressing, a deep block, a formation, a line height) that the facts do not measure; a headline about
something other than the moment; a claim that contradicts a number. Do not flag style or wording, what the
card leaves out, or a fair reading of a number (55% possession is "more of the ball").
Also flag a claim that needs knowledge of the future (calling a goal "the winner", "decisive", "sealed it")
unless the facts show the match has ended. For a Hungarian card, also read it as a native proofreader and flag
grammar: subject and verb must agree ("Dorsey két gólja alapozta meg", not "alapozták"), suffixes after scores
and numbers ("2-0-s", "1-0-ra"), case endings, and phrases no Hungarian commentator would say ("várt gólmutató"
instead of "várható gólok", "önmaga által tartott vonal").
A clock like "45+2'" is the 2nd minute of first-half stoppage time; "47'" is the 2nd minute of the second half,
which you may call "early in the second half" but never "the second minute".
Window stats are counts inside that window. A goal's own "score_after" is the score it produced.
Back-line counts are averages: about 3 (2.6 or more) supports "a back three", about 4 "a back four".
A pundit's "may have", "helped" or "led to" about a goal that followed a change is fine; flag causation
only when the card states it as certain.
Work claim by claim: list every factual claim the card makes (numbers, minutes, names, events, tactics,
cause and effect) and decide whether the facts support it.
If the input has "translated_from", the card is a Hungarian translation of that English card: also flag
anything the translation adds, drops or changes compared with it.
Return ONLY JSON: {"claims": [{"claim": "...", "supported": true or false, "why": "..."}],
"ok": true only if every claim is supported, "problems": ["short description of each unsupported claim"]}"""

NUM = re.compile(r"\d+(?:[.,]\d+)?")
WORD_NUMBERS_OK = {0, 1, 90}  # "0-0", "one", "90 minutes"; every other digit must be in the facts
SMALL_COUNTS = set(range(12)) | {90}  # scores and small counts do not make a casual card "too numeric"


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
HU_ARTICLE_NUMBER = re.compile(r"\b([Aa]z?) (\d+)(?=[.,'\s%+-]|$)")
HU_SPELLING = {"Gólnélküli": "Gól nélküli", "gólnélküli": "gól nélküli"}


def _hu_article(n):
    # numbers read with a leading vowel: egy (1), öt (5), ötven.. (50-59), ezer.. (1000-1999)
    return "az" if n in (1, 5) or 50 <= n <= 59 or 1000 <= n <= 1999 else "a"


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
    text = re.sub(r"\s+-\s*(?=[,.;:!?])", "", text)  # a dash left dangling before punctuation
    text = re.sub(r"\s+-\s+", ", ", text)
    if lang == "hu":
        def article(m):
            art = _hu_article(int(m.group(2)))
            return (art.capitalize() if m.group(1)[0] == "A" else art) + " " + m.group(2)
        text = HU_ARTICLE_NUMBER.sub(article, text)
        for wrong, right in HU_SPELLING.items():
            text = text.replace(wrong, right)
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


CLOCK = re.compile(r"(\d+)(?:\+\d+)?'")
TEXT_MINUTES = re.compile(r"(\d+)(?:\+\d+)?(?:'|(?:st|nd|rd|th) minute|\.\s*perc)")


def fact_minutes(facts):
    # every clock label in the facts of the moment, e.g. "67'" or "45+2'"
    return {int(m) for m in CLOCK.findall(json.dumps(facts, ensure_ascii=False))}


def wrong_minutes(text, minutes):
    # minutes in the text that are not a clock of this moment ("since the 70' change" when it began at 67')
    return [m for m in TEXT_MINUTES.findall(text) if int(m) not in minutes]


PRESSURE_WORDS = re.compile(r"pressur|\bpress(es|ed|ing)?\b|nyomás|nyomást|letámad|szorongat", re.I)


def unmeasured_pressure(text, facts):
    # "invited pressure", "despite the pressure": pressing is measured (the pressers signal), so a card may
    # only talk about it when the facts of the moment carry that measurement
    if not PRESSURE_WORDS.search(text):
        return False
    return "pressers" not in json.dumps(facts) and "press" not in json.dumps(facts).lower().replace("pressers", "")


CAUSAL = re.compile(
    r"\b(this|the|that|their|which)\s+(change|shift|adjustment|switch|move|tweak|deeper line|higher line)\b"
    r"[^.]{0,40}?\b(tripled|doubled|halved|increased|boosted|raised|lifted|led to|caused|resulted in|produced|"
    r"gave|created|cost|forced|allowed|enabled|shifted|moved|swung|pushed|took|brought|improved|cut|reduced)\b"
    r"|\b(resulted in|led to|caused)\b[^.]{0,30}\b(goal|possession|shots?|xg|chances?)\b"
    r"|\b(this|that|which)\s+(tripled|doubled|halved|increased|boosted|raised|lifted|led to|caused|resulted in|"
    r"produced|gave|created|cost|shifted|moved|swung|pushed|took|brought|improved|cut|reduced)\b"
    r"|,\s*(boosting|increasing|raising|lifting|tripling|doubling|giving|creating|producing)\b"
    r"|\b(line|change|shift|adjustment)\s+(brings|brought|gives|gave|creates|created|delivers|delivered)\b", re.I)


def certain_cause(text):
    # the facts never prove that a change produced what followed ("this change tripled their xG")
    return bool(CAUSAL.search(text))


def drop_article_before_players(text, players):
    # Hungarian puts no article before a person's name: "az Umar Dorsey révén" -> "Umar Dorsey révén"
    # (team names keep theirs: "a Riverside Athletic")
    capital = text[:1].isupper()
    for name in players:
        text = re.sub(rf"\b(?:[Aa]z?) (?={re.escape(name)}\b)", "", text)
    return text[:1].upper() + text[1:] if capital else text


def expand_codes(text, teams):
    # "RIV dominate" -> "Riverside Athletic dominate": viewers know the names, not the codes
    for short, name in teams.items():
        text = re.sub(rf"\b{short}\b(?![-'’]?\d)", name, text)
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


def editor_verdict(text):
    """Problems the Editor found; an unreadable verdict doesn't block a card that passed the checks."""
    try:
        v = _json_from(text)
    except ValueError:
        return []
    problems = [str(x) for x in v.get("problems", []) if str(x).strip()]
    problems += [f"unsupported: {c.get('claim')}" for c in v.get("claims", [])
                 if isinstance(c, dict) and c.get("supported") is False and not problems]
    return [] if v.get("ok") and not problems else (problems or ["the editor rejected the card"])[:4]


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
        self.failed = {}  # key -> time of the last failure; retried after FAIL_COOLDOWN_S
        self.runs = collections.deque()  # start times of live (uncached) jobs, for the hourly budget
        self.max_runs_per_hour = int(os.environ.get("FULLPITCH_AGENT_RUNS_PER_HOUR", "60"))
        self.jobs = asyncio.Semaphore(2)
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
        self.translator = Agent(client=writer, name="Translator", instructions=TRANSLATOR)
        self.inflight = {}  # card key -> task, so English and Hungarian don't write the same card twice
        md = store.md
        self.players = sorted({p["name"] for p in md.meta["players"]}, key=len, reverse=True)
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
            change = next(c for c in s.tactical_changes(asof) if c["team"] == card["team"]
                          and c["confirmed_minute"] == card["confirmed_minute"])
            m = float(change["since_minute"])  # as known at confirmation, not the later refined start
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
            asof = s.ht_minute if card["recap"] == "ht" else s.length_min
            r = s.recap(card["recap"], asof)  # only what was known at the whistle
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
        # the Analyst and the Tactician are checked too: their notes are what the Narrator builds on
        ask = f"Investigate this moment. Facts we already measured:\n{json.dumps(facts, ensure_ascii=False)}"
        for _ in range(3):
            async with self.analyst_lock:  # the clock file is shared, so one investigation at a time
                Path(self.clock_file).write_text(str(asof), encoding="utf-8")
                notes_text, tool_results = await self._run(self.analyst, ask, trace, "Analyst")
            try:
                notes = _json_from(notes_text)
            except ValueError:
                notes = {"observations": []}
            allowed = allowed_numbers(facts, tool_results)
            problems = self._check_notes(notes.get("observations", []), allowed, trace)
            if not problems:
                break
            ask += f"\n\nYour previous notes had {problems}. Investigate again; use only names and numbers from the data."
        else:
            notes = {"observations": []}  # unchecked notes are never passed on
        tell = json.dumps({"facts": facts, "analyst": notes}, ensure_ascii=False)
        tactic = None
        for _ in range(3):
            tac_text, _ = await self._run(self.tactician, tell, trace, "Tactician")
            try:
                tactic = _json_from(tac_text)
            except ValueError:
                continue
            problems = self._check_notes(list(tactic.values()), allowed, trace)
            if not problems:
                break
            tell += f"\n\nYour previous answer had {problems}. Rewrite it with only names and numbers from the input."
            tactic = None
        if tactic is None:
            raise ValueError("the Tactician could not give a checked explanation")
        out = {"asof": asof, "facts": facts, "notes": notes, "tactic": tactic,
               "allowed": sorted(allowed), "trace": trace}
        self.cache[f"{key}|explain"] = out
        self._save()
        return out

    def _check_notes(self, sentences, allowed, trace):
        """Fact-check the Analyst's notes or the Tactician's answer; '' when they hold."""
        teams = {t["short"]: t["name"] for t in self.store.md.meta["teams"]}
        text = " ".join(str(x).strip().rstrip(".") + "." for x in sentences)
        bad = fact_check(text, allowed)
        names_bad = unknown_names(expand_codes(text, teams), self.names)
        trace.append({"agent": "Fact-checker", "numbers_checked": len(NUM.findall(text)), "unsupported": bad,
                      "unknown_names": names_bad, "passed": not (bad or names_bad)})
        problems = []
        if bad:
            problems.append(f"numbers that are not in the data: {bad}")
        if names_bad:
            problems.append(f"names that are not in this match: {names_bad}")
        return "; ".join(problems)

    async def narrate(self, card, persona, lang, follow=None):
        key = f"{self.event_key(card)}|{persona}|{lang}|{follow or ''}"
        if key in self.cache:
            return self.cache[key]
        if key not in self.inflight:
            self.inflight[key] = asyncio.ensure_future(self._narrate(key, card, persona, lang, follow))
        try:
            return await asyncio.shield(self.inflight[key])
        finally:
            if self.inflight.get(key) and self.inflight[key].done():
                self.inflight.pop(key, None)

    async def _narrate(self, key, card, persona, lang, follow):
        if lang == "hu":
            # Hungarian is translated from the checked English card: same facts in both languages,
            # and the model translates into Hungarian far better than it composes in it
            en = await self.narrate(card, persona, "en", follow)
            result = await self._translate(card, en, persona, follow) if en.get("source") == "agent" else None
            # no checked English card (or no faithful translation): the Hungarian template stands too,
            # so both languages always say the same thing
            result = result or {"source": "template", "trace": list(en.get("trace", []))}
            self.cache[key] = result
            self._save()
            return result
        exp = await self.explain(card)
        trace = list(exp["trace"])
        allowed = set(exp["allowed"])
        minutes = fact_minutes(exp["facts"])
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
                teams = {t["short"]: t["name"] for t in self.store.md.meta["teams"]}
                title = polish(expand_codes(title, teams), lang, self.names, title=True)
                body = polish(expand_codes(body, teams), lang, self.names)
            except (ValueError, KeyError):
                trace.append({"agent": "Fact-checker", "check": "reply was not a card", "passed": False})
                continue
            bad = fact_check(f"{title} {body}", allowed) + wrong_minutes(f"{title} {body}", minutes)
            names_bad = unknown_names(body, self.names)
            n_numbers = len([x for x in NUM.findall(f"{title} {body}") if float(x.replace(",", ".")) not in SMALL_COUNTS])
            too_many = persona == "casual" and n_numbers > 2
            lang_bad = wrong_language(f"{title} {body}", lang)
            press_bad = unmeasured_pressure(f"{title} {body}", exp["facts"])
            cause_bad = certain_cause(f"{title} {body}")
            trace.append({"agent": "Fact-checker", "numbers_checked": len(NUM.findall(f"{title} {body}")),
                          "unsupported": bad, "unknown_names": names_bad, "too_many_numbers": too_many,
                          "wrong_language": lang_bad, "unmeasured_pressure": press_bad, "certain_cause": cause_bad,
                          "passed": not (bad or names_bad or too_many or lang_bad or press_bad or cause_bad)})
            editor_problems = []
            if not (bad or names_bad or too_many or lang_bad or press_bad or cause_bad):
                verdict_text, _ = await self._run(
                    self.editor, json.dumps({"facts": exp["facts"], "card": {"title": title, "body": body},
                                             "language": lang}, ensure_ascii=False), trace, "Editor")
                editor_problems = editor_verdict(verdict_text)
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
            if press_bad:
                problems.append("it talks about pressure or pressing, which the facts of this moment do not measure")
            if cause_bad:
                problems.append("it says the change produced what followed; say it came after the change instead")
            if editor_problems:
                problems.append("these errors found by the editor: " + "; ".join(editor_problems))
            prompt += "\n\nYour previous card had " + "; ".join(problems) + ". Rewrite it."
        if result is None:  # the template stands, and the trace says why
            result = {"source": "template", "trace": trace}
        self.cache[key] = result
        self._save()
        return result

    async def reverify(self, key):
        """Run the current Editor over a cached agent card; returns its problems (empty when it holds)."""
        hit = self.cache.get(key)
        exp = self.cache.get(key.split("|")[0] + "|explain")
        if not hit or hit.get("source") != "agent" or not exp:
            return []
        lang = key.split("|")[2]
        ask = {"facts": exp["facts"], "card": {"title": hit["title"], "body": hit["body"]}, "language": lang}
        en = self.cache.get(key.replace("|hu|", "|en|")) if lang == "hu" else None
        if en and en.get("source") == "agent":
            ask["translated_from"] = {"title": en["title"], "body": en["body"]}
        text, _ = await self._run(self.editor, json.dumps(ask, ensure_ascii=False), [], "Editor")
        return editor_verdict(text)

    async def _translate(self, card, en, persona, follow):
        exp = await self.explain(card)
        trace = list(en["trace"])
        allowed = set(exp["allowed"])
        if persona == "player" and follow:
            allowed |= allowed_numbers(self.store.player_report(follow, 0, exp["asof"], exp["asof"]))
        minutes = fact_minutes(exp["facts"])
        teams = {t["short"]: t["name"] for t in self.store.md.meta["teams"]}
        ask = json.dumps({"title": en["title"], "body": en["body"]}, ensure_ascii=False)
        for _ in range(3):
            text, _ = await self._run(self.translator, ask, trace, "Translator")
            try:
                card_text = _json_from(text)
                title = polish(expand_codes(str(card_text["title"]).strip(), teams), "hu", self.names, title=True)
                body = polish(expand_codes(str(card_text["body"]).strip(), teams), "hu", self.names)
                title = drop_article_before_players(title, self.players)
                body = drop_article_before_players(body, self.players)
            except (ValueError, KeyError):
                continue
            bad = fact_check(f"{title} {body}", allowed) + wrong_minutes(f"{title} {body}", minutes)
            names_bad = unknown_names(body, self.names)
            lang_bad = wrong_language(f"{title} {body}", "hu") or unmeasured_pressure(f"{title} {body}", exp["facts"])
            trace.append({"agent": "Fact-checker", "numbers_checked": len(NUM.findall(f"{title} {body}")),
                          "unsupported": bad, "unknown_names": names_bad, "wrong_language": lang_bad,
                          "passed": not (bad or names_bad or lang_bad)})
            problems = []
            if not (bad or names_bad or lang_bad):
                verdict_text, _ = await self._run(
                    self.editor, json.dumps({"facts": exp["facts"], "card": {"title": title, "body": body},
                                             "translated_from": {"title": en["title"], "body": en["body"]},
                                             "language": "hu"}, ensure_ascii=False), trace, "Editor")
                problems = editor_verdict(verdict_text)
                if not problems:
                    return {"title": title, "body": body, "source": "agent", "trace": trace}
            else:
                problems = [f"numbers {bad}", f"names {names_bad}", "not Hungarian" if lang_bad else ""]
            ask += "\n\nYour previous translation had these problems: " + "; ".join(x for x in problems if x) + ". Translate again."
        return None

    def lookup(self, card, persona, lang, follow=None):
        return self.cache.get(f"{self.event_key(card)}|{persona}|{lang}|{follow or ''}")

    def take_run(self):
        # a public demo must not be able to spend the model budget: live jobs per hour are capped
        now = time.time()
        while self.runs and now - self.runs[0] > 3600:
            self.runs.popleft()
        if len(self.runs) >= self.max_runs_per_hour:
            return False
        self.runs.append(now)
        return True

    def ensure(self, card, persona, lang, follow=None):
        """Start writing a card in the background. True while the agents are on it; False when the
        template text stays (already written, failed recently, or over the hourly budget)."""
        key = f"{self.event_key(card)}|{persona}|{lang}|{follow or ''}"
        if key in self.pending:
            return True
        if key in self.cache or time.time() - self.failed.get(key, -1e9) < FAIL_COOLDOWN_S or not self.take_run():
            return False
        task = asyncio.create_task(self._safe(key, self.narrate(card, persona, lang, follow)))
        self.pending[key] = task
        task.add_done_callback(lambda _t: self.pending.pop(key, None))
        return True

    async def _safe(self, key, coro):
        try:
            async with self.jobs:
                return await coro
        except Exception as exc:  # noqa: BLE001 - a failed agent must never break the live page
            self.failed[key] = time.time()  # no retry storm: the page asks again every few seconds
            print(f"[agents] {type(exc).__name__}: {exc}", file=sys.stderr)
            return None

    # ---------------------------------------------------------------- catch-up (on demand)
    def _check_lines(self, lines, lang, allowed, minutes, must, trace, facts):
        """Deterministic checks on catch-up lines; a list of problems, empty when they hold."""
        joined = " ".join(lines)
        bad = fact_check(joined, allowed) + wrong_minutes(joined, minutes)
        names_bad = unknown_names(joined, self.names)
        missing = [what for what, word in must if word not in joined]
        lang_bad = wrong_language(joined, lang)
        press_bad = unmeasured_pressure(joined, facts)
        trace.append({"agent": "Fact-checker", "numbers_checked": len(NUM.findall(joined)), "unsupported": bad,
                      "unknown_names": names_bad, "missing": missing, "wrong_language": lang_bad,
                      "unmeasured_pressure": press_bad,
                      "passed": not (bad or names_bad or missing or lang_bad or press_bad)})
        problems = []
        if bad:
            problems.append(f"numbers not in the facts {bad}")
        if names_bad:
            problems.append(f"names not in the match {names_bad}")
        if missing:
            problems.append(f"left out {missing}")
        if lang_bad:
            problems.append(f"not written in {LANGUAGE[lang]}")
        if press_bad:
            problems.append("pressure or pressing, which the facts do not measure")
        return problems

    async def _edit_lines(self, facts, lines, lang, trace, source=None):
        ask = {"facts": facts, "card": {"title": "What did I miss?", "body": " ".join(lines)}, "language": lang}
        if source:
            ask["translated_from"] = {"title": "What did I miss?", "body": " ".join(source)}
        verdict, _ = await self._run(self.editor, json.dumps(ask, ensure_ascii=False), trace, "Editor")
        return editor_verdict(verdict)

    async def catch_up(self, facts, persona, lang):
        """'What did I miss?' written by the Narrator straight from the measured facts, checked by the
        Fact-checker and the Editor; Hungarian is translated from the checked English lines."""
        trace = []
        allowed = allowed_numbers(facts)
        minutes = fact_minutes(facts)
        teams = {t["short"]: t["name"] for t in self.store.md.meta["teams"]}
        must = [(t, teams[t].split()[0]) for t in {c["team"] for c in facts.get("shape_changes", [])}]
        must += [(g["scorer"], g["scorer"].split()[-1]) for g in facts.get("goals", [])]
        if lang == "hu":
            en = await self.catch_up(facts, persona, "en")
            if not en:
                return None
            trace = list(en["trace"])
            ask = (json.dumps({"lines": en["lines"]}, ensure_ascii=False) + '\n\nTranslate every line. '
                   'Return ONLY JSON: {"lines": ["...", "..."]}')
            for _ in range(3):
                text, _r = await self._run(self.translator, ask, trace, "Translator")
                try:
                    lines = [drop_article_before_players(polish(expand_codes(str(x).strip(), teams), "hu", self.names),
                                                         self.players)
                             for x in _json_from(text)["lines"] if str(x).strip()]
                except (ValueError, KeyError, TypeError):
                    continue
                problems = self._check_lines(lines, "hu", allowed, minutes, must, trace, facts)
                if not problems and lines:
                    problems = await self._edit_lines(facts, lines, "hu", trace, source=en["lines"])
                    if not problems:
                        return {"lines": lines, "source": "agent", "trace": trace}
                ask += "\n\nYour previous translation had: " + "; ".join(problems) + ". Translate again."
            return None
        narrator = self.narrators[persona]
        rules = NARRATOR_RULES.format(language=LANGUAGE[lang]).replace(
            '{"title": "...", "body": "..."}', '{"lines": ["...", "..."]}')
        prompt = ("The viewer stepped away. Tell them what they missed in 2 to 5 short bullet lines, most important "
                  "first. The screen already shows the time window, so do not repeat it. Cover every goal, red card and "
                  "every entry in shape_changes (what the team changed, in plain words), then the score. Mention "
                  "substitutions only when nothing bigger happened. Facts:\n" + json.dumps(facts, ensure_ascii=False))
        for _ in range(3):
            text, _r = await self._run(narrator, f"{rules}\n\n{prompt}", trace, narrator.name)
            try:
                lines = [polish(expand_codes(str(x).strip(), teams), lang, self.names)
                         for x in _json_from(text)["lines"] if str(x).strip()]
            except (ValueError, KeyError, TypeError):
                continue
            problems = self._check_lines(lines, lang, allowed, minutes, must, trace, facts)
            if not problems and lines:
                problems = await self._edit_lines(facts, lines, lang, trace)
                if not problems:
                    return {"lines": lines, "source": "agent", "trace": trace}
            prompt += "\n\nYour previous lines had: " + "; ".join(problems or ["no lines"]) + ". Rewrite them."
        return None


def _team_name(md, short):
    return next(t["name"] for t in md.meta["teams"] if t["short"] == short)

