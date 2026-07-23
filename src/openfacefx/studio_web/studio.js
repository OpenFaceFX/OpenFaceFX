/* ===================================================================== *
 *  OpenFaceFX Studio — engine
 *  One transport clock drives Preview / Curves / Phonemes / Face Graph.
 *  Pipeline runs via Pyodide (browser) or a native /api backend if present.
 * ===================================================================== */
"use strict";
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const OFFX_VERSION = "0.21.0";
const PYODIDE_VER = "v0.26.1";

/* ---- categorical curve palette (distinct on dark) --------------------- */
const CURVE_COLORS = ["#f4b942","#4cc2ff","#e06c9f","#5ad19a","#b78cff","#ff8f6b",
  "#8fd14f","#59b0ff","#f0c674","#7fd1c4","#ef6f9e","#a0e05a","#c39bff","#ffb15e",
  "#5ec8d8","#e8875a"];

/* ---- global state ---------------------------------------------------- */
const S = {
  runtime:null, pyodide:null, native:false,
  track:null, segments:[], duration:0, fps:60,
  wavBytes:null, wavPeaks:null,
  chan:{},               // name -> {color, visible, idx}
  sel:null,              // selected channel name
  view:"preview",
  t:0, playing:false, lastTs:0,
  presets:[], presetSel:"arkit", presetMap:null,
};

/* bridge for assistant.js (separate script) to read/write studio context */
window.StudioBridge = {
  transcript:()=>$("#text").value,
  setTranscript:t=>{ $("#text").value=t; },
  track:()=>S.track, segments:()=>S.segments,
};

/* ===================================================================== *
 *  Pipeline bridge (Pyodide, with native-backend detection)
 * ===================================================================== */
const boot = {
  msg:$("#bootMsg"), fill:$("#bootFill"), overlay:$("#bootOverlay"),
  set(m,f){ if(m)this.msg.textContent=m; if(f!=null)this.fill.style.width=Math.round(f*100)+"%"; },
  done(){ this.overlay.classList.add("gone"); setTimeout(()=>this.overlay.remove(),450); }
};
function setRuntime(kind,label){
  S.runtime=kind;
  $("#runtimeDot").className="dot "+(kind==="error"?"err":"ready");
  $("#runtimeLabel").textContent=label;
}

