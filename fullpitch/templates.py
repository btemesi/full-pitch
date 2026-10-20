"""Template narration: plain, reliable sentences straight from the facts.

Used two ways: before the agents are wired in, and as the fallback when an
agent fails or is too slow for live play. Every number comes from the facts.

personas: "casual" (few numbers), "analyst" (all numbers), "player" (follows one player)
langs:    "en", "hu"
"""

SIGNAL_TEXT = {
    "en": {
        "line_adj": ("their defensive line is {dir} ({before:.0f} m → {after:.0f} m from goal)", "higher", "deeper"),
        "def_line": ("their defensive line is {dir} ({before:.0f} m → {after:.0f} m)", "higher", "deeper"),
        "n_back": ("{after:.0f} players now hold the back line (was {before:.0f})", "", ""),
        "length": ("the team is {dir} ({before:.0f} m → {after:.0f} m from back to front)", "more stretched", "more compact"),
        "width": ("the team is {dir} ({before:.0f} m → {after:.0f} m)", "wider", "narrower"),
        "pressers": ("{dir} players are hunting the ball ({before:.1f} → {after:.1f} near it)", "more", "fewer"),
        "left_share": ("{after:.0f}% of their attacks now go down the left (was {before:.0f}%)", "", ""),
    },
    "hu": {
        "line_adj": ("a védővonaluk {dir} ({before:.0f} m → {after:.0f} m a kaputól)", "feljebb jött", "lejjebb húzódott"),
        "def_line": ("a védővonaluk {dir} ({before:.0f} m → {after:.0f} m)", "feljebb jött", "lejjebb húzódott"),
        "n_back": ("most {after:.0f} játékos áll a hátsó sorban ({before:.0f} helyett)", "", ""),
        "length": ("a csapat {dir} ({before:.0f} m → {after:.0f} m elölről hátra)", "szétnyúlt", "összehúzódott"),
        "width": ("a csapat {dir} ({before:.0f} m → {after:.0f} m)", "szélesebb lett", "szűkebb lett"),
        "pressers": ("{dir} játékos támadja a labdát ({before:.1f} → {after:.1f})", "több", "kevesebb"),
        "left_share": ("a támadásaik {after:.0f}%-a megy most a bal oldalon ({before:.0f}% volt)", "", ""),
    },
}

HEADLINES = {
    "en": {
        ("line_adj", "down"): "{team} have dropped deeper",
        ("line_adj", "up"): "{team} are pushing up",
        ("n_back", "up"): "{team} have changed their back line",
        ("n_back", "down"): "{team} have changed their back line",
        ("left_share", "up"): "{team} are attacking down the left",
        ("pressers", "up"): "{team} are pressing harder",
        ("pressers", "down"): "{team} have stopped pressing",
        "default": "{team} have changed shape",
    },
    "hu": {
        ("line_adj", "down"): "A {team} visszahúzódott",
        ("line_adj", "up"): "A {team} feljebb tolta a védelmét",
        ("n_back", "up"): "A {team} átrendezte a védelmét",
        ("n_back", "down"): "A {team} átrendezte a védelmét",
        ("left_share", "up"): "A {team} a bal oldalon támad",
        ("pressers", "up"): "A {team} erősebben letámad",
        ("pressers", "down"): "A {team} abbahagyta a letámadást",
        "default": "A {team} formát váltott",
    },
}

WORDS = {
    "en": {"since": "Since {clock}", "goal": "GOAL", "for": "for", "assist": "assist", "missed": "While you were away ({frm}–{to})",
           "nothing": "Nothing big happened: no goals, no big chances.", "score_same": "Still {score}.",
           "score_new": "It's now {score}.", "subs": "Subs", "cards": "Cards", "big": "Big chance",
           "poss": "{team} had {pct:.0f}% of the ball", "shots": "Shots {a}–{b} (xG {xa:.2f}–{xb:.2f})",
           "player": "{name}: {km:.1f} km, {sprints} sprints, top speed {top:.0f} km/h, {pc}/{p} passes",
           "player_short": "{name} covered {km:.1f} km with {sprints} sprints",
           "watch": "Why it matters"},
    "hu": {"since": "{clock} óta", "goal": "GÓL", "for": "", "assist": "gólpassz", "missed": "Amíg nem voltál itt ({frm}–{to})",
           "nothing": "Nagy dolog nem történt: se gól, se nagy helyzet.", "score_same": "Még mindig {score}.",
           "score_new": "Most {score} az állás.", "subs": "Cserék", "cards": "Lapok", "big": "Nagy helyzet",
           "poss": "A labda {pct:.0f}%-ban a {team} csapatánál volt", "shots": "Lövések {a}–{b} (xG {xa:.2f}–{xb:.2f})",
           "player": "{name}: {km:.1f} km, {sprints} sprint, csúcs {top:.0f} km/h, {pc}/{p} passz",
           "player_short": "{name} {km:.1f} km-t futott, {sprints} sprinttel",
           "watch": "Miért számít"},
}


def _score(facts_score, teams):
    a, b = list(facts_score.keys())
    return f"{a} {facts_score[a]}–{facts_score[b]} {b}"


