"""Build a self-contained preview.html to eyeball a generated match.

Usage: python scripts/preview.py [data/FP-0001] [--out preview.html]
"""

import argparse
import base64
import json
from pathlib import Path

import numpy as np

HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Full Pitch preview</title>
<style>
:root{--bg:#0e1411;--panel:#151d18;--ink:#e8efe9;--mute:#8fa197;--line:#2a3830}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.4 system-ui,Segoe UI,sans-serif}
header{display:flex;gap:16px;align-items:center;padding:10px 16px;flex-wrap:wrap}
.score{font-size:22px;font-weight:700;letter-spacing:.5px}.clock{font-variant-numeric:tabular-nums;color:var(--mute)}
main{display:grid;grid-template-columns:1fr 300px;gap:12px;padding:0 16px 16px}
@media(max-width:900px){main{grid-template-columns:1fr}}
canvas{width:100%;background:#1d5a33;border-radius:8px;display:block}
.controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
button,select{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 10px;cursor:pointer}
button.on{border-color:#e8efe9}
input[type=range]{flex:1;min-width:160px}
aside{background:var(--panel);border-radius:8px;padding:10px;max-height:80vh;overflow:auto}
aside h3{margin:4px 0 8px;font-size:13px;color:var(--mute);text-transform:uppercase;letter-spacing:.06em}
.ev{padding:3px 0;border-bottom:1px solid var(--line);font-size:13px}.ev b{font-variant-numeric:tabular-nums;color:var(--mute);font-weight:500;margin-right:6px}
.follow{margin-top:10px;font-size:13px}
</style></head><body>
<header><span class="score" id="score"></span><span class="clock" id="clock"></span><span id="follow" class="follow"></span></header>
<main><div>
<canvas id="c" width="1260" height="816"></canvas>
<div class="controls">
<button id="play">Play</button>
<select id="speed"><option>1</option><option>4</option><option selected>8</option><option>16</option><option>60</option></select>
<button id="tv">TV view</button><button id="shape" class="on">Team shape</button>
<input type="range" id="scrub" min="0" value="0">
</div>
<div style="color:var(--mute);font-size:12px">Click a player to follow them. Synthetic data, fictional teams.</div>
</div>
<aside><h3>Events</h3><div id="events"></div></aside></main>
<script>
const META=__META__, EVENTS=__EVENTS__;
const raw=Uint8Array.from(atob("__XY__"),c=>c.charCodeAt(0));
const XY=new Int16Array(raw.buffer); const POSS=Int8Array.from(atob("__POSS__"),c=>c.charCodeAt(0));
const CLOCK=new Float32Array(Uint8Array.from(atob("__CLOCK__"),c=>c.charCodeAt(0)).buffer);
const N=CLOCK.length, E=23, HZ=META.hz, L=105, W=68;
const cv=document.getElementById('c'), g=cv.getContext('2d');
const scrub=document.getElementById('scrub'); scrub.max=N-1;
let f=0, playing=false, tv=false, shape=true, follow=null, cam={x:52.5,y:34,z:1};
const teams=META.teams, P={}; META.players.forEach(p=>P[p.id]=p);
const subs=EVENTS.filter(e=>e.type==='substitution');
function pos(fr,i){const o=(fr*E+i)*2;return [XY[o]/10,XY[o+1]/10]}
function slotPlayer(team,slot,fr){const short=teams[team].short;let id=`${short}-${String(slot+1).padStart(2,'0')}`;
  for(const s of subs){if(s.frame<=fr&&s.team===short&&s.off===id)id=s.on;} return id;}
function slotIdAt(team,slot,fr){let id=`${teams[team].short}-${String(slot+1).padStart(2,'0')}`;
  for(const s of subs){if(s.frame<=fr&&s.team===teams[team].short){ if(s.off===id) id=s.on; }} return id;}
function formationAt(team,t){let fm=teams[team].formation;for(const x of META.formation_log){if(x.team===team&&x.t<=t)fm=x.formation;}return fm;}
function fmt(t){const p=t>=2700?2:1;const m=Math.floor(t/60),s=Math.floor(t%60);return `${m}:${String(s).padStart(2,'0')}`}
function view(){ // world->canvas transform
  const sx=cv.width/(L+6), sy=cv.height/(W+6);
  if(!tv) return {k:Math.min(sx,sy),ox:3,oy:3,cx:0,cy:0};
  const z=2.6, k=Math.min(sx,sy)*z; const vw=cv.width/k, vh=cv.height/k;
  const cx=Math.max(-3,Math.min(L+3-vw,cam.x-vw/2)), cy=Math.max(-3,Math.min(W+3-vh,(W-cam.y)-vh/2));
  return {k,ox:-cx,oy:-cy};
}
function X(v,x){return (x+v.ox)*v.k} function Y(v,y){return (W-y+v.oy)*v.k}
function pitch(v){g.fillStyle='#1d5a33';g.fillRect(0,0,cv.width,cv.height);
  for(let i=0;i<10;i++){if(i%2)continue;g.fillStyle='#1f6036';g.fillRect(X(v,i*10.5),Y(v,W),10.5*v.k,W*v.k)}
  g.strokeStyle='rgba(255,255,255,.75)';g.lineWidth=Math.max(1.5,v.k*.12);
  const r=(x,y,w,h)=>g.strokeRect(X(v,x),Y(v,y+h),w*v.k,h*v.k);
  r(0,0,L,W);g.beginPath();g.moveTo(X(v,L/2),Y(v,0));g.lineTo(X(v,L/2),Y(v,W));g.stroke();
  g.beginPath();g.arc(X(v,L/2),Y(v,W/2),9.15*v.k,0,7);g.stroke();
  r(0,W/2-20.16,16.5,40.32);r(L-16.5,W/2-20.16,16.5,40.32);r(0,W/2-9.16,5.5,18.32);r(L-5.5,W/2-9.16,5.5,18.32);
  g.fillStyle='rgba(255,255,255,.8)';g.fillRect(X(v,-1),Y(v,W/2+3.66),v.k,7.32*v.k);g.fillRect(X(v,L),Y(v,W/2+3.66),v.k,7.32*v.k);}
function draw(){
  const v0=pos(f,22); cam.x+= (v0[0]-cam.x)*.12; cam.y+=(v0[1]-cam.y)*.12;
  const v=view(); pitch(v); const t=CLOCK[f], poss=POSS[f];
  if(shape){for(let k=0;k<2;k++){const fm=META.formations[formationAt(k,t)];const grp=(poss===k?fm.att_groups:fm.def_groups);
    const lines={};for(let s=1;s<11;s++){(lines[grp[s]]=lines[grp[s]]||[]).push(pos(f,k*11+s))}
    g.strokeStyle=teams[k].color;g.globalAlpha=.55;g.lineWidth=Math.max(2,v.k*.25);
    for(const key in lines){const pts=lines[key].sort((a,b)=>a[1]-b[1]);if(pts.length<2)continue;g.beginPath();
      pts.forEach((p,i)=>i?g.lineTo(X(v,p[0]),Y(v,p[1])):g.moveTo(X(v,p[0]),Y(v,p[1])));g.stroke();}
    g.globalAlpha=1;}}
  if(follow){const [k,s]=follow;g.strokeStyle='rgba(255,230,120,.8)';g.lineWidth=2;g.beginPath();
    for(let i=Math.max(0,f-HZ*20);i<=f;i+=1){const p=pos(i,k*11+s);i===Math.max(0,f-HZ*20)?g.moveTo(X(v,p[0]),Y(v,p[1])):g.lineTo(X(v,p[0]),Y(v,p[1]))}g.stroke();}
  const rad=Math.max(5,v.k*.85);
  for(let k=0;k<2;k++)for(let s=0;s<11;s++){const p=pos(f,k*11+s);const id=slotIdAt(k,s,f);
    g.beginPath();g.arc(X(v,p[0]),Y(v,p[1]),rad,0,7);g.fillStyle=s===0?'#222':teams[k].color;g.fill();
    g.lineWidth=follow&&follow[0]===k&&follow[1]===s?3:1.2;g.strokeStyle=follow&&follow[0]===k&&follow[1]===s?'#ffe680':'#fff';g.stroke();
    g.fillStyle='#fff';g.font=`600 ${Math.round(rad*1.05)}px system-ui`;g.textAlign='center';g.textBaseline='middle';g.fillText(P[id].number,X(v,p[0]),Y(v,p[1])+.5);}
  const b=pos(f,22);g.beginPath();g.arc(X(v,b[0]),Y(v,b[1]),Math.max(3.5,v.k*.45),0,7);g.fillStyle='#fff';g.fill();g.strokeStyle='#000';g.lineWidth=1;g.stroke();
  // header
  let sc=[0,0];for(const e of EVENTS){if(e.frame>f)break;if(e.type==='goal')sc=e.score;}
  document.getElementById('score').textContent=`${teams[0].short} ${sc[0]} – ${sc[1]} ${teams[1].short}`;
  document.getElementById('clock').textContent=fmt(t);
  if(follow){const [k,s]=follow,id=slotIdAt(k,s,f),a=pos(Math.max(0,f-HZ),k*11+s),c=pos(f,k*11+s);
    let dist=0;for(let i=1;i<=f;i+=HZ){const p1=pos(i-1,k*11+s),p2=pos(Math.min(i-1+HZ,f),k*11+s);dist+=Math.hypot(p2[0]-p1[0],p2[1]-p1[1])}
    const sp=Math.hypot(c[0]-a[0],c[1]-a[1])*3.6;
    document.getElementById('follow').textContent=`Following #${P[id].number} ${P[id].name} · ${sp.toFixed(0)} km/h · ${(dist/1000).toFixed(1)} km`;}
  scrub.value=f; renderEvents();
}
let lastEv=-1;
const LABEL={goal:'GOAL',shot:'Shot',substitution:'Sub',card:'Card',foul:'Foul',save:'Save',restart:'Restart'};
function renderEvents(){let i=EVENTS.findIndex(e=>e.frame>f);if(i<0)i=EVENTS.length;if(i===lastEv)return;lastEv=i;
  const shown=[];for(let j=i-1;j>=0&&shown.length<40;j--){const e=EVENTS[j];if(!LABEL[e.type])continue;if(e.type==='restart'&&e.kind!=='corner')continue;
    let txt=LABEL[e.type]+' · '+(e.team||'');if(e.player)txt+=' '+P[e.player].name;
    if(e.type==='shot')txt+=` (xG ${e.xg.toFixed(2)}, ${e.outcome.replace('_',' ')})`;
    if(e.type==='substitution')txt=`Sub · ${e.team} ${P[e.on].name} on for ${P[e.off].name}`;
    if(e.type==='restart')txt='Corner · '+e.team;
    shown.push(`<div class="ev"><b>${fmt(e.t)}</b>${txt}</div>`)}
  document.getElementById('events').innerHTML=shown.join('');}
let acc=0,last=performance.now();
function loop(now){const dt=(now-last)/1000;last=now;if(playing){acc+=dt*HZ*+document.getElementById('speed').value;const n=Math.floor(acc);acc-=n;f=Math.min(N-1,f+n);if(f>=N-1)playing=false;}draw();requestAnimationFrame(loop)}
document.getElementById('play').onclick=e=>{playing=!playing;e.target.textContent=playing?'Pause':'Play'};
document.getElementById('tv').onclick=e=>{tv=!tv;e.target.classList.toggle('on',tv)};
document.getElementById('shape').onclick=e=>{shape=!shape;e.target.classList.toggle('on',shape)};
scrub.oninput=()=>{f=+scrub.value};
cv.onclick=ev=>{const r=cv.getBoundingClientRect(),mx=(ev.clientX-r.left)*cv.width/r.width,my=(ev.clientY-r.top)*cv.height/r.height,v=view();
  let best=null,bd=1e9;for(let k=0;k<2;k++)for(let s=0;s<11;s++){const p=pos(f,k*11+s),d=Math.hypot(X(v,p[0])-mx,Y(v,p[1])-my);if(d<bd){bd=d;best=[k,s]}}
  follow=bd<30?best:null;if(!follow)document.getElementById('follow').textContent='';};
function snapCam(){const b=pos(f,22);cam.x=b[0];cam.y=b[1]}
window.seek=(min)=>{f=Math.max(0,Math.min(N-1,CLOCK.findIndex(c=>c>=min*60)));snapCam();draw();return fmt(CLOCK[f])};
scrub.addEventListener('change',snapCam);
window.setView=(o)=>{if('tv'in o)tv=o.tv;if('follow'in o)follow=o.follow;draw()};
const h=new URLSearchParams(location.hash.slice(1));if(h.get('t'))seek(+h.get('t'));
requestAnimationFrame(loop);
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("match", nargs="?", default="data/FP-0001")
    ap.add_argument("--out", default="preview.html")
    args = ap.parse_args()
    d = Path(args.match)
    tr = np.load(d / "tracking.npz")
    xy = np.round(tr["xy"] * 10).astype("<i2")
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (d / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    html = (HTML.replace("__META__", json.dumps(meta))
            .replace("__EVENTS__", json.dumps(events))
            .replace("__XY__", base64.b64encode(xy.tobytes()).decode())
            .replace("__POSS__", base64.b64encode(tr["poss"].astype(np.int8).tobytes()).decode())
            .replace("__CLOCK__", base64.b64encode(tr["clock"].astype("<f4").tobytes()).decode()))
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"wrote {args.out} ({len(html) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