const PY_BRIDGE = String.raw`
import json, base64, os
import openfacefx as offx
from openfacefx import (naive_segments, generate_from_alignment, generate_naive,
    GestureParams, add_gestures_to_track, to_dict, from_dict, retarget, PRESETS)
from openfacefx.alignment import dump_segments

def _style_coart(style):
    if not style: return None
    try:
        from openfacefx.coarticulation import style_params
        return style_params(style)
    except Exception:
        return None

def studio_generate(text, engine, dur, style, gestures, breath, has_wav, fps):
    fps = float(fps) or 60.0
    if has_wav:
        try: dur = offx.wav_duration('/tmp/in.wav')
        except Exception: pass
    dur = float(dur)
    segs = naive_segments(text, dur)
    coart = _style_coart(style)
    if has_wav and engine == 'energy':
        try: track = generate_naive(text, dur, wav='/tmp/in.wav', fps=fps)
        except TypeError: track = generate_naive(text, dur, wav='/tmp/in.wav')
    else:
        try:
            track = generate_from_alignment(segs, fps=fps, coart=coart) if coart is not None \
                    else generate_from_alignment(segs, fps=fps)
        except TypeError:
            track = generate_from_alignment(segs, fps=fps)
    if gestures or breath:
        try:
            gp = GestureParams(seed=1, breath_enable=bool(breath))
            if breath and not gestures:
                for a in ('blink_enable','brow_enable','gaze_enable','head_ambient','head_nod_on_stress'):
                    if hasattr(gp,a): setattr(gp,a,False)
            track = add_gestures_to_track(track, track.duration, params=gp)
        except Exception as e:
            pass
    return json.dumps({
        "track": to_dict(track),
        "segments": dump_segments(segs),
        "duration": round(track.duration,4),
        "fps": track.fps,
    })

def studio_presets():
    return json.dumps(sorted(PRESETS))

def studio_preset_map(name):
    m = PRESETS.get(name, {})
    return json.dumps({v: [[t,round(float(w),3)] for (t,w) in tgts] for v,tgts in m.items()})

_EXPORTERS = {}
def _reg(fmt, fn): _EXPORTERS[fmt]=fn

def _arkit(tk):
    try: return retarget(tk, PRESETS['arkit'])
    except Exception: return tk

def studio_export(fmt, track_json, fps):
    tk = from_dict(json.loads(track_json))
    p = '/tmp/out_'+fmt
    def W(fn, path):
        fn();
        with open(path,'rb') as f: return f.read()
    try:
        if fmt=='json':   data=json.dumps(json.loads(track_json),indent=2).encode(); name='take.track.json'
        elif fmt=='csv':  from openfacefx import write_csv; data=W(lambda:write_csv(tk,p),p); name='take.csv'
        elif fmt=='glb':  from openfacefx import write_gltf; data=W(lambda:write_gltf(tk,p+'.glb'),p+'.glb'); name='take.glb'
        elif fmt=='vrma': from openfacefx import write_vrma; data=W(lambda:write_vrma(tk,p+'.vrma'),p+'.vrma'); name='take.vrma'
        elif fmt=='spine':from openfacefx import write_spine; data=W(lambda:write_spine(tk,p+'.spine.json'),p+'.spine.json'); name='take.spine.json'
        elif fmt=='live2d':from openfacefx import write_live2d_motion; data=W(lambda:write_live2d_motion(tk,p+'.motion3.json'),p+'.motion3.json'); name='take.motion3.json'
        elif fmt=='exp3': from openfacefx import write_live2d_expression; data=W(lambda:write_live2d_expression(tk,p+'.exp3.json'),p+'.exp3.json'); name='pose.exp3.json'
        elif fmt=='unity':from openfacefx import write_unity_anim; data=W(lambda:write_unity_anim(tk,p+'.anim'),p+'.anim'); name='take.anim'
        elif fmt=='godot':from openfacefx import write_godot_anim; data=W(lambda:write_godot_anim(tk,p+'.tres'),p+'.tres'); name='take.tres'
        elif fmt=='vmd':  from openfacefx import write_vmd; data=W(lambda:write_vmd(tk,p+'.vmd'),p+'.vmd'); name='take.vmd'
        elif fmt=='livelink': from openfacefx import write_livelink_csv; a=_arkit(tk); data=W(lambda:write_livelink_csv(a,p+'.livelink.csv'),p+'.livelink.csv'); name='take.livelink.csv'
        elif fmt=='a2f':  from openfacefx import write_a2f; a=_arkit(tk); data=W(lambda:write_a2f(a,p+'.a2f.json'),p+'.a2f.json'); name='take.a2f.json'
        elif fmt=='rhubarb': from openfacefx import write_rhubarb_tsv; data=W(lambda:write_rhubarb_tsv(tk,p+'.tsv'),p+'.tsv'); name='take.tsv'
        elif fmt=='moho': from openfacefx import write_moho_dat; data=W(lambda:write_moho_dat(tk,p+'.dat'),p+'.dat'); name='take.dat'
        else: return json.dumps({"error":"unknown format "+fmt})
        return json.dumps({"filename":name,"b64":base64.b64encode(data).decode()})
    except Exception as e:
        return json.dumps({"error":str(e)})
`;

async function bootPyodide(){
  boot.set("Loading the WebAssembly runtime (~24 MB first visit, then cached)…",0.08);
  const s=document.createElement("script");
  s.src=`https://cdn.jsdelivr.net/pyodide/${PYODIDE_VER}/full/pyodide.js`;
  await new Promise((res,rej)=>{s.onload=res;s.onerror=()=>rej(new Error("Pyodide CDN unreachable"));document.head.appendChild(s);});
  boot.set("Starting CPython…",0.28); S.pyodide=await loadPyodide();
  boot.set("Loading numpy (wasm)…",0.5); await S.pyodide.loadPackage(["micropip","numpy"]);
  boot.set(`Installing openfacefx ${OFFX_VERSION}…`,0.72);
  const mp=S.pyodide.pyimport("micropip"); await mp.install(`openfacefx==${OFFX_VERSION}`);
  boot.set("Wiring the studio bridge…",0.9); await S.pyodide.runPythonAsync(PY_BRIDGE);
  boot.set("Ready.",1); setRuntime("browser",`browser · openfacefx ${OFFX_VERSION}`);
}

