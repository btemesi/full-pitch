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
        "n_back": ("{after_n} players now form the back line in possession (was {before_n})", "", ""),
        "n_back_def": ("{after_n} players now form the back line without the ball (was {before_n})", "", ""),
        "length": ("the team is {dir} ({before:.0f} m → {after:.0f} m from back to front)", "more stretched", "more compact"),
        "width": ("the team is {dir} ({before:.0f} m → {after:.0f} m)", "wider", "narrower"),
        "pressers": ("{dir} players are hunting the ball ({before:.1f} → {after:.1f} near it)", "more", "fewer"),
        "left_share": ("{after:.0f}% of their attacks now go down the left (was {before:.0f}%)", "", ""),
    },
    "hu": {
        "line_adj": ("a védővonaluk {dir} ({before:.0f} m → {after:.0f} m a kaputól)", "feljebb jött", "lejjebb húzódott"),
        "def_line": ("a védővonaluk {dir} ({before:.0f} m → {after:.0f} m)", "feljebb jött", "lejjebb húzódott"),
        "n_back": ("labdabirtoklásnál most {after_n} játékos áll a hátsó sorban ({before_n} helyett)", "", ""),
        "n_back_def": ("labda nélkül most {after_n} játékos áll a hátsó sorban ({before_n} helyett)", "", ""),
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
        ("n_back", "up"): "{team} have switched to a back three",
        ("n_back", "down"): "{team} have gone back to a back four",
        ("n_back_def", "up"): "{team} are defending with a back five",
        ("n_back_def", "down"): "{team} have gone back to a back four",
        ("left_share", "up"): "{team} are attacking down the left",
        ("left_share", "down"): "{team} have stopped attacking down the left",
        ("pressers", "up"): "{team} are pressing harder",
        ("pressers", "down"): "{team} have stopped pressing",
        "default": "{team} have changed shape",
    },
    "hu": {
        ("line_adj", "down"): "{A} {team} visszahúzódott",
        ("line_adj", "up"): "{A} {team} feljebb tolta a védelmét",
        ("n_back", "up"): "{A} {team} háromvédős rendszerre váltott",
        ("n_back", "down"): "{A} {team} visszatért a négyvédős rendszerhez",
        ("n_back_def", "up"): "{A} {team} ötös védősorral védekezik",
        ("n_back_def", "down"): "{A} {team} visszatért a négyvédős rendszerhez",
        ("left_share", "up"): "{A} {team} a bal oldalon támad",
        ("left_share", "down"): "{A} {team} már nem a bal oldalon támad",
        ("pressers", "up"): "{A} {team} erősebben letámad",
        ("pressers", "down"): "{A} {team} abbahagyta a letámadást",
        "default": "{A} {team} formát váltott",
    },
}

WORDS = {
    "en": {"since": "Since {clock}", "goal": "GOAL", "assist": "assist", "missed": "While you were away ({frm}–{to})",
           "nothing": "Nothing big happened: no goals, no big chances.", "score_same": "Still {score}.",
           "score_new": "It's now {score}.", "subs": "Subs", "cards": "Cards", "big": "Big chance",
           "poss": "{team} had {pct:.0f}% of the ball", "shots": "Shots {ta} {a}, {tb} {b} (xG {xa:.2f}–{xb:.2f})",
           "player": "{name}: {km:.1f} km, {sprints} sprints, top speed {top:.0f} km/h, {pc}/{p} passes",
           "player_short": "{name} covered {km:.1f} km with {sprints} sprints",
           "pick": "Tap a player on the pitch to follow them."},
    "hu": {"since": "{clock} óta", "goal": "GÓL", "assist": "gólpassz", "missed": "Amíg nem voltál itt ({frm}–{to})",
           "nothing": "Nagy dolog nem történt: se gól, se nagy helyzet.", "score_same": "Még mindig {score}.",
           "score_new": "Most {score} az állás.", "subs": "Cserék", "cards": "Lapok", "big": "Nagy helyzet",
           "poss": "A labda {pct:.0f}%-ban a {team} csapatánál volt", "shots": "Lövések: {ta} {a}, {tb} {b} (xG {xa:.2f}–{xb:.2f})",
           "player": "{name}: {km:.1f} km, {sprints} sprint, csúcs {top:.0f} km/h, {pc}/{p} passz",
           "player_short": "{name} {km:.1f} km-t futott, {sprints} sprinttel",
           "pick": "Koppints egy játékosra a pályán, hogy kövesd."},
}