def change_card(change, team_names, persona="casual", lang="en"):
    """A tactical change as an on-screen card."""
    # lead with the signal a fan understands best, not just the statistically strongest
    priority = ["line_adj", "def_line", "n_back", "left_share", "pressers", "length", "width"]
    sigs = sorted(change["signals"], key=lambda h: priority.index(_signal_key(h)))
    top = sigs[0]
    key = _signal_key(top)
    direction = "up" if top["after"] > top["before"] else "down"
    heads = HEADLINES[lang]
    team = team_names.get(change["team"], change["team"])
    title = heads.get((key, direction), heads["default"]).format(team=team)
    lines = []
    for h in (sigs if persona == "analyst" else sigs[:1]):
        k = _signal_key(h)
        tpl, up, down = SIGNAL_TEXT[lang].get(k, SIGNAL_TEXT[lang]["def_line"])
        d = up if h["after"] > h["before"] else down
        text = tpl.format(dir=d, before=h["before"], after=h["after"])
        if "in possession" in h.get("what", h.get("label", "")) or h.get("phase") == "att":
            text += " " + ("with the ball" if lang == "en" else "labdabirtoklásnál")
        lines.append(text)
    body = "; ".join(dict.fromkeys(lines))
    return {"kind": "change", "team": change["team"], "title": title,
            "subtitle": WORDS[lang]["since"].format(clock=change["since"]),
            "body": _num(body[:1].upper() + body[1:] + ".", lang), "confidence": change.get("confidence"),
            "source": "template"}


def _num(text, lang):
    """Hungarian uses a decimal comma."""
    if lang != "hu":
        return text
    import re
    return re.sub(r"(\d)\.(\d)", lambda m: m.group(1) + "," + m.group(2), text)


def _signal_key(h):
    what = h.get("signal") or h.get("what", "")
    for k in ("line_adj", "def_line", "n_back", "length", "width", "pressers", "left_share"):
        if k in what:
            return k
    if "pressing" in what:
        return "pressers"
    if "back line" in what:
        return "n_back"
    if "line height" in what:
        return "line_adj"
    if "attacks" in what:
        return "left_share"
    if "length" in what:
        return "length"
    if "width" in what:
        return "width"
    return "def_line"


def goal_card(ev, lang="en"):
    w = WORDS[lang]
    body = ev.get("player", "")
    if ev.get("assist"):
        body += f" ({w['assist']}: {ev['assist']})"
    return {"kind": "goal", "team": ev.get("team"), "title": f"{w['goal']} · {ev.get('team')}",
            "subtitle": ev.get("clock"), "body": body, "source": "template"}


def catch_up_text(facts, persona="casual", lang="en"):
    """'What did I miss?' in a few short lines."""
    w = WORDS[lang]
    teams = facts["teams"]
    shorts = list(teams.keys())
    out = [w["missed"].format(frm=facts["window"]["from"], to=facts["window"]["to"])]
    changed = facts["score_before"] != facts["score_now"]
    for g in facts["goals"]:
        line = f"{g['minute']} {w['goal']} {teams[g['team']]}: {g['scorer']}"
        if g.get("assist") and persona != "casual":
            line += f" ({w['assist']}: {g['assist']})"
        out.append(line)
    if persona != "casual" or not facts["goals"]:
        for c in facts["big_chances"][:3 if persona == "analyst" else 1]:
            out.append(f"{c['minute']} {w['big']}: {c['player']} ({teams[c['team']]}), xG {c['xg']:.2f}, {c['outcome']}")
    if not facts["goals"] and not facts["big_chances"]:
        out.append(w["nothing"])
    out.append((w["score_new"] if changed else w["score_same"]).format(score=_score(facts["score_now"], teams)))

    for ch in facts.get("shape_changes", []):
        card = change_card({"team": ch["team"], "since": ch["since"],
                            "signals": [{"what": x["signal"], "before": x["before"], "after": x["after"]} for x in ch["what"]]},
                           teams, persona, lang)
        out.append(f"{card['title']}: {card['body']}")

    st = facts["stats"]
    if persona == "analyst":
        a, b = shorts
        top = max(shorts, key=lambda s: st[s]["possession_pct"] or 0)
        out.append(w["poss"].format(team=teams[top], pct=st[top]["possession_pct"] or 0))
        out.append(w["shots"].format(a=st[a]["shots"], b=st[b]["shots"], xa=st[a]["xg"], xb=st[b]["xg"]))
    if facts.get("substitutions") and persona != "casual":
        out.append(f"{w['subs']}: " + ", ".join(f"{s['minute']} {s['on']} ↔ {s['off']}" for s in facts["substitutions"]))
    if facts.get("cards"):
        out.append(f"{w['cards']}: " + ", ".join(f"{c['minute']} {c['player']}" for c in facts["cards"]))
    fp = facts.get("followed_player")
    if fp:
        if persona == "player" or persona == "analyst":
            out.append(w["player"].format(name=fp["name"], km=fp["distance_m"] / 1000, sprints=fp["sprints"],
                                          top=fp["top_speed_kmh"], pc=fp["passes_completed"], p=fp["passes"]))
        else:
            out.append(w["player_short"].format(name=fp["name"], km=fp["distance_m"] / 1000, sprints=fp["sprints"]))
    return {"lines": [_num(x, lang) for x in out], "source": "template"}