const Pipe = {
  async generate(args){
    if(S.native) return fetch("/api/generate",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(args)}).then(r=>r.json());
    const fn=S.pyodide.globals.get("studio_generate");
    const out=await fn(args.text,args.engine,args.dur,args.style,args.gestures,args.breath,args.has_wav,args.fps);
    fn.destroy(); return JSON.parse(out);
  },
  async export(fmt){
    if(S.native) return fetch(`/api/export/${fmt}`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({track:S.track})}).then(r=>r.json());
    const fn=S.pyodide.globals.get("studio_export");
    const out=await fn(fmt,JSON.stringify(S.track),S.fps); fn.destroy(); return JSON.parse(out);
  },
  async presets(){
    if(S.native) return fetch("/api/presets").then(r=>r.json());
    const a=S.pyodide.globals.get("studio_presets"); const r=JSON.parse(a()); a.destroy(); return r;
  },
  async presetMap(name){
    if(S.native) return fetch(`/api/preset/${name}`).then(r=>r.json());
    const a=S.pyodide.globals.get("studio_preset_map"); const r=JSON.parse(a(name)); a.destroy(); return r;
  }
};

async function bootstrap(){
  try{
    // native backend? (openfacefx studio serves /api using real Python)
    try{ const r=await fetch("/api/health",{signal:AbortSignal.timeout(600)});
      if(r.ok){ S.native=true; const j=await r.json(); setRuntime("native",`native · openfacefx ${j.version||""}`.trim()); }
    }catch(_){}
    if(!S.native){ if(typeof WebAssembly==="undefined") throw new Error("WebAssembly unavailable"); await bootPyodide(); }
    S.presets=await Pipe.presets(); initFaceGraphPresets();
    try{ S.arkitMap=await Pipe.presetMap("arkit"); }catch(_){ S.arkitMap=null; }  // for 3D preview
    buildExportGrid(); $("#run").disabled=false; $("#run").textContent="Generate take";
    boot.done();
  }catch(err){ setRuntime("error","runtime failed"); boot.set("Couldn't start: "+err.message,1);
    boot.fill.style.background="var(--crit)"; }
}

/* ===================================================================== *
 *  Generate
 * ===================================================================== */
$("#wav").onchange=async e=>{ const f=e.target.files[0]; if(!f) return;
  try{
    const ab=await f.arrayBuffer(); const ac=new (window.AudioContext||window.webkitAudioContext)();
    const audio=await ac.decodeAudioData(ab); const ch=audio.getChannelData(0);
    S.wavBytes=encodeWav(ch,audio.sampleRate); S.wavPeaks=peaks(ch,1600);
    $("#wavName").textContent=`${f.name} · ${audio.duration.toFixed(1)}s`;
    $("#dur").value=audio.duration.toFixed(2); $("#engine").value="energy";
  }catch(err){ $("#wavName").textContent="couldn't decode audio"; }
};

$("#run").onclick=async ()=>{
  const btn=$("#run"); btn.disabled=true; btn.textContent="Generating…";
  try{
    S.fps=parseFloat($("#fps").value)||60;
    const hasWav=!!S.wavBytes && $("#engine").value==="energy";
    let wav_b64;
    if(hasWav){ if(S.native) wav_b64=toB64(S.wavBytes); else S.pyodide.FS.writeFile("/tmp/in.wav",S.wavBytes); }
    const res=await Pipe.generate({
      text:$("#text").value.trim()||"hello", engine:$("#engine").value,
      dur:parseFloat($("#dur").value)||4, style:$("#style").value,
      gestures:$("#optGestures").checked, breath:$("#optBreath").checked,
      has_wav:hasWav, wav_b64, fps:S.fps });
    if(res.error) throw new Error(res.error);
    S.track=res.track; S.segments=res.segments||[]; S.duration=res.duration; S.t=0;
    ingestChannels(); buildChannelList(); drawAll(); setScrub();
    $("#tpDur").textContent="/ "+fmt(S.duration);
    btn.textContent="Generate take";
  }catch(err){ btn.textContent="Generate — failed"; console.error(err); alert("Generate failed: "+err.message); }
  btn.disabled=false;
};

function ingestChannels(){
  S.chan={}; S.track.channels.forEach((c,i)=>{ S.chan[c.name]={color:CURVE_COLORS[i%CURVE_COLORS.length],visible:true,idx:i}; });
}

/* ===================================================================== *
 *  Channel list + inspector
 * ===================================================================== */