def _score(facts_score, teams):
    a, b = list(facts_score.keys())
    return f"{a} {facts_score[a]}–{facts_score[b]} {b}"


PRIORITY = ["n_back", "n_back_def", "left_share", "line_adj", "def_line", "pressers", "length", "width"]
SHAPE_KEYS = {"n_back", "n_back_def", "left_share"}


def hu_article(name):
    """'A' or 'Az' before a Hungarian noun phrase."""
    return "Az" if name[:1].lower() in "aáeéiíoóöőuúüű" else "A"


def _formation_flip(h):
    """A back-line count only names a formation when it crosses a whole-player boundary."""
    return round(h["before"]) != round(h["after"])


def change_card(change, team_names, persona="casual", lang="en"):
    """A tactical change as an on-screen card. The headline leads with what a fan recognises:
    a new formation or attacking side first, then the line height, then pressing."""
    sigs = [h for h in change["signals"] if _signal_key(h) not in ("n_back", "n_back_def") or _formation_flip(h)]
    if not sigs:
        sigs = change["signals"]
    sigs = sorted(sigs, key=lambda h: PRIORITY.index(_signal_key(h)))
    top = sigs[0]
    key = _signal_key(top)
    direction = "up" if top["after"] > top["before"] else "down"
    heads = HEADLINES[lang]
    team = team_names.get(change["team"], change["team"])
    title = heads.get((key, direction), heads["default"]).format(team=team, A=hu_article(team))
    shown = sigs if persona == "analyst" else sigs[:2 if key in SHAPE_KEYS else 1]
    lines = []
    for h in shown:
        k = _signal_key(h)
        tpl, up, down = SIGNAL_TEXT[lang].get(k, SIGNAL_TEXT[lang]["def_line"])
        d = up if h["after"] > h["before"] else down
        text = tpl.format(dir=d, before=h["before"], after=h["after"],
                          before_n=round(h["before"]), after_n=round(h["after"]))
        if k == "line_adj" and _is_att(h):
            text += " " + ("with the ball" if lang == "en" else "labdabirtoklásnál")
        lines.append(text)
    body = "; ".join(dict.fromkeys(lines))
    return {"kind": "change", "team": change["team"], "title": title, "signal": key,
            "subtitle": WORDS[lang]["since"].format(clock=change["since"]),
            "body": _num(body[:1].upper() + body[1:] + ".", lang), "confidence": change.get("confidence"),
            "source": "template"}


def _is_att(h):
    return h.get("phase") == "att" or "in possession" in h.get("what", h.get("label", ""))


def _num(text, lang):
    """Hungarian uses a decimal comma."""
    if lang != "hu":
        return text
    import re
    return re.sub(r"(\d)\.(\d)", lambda m: m.group(1) + "," + m.group(2), text)


