// Full Pitch web client: live pitch, broadcast/full view, follow a player, insight cards, catch-up.
(() => {
  const L = 105, W = 68;
  const $ = (id) => document.getElementById(id);
  const cv = $("pitch"), g = cv.getContext("2d");

  const S = {
    match: null, lineup: null, colors: {}, shorts: [],
    frames: [], playhead: 0, playing: true, speed: 8, es: null, startMinute: 0,
    view: "full", zoom: 1, cam: { x: L / 2, y: W / 2 },
    follow: null, followStats: null, persona: "casual", lang: "en",
    cards: [], seenCards: new Set(), score: [0, 0], lastCardMinute: -1, lastStatMinute: -1,
  };

  // ------------------------------------------------------------ data
  const api = (path, params = {}) =>
    fetch(path + "?" + new URLSearchParams(params)).then((r) => r.json());

  async function init() {
    S.match = await api("/api/match");
    S.shorts = S.match.teams.map((t) => t.short);
    S.colors = S.match.team_colors;
    document.documentElement.style.setProperty("--home", S.colors[S.shorts[0]]);
    document.documentElement.style.setProperty("--away", S.colors[S.shorts[1]]);
    for (const [el, k] of [["home", 0], ["away", 1]]) {
      $(el).querySelector("b").textContent = S.shorts[k];
      $(el).querySelector("i").style.background = S.colors[S.shorts[k]];
      $(el).title = S.match.teams[k].name;
    }
    const start = +(new URLSearchParams(location.search).get("t") || 0);
    await seek(start);
    requestAnimationFrame(loop);
  }

  async function seek(minute) {
    if (S.es) S.es.close();
    S.frames = []; S.playhead = 0; S.startMinute = minute;
    S.lastCardMinute = -1; S.lastStatMinute = -1;
    const st = await api("/api/state", { at: minute });
    S.score = S.shorts.map((s) => st.score[s]);
    S.lineup = await api("/api/lineup", { at: minute });
    renderScore();
    refreshCards(minute);
    if (S.playing) openStream(minute);
  }

  function openStream(minute) {
    if (S.es) S.es.close();
    const es = new EventSource("/api/stream?" + new URLSearchParams({ from: minute, speed: S.speed }));
    es.addEventListener("frames", (e) => {
      for (const fr of JSON.parse(e.data)) S.frames.push(fr);
    });
    es.addEventListener("match_event", (e) => onEvent(JSON.parse(e.data)));
    es.addEventListener("changes", () => refreshCards(currentMinute()));
    es.addEventListener("end", () => es.close());
    S.es = es;
  }

  function onEvent(ev) {
    if (ev.type === "goal" && ev.score) {
      S.score = S.shorts.map((s) => ev.score[S.shorts.indexOf(s)]);
      renderScore();
      setTimeout(() => refreshCards(ev.minute + 0.01), 50);
    }
    if (ev.type === "substitution") api("/api/lineup", { at: ev.minute + 0.01 }).then((l) => (S.lineup = l));
  }

  async function refreshCards(minute, force = false) {
    const m = Math.floor(minute);
    if (!force && m === S.lastCardMinute) return;
    S.lastCardMinute = m;
    S.cards = await api("/api/cards", { at: minute, persona: S.persona, lang: S.lang });
    renderCards();
    renderMarks();
  }

  // ------------------------------------------------------------ playback
  function currentFrame() {
    if (!S.frames.length) return null;
    const i = Math.min(S.frames.length - 1, Math.max(0, S.playhead));
    const a = S.frames[Math.floor(i)], b = S.frames[Math.min(S.frames.length - 1, Math.floor(i) + 1)];
    const t = i - Math.floor(i);
    const xy = a.xy.map((v, j) => v + (b.xy[j] - v) * t);
    return { ...a, xy };
  }
  const currentMinute = () => (S.frames.length ? currentFrame().m : S.startMinute);

  let last = performance.now();
  function loop(now) {
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (S.playing && S.frames.length) {
      const hz = S.match.hz;
      S.playhead = Math.min(S.frames.length - 1.001, S.playhead + dt * hz * S.speed);
      // stay close to live edge if the stream ran ahead
      if (S.frames.length - S.playhead > hz * S.speed * 2) S.playhead = S.frames.length - hz * S.speed;
    }
    const fr = currentFrame();
    if (fr) {
      draw(fr);
      $("clock").textContent = fr.c;
      $("fill").style.width = (100 * fr.m) / S.match.length_minutes + "%";
      const m = Math.floor(fr.m);
      if (S.follow && m !== S.lastStatMinute) {
        S.lastStatMinute = m;
        loadFollowStats(fr.m);
      }
    }
    requestAnimationFrame(loop);
  }

  // ------------------------------------------------------------ drawing
  function transform() {
    const base = Math.min(cv.width / (L + 6), cv.height / (W + 6));
    const target = S.view === "tv" ? 2.7 : 1;
    S.zoom += (target - S.zoom) * 0.06;
    const k = base * S.zoom;
    const vw = cv.width / k, vh = cv.height / k;
    const fx = S.zoom > 1.01 ? S.cam.x : L / 2, fy = S.zoom > 1.01 ? S.cam.y : W / 2;
    // blend from pitch centre to ball as we zoom in
    const z = (S.zoom - 1) / 1.7;
    const cx = L / 2 + (fx - L / 2) * z, cy = W / 2 + (fy - W / 2) * z;
    const ox = Math.max(-3, Math.min(L + 3 - vw, cx - vw / 2));
    const oy = Math.max(-3, Math.min(W + 3 - vh, W - cy - vh / 2));
    return {
      k, base, X: (x) => (x - ox) * k, Y: (y) => (W - y - oy) * k,
      inView: (x, y) => x >= ox && x <= ox + vw && W - y >= oy && W - y <= oy + vh,
    };
  }

  function formationAt(team, t) {
    let fm = S.match.teams[team].starting_formation;
    for (const x of S.match.formation_log) if (x.team === team && x.t <= t) fm = x.formation;
    return S.match.formations[fm];
  }

  function draw(fr) {
    const P = (i) => [fr.xy[i * 2], fr.xy[i * 2 + 1]];
    const ball = P(22);
    S.cam.x += (ball[0] - S.cam.x) * 0.08;
    S.cam.y += (ball[1] - S.cam.y) * 0.08;
    const v = transform();

    // grass + markings
    g.fillStyle = "#1b5631"; g.fillRect(0, 0, cv.width, cv.height);
    g.fillStyle = "#1e5d35";
    for (let i = 0; i < 10; i += 2) g.fillRect(v.X(i * 10.5), v.Y(W), 10.5 * v.k, W * v.k);
    g.strokeStyle = "rgba(255,255,255,.78)"; g.lineWidth = Math.max(1.5, v.k * 0.12);
    const rect = (x, y, w, h) => g.strokeRect(v.X(x), v.Y(y + h), w * v.k, h * v.k);
    rect(0, 0, L, W);
    g.beginPath(); g.moveTo(v.X(L / 2), v.Y(0)); g.lineTo(v.X(L / 2), v.Y(W)); g.stroke();
    g.beginPath(); g.arc(v.X(L / 2), v.Y(W / 2), 9.15 * v.k, 0, 7); g.stroke();
    rect(0, W / 2 - 20.16, 16.5, 40.32); rect(L - 16.5, W / 2 - 20.16, 16.5, 40.32);
    rect(0, W / 2 - 9.16, 5.5, 18.32); rect(L - 5.5, W / 2 - 9.16, 5.5, 18.32);
    g.fillStyle = "rgba(255,255,255,.85)";
    g.fillRect(v.X(-1.2), v.Y(W / 2 + 3.66), 1.2 * v.k, 7.32 * v.k);
    g.fillRect(v.X(L), v.Y(W / 2 + 3.66), 1.2 * v.k, 7.32 * v.k);

    // team shape: connect each line of the formation
    if ($("shapes").checked) {
      for (let k = 0; k < 2; k++) {
        const fm = formationAt(k, fr.t);
        const groups = fr.p === k ? fm.att_groups : fm.def_groups;
        const lines = {};
        for (let s = 1; s < 11; s++) (lines[groups[s]] = lines[groups[s]] || []).push(P(k * 11 + s));
        g.strokeStyle = S.colors[S.shorts[k]]; g.globalAlpha = 0.6; g.lineWidth = Math.max(2, v.base * 0.28 * (1 + (S.zoom - 1) * 0.4));
        g.lineCap = "round"; g.lineJoin = "round";
        for (const key in lines) {
          const pts = lines[key].sort((a, b) => a[1] - b[1]);
          if (pts.length < 2) continue;
          g.beginPath();
          pts.forEach((p, i) => (i ? g.lineTo(v.X(p[0]), v.Y(p[1])) : g.moveTo(v.X(p[0]), v.Y(p[1]))));
          g.stroke();
        }
        g.globalAlpha = 1;
      }
    }

    // followed player's trail (last 12 s)
    if (S.follow) {
      const [k, s] = S.follow, idx = k * 11 + s, i0 = Math.floor(S.playhead);
      g.strokeStyle = "rgba(215,255,58,.85)"; g.lineWidth = Math.max(2, v.k * 0.22);
      g.beginPath();
      for (let i = Math.max(0, i0 - S.match.hz * 12), n = 0; i <= i0; i++, n++) {
        const f2 = S.frames[i]; if (!f2) continue;
        const x = v.X(f2.xy[idx * 2]), y = v.Y(f2.xy[idx * 2 + 1]);
        n ? g.lineTo(x, y) : g.moveTo(x, y);
      }
      g.stroke();
    }

    // players
    const r = Math.max(7, v.base * 0.95 * (1 + (S.zoom - 1) * 0.45));
    let visible = 0;
    for (let k = 0; k < 2; k++) {
      for (let s = 0; s < 11; s++) {
        const [x, y] = P(k * 11 + s);
        if (v.inView(x, y)) visible++;
        const me = S.follow && S.follow[0] === k && S.follow[1] === s;
        g.beginPath(); g.arc(v.X(x), v.Y(y), me ? r * 1.25 : r, 0, 7);
        g.fillStyle = s === 0 ? "#1d1f1e" : S.colors[S.shorts[k]]; g.fill();
        g.lineWidth = me ? 3 : 1.4; g.strokeStyle = me ? "#d7ff3a" : "rgba(255,255,255,.9)"; g.stroke();
        const pl = S.lineup && S.lineup[S.shorts[k]][s];
        if (pl) {
          g.fillStyle = "#fff"; g.font = `600 ${Math.round(r * 1.05)}px Inter, sans-serif`;
          g.textAlign = "center"; g.textBaseline = "middle";
          g.fillText(pl.number, v.X(x), v.Y(y) + 0.5);
          if (me) {
            g.font = `600 ${Math.round(r * 1.1)}px Inter, sans-serif`;
            g.fillStyle = "rgba(11,15,13,.8)";
            const label = pl.name, tw = g.measureText(label).width + 14;
            g.fillRect(v.X(x) - tw / 2, v.Y(y) - r * 3.1, tw, r * 1.7);
            g.fillStyle = "#d7ff3a"; g.fillText(label, v.X(x), v.Y(y) - r * 2.25);
          }
        }
      }
    }
    S.visible = visible;
    // ball
    g.beginPath(); g.arc(v.X(ball[0]), v.Y(ball[1]), Math.max(4, v.k * 0.5), 0, 7);
    g.fillStyle = "#fff"; g.fill(); g.lineWidth = 1.2; g.strokeStyle = "#000"; g.stroke();
    S._v = v; S._fr = fr;
  }

  // ------------------------------------------------------------ UI
  function renderScore() {
    $("score").textContent = `${S.score[0]}–${S.score[1]}`;
  }

  function cardKey(c) { return c.kind + c.team + c.subtitle; }

  function renderCards() {
    const el = $("cards");
    if (!S.cards.length) {
      el.innerHTML = `<p class="empty">${S.lang === "hu" ? "A meccs elemzései itt jelennek meg." : "Insights appear here as the match unfolds."}</p>`;
      return;
    }
    const sorted = [...S.cards].sort((a, b) => b.minute - a.minute);
    el.innerHTML = "";
    for (const c of sorted) {
      const d = document.createElement("article");
      d.className = "card " + c.kind;
      d.style.setProperty("--c", S.colors[c.team] || "#888");
      const kind = c.kind === "goal" ? (S.lang === "hu" ? "Gól" : "Goal") : (S.lang === "hu" ? "Taktikai változás" : "Shape change");
      d.innerHTML = `<div class="k"><span>${kind}</span><span>${c.subtitle || ""}</span></div>
        <h3></h3><p></p>${c.source === "agent" ? '<div class="src">Explained by Full Pitch agents</div>' : ""}`;
      d.querySelector("h3").textContent = c.title;
      d.querySelector("p").textContent = c.body;
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
      m.style.color = S.colors[c.team]; m.style.background = c.kind === "goal" ? S.colors[c.team] : "";
      m.title = `${c.subtitle} ${c.title}`;
      el.appendChild(m);
    }
  }

  async function loadFollowStats(minute) {
    const [k, s] = S.follow;
    const pl = S.lineup[S.shorts[k]][s];
    const rep = await api("/api/player", { id: pl.id, at: minute });
    S.followStats = rep;
    renderFollow();
  }

  function renderFollow() {
    const box = $("followCard");
    if (!S.follow || !S.followStats) { box.hidden = true; return; }
    const [k, s] = S.follow, pl = S.lineup[S.shorts[k]][s], st = S.followStats.physical || {};
    const fr = S._fr, i0 = Math.floor(S.playhead), prev = S.frames[Math.max(0, i0 - S.match.hz)];
    const idx = k * 11 + s;
    const speed = prev ? Math.hypot(fr.xy[idx * 2] - prev.xy[idx * 2], fr.xy[idx * 2 + 1] - prev.xy[idx * 2 + 1]) * 3.6 : 0;
    box.hidden = false;
    box.innerHTML = `<div class="n">#${pl.number} ${pl.name} <span style="color:${S.colors[S.shorts[k]]}">${S.shorts[k]}</span></div>
      <div class="row">
        <div><small>Now</small><b class="${speed > 25 ? "sprint" : ""}">${speed.toFixed(0)} km/h</b></div>
        <div><small>Distance</small><b>${((st.distance_m || 0) / 1000).toFixed(1)} km</b></div>
        <div><small>Sprints</small><b>${st.sprints || 0}</b></div>
        <div><small>Top</small><b>${(st.top_speed_kmh || 0).toFixed(0)}</b></div>
      </div>`;
  }
  setInterval(() => S.follow && S.followStats && renderFollow(), 400);

  let hookTimer;
  function hook(text) {
    const h = $("hook"); h.textContent = text; h.classList.add("show");
    clearTimeout(hookTimer); hookTimer = setTimeout(() => h.classList.remove("show"), 2600);
  }

  document.querySelectorAll(".view-toggle button").forEach((b) =>
    b.addEventListener("click", () => {
      S.view = b.dataset.view;
      document.querySelectorAll(".view-toggle button").forEach((x) => x.classList.toggle("on", x === b));
      if (S.view === "tv") setTimeout(() => hook(`On TV, you see ${S.visible} players.`), 900);
      else hook("The match has twenty-two.");
    }));

  cv.addEventListener("click", (e) => {
    if (!S._v || !S._fr) return;
    const rc = cv.getBoundingClientRect();
    const mx = ((e.clientX - rc.left) * cv.width) / rc.width, my = ((e.clientY - rc.top) * cv.height) / rc.height;
    let best = null, bd = 1e9;
    for (let k = 0; k < 2; k++) for (let s = 0; s < 11; s++) {
      const x = S._v.X(S._fr.xy[(k * 11 + s) * 2]), y = S._v.Y(S._fr.xy[(k * 11 + s) * 2 + 1]);
      const d = Math.hypot(x - mx, y - my);
      if (d < bd) { bd = d; best = [k, s]; }
    }
    S.follow = bd < 40 ? best : null;
    S.followStats = null; S.lastStatMinute = -1;
    $("hint").hidden = !!S.follow;
    if (!S.follow) $("followCard").hidden = true;
  });

  $("play").addEventListener("click", () => {
    S.playing = !S.playing;
    $("play").textContent = S.playing ? "❚❚" : "▶";
    $("live").classList.toggle("paused", !S.playing);
    if (S.playing) { const m = currentMinute(); seek(m); } else if (S.es) S.es.close();
  });
  $("speed").addEventListener("change", (e) => { S.speed = +e.target.value; seek(currentMinute()); });
  $("track").addEventListener("click", (e) => {
    const rc = $("track").getBoundingClientRect();
    seek(((e.clientX - rc.left) / rc.width) * S.match.length_minutes);
  });

  const segment = (id, attr, key) =>
    document.querySelectorAll(`#${id} button`).forEach((b) =>
      b.addEventListener("click", () => {
        S[key] = b.dataset[attr];
        document.querySelectorAll(`#${id} button`).forEach((x) => x.classList.toggle("on", x === b));
        refreshCards(currentMinute(), true);
        if (!$("drawer").hidden) loadMissed();
      }));
  segment("persona", "p", "persona");
  segment("lang", "l", "lang");

  // ------------------------------------------------------------ what did I miss?
  let awayFor = 10;
  async function loadMissed() {
    const now = currentMinute();
    const params = { from: Math.max(0, now - awayFor), to: now, persona: S.persona, lang: S.lang };
    if (S.follow) params.follow = S.lineup[S.shorts[S.follow[0]]][S.follow[1]].id;
    const res = await api("/api/catchup", params);
    const ol = $("missedLines"); ol.innerHTML = "";
    const [head, ...rest] = res.text.lines;
    $("missedWindow").textContent = head;
    for (const line of rest) { const li = document.createElement("li"); li.textContent = line; ol.appendChild(li); }
    $("missedSource").textContent = res.text.source === "agent"
      ? "Written by Full Pitch agents on Microsoft Foundry from measured match data."
      : "Built straight from measured match data.";
  }
  $("missedBtn").addEventListener("click", () => { $("drawer").hidden = false; loadMissed(); });
  $("drawerClose").addEventListener("click", () => ($("drawer").hidden = true));
  $("drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") $("drawer").hidden = true; });
  document.querySelectorAll("#awayFor button").forEach((b) =>
    b.addEventListener("click", () => {
      awayFor = +b.dataset.min;
      document.querySelectorAll("#awayFor button").forEach((x) => x.classList.toggle("on", x === b));
      loadMissed();
    }));

  init();
})();