function buildChannelList(){
  const list=$("#channelList"); $("#chCount").textContent=S.track.channels.length;
  list.innerHTML="";
  for(const c of S.track.channels){
    const m=S.chan[c.name];
    const li=document.createElement("li"); li.className="chan"+(m.visible?"":" off")+(S.sel===c.name?" sel":"");
    li.innerHTML=`<span class="sw" style="background:${m.color}"></span>
      <span class="nm">${c.name}</span><span class="kc">${c.keys.length}</span>
      <span class="vis">${m.visible?"◉":"○"}</span>`;
    li.querySelector(".vis").onclick=e=>{e.stopPropagation(); m.visible=!m.visible; buildChannelList(); if(S.view==="curves")drawCurves(); if(S.view==="preview")drawPreview();};
    li.onclick=()=>{ S.sel=c.name; buildChannelList(); buildInspector(); };
    list.appendChild(li);
  }
}
function buildInspector(){
  const box=$("#inspector"); if(!S.sel){ box.innerHTML='<p class="empty">Select a channel to edit its properties.</p>'; return; }
  const c=S.track.channels.find(x=>x.name===S.sel), m=S.chan[S.sel];
  const vals=c.keys.map(k=>k[1]); const mn=Math.min(...vals), mx=Math.max(...vals);
  box.innerHTML=`
    <div class="insp-row"><label>Channel</label><span class="mono">${c.name}</span></div>
    <div class="insp-row"><label>Colour</label><span class="insp-swatch" style="background:${m.color}"></span></div>
    <div class="insp-row"><label>Keyframes</label><span class="mono">${c.keys.length}</span></div>
    <div class="insp-row"><label>Range</label><span class="mono">${mn.toFixed(2)} – ${mx.toFixed(2)}</span></div>
    <div class="insp-row"><label>Visible</label><input type="checkbox" ${m.visible?"checked":""} id="inspVis"></div>`;
  $("#inspVis").onchange=e=>{ m.visible=e.target.checked; buildChannelList(); drawAll(); };
}

/* ===================================================================== *
 *  Views: dispatch + drawing
 * ===================================================================== */
$$("#tabs .tab").forEach(t=>t.onclick=()=>{
  $$("#tabs .tab").forEach(x=>x.classList.remove("active")); t.classList.add("active");
  $$(".view").forEach(v=>v.classList.remove("active"));
  S.view=t.dataset.view; $(`.view[data-view="${S.view}"]`).classList.add("active"); drawAll();
  if(S.view==="preview" && window.Preview3D&&window.Preview3D.ready) window.Preview3D.resize();
});
function drawAll(){ drawPreview(); if(S.view==="curves")drawCurves(); if(S.view==="phonemes")drawPhonemes(); if(S.view==="facegraph")drawFaceGraph(); }

/* linear-sample a channel [[t,v]...] at time t */
function sample(keys,t){ if(!keys||!keys.length)return 0;
  if(t<=keys[0][0])return keys[0][1]; const n=keys.length; if(t>=keys[n-1][0])return keys[n-1][1];
  for(let i=1;i<n;i++){ if(t<=keys[i][0]){ const[a,va]=keys[i-1],[b,vb]=keys[i]; const f=b===a?0:(t-a)/(b-a); return va+(vb-va)*f; } }
  return keys[n-1][1]; }
const chan=name=>S.track&&S.track.channels.find(c=>c.name===name);

/* ---- Preview (schematic face blends visemes + gestures) -------------- */
const SHAPES={sil:[.30,.03,.2],PP:[.34,.02,.1],FF:[.34,.10,.1],TH:[.34,.16,.2],DD:[.34,.18,.2],
  kk:[.34,.20,.3],CH:[.24,.20,.8],SS:[.30,.08,.2],nn:[.32,.16,.2],RR:[.26,.20,.6],
  aa:[.40,.55,.4],E:[.44,.30,.2],I:[.50,.14,.1],O:[.30,.42,.9],U:[.22,.22,1]};
function drawPreview(){
  const p3d=window.Preview3D;
  if(p3d&&p3d.ready){ if(S.track) drive3D(p3d); }
  else drawSchematic();
  if(S.track){ $("#tcRead").textContent=fmt(S.t); $("#tpTime").textContent=fmt(S.t); }
}

/* drive the 3D head: retarget visemes -> ARKit in JS, + gestures + head pose */
function drive3D(p3d){
  const arkit={};
  if(S.arkitMap) for(const [vis,tgts] of Object.entries(S.arkitMap)){
    const c=chan(vis); if(!c) continue; const v=Math.max(0,sample(c.keys,S.t)); if(v<1e-4) continue;
    for(const [t,w] of tgts) arkit[t]=Math.min(1,(arkit[t]||0)+v*w);
  }
  const g={ blink_L:sampleName("blink_L"), blink_R:sampleName("blink_R"), blink:sampleName("blink"),
    browUp:sampleName("browUp"), browInnerUp:sampleName("browInnerUp"), browOuterUp:sampleName("browOuterUp") };
  const rad=d=>(d||0)*Math.PI/180;
  p3d.update(arkit,g,{ pitch:rad(sampleSigned("headPitch")), yaw:rad(sampleSigned("headYaw")), roll:rad(sampleSigned("headRoll")) });
}
function sampleSigned(n){ const c=chan(n); return c?sample(c.keys,S.t):0; }

