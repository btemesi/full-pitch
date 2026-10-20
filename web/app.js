// Full Pitch web client: live pitch, broadcast/full view, follow a player, insight cards, catch-up.
// The live clock is held by the server session; this page only asks "what is true now".
(() => {
  const L = 105, W = 68, PAD = 3;
  const $ = (id) => document.getElementById(id);
  const cv = $("pitch"), g = cv.getContext("2d");

  const S = {
    match: null, sid: null, lineup: null, colors: {}, shorts: [],
    frames: [], playhead: 0, playing: true, speed: 8, es: null,
    view: "full", zoom: 1, cam: { x: L / 2, y: W / 2 }, portrait: false, dpr: 1,
    follow: null, followStats: null, persona: "casual", lang: "en",
    cards: [], seenCards: new Set(), score: [0, 0], lastCardMinute: -1, lastStatMinute: -1, cardReq: 0,
    still: null,
  };

  // ------------------------------------------------------------ data
  const api = (path, params = {}, method = "GET") =>
    fetch(path + "?" + new URLSearchParams(params), { method }).then((r) => {
      if (!r.ok) throw new Error(`${path} ${r.status}`);
      return r.json();
    });
  const withSession = (params = {}) => ({ session: S.sid, ...params });

  async function init() {
    S.match = await api("/api/match");
    S.shorts = S.match.teams.map((t) => t.short);
    S.colors = S.match.team_colors;
    for (const [el, k] of [["home", 0], ["away", 1]]) {
      $(el).querySelector("b").textContent = S.shorts[k];
      $(el).querySelector("i").style.background = S.colors[S.shorts[k]];
      $(el).title = S.match.teams[k].name;
    }
    const start = +(new URLSearchParams(location.search).get("t") || 0);
    S.sid = (await api("/api/session", { from: start }, "POST")).session;
    resize();
    await afterJump();
    requestAnimationFrame(loop);
  }

  // Everything that must be refreshed after the session minute changes by a jump.
  async function afterJump() {
    S.frames = []; S.playhead = 0;
    S.lastCardMinute = -1; S.lastStatMinute = -1;
    const [st, lineup, still] = await Promise.all([
      api("/api/state", withSession()), api("/api/lineup", withSession()), api("/api/frame", withSession()),
    ]);
    S.score = S.shorts.map((s) => st.score[s]);
    S.lineup = lineup;
    S.still = still;
    renderScore(); fillFollowSelect();
    refreshCards(still.m, true);
    if (S.follow) loadFollowStats();
    if (S.playing) openStream();
  }

  async function seek(minute) {
    if (S.es) { S.es.close(); S.es = null; }
    await api(`/api/session/${S.sid}/seek`, { to: minute }, "POST");
    await afterJump();
  }

  function openStream() {
    if (S.es) S.es.close();
    const es = new EventSource("/api/stream?" + new URLSearchParams(withSession({ speed: S.speed })));
    es.addEventListener("frames", (e) => { for (const fr of JSON.parse(e.data)) S.frames.push(fr); });
    es.addEventListener("match_event", (e) => onEvent(JSON.parse(e.data)));
    es.addEventListener("changes", () => refreshCards(currentMinute()));
    es.addEventListener("end", () => { es.close(); setPlaying(false); });
    es.onerror = () => { if (es.readyState === EventSource.CLOSED) setPlaying(false); };
    S.es = es;
  }

  function onEvent(ev) {
    if (ev.type === "goal" && ev.score) {
      S.score = ev.score.slice();
      renderScore();
      setTimeout(() => refreshCards(ev.minute, true), 50);
    }
    if (ev.type === "card" && ev.card === "red") setTimeout(() => { refreshCards(ev.minute, true); refreshLineup(); }, 50);
    if (ev.type === "substitution") setTimeout(() => refreshLineup(ev), 50);
  }

  async function refreshLineup(subEvent) {
    const before = S.follow;
    S.lineup = await api("/api/lineup", withSession());
    fillFollowSelect();
    if (before && !slotOf(before)) {
      const name = playerName(before);
      S.follow = null; S.followStats = null; $("followCard").hidden = true; $("followSelect").value = "";
      toast(subEvent && subEvent.off === name
        ? (S.lang === "hu" ? `${name} lecserélték, helyette ${subEvent.on} jött be.` : `${name} was substituted. ${subEvent.on} came on.`)
        : (S.lang === "hu" ? `${name} már nincs a pályán.` : `${name} has left the pitch.`));
    }
  }

  async function refreshCards(minute, force = false) {
    const m = Math.floor(minute);
    if (!force && m === S.lastCardMinute) return;
    S.lastCardMinute = m;
    const req = ++S.cardReq;
    const cards = await api("/api/cards", withSession({ persona: S.persona, lang: S.lang }));
    if (req !== S.cardReq) return; // a newer request already answered
    S.cards = cards;
    renderCards(); renderMarks();
  }

  // ------------------------------------------------------------ playback
  function currentFrame() {
    if (!S.frames.length) return S.still;
    const i = Math.min(S.frames.length - 1, Math.max(0, S.playhead));
    const a = S.frames[Math.floor(i)], b = S.frames[Math.min(S.frames.length - 1, Math.floor(i) + 1)];
    const t = i - Math.floor(i);
    return { ...a, xy: a.xy.map((v, j) => v + (b.xy[j] - v) * t) };
  }
  const currentMinute = () => { const f = currentFrame(); return f ? f.m : 0; };

  let last = performance.now();
  function loop(now) {
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (S.playing && S.frames.length) {
      const hz = S.match.hz;
      S.playhead = Math.min(S.frames.length - 1.001, S.playhead + dt * hz * S.speed);
      if (S.frames.length - S.playhead > hz * S.speed * 2) S.playhead = S.frames.length - hz * S.speed;
    }
    const fr = currentFrame();
    if (fr) {
      draw(fr);
      $("clock").textContent = fr.c;
      $("fill").style.width = (100 * fr.m) / S.match.length_minutes + "%";
      if (S.follow && Math.floor(fr.m) !== S.lastStatMinute) { S.lastStatMinute = Math.floor(fr.m); loadFollowStats(); }
    }
    requestAnimationFrame(loop);
  }

  // ------------------------------------------------------------ geometry
  // World metres -> "screen metres": landscape keeps the pitch lying down; portrait stands it up
  // (attack towards the top in the first half), which is how a phone is held next to a TV.
  function toScreen(x, y) { return S.portrait ? [W - y, L - x] : [x, W - y]; }
  function screenDims() { return S.portrait ? [W, L] : [L, W]; }

  function resize() {
    const wrap = cv.parentElement;
    S.portrait = wrap.clientWidth < 640;
    S.dpr = window.devicePixelRatio || 1;
    const [dw, dh] = screenDims();
    const cssW = wrap.clientWidth;
    let cssH = cssW * (dh + 2 * PAD) / (dw + 2 * PAD);
    if (S.portrait) cssH = Math.min(cssH, window.innerHeight * 0.72);
    cv.style.height = cssH + "px";
    cv.width = Math.round(cssW * S.dpr);
    cv.height = Math.round(cssH * S.dpr);
  }
  window.addEventListener("resize", resize);

  function transform() {
    const [dw, dh] = screenDims();
    const base = Math.min(cv.width / (dw + 2 * PAD), cv.height / (dh + 2 * PAD));
    S.zoom += ((S.view === "tv" ? 2.7 : 1) - S.zoom) * 0.06;
    const k = base * S.zoom;
    const vw = cv.width / k, vh = cv.height / k;
    const [bx, by] = toScreen(S.cam.x, S.cam.y);
    const z = Math.min(1, Math.max(0, (S.zoom - 1) / 1.7));
    const cx = dw / 2 + (bx - dw / 2) * z, cy = dh / 2 + (by - dh / 2) * z;
    const ox = (dw - vw) / 2 > 0 ? (dw - vw) / 2 : Math.max(-PAD, Math.min(dw + PAD - vw, cx - vw / 2));
    const oy = (dh - vh) / 2 > 0 ? (dh - vh) / 2 : Math.max(-PAD, Math.min(dh + PAD - vh, cy - vh / 2));
    const P = (x, y) => { const [u, v] = toScreen(x, y); return [(u - ox) * k, (v - oy) * k]; };
    return { k, base, P, inView: (x, y) => { const [u, v] = toScreen(x, y); return u >= ox && u <= ox + vw && v >= oy && v <= oy + vh; } };
  }

  function formationAt(team, t) {
    let fm = S.match.teams[team].starting_formation;
    for (const x of S.match.formation_log) if (x.team === team && x.t <= t) fm = x.formation;
    return S.match.formations[fm];
  }

  // ------------------------------------------------------------ drawing
  function draw(fr) {
    const Pw = (i) => [fr.xy[i * 2], fr.xy[i * 2 + 1]];
    const ball = Pw(22);
    S.cam.x += (ball[0] - S.cam.x) * 0.08; S.cam.y += (ball[1] - S.cam.y) * 0.08;
    const v = transform(), d = S.dpr;
    const rect = (x0, y0, x1, y1) => { const [a, b] = v.P(x0, y0), [c, e] = v.P(x1, y1); return [Math.min(a, c), Math.min(b, e), Math.abs(c - a), Math.abs(e - b)]; };

    g.fillStyle = "#1b5631"; g.fillRect(0, 0, cv.width, cv.height);
    g.fillStyle = "#1e5d35";
    for (let i = 0; i < 10; i += 2) g.fillRect(...rect(i * 10.5, 0, (i + 1) * 10.5, W));
    g.strokeStyle = "rgba(255,255,255,.78)"; g.lineWidth = Math.max(1.2 * d, v.k * 0.12);
    const box = (x0, y0, x1, y1) => g.strokeRect(...rect(x0, y0, x1, y1));
    box(0, 0, L, W);
    const [m1x, m1y] = v.P(L / 2, 0), [m2x, m2y] = v.P(L / 2, W);
    g.beginPath(); g.moveTo(m1x, m1y); g.lineTo(m2x, m2y); g.stroke();
    const [ccx, ccy] = v.P(L / 2, W / 2);
    g.beginPath(); g.arc(ccx, ccy, 9.15 * v.k, 0, 7); g.stroke();
    box(0, W / 2 - 20.16, 16.5, W / 2 + 20.16); box(L - 16.5, W / 2 - 20.16, L, W / 2 + 20.16);
    box(0, W / 2 - 9.16, 5.5, W / 2 + 9.16); box(L - 5.5, W / 2 - 9.16, L, W / 2 + 9.16);
    g.fillStyle = "rgba(255,255,255,.85)";
    g.fillRect(...rect(-1.2, W / 2 - 3.66, 0, W / 2 + 3.66)); g.fillRect(...rect(L, W / 2 - 3.66, L + 1.2, W / 2 + 3.66));

    const onPitch = (k, s) => !S.lineup || S.lineup[S.shorts[k]][s].on_pitch !== false;

    if ($("shapes").checked) {
      for (let k = 0; k < 2; k++) {
        const fm = formationAt(k, fr.t);
        const groups = fr.p === k ? fm.att_groups : fm.def_groups;
        const lines = {};
        for (let s = 1; s < 11; s++) if (onPitch(k, s)) (lines[groups[s]] = lines[groups[s]] || []).push(Pw(k * 11 + s));
        g.strokeStyle = S.colors[S.shorts[k]]; g.globalAlpha = 0.6;
        g.lineWidth = Math.max(2 * d, v.base * 0.26 * (1 + (S.zoom - 1) * 0.4)); g.lineCap = "round"; g.lineJoin = "round";
        for (const key in lines) {
          const pts = lines[key].sort((a, b) => a[1] - b[1]);
          if (pts.length < 2) continue;
          g.beginPath();
          pts.forEach((p, i) => { const [x, y] = v.P(p[0], p[1]); i ? g.lineTo(x, y) : g.moveTo(x, y); });
          g.stroke();
        }
        g.globalAlpha = 1;
      }
    }

    const fslot = S.follow ? slotOf(S.follow) : null;
    if (fslot && S.frames.length) {
      const idx = fslot[0] * 11 + fslot[1], i0 = Math.floor(S.playhead);
      g.strokeStyle = "rgba(215,255,58,.85)"; g.lineWidth = Math.max(2 * d, v.k * 0.22);
      g.beginPath();
      for (let i = Math.max(0, i0 - S.match.hz * 12), n = 0; i <= i0; i++, n++) {
        const f2 = S.frames[i]; if (!f2) continue;
        const [x, y] = v.P(f2.xy[idx * 2], f2.xy[idx * 2 + 1]);
        n ? g.lineTo(x, y) : g.moveTo(x, y);
      }
      g.stroke();
    }

    const r = Math.min(15 * d, Math.max(6.5 * d, v.base * 0.95)) * (1 + (S.zoom - 1) * 0.45);
    let visible = 0;
    for (let k = 0; k < 2; k++) for (let s = 0; s < 11; s++) {
      if (!onPitch(k, s)) continue;
      const [wx, wy] = Pw(k * 11 + s);
      if (v.inView(wx, wy)) visible++;
      const [x, y] = v.P(wx, wy);
      const me = fslot && fslot[0] === k && fslot[1] === s;
      g.beginPath(); g.arc(x, y, me ? r * 1.25 : r, 0, 7);
      g.fillStyle = s === 0 ? "#1d1f1e" : S.colors[S.shorts[k]]; g.fill();
      g.lineWidth = (me ? 3 : 1.4) * d; g.strokeStyle = me ? "#d7ff3a" : "rgba(255,255,255,.9)"; g.stroke();
      const pl = S.lineup && S.lineup[S.shorts[k]][s];
      if (!pl) continue;
      g.fillStyle = "#fff"; g.font = `600 ${Math.round(r * 1.02)}px Inter, sans-serif`;
      g.textAlign = "center"; g.textBaseline = "middle";
      g.fillText(pl.number, x, y + 0.5);
      if (me) {
        g.font = `600 ${Math.round(Math.max(11 * d, r * 1.05))}px Inter, sans-serif`;
        const tw = g.measureText(pl.name).width + 14 * d, th = Math.max(18 * d, r * 1.7);
        g.fillStyle = "rgba(11,15,13,.82)"; g.fillRect(x - tw / 2, y - r * 1.4 - th, tw, th);
        g.fillStyle = "#d7ff3a"; g.fillText(pl.name, x, y - r * 1.4 - th / 2);
      }
    }
    S.visible = visible;
    const [bxp, byp] = v.P(ball[0], ball[1]);
    g.beginPath(); g.arc(bxp, byp, Math.max(3.5 * d, v.k * 0.5), 0, 7);
    g.fillStyle = "#fff"; g.fill(); g.lineWidth = 1.2 * d; g.strokeStyle = "#000"; g.stroke();
    S._v = v; S._fr = fr;
  }

  // ------------------------------------------------------------ players
  function slotOf(pid) {
    if (!S.lineup) return null;
    for (let k = 0; k < 2; k++) {
      const s = S.lineup[S.shorts[k]].findIndex((p) => p.id === pid && p.on_pitch !== false);
      if (s >= 0) return [k, s];
    }
    return null;
  }
  function playerName(pid) {
    const p = S.match.players.find((x) => x.id === pid);
    return p ? p.name : pid;
  }

  function fillFollowSelect() {
    const sel = $("followSelect"), keep = S.follow || "";
    sel.innerHTML = `<option value="">${S.lang === "hu" ? "Játékos követése…" : "Follow a player…"}</option>`;
    for (let k = 0; k < 2; k++) {
      const og = document.createElement("optgroup");
      og.label = S.match.teams[k].name;
      for (const p of S.lineup[S.shorts[k]]) {
        if (p.on_pitch === false) continue;
        const o = document.createElement("option");
        o.value = p.id; o.textContent = `#${p.number} ${p.name}`;
        og.appendChild(o);
      }
      sel.appendChild(og);
    }
    sel.value = keep;
  }

  function setFollow(pid) {
    S.follow = pid || null; S.followStats = null; S.lastStatMinute = -1;
    $("followSelect").value = pid || "";
    $("hint").hidden = !!S.follow;
    if (!S.follow) $("followCard").hidden = true; else loadFollowStats();
    if (S.persona === "player") refreshCards(currentMinute(), true);
  }

  async function loadFollowStats() {
    if (!S.follow) return;
    const pid = S.follow;
    try {
      const rep = await api("/api/player", withSession({ id: pid }));
      if (S.follow === pid) { S.followStats = rep; renderFollow(); }
    } catch (e) { /* player left the pitch between requests */ }
  }

  function renderFollow() {
    const box = $("followCard"), fs = S.follow && slotOf(S.follow);
    if (!fs || !S.followStats) { box.hidden = true; return; }
    const [k, s] = fs, pl = S.lineup[S.shorts[k]][s], st = S.followStats.physical || {};
    const fr = S._fr, i0 = Math.floor(S.playhead), prev = S.frames[Math.max(0, i0 - S.match.hz)];
    const idx = k * 11 + s;
    const speed = prev && fr ? Math.hypot(fr.xy[idx * 2] - prev.xy[idx * 2], fr.xy[idx * 2 + 1] - prev.xy[idx * 2 + 1]) * 3.6 : 0;
    const hu = S.lang === "hu";
    box.hidden = false;
    box.innerHTML = "";
    const n = document.createElement("div"); n.className = "n";
    n.textContent = `#${pl.number} ${pl.name} `;
    const t = document.createElement("span"); t.style.color = S.colors[S.shorts[k]]; t.textContent = S.shorts[k]; n.appendChild(t);
    const row = document.createElement("div"); row.className = "row";
    for (const [label, value, cls] of [[hu ? "Most" : "Now", `${speed.toFixed(0)} km/h`, speed > 25 ? "sprint" : ""],
      [hu ? "Táv" : "Distance", `${((st.distance_m || 0) / 1000).toFixed(1)} km`, ""],
      ["Sprint", `${st.sprints || 0}`, ""], [hu ? "Csúcs" : "Top", `${(st.top_speed_kmh || 0).toFixed(0)}`, ""]]) {
      const c = document.createElement("div");
      c.innerHTML = "<small></small><b></b>";
      c.querySelector("small").textContent = label; c.querySelector("b").textContent = value;
      if (cls) c.querySelector("b").className = cls;
      row.appendChild(c);
    }
    box.append(n, row);
  }
  setInterval(() => S.follow && S.followStats && renderFollow(), 400);

  // ------------------------------------------------------------ UI
  function renderScore() { $("score").textContent = `${S.score[0]}–${S.score[1]}`; }
  const cardKey = (c) => c.kind + c.team + c.minute;

  function renderCards() {
    const el = $("cards"), hu = S.lang === "hu";
    el.innerHTML = "";
    if (S.persona === "player" && !S.follow) {
      const p = document.createElement("p"); p.className = "empty";
      p.textContent = hu ? "Koppints egy játékosra, vagy válaszd ki a listából, és az ő szemszögéből követed a meccset." : "Tap a player, or pick one from the list, to follow the match through their eyes.";
      el.appendChild(p);
    }
    if (!S.cards.length) {
      const p = document.createElement("p"); p.className = "empty";
      p.textContent = hu ? "A meccs elemzései itt jelennek meg." : "Insights appear here as the match unfolds.";
      el.appendChild(p); return;
    }
    const label = { goal: hu ? "Gól" : "Goal", change: hu ? "Taktikai változás" : "Shape change", red: hu ? "Kiállítás" : "Sending off" };
    for (const c of [...S.cards].sort((a, b) => b.minute - a.minute)) {
      const d = document.createElement("article");
      d.className = "card " + c.kind;
      d.style.setProperty("--c", S.colors[c.team] || "#888");
      d.innerHTML = '<div class="k"><span></span><span></span></div><h3></h3><p></p>';
      const [k1, k2] = d.querySelectorAll(".k span");
      k1.textContent = label[c.kind] || ""; k2.textContent = c.subtitle || "";
      d.querySelector("h3").textContent = c.title;
      d.querySelector("p").textContent = c.body;
      if (c.source === "agent") { const s = document.createElement("div"); s.className = "src"; s.textContent = "Explained by Full Pitch agents"; d.appendChild(s); }
      if (S.seenCards.has(cardKey(c))) d.style.animation = "none";
      S.seenCards.add(cardKey(c));
      el.appendChild(d);
    }
  }

  function renderMarks() {
    const el = $("marks"); el.innerHTML = "";
    for (const c of S.cards) {
      const m = document.createElement("div");
      m.className = "mark " + c.kind;
      m.style.left = (100 * c.minute) / S.match.length_minutes + "%";
      m.style.color = S.colors[c.team];
      if (c.kind !== "change") m.style.background = c.kind === "red" ? "#ff4d4d" : S.colors[c.team];
      m.title = `${c.subtitle} ${c.title}`;
      el.appendChild(m);
    }
  }

  let toastTimer;
  function toast(text) {
    const t = $("toast"); t.textContent = text; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 4500);
  }

  let hookTimer;
  function hook(text) {
    const h = $("hook"); h.textContent = text; h.classList.add("show");
    clearTimeout(hookTimer); hookTimer = setTimeout(() => h.classList.remove("show"), 2600);
  }

  function setPlaying(on) {
    S.playing = on;
    $("play").textContent = on ? "❚❚" : "▶";
    $("play").setAttribute("aria-label", on ? "Pause" : "Play");
    $("live").classList.toggle("paused", !on);
  }

  document.querySelectorAll(".view-toggle button").forEach((b) =>
    b.addEventListener("click", () => {
      S.view = b.dataset.view;
      document.querySelectorAll(".view-toggle button").forEach((x) => {
        x.classList.toggle("on", x === b); x.setAttribute("aria-selected", x === b);
      });
      if (S.view === "tv") setTimeout(() => hook(`On TV, you see ${S.visible} player${S.visible === 1 ? "" : "s"}.`), 900);
      else hook("The match has twenty-two.");
    }));

  cv.addEventListener("click", (e) => {
    if (!S._v || !S._fr) return;
    const rc = cv.getBoundingClientRect();
    const mx = (e.clientX - rc.left) * cv.width / rc.width, my = (e.clientY - rc.top) * cv.height / rc.height;
    let best = null, bd = 1e9;
    for (let k = 0; k < 2; k++) for (let s = 0; s < 11; s++) {
      if (S.lineup && S.lineup[S.shorts[k]][s].on_pitch === false) continue;
      const [x, y] = S._v.P(S._fr.xy[(k * 11 + s) * 2], S._fr.xy[(k * 11 + s) * 2 + 1]);
      const dd = Math.hypot(x - mx, y - my);
      if (dd < bd) { bd = dd; best = [k, s]; }
    }
    setFollow(bd < 26 * S.dpr ? S.lineup[S.shorts[best[0]]][best[1]].id : null);
  });
  $("followSelect").addEventListener("change", (e) => setFollow(e.target.value));

  $("play").addEventListener("click", async () => {
    if (S.playing) { setPlaying(false); if (S.es) { S.es.close(); S.es = null; } await seek(currentMinute()); }
    else { setPlaying(true); await seek(currentMinute()); }
  });
  $("speed").addEventListener("change", (e) => { S.speed = +e.target.value; seek(currentMinute()); });
  $("track").addEventListener("click", (e) => {
    const rc = $("track").getBoundingClientRect();
    seek(((e.clientX - rc.left) / rc.width) * S.match.length_minutes);
  });
  $("track").addEventListener("keydown", (e) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (step) { e.preventDefault(); seek(Math.max(0, currentMinute() + step * 2)); }
  });

  const segment = (id, attr, key) =>
    document.querySelectorAll(`#${id} button`).forEach((b) =>
      b.addEventListener("click", () => {
        S[key] = b.dataset[attr];
        document.querySelectorAll(`#${id} button`).forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", x === b); });
        if (key === "lang") fillFollowSelect();
        refreshCards(currentMinute(), true);
        if (!$("drawer").hidden) loadMissed();
      }));
  segment("persona", "p", "persona");
  segment("lang", "l", "lang");

  // ------------------------------------------------------------ what did I miss?
  let awayFor = 10, lastFocus = null;
  async function loadMissed() {
    const params = withSession({ away: awayFor, persona: S.persona, lang: S.lang });
    if (S.follow) params.follow = S.follow;
    const res = await api("/api/catchup", params);
    const ul = $("missedLines"); ul.innerHTML = "";
    const [head, ...rest] = res.text.lines;
    $("missedWindow").textContent = head;
    for (const line of rest) { const li = document.createElement("li"); li.textContent = line; ul.appendChild(li); }
    $("missedSource").textContent = res.text.source === "agent"
      ? "Written by Full Pitch agents on Microsoft Foundry from measured match data."
      : "Built straight from measured match data.";
  }
  function openDrawer() { lastFocus = document.activeElement; $("drawer").hidden = false; $("drawerClose").focus(); loadMissed(); }
  function closeDrawer() { $("drawer").hidden = true; if (lastFocus) lastFocus.focus(); }
  $("missedBtn").addEventListener("click", openDrawer);
  $("drawerClose").addEventListener("click", closeDrawer);
  $("drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") closeDrawer(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("drawer").hidden) closeDrawer(); });
  document.querySelectorAll("#awayFor button").forEach((b) =>
    b.addEventListener("click", () => {
      awayFor = +b.dataset.min;
      document.querySelectorAll("#awayFor button").forEach((x) => x.classList.toggle("on", x === b));
      loadMissed();
    }));

  init();
})();