def _signal_key(h):
    """Map a signal (from analysis or from the MCP/store view) to a template key."""
    key = h.get("signal")
    what = h.get("what", h.get("label", ""))
    if key == "n_back" or "back line" in what:
        return "n_back" if _is_att(h) else "n_back_def"
    if key in ("line_adj", "def_line") or "line height" in what:
        return "line_adj"
    if key == "left_share" or "attacks" in what:
        return "left_share"
    if key == "pressers" or "pressing" in what:
        return "pressers"
    if key == "length" or "length" in what:
        return "length"
    if key == "width" or "width" in what:
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
                            "signals": [{"what": x["signal"], "signal": x.get("key"), "phase": x.get("phase"),
                                         "before": x["before"], "after": x["after"]} for x in ch["what"]]},
                           teams, persona, lang)
        out.append(f"{card['title']}: {card['body']}")

    st = facts["stats"]
    if persona == "analyst":
        a, b = shorts
        top = max(shorts, key=lambda s: st[s]["possession_pct"] or 0)
        out.append(w["poss"].format(team=teams[top], pct=st[top]["possession_pct"] or 0))
        out.append(w["shots"].format(ta=a, tb=b, a=st[a]["shots"], b=st[b]["shots"], xa=st[a]["xg"], xb=st[b]["xg"]))
    if facts.get("substitutions") and persona != "casual":
        out.append(f"{w['subs']}: " + ", ".join(f"{s['minute']} {s['on']} ↔ {s['off']}" for s in facts["substitutions"]))
    if facts.get("cards"):
        out.append(f"{w['cards']}: " + ", ".join(f"{c['minute']} {c['player']}" for c in facts["cards"]))
    fp = facts.get("followed_player")
    if persona == "player" and not fp:
        out.append(w["pick"])
    if fp:
        if persona == "player" or persona == "analyst":
            out.append(w["player"].format(name=fp["name"], km=fp["distance_m"] / 1000, sprints=fp["sprints"],
                                          top=fp["top_speed_kmh"], pc=fp["passes_completed"], p=fp["passes"]))
        else:
            out.append(w["player_short"].format(name=fp["name"], km=fp["distance_m"] / 1000, sprints=fp["sprints"]))
    return {"lines": [_num(x, lang) for x in out], "source": "template"}


RECAP = {
    "en": {"ht": "Half-time", "ft": "Full-time", "goals": "Goals", "none": "No goals", "xg": "xG",
           "shots": "shots", "poss": "possession", "changed": "Shape changes", "potm": "Player of the match",
           "half_best": "Best so far", "miss": "Biggest miss"},
    "hu": {"ht": "Félidő", "ft": "Vége", "goals": "Gólok", "none": "Nincs gól", "xg": "xG",
           "shots": "lövés", "poss": "labdabirtoklás", "changed": "Taktikai váltások", "potm": "A meccs embere",
           "half_best": "Eddig a legjobb", "miss": "Legnagyobb kihagyott helyzet"},
}


def recap_text(r, lang="en"):
    """Half-time / full-time recap as a title and a few lines, straight from the facts."""
    w = RECAP[lang]
    a, b = list(r["score"].keys())
    st = r["stats"]
    lines = [f"{a} {r['score'][a]}–{r['score'][b]} {b}"]
    if r["goals"]:
        lines.append(f"{w['goals']}: " + ", ".join(f"{g['clock']} {g['scorer']} ({g['team']})" for g in r["goals"]))
    else:
        lines.append(w["none"])
    lines.append(f"{w['xg']} {st[a]['xg']:.2f}–{st[b]['xg']:.2f} · {st[a]['shots']}–{st[b]['shots']} {w['shots']} · "
                 f"{w['poss']} {st[a]['possession_pct']}%–{st[b]['possession_pct']}%")
    if r["big_misses"]:
        m = r["big_misses"][0]
        lines.append(f"{w['miss']}: {m['clock']} {m['player']} ({m['team']}), xG {m['xg']:.2f}")
    if r["changes"]:
        lines.append(f"{w['changed']}: " + ", ".join(f"{c['team']} {c['since']}" for c in r["changes"]))
    p = r["player_of_the_match"]
    if p and p["name"]:
        lines.append(f"{w['potm'] if r['kind'] == 'ft' else w['half_best']}: {p['name']} ({p['team']})")
    return {"title": f"{w[r['kind']]}: {a} {r['score'][a]}–{r['score'][b]} {b}", "lines": [_num(x, lang) for x in lines]}