function drawSchematic(){
  if(!S.track){ return; }
  let W=0,w=0,h=0,r=0;
  for(const c of S.track.channels){ const s=SHAPES[c.name]; if(!s)continue;
    const v=Math.max(0,sample(c.keys,S.t)); if(v<1e-3)continue; W+=v; w+=v*s[0]; h+=v*s[1]; r+=v*s[2]; }
  if(W<1e-3){[w,h,r]=SHAPES.sil;} else {w/=W;h/=W;r/=W;}
  const cx=130,cy=150,rx=Math.max(9,w*130),ry=Math.max(3,h*95),k=.55*ry+r*.25*rx;
  $("#mouth").setAttribute("d",
    `M ${cx-rx} ${cy} C ${cx-rx} ${cy-k}, ${cx-rx*(1-r*.3)} ${cy-ry}, ${cx} ${cy-ry}`+
    ` C ${cx+rx*(1-r*.3)} ${cy-ry}, ${cx+rx} ${cy-k}, ${cx+rx} ${cy}`+
    ` C ${cx+rx} ${cy+k}, ${cx+rx*(1-r*.3)} ${cy+ry}, ${cx} ${cy+ry}`+
    ` C ${cx-rx*(1-r*.3)} ${cy+ry}, ${cx-rx} ${cy+k}, ${cx-rx} ${cy} Z`);
  // eyes: blink channels close the lids
  const bl=Math.max(sampleName("blink_L"),sampleName("blink")), br=Math.max(sampleName("blink_R"),sampleName("blink"));
  $("#eyeL").setAttribute("ry",Math.max(1,9*(1-bl))); $("#eyeR").setAttribute("ry",Math.max(1,9*(1-br)));
  // brows: browUp raises
  const bu=Math.max(sampleName("browUp"),sampleName("browInnerUp"))*8;
  $("#browL").innerHTML=`<rect x="78" y="${66-bu}" width="32" height="3.4" rx="2" fill="#2b3745"/>`;
  $("#browR").innerHTML=`<rect x="150" y="${66-bu}" width="32" height="3.4" rx="2" fill="#2b3745"/>`;
  $("#tcRead").textContent=fmt(S.t); $("#tpTime").textContent=fmt(S.t);
}
function sampleName(n){ const c=chan(n); return c?Math.max(0,sample(c.keys,S.t)):0; }

/* ---- canvas helpers ------------------------------------------------- */
function fitCanvas(cv){ const r=cv.getBoundingClientRect(); const dpr=devicePixelRatio||1;
  cv.width=Math.max(2,r.width*dpr); cv.height=Math.max(2,(cv.clientHeight||r.height)*dpr);
  const x=cv.getContext("2d"); x.setTransform(dpr,0,0,dpr,0,0); return {x,w:r.width,h:cv.clientHeight||r.height}; }
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();

/* ---- Curves --------------------------------------------------------- */
function drawCurves(){
  const cv=$("#curves"); if(!S.track)return; const {x,w,h}=fitCanvas(cv);
  x.clearRect(0,0,w,h); const padL=8,padR=8,padT=8,padB=18, gw=w-padL-padR, gh=h-padT-padB;
  const T=Math.max(0.001,S.duration);
  // grid
  x.strokeStyle=css("--line"); x.lineWidth=1; x.fillStyle=css("--fg-mute"); x.font="10px "+css("--font-mono");
  for(let i=0;i<=8;i++){ const gx=padL+gw*i/8; x.globalAlpha=.5; x.beginPath();x.moveTo(gx,padT);x.lineTo(gx,padT+gh);x.stroke(); x.globalAlpha=1; x.fillText((T*i/8).toFixed(1),gx+2,h-6); }
  for(let j=0;j<=4;j++){ const gy=padT+gh*j/4; x.globalAlpha=.4; x.beginPath();x.moveTo(padL,gy);x.lineTo(padL+gw,gy);x.stroke(); x.globalAlpha=1; }
  // curves
  const smooth=$("#curvesSmooth").checked;
  for(const c of S.track.channels){ const m=S.chan[c.name]; if(!m.visible)continue;
    x.strokeStyle=m.color; x.lineWidth=(c.name===S.sel)?2.4:1.5; x.globalAlpha=(S.sel&&c.name!==S.sel)?.5:1;
    x.beginPath(); const N=Math.max(2,Math.floor(gw));
    for(let i=0;i<=N;i++){ const tt=T*i/N; const v=Math.min(1,Math.max(0,sample(c.keys,tt)));
      const px=padL+gw*i/N, py=padT+gh*(1-v); i?x.lineTo(px,py):x.moveTo(px,py); }
    x.stroke();
  }
  x.globalAlpha=1;
  // playhead
  const hx=padL+gw*(S.t/T); x.strokeStyle=css("--accent"); x.lineWidth=1.5;
  x.beginPath();x.moveTo(hx,padT);x.lineTo(hx,padT+gh);x.stroke();
}

