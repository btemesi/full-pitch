// Full Pitch web client: live pitch, broadcast/full view, follow a player, insight cards, stats,
// player profile, replays with drawn explanations, catch-up. The live clock lives on the server.
(() => {
  const L = 105, W = 68, PAD = 3;
  const $ = (id) => document.getElementById(id);
  const cv = $("pitch"), g = cv.getContext("2d");
  const SVGNS = "http://www.w3.org/2000/svg";

  const S = {
    match: null, sid: null, lineup: null, colors: {}, shorts: [],
    frames: [], playhead: 0, playing: true, speed: 8, es: null,
    view: "full", zoom: 1, cam: { x: L / 2, y: W / 2 }, portrait: false, dpr: 1,
    follow: null, followStats: null, persona: "casual", lang: "en", tab: "insights",
    cards: [], seenCards: new Set(), score: [0, 0], lastCardMinute: -1, lastStatMinute: -1, cardReq: 0,
    still: null, replay: null, resumeMinute: null, lastPanelMinute: -1, pnTeam: null, pnWindow: "match",
  };
  const hu = () => S.lang === "hu";

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
    S.pnTeam = S.shorts[0];
    for (const [el, k] of [["home", 0], ["away", 1]]) {
      $(el).querySelector("b").textContent = S.shorts[k];
      $(el).querySelector(".crest").appendChild(crest(S.shorts[k], S.colors[S.shorts[k]], k));
      $(el).title = S.match.teams[k].name;
    }
    const pn = $("pnTeam");
    S.shorts.forEach((s, k) => {
      const b = document.createElement("button"); b.textContent = s; b.dataset.t = s;
      if (k === 0) b.classList.add("on");
      b.addEventListener("click", () => { S.pnTeam = s; pn.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b)); loadPassNet(); });
      pn.appendChild(b);
    });
    const qs = new URLSearchParams(location.search);
    if (qs.get("present")) togglePresent(true);
    S.sid = (await api("/api/session", { from: +(qs.get("t") || 0) }, "POST")).session;
    resize();
    await afterJump();
    requestAnimationFrame(loop);
  }

  // A fictional crest: a shield in the team colour with the club's initial.
  function crest(short, color, k) {
    const svg = document.createElementNS(SVGNS, "svg");
    svg.setAttribute("viewBox", "0 0 22 26");
    svg.innerHTML = `<path d="M11 1 L21 4 V12 C21 19 16 23 11 25 C6 23 1 19 1 12 V4 Z" fill="${color}" stroke="#fff" stroke-width="1.2"/>
      <path d="M1 ${k ? 12 : 9} L21 ${k ? 6 : 15}" stroke="rgba(255,255,255,.35)" stroke-width="2"/>
      <text x="11" y="17" text-anchor="middle" font-family="Barlow Condensed, sans-serif" font-weight="700" font-size="11" fill="#fff">${short[0]}</text>`;
    return svg;
  }

  async function afterJump() {
    S.frames = []; S.playhead = 0;
    S.lastCardMinute = -1; S.lastStatMinute = -1; S.lastPanelMinute = -1; S.cardSig = null;
    const [st, lineup, still] = await Promise.all([
      api("/api/state", withSession()), api("/api/lineup", withSession()), api("/api/frame", withSession()),
    ]);
    S.score = S.shorts.map((s) => st.score[s]);
    S.lineup = lineup; S.still = still;
    renderScore(); fillFollowSelect();
    refreshCards(still.m, true);
    refreshPanel(true);
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
      S.score = ev.score.slice(); renderScore();
      setTimeout(() => refreshCards(ev.minute, true), 50);
    }
    if (ev.type === "card" && ev.card === "red") setTimeout(() => { refreshCards(ev.minute, true); refreshLineup(); }, 50);
    if (ev.type === "substitution") setTimeout(() => refreshLineup(ev), 50);
    if (ev.type === "period_end") setTimeout(() => refreshCards(ev.minute, true), 300);
  }

  async function refreshLineup(subEvent) {
    const before = S.follow;
    S.lineup = await api("/api/lineup", withSession());
    fillFollowSelect();
    if (before && !slotOf(before)) {
      const name = playerName(before);
      setFollow(null);
      toast(subEvent && subEvent.off === name
        ? (hu() ? `${name} lecserélték, helyette ${subEvent.on} jött be.` : `${name} was substituted. ${subEvent.on} came on.`)
        : (hu() ? `${name} már nincs a pályán.` : `${name} has left the pitch.`));
    }
  }

  async function refreshCards(minute, force = false) {
    const m = Math.floor(minute);
    if (!force && m === S.lastCardMinute) return;
    S.lastCardMinute = m;
    const req = ++S.cardReq;
    const cards = await api("/api/cards", withSession({ persona: S.persona, lang: S.lang }));
    if (req !== S.cardReq) return;
    const fresh = cards.filter((c) => !S.seenCards.has(cardKey(c)));
    const sig = JSON.stringify(cards.map((c) => [c.kind, c.minute, c.title, c.body])) + S.persona + S.lang + !!S.follow;
    S.cards = cards;
    if (sig === S.cardSig) return;  // nothing new: keep the DOM so clicks don't land on a rebuilt list
    S.cardSig = sig;
    renderCards(); renderMarks();
    // announce new moments broadcast-style, but not the backlog after a jump
    if (fresh.length && S.frames.length && !S.replay) {
      const c = fresh.sort((a, b) => b.minute - a.minute)[0];
      lowerThird(c);
    }
  }

  // ------------------------------------------------------------ playback
  function liveFrame() {
    if (!S.frames.length) return S.still;
    const i = Math.min(S.frames.length - 1, Math.max(0, S.playhead));
    const a = S.frames[Math.floor(i)], b = S.frames[Math.min(S.frames.length - 1, Math.floor(i) + 1)];
    const t = i - Math.floor(i);
    return { ...a, xy: a.xy.map((v, j) => v + (b.xy[j] - v) * t) };
  }
  function currentFrame() {
    if (S.replay) {
      const R = S.replay, i = Math.min(R.frames.length - 1, R.i);
      const a = R.frames[Math.floor(i)], b = R.frames[Math.min(R.frames.length - 1, Math.floor(i) + 1)];
      const t = i - Math.floor(i);
      return { ...a, xy: a.xy.map((v, j) => v + (b.xy[j] - v) * t) };
    }
    return liveFrame();
  }
  const currentMinute = () => { const f = liveFrame(); return f ? f.m : 0; };
  const framesNow = () => (S.replay ? S.replay.frames : S.frames);
  const headNow = () => (S.replay ? S.replay.i : S.playhead);

  let last = performance.now();
  function loop(now) {
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    const hz = S.match.hz;
    if (S.replay) {
      S.replay.i += dt * hz * 1.5;
      if (S.replay.i >= S.replay.frames.length - 1) {
        S.replay.i = S.replay.frames.length - 1;
        if (!S.replay.doneAt) S.replay.doneAt = now;
        if (now - S.replay.doneAt > 1800) S.replay.i = 0, S.replay.doneAt = null;  // loop the moment
      }
    } else if (S.playing && S.frames.length) {
      S.playhead = Math.min(S.frames.length - 1.001, S.playhead + dt * hz * S.speed);
      if (S.frames.length - S.playhead > hz * S.speed * 2) S.playhead = S.frames.length - hz * S.speed;
    }
    const fr = currentFrame();
    if (fr) {
      draw(fr);
      $("clock").textContent = fr.c;
      if (!S.replay) {
        $("fill").style.width = (100 * fr.m) / S.match.length_minutes + "%";
        const m = Math.floor(fr.m);
        if (S.follow && m !== S.lastStatMinute) { S.lastStatMinute = m; loadFollowStats(); }
        if (m !== S.lastPanelMinute) { S.lastPanelMinute = m; refreshPanel(); }
      }
    }
    requestAnimationFrame(loop);
  }

  // ------------------------------------------------------------ geometry
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
    if (document.body.classList.contains("present")) cssH = Math.min(cssH, window.innerHeight - 90);  // whole pitch on screen
    cv.style.height = cssH + "px";
    cv.width = Math.round(cssW * S.dpr);
    cv.height = Math.round(cssH * S.dpr);
  }
  window.addEventListener("resize", resize);

  function transform() {
    const [dw, dh] = screenDims();
    const base = Math.min(cv.width / (dw + 2 * PAD), cv.height / (dh + 2 * PAD));
    S.zoom += ((S.view === "tv" && !S.replay ? 2.7 : 1) - S.zoom) * 0.06;
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
    const ov = S.replay && S.replay.overlay;
    const ri = S.replay ? Math.min(S.replay.frames.length - 1, Math.floor(S.replay.i)) : 0;

    // replay: ghost lines (where the back line would stand with the old instruction vs where it stands now)
    if (ov && ov.type === "change" && ov.ghost) {
      for (const [label, xs, dash, alpha] of [["before", ov.ghost.before, [8 * d, 6 * d], 0.75], ["now", ov.ghost.now, [], 1]]) {
        if (!xs || !xs.length) continue;
        const x = xs[Math.min(xs.length - 1, ri)];
        const [a1, b1] = v.P(x, -1), [a2, b2] = v.P(x, W + 1);
        g.save(); g.setLineDash(dash); g.globalAlpha = alpha;
        g.strokeStyle = label === "now" ? "#d7ff3a" : "#ffffff"; g.lineWidth = 3 * d;
        g.beginPath(); g.moveTo(a1, b1); g.lineTo(a2, b2); g.stroke(); g.restore();
        g.font = `700 ${13 * d}px Inter, sans-serif`; g.fillStyle = label === "now" ? "#d7ff3a" : "#fff";
        g.textAlign = "center"; g.textBaseline = "bottom";
        const [lx, ly] = v.P(x, W + 0.5);
        g.fillText(label === "now" ? (hu() ? "MOST" : "NOW") : (hu() ? "KORÁBBAN" : "BEFORE"), lx, ly);
      }
    }
    if (ov && ov.type === "change" && ov.channel) {
      const k = ov.team_index, period = fr.t >= 2700 ? 2 : 1;
      const plusX = (k === 0) === (period === 1);
      const yLeft = plusX ? W - 12 : 12;  // the team's left touchline side
      g.save(); g.globalAlpha = 0.18; g.fillStyle = "#d7ff3a";
      g.fillRect(...rect(plusX ? 52.5 : 0, yLeft - 12, plusX ? L : 52.5, yLeft + 12)); g.restore();
    }

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

    const frames = framesNow(), head = headNow();
    // the ball's path over the last 1.5 s
    if (frames.length > 2) {
      const i0 = Math.floor(head);
      g.strokeStyle = "rgba(255,255,255,.35)"; g.lineWidth = 2 * d; g.beginPath();
      for (let i = Math.max(0, i0 - 8), n = 0; i <= i0; i++, n++) {
        const f2 = frames[i]; if (!f2) continue;
        const [x, y] = v.P(f2.xy[44], f2.xy[45]); n ? g.lineTo(x, y) : g.moveTo(x, y);
      }
      g.stroke();
    }
    const fslot = S.follow ? slotOf(S.follow) : null;
    if (fslot && frames.length) {
      const idx = fslot[0] * 11 + fslot[1], i0 = Math.floor(head);
      g.strokeStyle = "rgba(215,255,58,.85)"; g.lineWidth = Math.max(2 * d, v.k * 0.22);
      g.beginPath();
      for (let i = Math.max(0, i0 - S.match.hz * 12), n = 0; i <= i0; i++, n++) {
        const f2 = frames[i]; if (!f2) continue;
        const [x, y] = v.P(f2.xy[idx * 2], f2.xy[idx * 2 + 1]);
        n ? g.lineTo(x, y) : g.moveTo(x, y);
      }
      g.stroke();
    }

    const prev = frames.length ? frames[Math.max(0, Math.floor(head) - 3)] : null;
    const r = Math.min(15 * d, Math.max(6.5 * d, v.base * 0.95)) * (1 + (S.zoom - 1) * 0.45);
    const ringed = ov && ov.ring ? new Set(ov.ring[Math.min(ov.ring.length - 1, ri)] || []) : null;
    let visible = 0;
    for (let k = 0; k < 2; k++) for (let s = 0; s < 11; s++) {
      if (!onPitch(k, s)) continue;
      const [wx, wy] = Pw(k * 11 + s);
      if (v.inView(wx, wy)) visible++;
      const [x, y] = v.P(wx, wy);
      const me = fslot && fslot[0] === k && fslot[1] === s;
      // direction of travel: a small notch on the rim when moving faster than a walk
      if (prev) {
        const dx = wx - prev.xy[(k * 11 + s) * 2], dy = wy - prev.xy[(k * 11 + s) * 2 + 1];
        const sp = Math.hypot(dx, dy) / (3 / S.match.hz);
        if (sp > 2.2) {
          const [tx, ty] = v.P(wx + dx / Math.hypot(dx, dy), wy + dy / Math.hypot(dx, dy));
          const ang = Math.atan2(ty - y, tx - x), rr = r * 1.55;
          g.beginPath();
          g.moveTo(x + Math.cos(ang) * rr, y + Math.sin(ang) * rr);
          g.lineTo(x + Math.cos(ang + 0.5) * r * 0.95, y + Math.sin(ang + 0.5) * r * 0.95);
          g.lineTo(x + Math.cos(ang - 0.5) * r * 0.95, y + Math.sin(ang - 0.5) * r * 0.95);
          g.closePath(); g.fillStyle = "rgba(255,255,255,.85)"; g.fill();
        }
      }
      if (ringed && ov.team_index === k && ringed.has(s)) {
        g.beginPath(); g.arc(x, y, r * 1.75, 0, 7); g.strokeStyle = "#d7ff3a"; g.lineWidth = 2.5 * d; g.stroke();
      }
      g.beginPath(); g.arc(x, y, me ? r * 1.25 : r, 0, 7);
      g.fillStyle = s === 0 ? "#1d1f1e" : S.colors[S.shorts[k]]; g.fill();
      g.lineWidth = (me ? 3 : 1.4) * d; g.strokeStyle = me ? "#d7ff3a" : "rgba(255,255,255,.9)"; g.stroke();
      const pl = S.lineup && S.lineup[S.shorts[k]][s];
      if (!pl) continue;
      g.fillStyle = "#fff"; g.font = `600 ${Math.round(r * 1.02)}px Inter, sans-serif`;
      g.textAlign = "center"; g.textBaseline = "middle";
      g.fillText(pl.number, x, y + 0.5);
      if (me) label(pl.name, x, y - r * 1.4, "#d7ff3a", d, r);
    }
    S.visible = visible;
    // ball with a soft shadow
    const [bxp, byp] = v.P(ball[0], ball[1]);
    const br = Math.max(3.5 * d, v.k * 0.5);
    g.beginPath(); g.ellipse(bxp + br * 0.5, byp + br * 0.7, br * 1.1, br * 0.6, 0, 0, 7); g.fillStyle = "rgba(0,0,0,.35)"; g.fill();
    g.beginPath(); g.arc(bxp, byp, br, 0, 7); g.fillStyle = "#fff"; g.fill(); g.lineWidth = 1.2 * d; g.strokeStyle = "#000"; g.stroke();
    if (ov && ov.type === "goal" && ov.xg != null) label(`xG ${ov.xg.toFixed(2)}`, bxp, byp - br * 2, "#fff", d, r);
    S._v = v; S._fr = fr;
  }

  function label(text, x, y, color, d, r) {
    g.font = `600 ${Math.round(Math.max(11 * d, r * 1.05))}px Inter, sans-serif`;
    const tw = g.measureText(text).width + 14 * d, th = Math.max(18 * d, r * 1.7);
    g.fillStyle = "rgba(11,15,13,.82)"; g.fillRect(x - tw / 2, y - th, tw, th);
    g.fillStyle = color; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(text, x, y - th / 2);
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
  function playerName(pid) { const p = S.match.players.find((x) => x.id === pid); return p ? p.name : pid; }

  function fillFollowSelect() {
    const sel = $("followSelect"), keep = S.follow || "";
    sel.innerHTML = "";
    const first = document.createElement("option"); first.value = ""; first.textContent = hu() ? "Játékos követése…" : "Follow a player…";
    sel.appendChild(first);
    for (let k = 0; k < 2; k++) {
      const og = document.createElement("optgroup"); og.label = S.match.teams[k].name;
      for (const p of S.lineup[S.shorts[k]]) {
        if (p.on_pitch === false) continue;
        const o = document.createElement("option"); o.value = p.id; o.textContent = `#${p.number} ${p.name}`;
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
    if (S.tab === "player") refreshPanel(true);
  }

  async function loadFollowStats() {
    if (!S.follow) return;
    const pid = S.follow;
    try { const rep = await api("/api/player", withSession({ id: pid })); if (S.follow === pid) { S.followStats = rep; renderFollow(); } } catch (e) { /* left the pitch */ }
  }

  function renderFollow() {
    const box = $("followCard"), fs = S.follow && slotOf(S.follow);
    if (!fs || !S.followStats || S.replay) { box.hidden = true; return; }
    const [k, s] = fs, pl = S.lineup[S.shorts[k]][s], st = S.followStats.physical || {};
    const fr = S._fr, i0 = Math.floor(S.playhead), prev = S.frames[Math.max(0, i0 - S.match.hz)];
    const idx = k * 11 + s;
    const speed = prev && fr ? Math.hypot(fr.xy[idx * 2] - prev.xy[idx * 2], fr.xy[idx * 2 + 1] - prev.xy[idx * 2 + 1]) * 3.6 : 0;
    box.hidden = false; box.innerHTML = "";
    const n = document.createElement("div"); n.className = "n"; n.textContent = `#${pl.number} ${pl.name} `;
    const t = document.createElement("span"); t.style.color = S.colors[S.shorts[k]]; t.textContent = S.shorts[k]; n.appendChild(t);
    const row = document.createElement("div"); row.className = "row";
    for (const [lab, value, cls] of [[hu() ? "Most" : "Now", `${speed.toFixed(0)} km/h`, speed > 25 ? "sprint" : ""],
      [hu() ? "Táv" : "Distance", `${((st.distance_m || 0) / 1000).toFixed(1)} km`, ""],
      ["Sprint", `${st.sprints || 0}`, ""], [hu() ? "Csúcs" : "Top", `${(st.top_speed_kmh || 0).toFixed(0)}`, ""]]) {
      const c = document.createElement("div"); c.innerHTML = "<small></small><b></b>";
      c.querySelector("small").textContent = lab; c.querySelector("b").textContent = value;
      if (cls) c.querySelector("b").className = cls;
      row.appendChild(c);
    }
    box.append(n, row);
  }
  setInterval(() => S.follow && S.followStats && renderFollow(), 400);

  // ------------------------------------------------------------ insights feed
  function renderScore() { $("score").textContent = `${S.score[0]}–${S.score[1]}`; }
  const cardKey = (c) => c.kind + (c.team || "") + c.minute;

  function renderCards() {
    const el = $("cards");
    el.innerHTML = "";
    if (S.persona === "player" && !S.follow) {
      const p = document.createElement("p"); p.className = "empty";
      p.textContent = hu() ? "Koppints egy játékosra, vagy válaszd ki a listából, és az ő szemszögéből követed a meccset." : "Tap a player, or pick one from the list, to follow the match through their eyes.";
      el.appendChild(p);
    }
    if (!S.cards.length) {
      const p = document.createElement("p"); p.className = "empty";
      p.textContent = hu() ? "A meccs elemzései itt jelennek meg." : "Insights appear here as the match unfolds.";
      el.appendChild(p); return;
    }
    const kindLabel = { goal: hu() ? "Gól" : "Goal", change: hu() ? "Taktikai változás" : "Shape change", red: hu() ? "Kiállítás" : "Sending off", recap: hu() ? "Összefoglaló" : "Recap" };
    for (const c of [...S.cards].sort((a, b) => b.minute - a.minute)) {
      const d = document.createElement("article");
      const replayable = c.kind === "goal" || c.kind === "change";
      d.className = "card " + c.kind + (replayable ? " clickable" : "");
      d.style.setProperty("--c", S.colors[c.team] || "#888");
      d.innerHTML = '<div class="k"><span></span><span></span></div><h3></h3><p></p>';
      const [k1, k2] = d.querySelectorAll(".k span");
      k1.textContent = kindLabel[c.kind] || ""; k2.textContent = c.subtitle || "";
      d.querySelector("h3").textContent = c.title;
      d.querySelector("p").textContent = c.body;
      if (replayable) {
        const h = document.createElement("span"); h.className = "play-hint"; h.textContent = hu() ? "▶ Visszajátszás" : "▶ Replay";
        d.querySelector(".k span").after(h);
        d.tabIndex = 0; d.setAttribute("role", "button");
        const go = () => startReplay(c);
        d.addEventListener("click", go);
        d.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
      }
      if (c.source === "agent") { const s = document.createElement("div"); s.className = "src"; s.textContent = "Explained by Full Pitch agents"; d.appendChild(s); }
      if (S.seenCards.has(cardKey(c))) d.style.animation = "none";
      S.seenCards.add(cardKey(c));
      el.appendChild(d);
    }
  }

  function renderMarks() {
    const el = $("marks"); el.innerHTML = "";
    for (const c of S.cards) {
      if (c.kind === "recap") continue;
      const m = document.createElement("div");
      m.className = "mark " + c.kind;
      m.style.left = (100 * c.minute) / S.match.length_minutes + "%";
      m.style.color = S.colors[c.team];
      if (c.kind !== "change") m.style.background = c.kind === "red" ? "#ff4d4d" : S.colors[c.team];
      m.title = `${c.subtitle} ${c.title}`;
      el.appendChild(m);
    }
  }

  let ltTimer;
  function lowerThird(c) {
    const lt = $("lowerThird");
    lt.querySelector("i").style.background = S.colors[c.team] || "#d7ff3a";
    lt.querySelector("small").textContent = { goal: hu() ? "Gól" : "Goal", change: hu() ? "Taktikai változás" : "Shape change", red: hu() ? "Kiállítás" : "Sending off", recap: c.subtitle }[c.kind] || "";
    lt.querySelector("b").textContent = c.title;
    lt.hidden = false; lt.style.animation = "none"; void lt.offsetWidth; lt.style.animation = "";
    clearTimeout(ltTimer); ltTimer = setTimeout(() => (lt.hidden = true), 6500);
  }

  // ------------------------------------------------------------ replays
  async function startReplay(c) {
    const params = c.kind === "goal" ? { kind: "goal", minute: c.minute } : { kind: "change", minute: c.minute, team: c.team, signal: c.signal || "" };
    let rep;
    try { rep = await api("/api/replay", withSession(params)); } catch (e) { toast(hu() ? "Ez a pillanat még nem történt meg." : "That moment hasn't happened yet."); return; }
    if (!rep.frames.length) return;
    S.resumeMinute = currentMinute();
    if (S.es) { S.es.close(); S.es = null; }
    S.replay = { frames: rep.frames, overlay: rep.overlay, i: 0, doneAt: null };
    $("replayTitle").textContent = c.title;
    $("replayBar").hidden = false; $("followCard").hidden = true; $("lowerThird").hidden = true;
    $("live").classList.add("paused");
    if (c.kind === "goal" && rep.overlay.scorer) {
      toast(`${rep.overlay.scorer}${rep.overlay.assist ? (hu() ? ", gólpassz: " : ", assist: ") + rep.overlay.assist : ""}`);
    } else if (c.kind === "change") {
      const sig = c.signal;
      toast(sig === "n_back" || sig === "n_back_def"
        ? (hu() ? "Bekarikázva: a hátsó sor játékosai." : "Ringed: the players in the back line.")
        : sig === "pressers" ? (hu() ? "Bekarikázva: akik a labdára rontanak." : "Ringed: the players hunting the ball.")
          : sig === "left_share" ? (hu() ? "Kiemelve: a bal oldali folyosó." : "Highlighted: their left-hand channel.")
            : (hu() ? "Szaggatott: ahol a védővonal a korábbi utasítással állna. Sárga: ahol most áll." : "Dashed: where the back line would stand on the old instruction. Yellow: where it stands now."));
    }
  }
  async function endReplay() {
    if (!S.replay) return;
    S.replay = null; $("replayBar").hidden = true;
    $("live").classList.toggle("paused", !S.playing);
    await seek(S.resumeMinute);
  }
  $("replayBack").addEventListener("click", endReplay);

  // ------------------------------------------------------------ stats + player panels
  function refreshPanel() {
    const retry = (e) => { console.warn(e); S.lastPanelMinute = -1; };  // try again on the next minute tick
    if (S.tab === "stats") loadStats().catch(retry);
    if (S.tab === "player") loadProfile().catch(retry);
  }

  async function loadStats() {
    const st = await api("/api/stats", withSession());
    const [a, b] = S.shorts, A = st.teams[a], B = st.teams[b];
    const rows = [
      [hu() ? "Labdabirtoklás" : "Possession", A.possession_pct, B.possession_pct, (x) => x + "%"],
      [hu() ? "Lövések (kaput eltaláló)" : "Shots (on target)", A.shots, B.shots, null, (t) => `${t.shots} (${t.shots_on_target})`],
      ["xG", A.xg, B.xg, (x) => x.toFixed(2)],
      [hu() ? "Passzok" : "Passes", A.passes, B.passes],
      [hu() ? "Passzpontosság" : "Pass accuracy", A.pass_accuracy_pct, B.pass_accuracy_pct, (x) => x + "%"],
      [hu() ? "Labdabirtoklás a támadóharmadban" : "Possession in final third", A.possession_by_third_pct[2], B.possession_by_third_pct[2], (x) => x + "%"],
      [hu() ? "Letámadás (PPDA, kisebb = erősebb)" : "Pressing (PPDA, lower = harder)", A.ppda ?? 0, B.ppda ?? 0, (x) => x ? x.toFixed(1) : "–", null, true],
      [hu() ? "Szögletek" : "Corners", A.corners, B.corners],
      [hu() ? "Szabálytalanságok" : "Fouls", A.fouls, B.fouls],
      [hu() ? "Lesek" : "Offsides", A.offsides, B.offsides],
      [hu() ? "Sárga / piros lap" : "Yellow / red cards", A.yellow_cards + A.red_cards, B.yellow_cards + B.red_cards, null, (t) => `${t.yellow_cards} / ${t.red_cards}`],
    ];
    const el = $("statRows"); el.innerHTML = "";
    for (const [name, va, vb, fmt, fmtTeam, lowerBetter] of rows) {
      const f = fmt || ((x) => x);
      const tot = (+va || 0) + (+vb || 0) || 1;
      let wa = (+va || 0) / tot, wb = (+vb || 0) / tot;
      if (lowerBetter && va && vb) { const ia = 1 / va, ib = 1 / vb; wa = ia / (ia + ib); wb = ib / (ia + ib); }
      const row = document.createElement("div"); row.className = "statrow";
      row.innerHTML = `<span class="v"></span><div class="mid"><span class="lbl"></span><div class="bars"><span></span><span></span></div></div><span class="v r"></span>`;
      const [v1, v2] = row.querySelectorAll(".v");
      v1.textContent = fmtTeam ? fmtTeam(A) : f(va); v2.textContent = fmtTeam ? fmtTeam(B) : f(vb);
      row.querySelector(".lbl").textContent = name;
      const [b1, b2] = row.querySelectorAll(".bars span");
      b1.style.cssText = `width:${48 * wa}%;background:${S.colors[a]}`; b2.style.cssText = `width:${48 * wb}%;background:${S.colors[b]}`;
      el.appendChild(row);
    }
    drawXg(st.xg);
    loadPassNet();
  }

  function svgEl(tag, attrs, parent, text) {
    const e = document.createElementNS(SVGNS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }

  function drawXg(xg) {
    const svg = $("xgChart"); svg.innerHTML = "";
    const w = 320, h = 150, l = 28, r = 8, t = 10, bt = 22;
    const maxT = Math.max(90, xg.now), maxY = Math.max(1, ...Object.values(xg.series).map((s) => s[s.length - 1][1])) * 1.15;
    const X = (m) => l + (w - l - r) * m / maxT, Y = (v) => h - bt - (h - t - bt) * v / maxY;
    for (let v = 0; v <= maxY; v += maxY > 3 ? 1 : 0.5) {
      svgEl("line", { x1: l, x2: w - r, y1: Y(v), y2: Y(v), stroke: "#24302a" }, svg);
      svgEl("text", { x: l - 4, y: Y(v) + 3, "text-anchor": "end", "font-size": 9, fill: "#93a39a" }, svg, v.toFixed(1));
    }
    for (const m of [0, 45, 90]) svgEl("text", { x: X(m), y: h - 6, "text-anchor": "middle", "font-size": 9, fill: "#93a39a" }, svg, m + "'");
    for (const c of xg.changes) {
      svgEl("line", { x1: X(c.minute), x2: X(c.minute), y1: t, y2: h - bt, stroke: S.colors[c.team], "stroke-dasharray": "3 3", opacity: 0.8 }, svg);
    }
    for (const s of S.shorts) {
      const pts = xg.series[s]; let d = `M${X(pts[0][0])},${Y(pts[0][1])}`;
      for (let i = 1; i < pts.length; i++) d += `H${X(pts[i][0])}V${Y(pts[i][1])}`;
      svgEl("path", { d, fill: "none", stroke: S.colors[s], "stroke-width": 2.5 }, svg);
    }
    for (const gl of xg.goals) {
      const pts = xg.series[gl.team]; let v = 0;
      for (const p of pts) if (p[0] <= gl.minute + 0.01) v = p[1];
      svgEl("circle", { cx: X(gl.minute), cy: Y(v), r: 4.5, fill: "#fff", stroke: S.colors[gl.team], "stroke-width": 2 }, svg);
    }
  }

  function miniPitch(svg) {
    svg.innerHTML = "";
    const st = { fill: "none", stroke: "rgba(255,255,255,.55)", "stroke-width": 0.4 };
    svgEl("rect", { x: 0, y: 0, width: L, height: W, ...st }, svg);
    svgEl("line", { x1: L / 2, x2: L / 2, y1: 0, y2: W, ...st }, svg);
    svgEl("circle", { cx: L / 2, cy: W / 2, r: 9.15, ...st }, svg);
    svgEl("rect", { x: 0, y: W / 2 - 20.16, width: 16.5, height: 40.32, ...st }, svg);
    svgEl("rect", { x: L - 16.5, y: W / 2 - 20.16, width: 16.5, height: 40.32, ...st }, svg);
    const yy = (y) => W - y; // team-relative y (left positive) -> SVG down
    return yy;
  }

  async function loadPassNet() {
    const net = await api("/api/passnet", withSession({ team: S.pnTeam, window: S.pnWindow }));
    const svg = $("passNet"), yy = miniPitch(svg), col = S.colors[S.pnTeam];
    const pos = Object.fromEntries(net.nodes.map((n) => [n.id, n]));
    const maxE = Math.max(1, ...net.edges.map((e) => e.count)), maxP = Math.max(1, ...net.nodes.map((n) => n.passes));
    for (const e of net.edges) {
      const a = pos[e.a], b = pos[e.b];
      svgEl("line", { x1: a.x, y1: yy(a.y), x2: b.x, y2: yy(b.y), stroke: "#fff", "stroke-opacity": 0.25 + 0.6 * e.count / maxE, "stroke-width": 0.4 + 2.2 * e.count / maxE, "stroke-linecap": "round" }, svg);
    }
    for (const n of net.nodes) {
      const r = 1.8 + 2.4 * Math.sqrt(n.passes / maxP);
      svgEl("circle", { cx: n.x, cy: yy(n.y), r, fill: col, stroke: "#fff", "stroke-width": 0.4 }, svg);
      svgEl("text", { x: n.x, y: yy(n.y) + 1.1, "text-anchor": "middle", "font-size": 3, "font-weight": 700, fill: "#fff" }, svg, n.number);
    }
    $("pnNote").textContent = (hu() ? `Átlagos pozíciók labdabirtoklásnál és a passzkapcsolatok (legalább 2 passz), ${net.window[0]}–${net.window[1]}. A csapat jobbra támad.`
      : `Average positions in possession and passing links (2+ passes), ${net.window[0]}–${net.window[1]}. Attacking to the right.`);
  }
  document.querySelectorAll("#pnWindow button").forEach((b) => b.addEventListener("click", () => {
    S.pnWindow = b.dataset.w; document.querySelectorAll("#pnWindow button").forEach((x) => x.classList.toggle("on", x === b)); loadPassNet();
  }));

  async function loadProfile() {
    $("playerEmpty").hidden = !!S.follow; $("playerBody").hidden = !S.follow;
    if (!S.follow) return;
    const p = await api("/api/profile", withSession({ id: S.follow }));
    $("pName").textContent = p.name; $("pTeam").textContent = p.team; $("pTeam").style.color = S.colors[p.team];
    const k = $("pKpis"); k.innerHTML = "";
    const last = p.splits[p.splits.length - 1] || {};
    for (const [lab, val] of [[hu() ? "Gól / gólpassz" : "Goals / assists", `${p.goals} / ${p.assists}`], ["xG", p.xg.toFixed(2)],
      [hu() ? "Passzpontosság" : "Pass accuracy", p.pass_accuracy_pct + "%"],
      [hu() ? "Befutások a vonal mögé" : "Runs in behind", p.off_ball_runs.length],
      [hu() ? "Csúcs most" : "Top speed now", (last.top_speed_kmh || 0).toFixed(0) + " km/h"], [hu() ? "Sprintek most" : "Sprints now", last.sprints ?? 0]]) {
      const d = document.createElement("div"); d.innerHTML = "<small></small><b></b>";
      d.querySelector("small").textContent = lab; d.querySelector("b").textContent = val; k.appendChild(d);
    }
    drawSplits(p);
    const f = p.fatigue;
    $("pFatigue").textContent = !f ? (hu() ? "A fáradás a 3. negyedórától mérhető." : "Fatigue shows from the third 15-minute period.")
      : f.top_speed_drop_kmh >= 1.5
        ? (hu() ? `Fárad: a csúcssebessége ${f.top_speed_drop_kmh.toFixed(1)} km/h-val alacsonyabb, mint a meccs elején.` : `Tiring: top speed is ${f.top_speed_drop_kmh.toFixed(1)} km/h below where it started.`)
        : (hu() ? "Bírja: a csúcssebessége a meccs elejéhez képest alig változott." : "Holding up: top speed is close to where it started.");
    drawHeat(p); drawMap(p);
  }

  function drawSplits(p) {
    const svg = $("pSplits"); svg.innerHTML = "";
    const w = 320, h = 120, l = 8, bt = 18, n = Math.max(6, p.splits.length), bw = (w - 2 * l) / n;
    const col = S.colors[p.team];
    const maxKm = 13, minTop = 20, maxTop = 36;
    p.splits.forEach((s, i) => {
      const x = l + i * bw, bh = (h - bt - 14) * Math.min(1, s.km_per_90 / maxKm);
      svgEl("rect", { x: x + 4, y: h - bt - bh, width: bw - 8, height: bh, rx: 3, fill: col, opacity: 0.85 }, svg);
      svgEl("text", { x: x + bw / 2, y: h - bt - bh - 3, "text-anchor": "middle", "font-size": 9, fill: "#eef3ef" }, svg, s.km_per_90.toFixed(1));
      svgEl("text", { x: x + bw / 2, y: h - 5, "text-anchor": "middle", "font-size": 9, fill: "#93a39a" }, svg, s.from);
    });
    let d = "";
    p.splits.forEach((s, i) => {
      const x = l + i * bw + bw / 2, y = 6 + (h - bt - 20) * (1 - (s.top_speed_kmh - minTop) / (maxTop - minTop));
      d += (i ? "L" : "M") + x + "," + y;
      svgEl("circle", { cx: x, cy: y, r: 2.5, fill: "#d7ff3a" }, svg);
    });
    if (d) svgEl("path", { d, fill: "none", stroke: "#d7ff3a", "stroke-width": 1.5 }, svg);
    svgEl("text", { x: w - 6, y: 12, "text-anchor": "end", "font-size": 9, fill: "#d7ff3a" }, svg, hu() ? "csúcssebesség" : "top speed");
    svgEl("text", { x: w - 6, y: 24, "text-anchor": "end", "font-size": 9, fill: "#93a39a" }, svg, hu() ? "oszlop: km / 90 perc" : "bars: km per 90");
  }

  function drawHeat(p) {
    const svg = $("pHeat"), yy = miniPitch(svg), [nx, ny] = p.heat_bins, cw = L / nx, ch = W / ny;
    const col = S.colors[p.team];
    for (let i = 0; i < nx; i++) for (let j = 0; j < ny; j++) {
      const v = p.heatmap[i][j]; if (v < 0.05) continue;
      svgEl("rect", { x: i * cw, y: yy((j + 1) * ch), width: cw + 0.2, height: ch + 0.2, fill: col, opacity: (0.15 + 0.75 * v).toFixed(2) }, svg);
    }
    miniPitchLinesOnTop(svg);
  }
  function miniPitchLinesOnTop(svg) {
    const st = { fill: "none", stroke: "rgba(255,255,255,.55)", "stroke-width": 0.4 };
    svgEl("rect", { x: 0, y: 0, width: L, height: W, ...st }, svg);
    svgEl("line", { x1: L / 2, x2: L / 2, y1: 0, y2: W, ...st }, svg);
  }

  function drawMap(p) {
    const svg = $("pMap"), yy = miniPitch(svg);
    for (const ps of p.passes) {
      svgEl("line", { x1: ps.from[0], y1: yy(ps.from[1]), x2: ps.to[0], y2: yy(ps.to[1]), stroke: ps.ok ? "#7be38b" : "#ff6b6b", "stroke-width": 0.45, "stroke-opacity": 0.75 }, svg);
      svgEl("circle", { cx: ps.to[0], cy: yy(ps.to[1]), r: 0.6, fill: ps.ok ? "#7be38b" : "#ff6b6b" }, svg);
    }
    for (const s of p.shots) {
      svgEl("circle", { cx: s.at[0], cy: yy(s.at[1]), r: 1.2 + 6 * Math.sqrt(s.xg), fill: s.outcome === "goal" ? "#d7ff3a" : "none", stroke: "#fff", "stroke-width": 0.6, opacity: 0.9 }, svg);
    }
  }

  // ------------------------------------------------------------ UI wiring
  let toastTimer;
  function toast(text) {
    const t = $("toast"); t.textContent = text; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 5000);
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
  function togglePresent(on) {
    document.body.classList.toggle("present", on ?? !document.body.classList.contains("present"));
    setTimeout(resize, 50);
  }

  document.querySelectorAll(".view-toggle button").forEach((b) =>
    b.addEventListener("click", () => {
      S.view = b.dataset.view;
      document.querySelectorAll(".view-toggle button").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-selected", x === b); });
      if (S.view === "tv") setTimeout(() => hook(`On TV, you see ${S.visible} player${S.visible === 1 ? "" : "s"}.`), 900);
      else hook("The match has twenty-two.");
    }));

  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
    S.tab = b.dataset.tab;
    document.querySelectorAll(".tabs button").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-selected", x === b); });
    for (const t of ["insights", "stats", "player"]) $("panel-" + t).hidden = t !== S.tab;
    refreshPanel(true);
  }));

  cv.addEventListener("click", (e) => {
    if (!S._v || !S._fr || S.replay) return;
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
    if (S.replay) await endReplay();
    if (S.playing) { setPlaying(false); if (S.es) { S.es.close(); S.es = null; } await seek(currentMinute()); }
    else { setPlaying(true); await seek(currentMinute()); }
  });
  $("speed").addEventListener("change", (e) => { S.speed = +e.target.value; if (!S.replay) seek(currentMinute()); });
  $("track").addEventListener("click", (e) => {
    const rc = $("track").getBoundingClientRect();
    S.replay = null; $("replayBar").hidden = true;
    seek(((e.clientX - rc.left) / rc.width) * S.match.length_minutes);
  });
  $("track").addEventListener("keydown", (e) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (step) { e.preventDefault(); seek(Math.max(0, currentMinute() + step * 2)); }
  });
  $("presentBtn").addEventListener("click", () => togglePresent());

  const segment = (id, attr, key) =>
    document.querySelectorAll(`#${id} button`).forEach((b) =>
      b.addEventListener("click", () => {
        S[key] = b.dataset[attr];
        document.querySelectorAll(`#${id} button`).forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", x === b); });
        if (key === "lang") fillFollowSelect();
        refreshCards(currentMinute(), true);
        refreshPanel(true);
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
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { if (!$("drawer").hidden) closeDrawer(); else if (S.replay) endReplay(); else if (document.body.classList.contains("present")) togglePresent(false); }
    if ((e.key === "p" || e.key === "P") && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) togglePresent();
  });
  document.querySelectorAll("#awayFor button").forEach((b) =>
    b.addEventListener("click", () => {
      awayFor = +b.dataset.min;
      document.querySelectorAll("#awayFor button").forEach((x) => x.classList.toggle("on", x === b));
      loadMissed();
    }));

  init();
})();