/* ---- Phonemes (waveform + strip) ------------------------------------ */
function peaks(data,n){ const out=new Float32Array(n); const step=Math.max(1,Math.floor(data.length/n));
  for(let i=0;i<n;i++){ let mx=0; for(let j=0;j<step;j++){ const v=Math.abs(data[i*step+j]||0); if(v>mx)mx=v; } out[i]=mx; } return out; }
function drawPhonemes(){
  const cv=$("#wave"); if(!S.track)return; const {x,w,h}=fitCanvas(cv); x.clearRect(0,0,w,h);
  const T=Math.max(.001,S.duration); const mid=h/2;
  x.fillStyle=css("--panel-2"); x.fillRect(0,0,w,h);
  // waveform: real peaks if audio, else synthetic openness envelope
  x.strokeStyle=css("--accent-2"); x.globalAlpha=.85; x.beginPath();
  if(S.wavPeaks){ for(let i=0;i<w;i++){ const p=S.wavPeaks[Math.floor(S.wavPeaks.length*i/w)]||0; x.moveTo(i,mid-p*mid*.9); x.lineTo(i,mid+p*mid*.9); } x.stroke(); }
  else { for(let i=0;i<=w;i++){ const tt=T*i/w; let o=0; for(const c of S.track.channels){ if(SHAPES[c.name]&&c.name!=="sil")o+=Math.max(0,sample(c.keys,tt)); } o=Math.min(1,o); const y=mid-o*mid*.9; i?x.lineTo(i,y):x.moveTo(i,y);} x.stroke();
    x.beginPath(); for(let i=0;i<=w;i++){ const tt=T*i/w; let o=0; for(const c of S.track.channels){ if(SHAPES[c.name]&&c.name!=="sil")o+=Math.max(0,sample(c.keys,tt)); } o=Math.min(1,o); const y=mid+o*mid*.9; i?x.lineTo(i,y):x.moveTo(i,y);} x.stroke(); }
  x.globalAlpha=1;
  // playhead
  x.strokeStyle=css("--accent"); x.beginPath(); const hx=w*(S.t/T); x.moveTo(hx,0);x.lineTo(hx,h);x.stroke();
  drawStrip();
}
function drawStrip(){
  const cv=$("#phonStrip"); const {x,w,h}=fitCanvas(cv); x.clearRect(0,0,w,h);
  const T=Math.max(.001,S.duration); x.font="11px "+css("--font-mono"); x.textBaseline="middle";
  for(const s of S.segments){ const a=w*(s.start/T), b=w*(s.end/T); const sil=(s.phoneme||"").toLowerCase()==="sil"||s.phoneme==="_";
    x.fillStyle=sil?css("--panel-2"):css("--elev"); x.fillRect(a+1,4,Math.max(1,b-a-2),h-8);
    x.strokeStyle=css("--line"); x.strokeRect(a+1,4,Math.max(1,b-a-2),h-8);
    if(b-a>14){ x.fillStyle=sil?css("--fg-mute"):css("--fg"); x.fillText(s.phoneme,a+5,h/2); } }
  x.strokeStyle=css("--accent"); x.beginPath(); const hx=w*(S.t/T); x.moveTo(hx,0);x.lineTo(hx,h);x.stroke();
}

/* ---- Face Graph (input visemes -> preset targets via link fns) ------ */
function initFaceGraphPresets(){
  const sel=$("#fgPreset"); sel.innerHTML=S.presets.map(p=>`<option ${p==="arkit"?"selected":""}>${p}</option>`).join("");
  sel.onchange=async()=>{ S.presetSel=sel.value; S.presetMap=await Pipe.presetMap(sel.value); if(S.view==="facegraph")drawFaceGraph(); };
}
async function drawFaceGraph(){
  const cv=$("#facegraph"); const {x,w,h}=fitCanvas(cv); x.clearRect(0,0,w,h); x.fillStyle=css("--panel-2"); x.fillRect(0,0,w,h);
  if(!S.presetMap){ S.presetMap=await Pipe.presetMap(S.presetSel); }
  const inputs=Object.keys(S.presetMap); const outs=[...new Set(Object.values(S.presetMap).flat().map(p=>p[0]))];
  const colL=w*.26, colR=w*.74, iy=h/(inputs.length+1), oy=h/(outs.length+1);
  const inPos={}, outPos={};
  inputs.forEach((n,i)=>inPos[n]=[colL,iy*(i+1)]); outs.forEach((n,i)=>outPos[n]=[colR,oy*(i+1)]);
  // links
  for(const [inp,tgts] of Object.entries(S.presetMap)) for(const [t,wt] of tgts){
    const a=inPos[inp], b=outPos[t]; if(!a||!b)continue;
    x.strokeStyle=css("--line-2"); x.globalAlpha=.35+wt*.55; x.lineWidth=.6+wt*2.2;
    x.beginPath(); x.moveTo(a[0]+6,a[1]); x.bezierCurveTo((a[0]+b[0])/2,a[1],(a[0]+b[0])/2,b[1],b[0]-6,b[1]); x.stroke(); }
  x.globalAlpha=1;
  const node=(px,py,label,fill)=>{ x.fillStyle=fill; x.strokeStyle=css("--line-2"); x.lineWidth=1;
    const tw=x.measureText(label).width, bw=Math.max(46,tw+18); roundRect(x,px-bw/2,py-11,bw,22,6); x.fill(); x.stroke();
    x.fillStyle=css("--fg"); x.font="12px "+css("--font-mono"); x.textAlign="center"; x.textBaseline="middle"; x.fillText(label,px,py); };
  x.font="12px "+css("--font-mono");
  for(const [n,[px,py]] of Object.entries(inPos)) node(px,py,n,css("--elev"));
  for(const [n,[px,py]] of Object.entries(outPos)) node(px,py,n,"color-mix(in srgb,"+css("--accent")+" 18%, "+css("--elev")+")");
  x.textAlign="left";
  x.fillStyle=css("--fg-dim"); x.font="11px "+css("--font-ui");
  x.fillText("inputs — visemes", colL-60, 16); x.fillText("outputs — "+S.presetSel+" rig", colR-60, 16);
}
function roundRect(x,a,b,w,h,r){ x.beginPath(); x.moveTo(a+r,b); x.arcTo(a+w,b,a+w,b+h,r); x.arcTo(a+w,b+h,a,b+h,r); x.arcTo(a,b+h,a,b,r); x.arcTo(a,b,a+w,b,r); x.closePath(); }

/* ===================================================================== *
 *  Export grid
 * ===================================================================== */
const EXPORTS=[
  ["json","Track JSON",".track.json","Canonical OpenFaceFX track — re-import, diff, convert."],
  ["glb","glTF 2.0",".glb","Morph-target animation for Blender / Three.js / engines."],
  ["vrma","VRM Animation",".vrma","VRM 1.0 expression clip (VRMC_vrm_animation) + emotion."],
  ["spine","Spine",".spine.json","Esoteric Spine slot-attachment lip-sync (2D games)."],
  ["live2d","Live2D motion",".motion3.json","Cubism lip-sync parameter curves."],
  ["exp3","Live2D expression",".exp3.json","A frozen pose as a hotkey-bindable expression."],
  ["unity","Unity clip",".anim","AnimationClip with viseme blendshape curves."],
  ["godot","Godot",".tres","AnimationPlayer resource."],
  ["vmd","MikuMikuDance",".vmd","MMD morph animation."],
  ["livelink","ARKit / Live Link",".livelink.csv","52-blendshape wide CSV (retargets to ARKit)."],
  ["a2f","NVIDIA Audio2Face",".a2f.json","facsNames/weightMat blendshape JSON."],
  ["rhubarb","Rhubarb cues",".tsv","Stepped mouth-shape cue list."],
  ["moho","Moho / OpenToonz",".dat","Switch-data mouth cues."],
  ["csv","CSV",".csv","One row per keyframe."],
];
function buildExportGrid(){
  $("#exportGrid").innerHTML=EXPORTS.map(([fmt,label,ext,desc])=>
    `<div class="exp-card"><h4>${label} <span class="ext">${ext}</span></h4><p>${desc}</p>
     <button class="btn" data-fmt="${fmt}">Export</button></div>`).join("");
  $$("#exportGrid button").forEach(b=>b.onclick=async()=>{
    if(!S.track){ alert("Generate a take first."); return; }
    b.disabled=true; const was=b.textContent; b.textContent="…";
    try{ const r=await Pipe.export(b.dataset.fmt); if(r.error)throw new Error(r.error);
      const blob=new Blob([Uint8Array.from(atob(r.b64),c=>c.charCodeAt(0))]);
      const a=document.createElement("a"); a.href=URL.createObjectURL(blob); a.download=r.filename; a.click();
      b.textContent="✓ "+r.filename.split(".").slice(-1); setTimeout(()=>b.textContent=was,1800);
    }catch(err){ b.textContent="failed"; alert("Export failed: "+err.message); setTimeout(()=>b.textContent=was,1800); }
    b.disabled=false;
  });
}

/* ===================================================================== *
 *  Transport
 * ===================================================================== */
function fmt(t){ t=Math.max(0,t); const m=Math.floor(t/60), s=Math.floor(t%60), ms=Math.floor((t*1000)%1000);
  return `${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}.${String(ms).padStart(3,"0")}`; }
function setScrub(){ $("#scrub").value=Math.round(1000*(S.duration?S.t/S.duration:0)); }
$("#scrub").oninput=e=>{ if(!S.duration)return; S.t=S.duration*e.target.value/1000; drawAll(); };
$("#tpStart").onclick=()=>{S.t=0;setScrub();drawAll();};
$("#tpEnd").onclick=()=>{S.t=S.duration;setScrub();drawAll();};
$("#tpPlay").onclick=togglePlay; $("#tpPlay").textContent="▶";
function togglePlay(){ if(!S.track)return; S.playing=!S.playing; $("#tpPlay").textContent=S.playing?"⏸":"▶";
  if(S.playing){ S.lastTs=0; requestAnimationFrame(loop); } }
function loop(ts){ if(!S.playing)return; if(S.lastTs)S.t+=(ts-S.lastTs)/1000; S.lastTs=ts;
  if(S.t>=S.duration){S.t=0;} setScrub(); drawAll(); requestAnimationFrame(loop); }

/* ===================================================================== *
 *  WAV encode (Float32 -> 16-bit mono, for the stdlib wave reader)
 * ===================================================================== */
function toB64(u8){ let s="",C=0x8000; for(let i=0;i<u8.length;i+=C) s+=String.fromCharCode.apply(null,u8.subarray(i,i+C)); return btoa(s); }
function encodeWav(f32,rate){ const n=f32.length,buf=new ArrayBuffer(44+n*2),dv=new DataView(buf);
  const ws=(o,s)=>{for(let i=0;i<s.length;i++)dv.setUint8(o+i,s.charCodeAt(i));};
  ws(0,"RIFF");dv.setUint32(4,36+n*2,true);ws(8,"WAVE");ws(12,"fmt ");dv.setUint32(16,16,true);
  dv.setUint16(20,1,true);dv.setUint16(22,1,true);dv.setUint32(24,rate,true);dv.setUint32(28,rate*2,true);
  dv.setUint16(32,2,true);dv.setUint16(34,16,true);ws(36,"data");dv.setUint32(40,n*2,true);
  let o=44;for(let i=0;i<n;i++){let x=Math.max(-1,Math.min(1,f32[i]));dv.setInt16(o,x<0?x*0x8000:x*0x7fff,true);o+=2;}
  return new Uint8Array(buf); }

/* ===================================================================== *
 *  Chrome: theme, resize
 * ===================================================================== */
$("#themeToggle").onclick=()=>{ const r=document.documentElement;
  r.dataset.theme=r.dataset.theme==="light"?"dark":"light"; drawAll(); };
let rt; addEventListener("resize",()=>{ clearTimeout(rt); rt=setTimeout(()=>{ drawAll();
  window.Preview3D&&window.Preview3D.ready&&window.Preview3D.resize(); },120); });

/* the 3D head finished loading → swap the schematic SVG for the WebGL canvas */
addEventListener("preview3d-ready",()=>{
  const c=$("#face3d"), s=$("#face"); if(c){ c.hidden=false; } if(s){ s.style.display="none"; }
  const cap=document.querySelector(".preview-readout .dim");
  if(cap) cap.textContent="ARKit-blendshape 3D head, driven by the take — drag to orbit, scroll to zoom";
  window.Preview3D.setActive(true); window.Preview3D.resize(); drawPreview();
});

bootstrap();
