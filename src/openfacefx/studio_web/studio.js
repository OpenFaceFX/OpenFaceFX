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
  track:null, segments:[], words:[], duration:0, fps:60,
  wavBytes:null, wavPeaks:null,
  chan:{},               // name -> {color, visible, idx}
  sel:null,              // selected channel name
  view:"preview",
  t:0, playing:false, lastTs:0, playClock:0,
  presets:[], presetSel:"arkit", presetMap:null,
  actors:[{name:"Untitled", takes:[]}], actorIdx:0, takeIdx:-1,   // actors → takes
  inspectKind:null, node:null, fgNodes:[], solo:null,             // inspector + graph hit-testing
  undo:[], redo:[],                                               // curve-edit history
  events:[],                                                      // derived event layer (Events tab)
  phonMap:null, mapCustom:false,                                  // Mapping tab: editable phoneme→viseme map
};
const esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const DEFAULT_PARAMS={ text:"Hello world — this is OpenFaceFX Studio, running the real pipeline right in your browser.",
  engine:"naive", dur:"4.0", style:"", gestures:false, breath:false, fps:"60" };

/* bridge for assistant.js + account.js (separate scripts) to read/write context */
window.StudioBridge = {
  transcript:()=>$("#text").value,
  setTranscript:t=>{ $("#text").value=t; },
  track:()=>S.track, segments:()=>S.segments,
  native:()=>S.native,
  // --- write surface: AI actions / controls mutate the current take through here ---
  hasTake:()=>!!S.track,
  regenerate:()=>runGenerate(true),                   // rebuild the take, preserving hand-edited channels
  setParams:(p)=>{ if(!p||typeof p!=="object") return false;
    const STY=["","whisper","mumble","neutral","broadcast","tense","exaggerated","broad","shout"];
    if(p.text!=null) $("#text").value=String(p.text);
    if(p.engine!=null && ["naive","energy"].includes(p.engine)) $("#engine").value=p.engine;
    if(p.dur!=null){ const d=parseFloat(p.dur); if(isFinite(d)&&d>0) $("#dur").value=String(Math.min(600,Math.max(0.3,d))); }
    if(p.style!=null && STY.includes(p.style)) $("#style").value=p.style;
    if(p.gestures!=null) $("#optGestures").checked=!!p.gestures;
    if(p.breath!=null) $("#optBreath").checked=!!p.breath;
    if(p.fps!=null){ const f=parseFloat(p.fps); if(isFinite(f)&&f>=1&&f<=120){ $("#fps").value=String(f); S.fps=f; } }
    return true; },
  selectChannel:(name)=>{ if(chan(name)){ selChannel(name); return true; } return false; },
  // replace the current take's track wholesale (e.g. emotion bake) + refresh views
  applyTrack:(trackDict)=>{ if(!trackDict||!Array.isArray(trackDict.channels)) return false;
    const tk=curTake(); if(tk) tk.track=trackDict; S.track=trackDict;
    ingestChannels(); buildChannelList(); buildInspector(); drawAll(); setScrub(); return true; },
  bakeEmotion:(env,intensity)=>Pipe.bakeEmotion(env,intensity),   // -> baked track dict
  normalize:(text)=>Pipe.normalize(text),                          // deterministic, keyless
  setCmudict:(text)=>{ const tk=curTake(); if(!tk) return false; tk.cmudict=text||""; return true; },  // pronunciation override for next regenerate
  // serialise the whole workspace (actors → takes: params + track), JSON-safe
  getWorkspace:()=>({ v:1, actorIdx:S.actorIdx, takeIdx:S.takeIdx,
    actors:S.actors.map(a=>({ name:a.name, takes:a.takes.map(t=>({
      name:t.name, params:t.params, wavName:t.wavName, colors:t.colors||undefined, cmudict:t.cmudict||undefined,
      track:t.track, segments:t.segments, words:t.words||undefined, duration:t.duration,
      peaks:t.wavPeaks?Array.from(t.wavPeaks):undefined,                 // waveform survives save/load
      wavB64:(t.wavBytes&&t.wavBytes.length<1500000)?toB64(t.wavBytes):undefined })) })) }),  // audio too if it fits the vault cap
  setWorkspace:w=>{ if(!w||!Array.isArray(w.actors)||!w.actors.length) return false;
    const fromB64=s=>{ try{ return Uint8Array.from(atob(s),c=>c.charCodeAt(0)); }catch(_){ return null; } };
    S.actors=w.actors.map(a=>({ name:a.name||"Untitled", takes:(a.takes||[]).map(t=>({
      name:t.name||"take_01", params:t.params||{...DEFAULT_PARAMS}, wavName:t.wavName||"no audio — timing from text",
      colors:t.colors||null, cmudict:t.cmudict||"",
      wavBytes:t.wavB64?fromB64(t.wavB64):null, wavPeaks:t.peaks?Float32Array.from(t.peaks):null,
      track:t.track||null, segments:t.segments||[], words:t.words||[], duration:t.duration||0 })) }));
    S.actorIdx=Math.min(Math.max(0,w.actorIdx||0), S.actors.length-1);
    const nt=curActor().takes.length;
    S.takeIdx=(w.takeIdx!=null)?Math.min(Math.max(-1,w.takeIdx), nt-1):(nt?0:-1);
    loadTake(); refreshIO(); return true; },
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
    GestureParams, add_gestures_to_track, to_dict, from_dict, retarget, PRESETS,
    normalize_transcript, bake_emotion, EmotionEnvelope)
from openfacefx.alignment import dump_segments

def _style_coart(style):
    if not style: return None
    try:
        from openfacefx.coarticulation import style_params
        return style_params(style)
    except Exception:
        return None

def studio_generate(text, engine, dur, style, gestures, breath, has_wav, fps, cmudict='', mapping_json=''):
    fps = float(fps) or 60.0
    if has_wav:
        try: dur = offx.wav_duration('/tmp/in.wav')
        except Exception: pass
    dur = float(dur)
    g2p = None
    if cmudict:
        try:
            with open('/tmp/cmu.dict','w') as f: f.write(cmudict)
            from openfacefx.g2p import G2P
            g2p = G2P(); g2p.load_cmudict('/tmp/cmu.dict')
        except Exception: g2p = None
    mapping = None
    if mapping_json:
        try:
            with open('/tmp/map.json','w') as f: f.write(mapping_json)
            from openfacefx.mapping import Mapping
            mapping = Mapping.from_json('/tmp/map.json')
        except Exception: mapping = None
    segs = naive_segments(text, dur, g2p=g2p)
    coart = _style_coart(style)
    gkw = {'fps': fps}
    if coart is not None: gkw['coart'] = coart
    if mapping is not None: gkw['mapping'] = mapping
    if has_wav and engine == 'energy':
        try: track = generate_naive(text, dur, wav='/tmp/in.wav', fps=fps, g2p=g2p)
        except TypeError:
            try: track = generate_naive(text, dur, wav='/tmp/in.wav', g2p=g2p)
            except TypeError: track = generate_naive(text, dur, wav='/tmp/in.wav')
    else:
        try:
            track = generate_from_alignment(segs, **gkw)
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
    try:
        from openfacefx import word_timings
        words = [[wt[0], round(float(wt[1]),4), round(float(wt[2]),4)] for wt in word_timings(text, dur, g2p)]
    except Exception:
        words = []
    return json.dumps({
        "track": to_dict(track),
        "segments": dump_segments(segs),
        "duration": round(track.duration,4),
        "fps": track.fps,
        "words": words,
    })

def studio_presets():
    return json.dumps(sorted(PRESETS))

def studio_preset_map(name):
    m = PRESETS.get(name, {})
    return json.dumps({v: [[t,round(float(w),3)] for (t,w) in tgts] for v,tgts in m.items()})

def studio_normalize(text):
    # deterministic, keyless Unicode->ASCII transcript folds (qa.normalize_transcript)
    out, subs = normalize_transcript(text or "")
    return json.dumps({"text": out, "subs": subs})

def studio_bake_emotion(track_json, envelope_json, intensity):
    # additive emotion bake onto the current take (emotion.bake_emotion)
    try:
        tk = from_dict(json.loads(track_json))
        env = EmotionEnvelope.from_dict(json.loads(envelope_json))
        baked = bake_emotion(tk, env, intensity=float(intensity))
        return json.dumps({"track": to_dict(baked)})
    except Exception as e:
        return json.dumps({"error": str(e)})

def studio_qa(track_json, segments_json, text):
    # deterministic QA (qa.summarize): cue-timing outliers, OOV words, warnings
    from types import SimpleNamespace
    from openfacefx import summarize
    tk = from_dict(json.loads(track_json)) if track_json and track_json != 'null' else None
    segs = [SimpleNamespace(phoneme=s.get('phoneme'), start=float(s.get('start',0) or 0),
            end=float(s.get('end',0) or 0), confidence=s.get('confidence'))
            for s in json.loads(segments_json or '[]')]
    oov = []
    try:
        from openfacefx.g2p import G2P
        oov = G2P().oov_words(text or '')
    except Exception:
        pass
    return json.dumps(summarize(tk, segments=segs, oov_words=oov))

def studio_events(segments_json, emphasis=True, phrase=True):
    # auto-author a typed event layer from the speech (pipeline.derive_events):
    # emphasis on stressed syllables, phrase markers at pauses.
    from openfacefx.alignment import PhonemeSegment
    from openfacefx import derive_events
    from openfacefx.events import event_to_dict
    segs = [PhonemeSegment(phoneme=s.get('phoneme'), start=float(s.get('start',0) or 0),
            end=float(s.get('end',0) or 0), confidence=s.get('confidence'))
            for s in json.loads(segments_json or '[]')]
    evs = derive_events(segments=segs, emphasis=bool(emphasis), phrase=bool(phrase))
    return json.dumps({"events": [event_to_dict(e) for e in evs]})

def studio_mapping_default():
    # the built-in phoneme->viseme mapping as {phoneme:[[viseme,weight]...]}
    from openfacefx.mapping import Mapping
    m = Mapping.default()
    return json.dumps({ph: [[t, round(float(w),3)] for t,w in row.items()]
                       for ph,row in m.rows.items()})

def studio_mapping_json(edit_json, base_preset):
    # serialise the edited {phoneme:[[viseme,weight]...]} to a canonical
    # openfacefx.mapping file (Mapping.to_json). Targets carry only their name
    # (default class/min/max), which round-trips through retarget --mapping.
    import tempfile
    from openfacefx.mapping import Mapping, Target
    edit = json.loads(edit_json or '{}')
    rows, used, seen = {}, [], set()
    for vis, tgts in edit.items():
        r = {}
        for tw in tgts:
            t = tw[0] if tw else None
            if not t: continue
            try: r[t] = float(tw[1])
            except (TypeError, ValueError, IndexError): continue
            if t not in seen: seen.add(t); used.append(t)
        if r: rows[vis] = r
    targets = [Target(n) for n in used] or [Target("_none")]
    p = os.path.join(tempfile.mkdtemp(), 'm.json'); Mapping(targets, rows).to_json(p)
    with open(p) as f: return json.dumps({"json": f.read()})

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
    const out=await fn(args.text,args.engine,args.dur,args.style,args.gestures,args.breath,args.has_wav,args.fps,args.cmudict||"",args.mapping_json||"");
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
  },
  async normalize(text){
    if(S.native) return fetch("/api/normalize",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({text})}).then(r=>r.json());
    const fn=S.pyodide.globals.get("studio_normalize"); const r=JSON.parse(fn(text||"")); fn.destroy(); return r;
  },
  async bakeEmotion(env,intensity){
    if(S.native) return fetch("/api/bake_emotion",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({track:S.track,envelope:env,intensity})}).then(r=>r.json());
    const fn=S.pyodide.globals.get("studio_bake_emotion"); const r=JSON.parse(fn(JSON.stringify(S.track),JSON.stringify(env),intensity)); fn.destroy(); return r;
  },
  async qa(){
    const text=$("#text").value||"";
    if(S.native) return fetch("/api/qa",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({track:S.track,segments:S.segments,text})}).then(r=>r.json());
    const fn=S.pyodide.globals.get("studio_qa"); const r=JSON.parse(fn(S.track?JSON.stringify(S.track):"null",JSON.stringify(S.segments||[]),text)); fn.destroy(); return r;
  },
  async events(emphasis,phrase){
    if(S.native) return fetch("/api/events",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({segments:S.segments,emphasis,phrase})}).then(r=>r.json());
    const fn=S.pyodide.globals.get("studio_events"); const r=JSON.parse(fn(JSON.stringify(S.segments||[]),!!emphasis,!!phrase)); fn.destroy(); return r;
  },
  async mappingJson(edit,preset){
    if(S.native) return fetch("/api/mapping_json",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({edit,preset})}).then(r=>r.json());
    const fn=S.pyodide.globals.get("studio_mapping_json"); const r=JSON.parse(fn(JSON.stringify(edit),preset)); fn.destroy(); return r;
  },
  async mappingDefault(){
    if(S.native) return fetch("/api/mapping_default").then(r=>r.json());
    const fn=S.pyodide.globals.get("studio_mapping_default"); const r=JSON.parse(fn()); fn.destroy(); return r;
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

async function runGenerate(preserve){
  const btn=$("#run"); btn.disabled=true; btn.textContent="Generating…";
  try{
    S.fps=parseFloat($("#fps").value)||60;
    const hasWav=!!S.wavBytes && $("#engine").value==="energy";
    let wav_b64;
    if(hasWav){ if(S.native) wav_b64=toB64(S.wavBytes); else S.pyodide.FS.writeFile("/tmp/in.wav",S.wavBytes); }
    const mapping_json=($("#mapApply")&&$("#mapApply").checked)?await customMappingJson():"";  // custom phoneme→viseme mapping (#15)
    const res=await Pipe.generate({
      text:$("#text").value.trim()||"hello", engine:$("#engine").value,
      dur:parseFloat($("#dur").value)||4, style:$("#style").value,
      gestures:$("#optGestures").checked, breath:$("#optBreath").checked,
      has_wav:hasWav, wav_b64, fps:S.fps, cmudict:(curTake()&&curTake().cmudict)||"", mapping_json });
    if(res.error) throw new Error(res.error);
    if(!curTake()) newTakeSlot();          // first Generate creates take_01
    const tk=curTake();
    let newTrack=res.track;
    // edit-ownership (#8): a Reanalyze rebuilds but KEEPS the user's hand-edited channels
    if(preserve && tk.track && tk.owned && Object.keys(tk.owned).length){
      const oldBy={}; tk.track.channels.forEach(c=>oldBy[c.name]=c);
      newTrack={...res.track, channels:res.track.channels.map(c=>
        (tk.owned[c.name]&&oldBy[c.name])?{name:c.name,keys:structuredClone(oldBy[c.name].keys)}:c)};
      for(const nm in tk.owned){ if(oldBy[nm]&&!newTrack.channels.some(c=>c.name===nm)) newTrack.channels.push({name:nm,keys:structuredClone(oldBy[nm].keys)}); }
    }
    tk.params={...captureParams()}; tk.wavBytes=S.wavBytes; tk.wavPeaks=S.wavPeaks; tk.wavName=$("#wavName").textContent;
    tk.track=newTrack; tk.segments=res.segments||[]; tk.words=res.words||[]; tk.duration=res.duration;
    if(!preserve){ tk.edited=false; tk.owned={}; }    // full Generate drops ownership; Reanalyze keeps it
    if(!preserve){ S.events=[]; } else if(S.events&&S.events.length){ newTrack.events=S.events; }  // event layer: reset on full gen, carried on Reanalyze
    S.track=newTrack; S.segments=res.segments||[]; S.words=res.words||[]; S.duration=res.duration; S.t=0;
    S.undo.length=0; S.redo.length=0;                 // rebuild clears undo history
    ingestChannels(); buildChannelList(); buildInspector(); drawAll(); setScrub(); refreshUndoButtons(); updateReanalyze();
    $("#tpDur").textContent="/ "+fmt(S.duration); refreshIO();
    btn.textContent="Generate take"; btn.disabled=false; return true;
  }catch(err){ btn.textContent="Generate — failed"; console.error(err); alert("Generate failed: "+err.message); btn.disabled=false; return false; }
}
$("#run").onclick=()=>{ const tk=curTake();
  if(tk&&tk.edited&&S.track&&!confirm("Generate replaces the whole take, discarding your hand edits.\n\nUse “Reanalyze — keep my edits” to rebuild but preserve edited channels.\n\nReplace anyway?")) return;
  return runGenerate(false); };
$("#reanalyze")&&($("#reanalyze").onclick=()=>runGenerate(true));
function updateReanalyze(){ const b=$("#reanalyze"); if(b) b.hidden=!(curTake()&&curTake().owned&&Object.keys(curTake().owned).length); }

function ingestChannels(){
  const tk=curTake(); const cols=(tk&&tk.colors)||{}, own=(tk&&tk.owned)||{};   // colours + edit-ownership persist
  S.chan={}; S.track.channels.forEach((c,i)=>{ S.chan[c.name]={color:cols[c.name]||CURVE_COLORS[i%CURVE_COLORS.length],visible:true,idx:i,owned:own[c.name]?"user":null}; });
}
const toHex=v=>/^#[0-9a-f]{6}$/i.test(v||"")?v:"#f4b942";   // <input type=color> needs #rrggbb
function setChannelColor(name,hex){ if(!S.chan[name])return; S.chan[name].color=hex;
  const tk=curTake(); if(tk){ tk.colors=tk.colors||{}; tk.colors[name]=hex; }
  buildChannelList(); if(S.view==="curves")drawCurves(); }
/* mark a channel hand-edited so a Reanalyze / AI regenerate keeps it (#8) */
function markChannelOwned(name){ if(S.chan[name]) S.chan[name].owned="user"; const tk=curTake(); if(tk){ tk.owned=tk.owned||{}; tk.owned[name]=true; } if(typeof updateReanalyze==="function") updateReanalyze(); }

/* ===================================================================== *
 *  Actors & takes
 *  An Actor owns a list of Takes; a Take = one generated performance
 *  (its params + audio + resulting track). Switch, add, duplicate, rename,
 *  delete — the form + workspace follow the selection.
 * ===================================================================== */
function curActor(){ return S.actors[S.actorIdx]; }
function curTake(){ const a=curActor(); return (a&&a.takes[S.takeIdx])||null; }
function captureParams(){ return { text:$("#text").value, engine:$("#engine").value, dur:$("#dur").value,
  style:$("#style").value, gestures:$("#optGestures").checked, breath:$("#optBreath").checked, fps:$("#fps").value }; }
function applyParams(p){ p=p||DEFAULT_PARAMS;
  $("#text").value=p.text??""; $("#engine").value=p.engine??"naive"; $("#dur").value=p.dur??"4.0";
  $("#style").value=p.style??""; $("#optGestures").checked=!!p.gestures; $("#optBreath").checked=!!p.breath;
  $("#fps").value=p.fps??60; S.fps=parseFloat($("#fps").value)||60; showFps(null); }
function nextTakeName(a){ const names=new Set(a.takes.map(t=>t.name)); let n=a.takes.length+1;
  const nm=i=>"take_"+String(i).padStart(2,"0"); while(names.has(nm(n)))n++; return nm(n); }
function syncFormToTake(){ const t=curTake(); if(!t)return;
  t.params={...captureParams()}; t.wavBytes=S.wavBytes; t.wavPeaks=S.wavPeaks; t.wavName=$("#wavName").textContent; }
function newTakeSlot(){ const a=curActor(); a.takes.push({ name:nextTakeName(a), params:captureParams(),
  wavBytes:S.wavBytes, wavPeaks:S.wavPeaks, wavName:$("#wavName").textContent, track:null, segments:[], duration:0 });
  S.takeIdx=a.takes.length-1; return curTake(); }
function clearResultOnly(){ S.track=null; S.segments=[]; S.words=[]; S.duration=0; S.t=0; S.sel=null; S.solo=null; S.chan={};
  S.inspectKind=null; S.node=null; S.events=[];
  const list=$("#channelList"); if(list) list.innerHTML='<li class="empty">Generate this take to see its animation channels.</li>';
  $("#chCount").textContent="0"; $("#tpDur").textContent="/ 00:00.000"; buildInspector(); setScrub(); drawAll(); renderEventList(); }
function loadTake(){ const t=curTake();
  if(!t){ applyParams(DEFAULT_PARAMS); S.wavBytes=null; S.wavPeaks=null; $("#wavName").textContent="no audio — timing from text"; clearResultOnly(); return; }
  applyParams(t.params); S.wavBytes=t.wavBytes||null; S.wavPeaks=t.wavPeaks||null;
  $("#wavName").textContent=t.wavName||"no audio — timing from text";
  if(t.track){ S.track=t.track; S.segments=t.segments||[]; S.words=t.words||[]; S.duration=t.duration; S.t=0; S.sel=null; S.solo=null;
    S.inspectKind=null; S.node=null; S.events=(t.track.events)||[]; ingestChannels(); buildChannelList(); buildInspector();
    $("#tpDur").textContent="/ "+fmt(S.duration); setScrub(); drawAll(); renderEventList(); }
  else clearResultOnly(); }
function addTake(){ syncFormToTake(); newTakeSlot(); clearResultOnly(); refreshIO(); }
/* real duplicate — deep-clone the current take (track/segments/audio/edits), not a blank slot */
function dupTake(){ const a=curActor(), src=curTake(); if(!src){ addTake(); return; }
  syncFormToTake();
  const names=new Set(a.takes.map(t=>t.name)); let nm=src.name+" copy", k=2; while(names.has(nm)) nm=src.name+" copy "+(k++);
  a.takes.push({ name:nm, params:{...src.params}, wavBytes:src.wavBytes, wavPeaks:src.wavPeaks,
    wavName:src.wavName, cmudict:src.cmudict||"", track:src.track?structuredClone(src.track):null,
    segments:src.segments?structuredClone(src.segments):[], duration:src.duration||0, edited:!!src.edited });
  S.takeIdx=a.takes.length-1; loadTake(); refreshIO(); }
function addActor(){ syncFormToTake(); const n=S.actors.length+1;
  S.actors.push({ name:"Actor "+String(n).padStart(2,"0"), takes:[] }); S.actorIdx=S.actors.length-1; S.takeIdx=-1;
  applyParams(DEFAULT_PARAMS); S.wavBytes=null; S.wavPeaks=null; $("#wavName").textContent="no audio — timing from text";
  newTakeSlot(); clearResultOnly(); refreshIO(); }
function switchActor(i){ if(i===S.actorIdx)return; syncFormToTake(); S.actorIdx=i;
  S.takeIdx=curActor().takes.length?0:-1; loadTake(); refreshIO(); }
function switchTake(i){ if(i===S.takeIdx)return; syncFormToTake(); S.takeIdx=i; loadTake(); refreshIO(); }
function delTake(){ const a=curActor(); if(!a.takes.length)return;
  a.takes.splice(S.takeIdx,1); S.takeIdx=Math.min(S.takeIdx,a.takes.length-1); loadTake(); refreshIO(); }
function delActor(){ if(S.actors.length<=1){ curActor().takes=[]; S.takeIdx=-1; loadTake(); refreshIO(); return; }
  S.actors.splice(S.actorIdx,1); S.actorIdx=Math.min(S.actorIdx,S.actors.length-1);
  S.takeIdx=curActor().takes.length?0:-1; loadTake(); refreshIO(); }
function refreshIO(){ const asel=$("#actorSelect"), tsel=$("#takeSelect"); if(!asel||!tsel)return;
  asel.innerHTML=S.actors.map((a,i)=>`<option value="${i}" ${i===S.actorIdx?"selected":""}>${esc(a.name)} (${a.takes.length})</option>`).join("");
  const a=curActor();
  tsel.innerHTML=a.takes.length
    ? a.takes.map((t,i)=>`<option value="${i}" ${i===S.takeIdx?"selected":""}>${esc(t.name)}${t.track?"":" ·new"}</option>`).join("")
    : `<option value="-1">— no takes —</option>`;
  if(typeof updateReanalyze==="function") updateReanalyze();
}
function inlineRename(kind,cur,cb){ const sel=(kind==="actor"?$("#actorSelect"):$("#takeSelect")); const r=sel.getBoundingClientRect();
  const inp=document.createElement("input"); inp.className="io-rename"; inp.value=cur; inp.spellcheck=false;
  inp.style.left=r.left+"px"; inp.style.top=r.top+"px"; inp.style.minWidth=r.width+"px";
  document.body.appendChild(inp); inp.focus(); inp.select(); let closed=false;
  const done=commit=>{ if(closed)return; closed=true; const v=inp.value.trim(); if(commit&&v)cb(v); inp.remove(); };
  inp.onkeydown=e=>{ if(e.key==="Enter"){e.preventDefault();done(true);} else if(e.key==="Escape")done(false); };
  inp.onblur=()=>done(true); }
function ioAction(act){ const t=curTake();
  if(act==="take-dup") dupTake();
  else if(act==="take-rename"){ if(t) inlineRename("take",t.name,v=>{t.name=v;refreshIO();}); }
  else if(act==="take-del") delTake();
  else if(act==="actor-rename") inlineRename("actor",curActor().name,v=>{curActor().name=v;refreshIO();});
  else if(act==="actor-del") delActor(); }
function wireIO(){
  $("#actorSelect").onchange=e=>switchActor(+e.target.value);
  $("#takeSelect").onchange=e=>{ const v=+e.target.value; if(v>=0) switchTake(v); };
  $("#actorAdd").onclick=addActor; $("#takeAdd").onclick=addTake;
  const menu=$("#ioMenu");
  $("#ioMenuBtn").onclick=e=>{ e.stopPropagation(); const b=e.currentTarget.getBoundingClientRect();
    menu.style.top=(b.bottom+4)+"px"; menu.style.right=(innerWidth-b.right)+"px"; menu.hidden=!menu.hidden; };
  menu.onclick=e=>{ e.stopPropagation(); const act=e.target.dataset.act; if(!act)return; menu.hidden=true; ioAction(act); };
  addEventListener("click",()=>{ if(menu) menu.hidden=true; });
  refreshIO();
}

/* ===================================================================== *
 *  Channel list + inspector
 * ===================================================================== */
function buildChannelList(){
  const list=$("#channelList"); if(!S.track||!list)return; $("#chCount").textContent=S.track.channels.length;
  list.innerHTML="";
  for(const c of S.track.channels){
    const m=S.chan[c.name];
    const li=document.createElement("li");
    li.className="chan"+(m.visible?"":" off")+(S.sel===c.name?" sel":"")+(S.solo===c.name?" solo":"");
    li.innerHTML=`<span class="sw" style="background:${m.color}"></span>
      <span class="nm">${esc(c.name)}</span>${m.owned?'<span class="own" title="hand-edited — kept on Reanalyze">✎</span>':''}<span class="kc">${c.keys.length}</span>
      <span class="vis">${m.visible?"◉":"○"}</span>`;
    li.querySelector(".vis").onclick=e=>{e.stopPropagation(); m.visible=!m.visible; buildChannelList(); if(S.view==="curves")drawCurves(); if(S.view==="preview")drawPreview();};
    li.onclick=()=>selChannel(c.name);
    list.appendChild(li);
  }
}
function selChannel(name){ S.sel=name; S.inspectKind="channel"; S.node=null; buildChannelList(); buildInspector();
  if(S.view==="curves")drawCurves(); }

function buildInspector(){
  const box=$("#inspector"); if(!box)return;
  if(S.inspectKind==="node" && S.node) return renderNodeInspector(box,S.node);
  if(!S.track || !S.sel || S.inspectKind!=="channel"){
    box.innerHTML='<p class="empty">Select a channel (left) or a Face&nbsp;Graph node to inspect it. On the Curves tab, click a curve to select it, then drag its keyframe dots to edit.</p>'; return; }
  const c=chan(S.sel), m=S.chan[S.sel]; if(!c){ box.innerHTML='<p class="empty">—</p>'; return; }
  const vals=c.keys.map(k=>k[1]); const mn=Math.min(...vals), mx=Math.max(...vals);
  box.innerHTML=`
    <div class="insp-head">Channel</div>
    <div class="insp-row"><label>Name</label><span class="mono">${esc(c.name)}</span></div>
    <div class="insp-row"><label>Colour</label><input type="color" id="inspColor" value="${toHex(m.color)}"></div>
    <div class="insp-row"><label>Keyframes</label><span class="mono">${c.keys.length}</span></div>
    <div class="insp-row"><label>Range</label><span class="mono">${mn.toFixed(2)} – ${mx.toFixed(2)}</span></div>
    <div class="insp-row"><label>Value @ playhead</label><span class="mono" id="inspVal">${sample(c.keys,S.t).toFixed(3)}</span></div>
    <div class="insp-row"><label>Visible</label><input type="checkbox" ${m.visible?"checked":""} id="inspVis"></div>
    <div class="insp-row"><label>Solo (isolate)</label><input type="checkbox" ${S.solo===c.name?"checked":""} id="inspSolo"></div>
    <p class="insp-tip dim">Curves tab: drag this channel's keyframe dots — vertical = value, horizontal = time. Edits update the take &amp; its exports.</p>`;
  $("#inspColor")&&($("#inspColor").oninput=e=>setChannelColor(c.name,e.target.value));
  $("#inspVis").onchange=e=>{ m.visible=e.target.checked; buildChannelList(); drawAll(); };
  $("#inspSolo").onchange=e=>{ if(e.target.checked){ for(const k in S.chan)S.chan[k].visible=(k===c.name); S.solo=c.name; }
    else { for(const k in S.chan)S.chan[k].visible=true; S.solo=null; } buildChannelList(); drawAll(); };
}
function renderNodeInspector(box,n){
  const rows=(n.data||[]).map(([k,w])=>`<div class="insp-row sub"><span class="mono">${esc(k)}</span><span class="mono">${(+w).toFixed(2)}</span></div>`).join("");
  box.innerHTML=`<div class="insp-head">${n.kind==="in"?"Viseme input node":"Rig-output node"}</div>
    <div class="insp-row"><label>${n.kind==="in"?"Viseme":"Output"}</label><span class="mono">${esc(n.label)}</span></div>
    <div class="insp-row"><label>${n.kind==="in"?"Drives":"Driven by"}</label><span class="mono">${(n.data||[]).length} ${n.kind==="in"?"targets":"visemes"}</span></div>
    ${rows||'<p class="insp-tip dim">No links.</p>'}
    <p class="insp-tip dim">Links come from the “${esc(S.presetSel)}” retarget preset.</p>`;
}
function updateInspVal(){ if(S.inspectKind!=="channel"||!S.sel)return; const el=$("#inspVal"); if(!el)return;
  const c=chan(S.sel); if(c) el.textContent=sample(c.keys,S.t).toFixed(3); }

/* ===================================================================== *
 *  Views: dispatch + drawing
 * ===================================================================== */
$$("#tabs .tab").forEach(t=>t.onclick=()=>{
  $$("#tabs .tab").forEach(x=>x.classList.remove("active")); t.classList.add("active");
  $$(".view").forEach(v=>v.classList.remove("active"));
  S.view=t.dataset.view; $(`.view[data-view="${S.view}"]`).classList.add("active"); drawAll();
  if(S.view==="events") renderEventList();
  if(S.view==="mapping") renderMapping();
  if(S.view==="preview" && window.Preview3D&&window.Preview3D.ready) window.Preview3D.resize();
});
function drawAll(){ drawPreview(); if(S.view==="curves"){drawCurves(); drawCurveStrip();} if(S.view==="phonemes")drawPhonemes(); if(S.view==="facegraph")drawFaceGraph(); if(S.view==="events"){drawEvents(); drawEventStrip();} updateInspVal(); }

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
  if(p3d&&p3d.ready){ if(S.track) drive3D(p3d); else p3d.update({},{},{pitch:0,yaw:0,roll:0}); }  // neutral when no take
  else drawSchematic();
  $("#tcRead").textContent=fmt(S.t); $("#tpTime").textContent=fmt(S.t);
}

/* Preview articulation shaping — DISPLAY ONLY. The pipeline and every exporter
 * stay byte-identical; this only shapes how the on-screen head reads. Under
 * coarticulation several visemes overlap, so a raw sum over-drives shared corner
 * targets (dimple/press) while the jaw barely opens (~0.5) — the mouth "presses"
 * instead of speaking. Boost the primary vowel/rounding articulators, damp the
 * corners, and close the lips on silence so the motion tracks the words. */
/* ---------------------------------------------------------------------------
 * Preview viseme → ARKit shapes — DISPLAY ONLY. The `arkit` retarget preset that
 * feeds the exporters is tuned for retarget weight math and is left UNTOUCHED
 * (exports stay byte-identical); it does not always read as a distinct mouth
 * shape on a real face. This is a purpose-built, phonetically-correct table for
 * the 3D head, one entry per viseme, keyed to the phonemes each covers. Target
 * names use the Left/Right suffix (preview3d.js maps → _L/_R). Values are the
 * shape at full activation; coarticulation blends them over time.
 * ------------------------------------------------------------------------- */
const PREVIEW_VISEME = {
  // B/M/P — bilabial plosive/nasal: lips pressed together, no teeth. (mouthClose
  // deforms the lower lip on this model + fights an open neighbour's jaw, so seal
  // with mouthPress + a light roll instead; the jaw is closed for P below.)
  PP: [["mouthPressLeft",0.5],["mouthPressRight",0.5],["mouthRollLower",0.22],["mouthRollUpper",0.22]],
  // F/V — labiodental fricative: lower lip RISES to meet the upper teeth
  // (mouthShrugLower lifts the lower lip; a little upperUp shows the teeth it meets)
  FF: [["mouthShrugLower",0.52],["mouthUpperUpLeft",0.3],["mouthUpperUpRight",0.3],["mouthRollLower",0.3],["jawOpen",0.07]],
  // TH/DH — dental fricative: tongue tip toward the teeth, close bite (the mesh's
  // tongueOut is subtle, so keep the jaw nearly shut so it reads as teeth-forward)
  TH: [["tongueOut",0.95],["jawOpen",0.12],["mouthUpperUpLeft",0.15],["mouthUpperUpRight",0.15],["mouthLowerDownLeft",0.15],["mouthLowerDownRight",0.15]],
  // D/T/L — alveolar: tongue tip up behind the teeth, neutral lips, slightly open
  DD: [["jawOpen",0.24],["tongueOut",0.28],["mouthUpperUpLeft",0.12],["mouthUpperUpRight",0.12]],
  // K/G/HH — velar/glottal: moderate neutral opening (shape follows the vowel)
  kk: [["jawOpen",0.32],["mouthLowerDownLeft",0.12],["mouthLowerDownRight",0.12]],
  // CH/JH/SH/ZH — postalveolar: lips protrude, slightly rounded ("sh")
  CH: [["mouthFunnel",0.6],["mouthPucker",0.4],["jawOpen",0.16]],
  // S/Z — sibilant: teeth close together & bared, corners slightly spread
  SS: [["jawOpen",0.09],["mouthStretchLeft",0.3],["mouthStretchRight",0.3],["mouthUpperUpLeft",0.28],["mouthUpperUpRight",0.28],["mouthLowerDownLeft",0.28],["mouthLowerDownRight",0.28]],
  // N/NG — nasal: tongue up, neutral lips, slightly open
  nn: [["jawOpen",0.2],["tongueOut",0.22],["mouthLowerDownLeft",0.1],["mouthLowerDownRight",0.1]],
  // ER/R — rhotic: slight lip rounding / protrusion
  RR: [["mouthPucker",0.45],["mouthFunnel",0.3],["jawOpen",0.18]],
  // AA/AE/AH/AY — open vowels: jaw drops to a natural "ah" (not a full gape)
  aa: [["jawOpen",0.6],["mouthLowerDownLeft",0.1],["mouthLowerDownRight",0.1]],
  // EH/EY/IH — mid-front vowels: open + spread corners
  E:  [["jawOpen",0.42],["mouthStretchLeft",0.32],["mouthStretchRight",0.32],["mouthDimpleLeft",0.22],["mouthDimpleRight",0.22]],
  // IY/Y — high-front "ee": wide spread smile, teeth showing, narrow vertical
  I:  [["jawOpen",0.16],["mouthStretchLeft",0.5],["mouthStretchRight",0.5],["mouthSmileLeft",0.32],["mouthSmileRight",0.32],["mouthUpperUpLeft",0.2],["mouthUpperUpRight",0.2]],
  // AO/AW/OW/OY — rounded open "oh"
  O:  [["mouthFunnel",0.7],["mouthPucker",0.42],["jawOpen",0.42]],
  // UH/UW/W — tight rounded "oo"
  U:  [["mouthPucker",0.85],["mouthFunnel",0.5],["jawOpen",0.12]],
};
/* drive the 3D head: viseme → ARKit (preview table), + gestures + head pose */
function drive3D(p3d){
  const arkit={}; let visSum=0;
  if(S.arkitMap) for(const vis of Object.keys(S.arkitMap)){
    const c=chan(vis); if(!c) continue; const v=Math.max(0,sample(c.keys,S.t)); if(v<1e-4) continue;
    visSum+=v;
    const tgts = PREVIEW_VISEME[vis] || S.arkitMap[vis];
    const ve = Math.min(1, v*1.4);                  // coarticulation peaks ~0.8; lift the dominant shape
    for(const [t,w] of tgts) arkit[t]=(arkit[t]||0)+ve*w;
  }
  for(const k in arkit) arkit[k]=Math.min(1,arkit[k]);
  // bilabial P/B/M: close the jaw so the pressed lips meet cleanly — an additive
  // blend with an open neighbouring vowel would otherwise stretch the lower lip up
  const ppe=Math.min(1, sampleName("PP")*1.4);
  if(ppe>0.02) arkit.jawOpen=(arkit.jawOpen||0)*(1-ppe*0.9);
  // rest the mouth on silence / low speech activity — relax the jaw so the lips
  // meet in the neutral pose (mouthClose deforms the lower lip on this mesh)
  const quiet=Math.max(sampleName("sil"), 1-Math.min(1,visSum));
  if(quiet>0.05) arkit.jawOpen=(arkit.jawOpen||0)*(1-quiet*0.85);
  // emotion channels (from bake_emotion) -> ARKit expression morphs, so baked
  // emotion shows on the head, not only in the Curves tab
  const addE=(k,v)=>{ if(v>0.001) arkit[k]=Math.min(1,(arkit[k]||0)+v); };
  const sm=sampleName("smile"), fr=sampleName("frown"), br=sampleName("brow_raise"),
        bl=sampleName("brow_lower"), ck=sampleName("cheek_raise");
  addE("mouthSmileLeft",sm); addE("mouthSmileRight",sm);
  addE("mouthFrownLeft",fr); addE("mouthFrownRight",fr);
  addE("browInnerUp",br); addE("browOuterUpLeft",br); addE("browOuterUpRight",br);
  addE("browDownLeft",bl); addE("browDownRight",bl);
  addE("cheekSquintLeft",ck); addE("cheekSquintRight",ck);
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
/* value range over visible channels — [0,1] by default, expands to fit SIGNED
 * pose channels (headYaw/Pitch/Roll…) so they don't render clamped-flat (#25) */
let CURVE_VR=[0,1];
function curveValueRange(){
  let lo=0, hi=1, ext=false;
  if(S.track) for(const c of S.track.channels){ const m=S.chan[c.name]; if(!m||!m.visible)continue;
    for(const k of c.keys){ if(k[1]<lo){lo=k[1];ext=true;} if(k[1]>hi){hi=k[1];ext=true;} } }
  if(!ext) return [0,1];                       // pure [0,1] view → unchanged
  const pad=(hi-lo)*0.06||0.1; return [lo-pad,hi+pad];
}
function valAtY(y,g){ const v0=CURVE_VR[0],v1=CURVE_VR[1]; return v0+(1-(y-g.padT)/g.gh)*(v1-v0); }
function yAtVal(v,g){ const v0=CURVE_VR[0],v1=CURVE_VR[1]; return g.padT+g.gh*(1-(v-v0)/((v1-v0)||1)); }
function strokeSmooth(x,pts){ if(pts.length<2)return; x.beginPath(); x.moveTo(pts[0][0],pts[0][1]);   // Catmull-Rom (#28)
  for(let i=0;i<pts.length-1;i++){ const p0=pts[i-1]||pts[i],p1=pts[i],p2=pts[i+1],p3=pts[i+2]||p2;
    x.bezierCurveTo(p1[0]+(p2[0]-p0[0])/6,p1[1]+(p2[1]-p0[1])/6,p2[0]-(p3[0]-p1[0])/6,p2[1]-(p3[1]-p1[1])/6,p2[0],p2[1]); }
  x.stroke(); }
function drawCurves(){
  const cv=$("#curves"); if(!S.track)return; const {x,w,h}=fitCanvas(cv);
  x.clearRect(0,0,w,h); const padL=8,padR=8,padT=8,padB=18, gw=w-padL-padR, gh=h-padT-padB, g={padL,padT,gw,gh};
  const T=Math.max(0.001,S.duration);
  CURVE_VR=curveValueRange(); const v0=CURVE_VR[0], v1=CURVE_VR[1];
  const X=t=>padL+gw*(t/T), Y=v=>yAtVal(v,g);
  // grid + value-axis labels
  x.strokeStyle=css("--line"); x.lineWidth=1; x.fillStyle=css("--fg-mute"); x.font="10px "+css("--font-mono");
  for(let i=0;i<=8;i++){ const gx=padL+gw*i/8; x.globalAlpha=.5; x.beginPath();x.moveTo(gx,padT);x.lineTo(gx,padT+gh);x.stroke(); x.globalAlpha=1; x.fillText((T*i/8).toFixed(1),gx+2,h-6); }
  const dp=(v1-v0>3)?0:2;
  for(let j=0;j<=4;j++){ const val=v1-(v1-v0)*j/4, gy=Y(val); x.globalAlpha=.4; x.beginPath();x.moveTo(padL,gy);x.lineTo(padL+gw,gy);x.stroke(); x.globalAlpha=.75; x.fillText(val.toFixed(dp),padL+2,gy-2); x.globalAlpha=1; }
  if(v0<0&&v1>0){ x.strokeStyle=css("--line-2"); x.globalAlpha=.9; x.beginPath();x.moveTo(padL,Y(0));x.lineTo(padL+gw,Y(0));x.stroke(); x.globalAlpha=1; }
  // curves
  const smooth=$("#curvesSmooth").checked;
  for(const c of S.track.channels){ const m=S.chan[c.name]; if(!m.visible)continue;
    x.strokeStyle=m.color; x.lineWidth=(c.name===S.sel)?2.4:1.5; x.globalAlpha=(S.sel&&c.name!==S.sel)?.5:1;
    if(smooth && c.keys.length>=2) strokeSmooth(x, c.keys.map(k=>[X(k[0]),Y(k[1])]));
    else { x.beginPath(); const N=Math.max(2,Math.floor(gw));
      for(let i=0;i<=N;i++){ const tt=T*i/N, px=padL+gw*i/N, py=Y(sample(c.keys,tt)); i?x.lineTo(px,py):x.moveTo(px,py); }
      x.stroke(); }
  }
  x.globalAlpha=1;
  // draggable keyframe dots for the selected channel
  if(S.sel){ const c=chan(S.sel); if(c){ x.fillStyle=S.chan[S.sel].color; x.strokeStyle=css("--bg"); x.lineWidth=1;
    for(const [t,v] of c.keys){ x.beginPath(); x.arc(X(t),Y(v),3.4,0,7); x.fill(); x.stroke(); } } }
  // playhead
  const hx=X(S.t); x.strokeStyle=css("--accent"); x.lineWidth=1.5;
  x.beginPath();x.moveTo(hx,padT);x.lineTo(hx,padT+gh);x.stroke();
}
/* geometry shared by drawCurves + the curve interaction handlers */
function curveGeom(w,h){ const padL=8,padR=8,padT=8,padB=18; return {padL,padT,gw:w-padL-padR,gh:h-padT-padB}; }
/* phoneme + word alignment strip under the Curves tab, sharing the curve x-axis (#16).
 * `cid` lets the Events tab reuse the same strip under its timeline (#14). */
function drawCurveStrip(cid){
  const cv=$(cid||"#curveStrip"); if(!cv||!S.track)return; const {x,w,h}=fitCanvas(cv); x.clearRect(0,0,w,h);
  const T=Math.max(.001,S.duration), padL=8, gw=w-16, X=t=>padL+gw*(t/T);   // match curveGeom's padL/gw
  x.fillStyle=css("--panel-2"); x.fillRect(0,0,w,h);
  const hasWords=!!(S.words&&S.words.length), rowH=hasWords?h/2:h;
  x.font="10px "+css("--font-mono"); x.textBaseline="middle";
  for(const s of S.segments){ const a=X(s.start), b=X(s.end); const sil=(s.phoneme||"").toLowerCase()==="sil"||s.phoneme==="_";
    x.fillStyle=sil?css("--panel-2"):css("--elev"); x.fillRect(a+1,2,Math.max(1,b-a-2),rowH-4);
    x.strokeStyle=css("--line"); x.strokeRect(a+1,2,Math.max(1,b-a-2),rowH-4);
    if(b-a>12&&!sil){ x.fillStyle=css("--fg-dim"); x.fillText(s.phoneme,a+4,2+(rowH-4)/2); } }
  if(hasWords) for(const wd of S.words){ const a=X(wd[1]), b=X(wd[2]);
    x.fillStyle="color-mix(in srgb,"+css("--accent-2")+" 15%,"+css("--elev")+")"; x.fillRect(a+1,rowH+2,Math.max(1,b-a-2),rowH-4);
    x.strokeStyle=css("--line"); x.strokeRect(a+1,rowH+2,Math.max(1,b-a-2),rowH-4);
    if(b-a>16){ x.fillStyle=css("--fg"); x.fillText(wd[0],a+4,rowH+2+(rowH-4)/2); } }
  x.strokeStyle=css("--accent"); x.beginPath(); const hx=X(S.t); x.moveTo(hx,0);x.lineTo(hx,h);x.stroke();
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
  sel.onchange=async()=>{ S.presetSel=sel.value; S.presetMap=await Pipe.presetMap(sel.value);
    if(S.view==="facegraph")drawFaceGraph(); };
}
async function drawFaceGraph(){
  const cv=$("#facegraph"); const {x,w,h}=fitCanvas(cv); x.clearRect(0,0,w,h); x.fillStyle=css("--panel-2"); x.fillRect(0,0,w,h);
  if(!S.presetMap){ S.presetMap=await Pipe.presetMap(S.presetSel); }
  const inputs=Object.keys(S.presetMap); const outs=[...new Set(Object.values(S.presetMap).flat().map(p=>p[0]))];
  const colL=w*.26, colR=w*.74, iy=h/(inputs.length+1), oy=h/(outs.length+1);
  const inPos={}, outPos={};
  inputs.forEach((n,i)=>inPos[n]=[colL,iy*(i+1)]); outs.forEach((n,i)=>outPos[n]=[colR,oy*(i+1)]);
  const selLabel=(S.inspectKind==="node"&&S.node)?S.node.label:null;
  // --- live signal at the playhead: input viseme activation propagates through the
  //     weights to output values (display-only; the pipeline/exports are untouched) ---
  const live=!!S.track; const inVal={}, outVal={};
  if(live){
    for(const v of inputs){ const c=chan(v); inVal[v]=c?Math.max(0,Math.min(1,sample(c.keys,S.t))):0; }
    for(const [inp,tgts] of Object.entries(S.presetMap)) for(const [t,wt] of tgts) outVal[t]=(outVal[t]||0)+(inVal[inp]||0)*wt;
    for(const t in outVal) outVal[t]=Math.max(0,Math.min(1,outVal[t]));
  }
  const acc=css("--accent");
  // links: dim static base, then a bright live overlay + travelling pulse on active edges
  for(const [inp,tgts] of Object.entries(S.presetMap)) for(const [t,wt] of tgts){
    const a=inPos[inp], b=outPos[t]; if(!a||!b)continue;
    const c1x=(a[0]+b[0])/2, c2x=(a[0]+b[0])/2, x0=a[0]+6, x3=b[0]-6;
    const path=()=>{ x.beginPath(); x.moveTo(x0,a[1]); x.bezierCurveTo(c1x,a[1],c2x,b[1],x3,b[1]); x.stroke(); };
    const on=selLabel&&(inp===selLabel||t===selLabel);
    x.strokeStyle=on?acc:css("--line-2"); x.globalAlpha=on?.9:(.2+wt*.35); x.lineWidth=(on?1.4:.6)+wt*1.7; path();
    const sig=live?(inVal[inp]||0)*wt:0;
    if(sig>0.02){
      x.strokeStyle=acc; x.globalAlpha=Math.min(1,.32+sig*.68); x.lineWidth=1+sig*4;
      x.shadowColor=acc; x.shadowBlur=7*sig; path(); x.shadowBlur=0;
      const u=((S.t*0.7)%1+1)%1, p=bezierPt(x0,a[1],c1x,a[1],c2x,b[1],x3,b[1],u);
      x.globalAlpha=Math.min(1,sig+.25); x.fillStyle=acc;
      x.beginPath(); x.arc(p[0],p[1],1.5+sig*2.4,0,7); x.fill();
    }
  }
  x.globalAlpha=1;
  S.fgNodes=[]; x.font="12px "+css("--font-mono");
  const node=(px,py,label,fill,kind,data,glow)=>{ const tw=x.measureText(label).width, bw=Math.max(46,tw+18);
    const sel=label===selLabel, hot=glow>0.03;
    if(hot){ x.shadowColor=acc; x.shadowBlur=4+glow*13; }
    x.fillStyle=hot?"color-mix(in srgb,"+acc+" "+Math.round(18+glow*62)+"%,"+fill+")":fill;
    x.strokeStyle=sel?acc:(hot?acc:css("--line-2")); x.lineWidth=sel?2:(hot?1.4:1);
    roundRect(x,px-bw/2,py-11,bw,22,6); x.fill(); x.shadowBlur=0; x.stroke();
    x.fillStyle=hot?css("--fg"):css("--fg"); x.textAlign="center"; x.textBaseline="middle"; x.fillText(label,px,py);
    if(glow>0.06){ x.fillStyle=acc; x.font="9px "+css("--font-mono"); x.textAlign=kind==="in"?"right":"left";
      x.fillText(glow.toFixed(2), kind==="in"?px-bw/2-5:px+bw/2+5, py); x.font="12px "+css("--font-mono"); x.textAlign="center"; }
    S.fgNodes.push({x:px,y:py,w:bw,h:22,label,kind,data}); };
  for(const [n,[px,py]] of Object.entries(inPos)) node(px,py,n,css("--elev"),"in",S.presetMap[n],inVal[n]||0);
  for(const [n,[px,py]] of Object.entries(outPos)){
    const incoming=Object.entries(S.presetMap).filter(([,tg])=>tg.some(p=>p[0]===n)).map(([inp,tg])=>[inp,tg.find(p=>p[0]===n)[1]]);
    node(px,py,n,"color-mix(in srgb,"+acc+" 18%, "+css("--elev")+")","out",incoming,outVal[n]||0); }
  x.textAlign="left"; x.fillStyle=css("--fg-dim"); x.font="11px "+css("--font-ui");
  x.fillText("inputs — visemes", colL-60, 16); x.fillText("outputs — "+S.presetSel+" rig", colR-60, 16);
  if(live){ x.textAlign="center"; x.fillStyle=css("--accent"); x.fillText((S.playing?"▶ live · ":"◦ ")+fmt(S.t), w/2, 16); }
}
function roundRect(x,a,b,w,h,r){ x.beginPath(); x.moveTo(a+r,b); x.arcTo(a+w,b,a+w,b+h,r); x.arcTo(a+w,b+h,a,b+h,r); x.arcTo(a,b+h,a,b,r); x.arcTo(a,b,a+w,b,r); x.closePath(); }
/* point on a cubic bezier at parameter u∈[0,1] — for the travelling signal pulse */
function bezierPt(x0,y0,x1,y1,x2,y2,x3,y3,u){ const m=1-u, a=m*m*m, b=3*m*m*u, c=3*m*u*u, d=u*u*u;
  return [a*x0+b*x1+c*x2+d*x3, a*y0+b*y1+c*y2+d*y3]; }

/* ---- Mapping (editable phoneme→viseme table, the real --mapping layer, #15) --
 * This is the openfacefx.mapping / `retarget --mapping` layer (phoneme →
 * weighted visemes), NOT the Face Graph (which is the viseme→rig retarget preset,
 * chosen by name, not file-customisable). Edits export as a canonical mapping
 * file AND, when "apply" is on, drive the next Generate. */
function mapPresetLabel(){ const l=$("#mapPresetLabel"); if(l) l.textContent=
  "· phoneme → viseme"+(S.phonMap?" · "+Object.keys(S.phonMap).length+" phonemes":"")+(S.mapCustom?" · edited":""); }
async function loadPhonMap(){ if(S.phonMap) return; try{ S.phonMap=await Pipe.mappingDefault(); }catch(_){ S.phonMap={}; } }
async function renderMapping(){
  const host=$("#mappingTable"); if(!host)return;
  await loadPhonMap(); mapPresetLabel();
  const phons=Object.keys(S.phonMap||{});
  if(!phons.length){ host.innerHTML='<p class="dim">Mapping unavailable.</p>'; return; }
  host.innerHTML=phons.map(ph=>{
    const rows=S.phonMap[ph]||[];
    const cells=rows.map((tw,i)=>
      `<span class="map-cell"><input class="map-w" type="number" min="0" max="1" step="0.05" value="${(+tw[1]).toFixed(2)}" data-p="${esc(ph)}" data-i="${i}">`+
      `<input class="map-t" type="text" value="${esc(tw[0])}" data-p="${esc(ph)}" data-i="${i}" spellcheck="false">`+
      `<button class="map-del" data-p="${esc(ph)}" data-i="${i}" title="Remove target">✕</button></span>`).join("");
    return `<div class="map-row"><span class="map-vis">${esc(ph)}</span><div class="map-cells">${cells}`+
      `<button class="map-add" data-p="${esc(ph)}" title="Add a viseme target">＋</button></div></div>`;
  }).join("");
  host.querySelectorAll(".map-w").forEach(inp=>inp.onchange=()=>{
    let w=parseFloat(inp.value); if(!isFinite(w))w=0; w=Math.max(0,Math.min(1,w)); inp.value=w.toFixed(2);
    S.phonMap[inp.dataset.p][+inp.dataset.i][1]=w; mapEdited(); });
  host.querySelectorAll(".map-t").forEach(inp=>inp.onchange=()=>{
    S.phonMap[inp.dataset.p][+inp.dataset.i][0]=inp.value.trim(); mapEdited(); });
  host.querySelectorAll(".map-del").forEach(b=>b.onclick=()=>{
    S.phonMap[b.dataset.p].splice(+b.dataset.i,1); mapEdited(); renderMapping(); });
  host.querySelectorAll(".map-add").forEach(b=>b.onclick=()=>{
    S.phonMap[b.dataset.p].push(["aa",1.0]); mapEdited(); renderMapping(); });
}
function mapEdited(){ S.mapCustom=true; mapPresetLabel(); updateReanalyze&&updateReanalyze(); }
async function resetMapping(){ try{ S.phonMap=await Pipe.mappingDefault(); }catch(_){}
  S.mapCustom=false; renderMapping(); }
/* the edited mapping as a canonical openfacefx.mapping JSON (or "" if unusable) */
async function customMappingJson(){ if(!(S.mapCustom&&S.phonMap)) return "";
  try{ const r=await Pipe.mappingJson(S.phonMap,"arkit"); return r&&r.json?r.json:""; }catch(_){ return ""; } }
async function downloadMapping(){
  try{ const r=await Pipe.mappingJson(S.phonMap||{},"arkit");
    if(r.error){ alert("Mapping export failed: "+r.error); return; }
    const blob=new Blob([r.json],{type:"application/json"}); const url=URL.createObjectURL(blob);
    const a=document.createElement("a"); a.href=url; a.download="phoneme.mapping.json"; a.click(); URL.revokeObjectURL(url);
  }catch(err){ alert("Mapping export failed: "+err.message); }
}

/* ===================================================================== *
 *  Canvas interactions — seek, select a curve, drag keyframes, inspect nodes
 * ===================================================================== */
function canvasMetrics(cv,e){ const r=cv.getBoundingClientRect(); return {x:e.clientX-r.left,y:e.clientY-r.top,w:r.width,h:r.height}; }
function seekAtX(x,gw,padL,T){ S.t=Math.min(T,Math.max(0,(x-padL)/gw*T)); S.playClock=S.t; setScrub(); drawAll(); }
function hitKeyframe(c,g,T,x,y){ if(!c)return -1;
  for(let i=0;i<c.keys.length;i++){ const px=g.padL+g.gw*(c.keys[i][0]/T), py=yAtVal(c.keys[i][1],g);
    if(Math.hypot(x-px,y-py)<7)return i; } return -1; }
function nearestChannel(g,T,x,y){ const t=(x-g.padL)/g.gw*T, vv=valAtY(y,g), span=(CURVE_VR[1]-CURVE_VR[0])||1; let best=null,bd=1e9;
  for(const c of S.track.channels){ const m=S.chan[c.name]; if(!m.visible)continue; const v=sample(c.keys,t);
    const d=Math.abs(v-vv); if(d<bd){bd=d;best=c.name;} } return bd<0.12*span?best:null; }

/* ---- curve editing: keyframe primitive + add/delete + undo/redo ------- */
const SIGNED_CH=/^(head|eye)(Pitch|Yaw|Roll|Gaze)?/i;   // pose channels are signed degrees
function markEdited(){ const tk=curTake(); if(tk) tk.edited=true; }
function snapshotUndo(){ if(!S.track)return; S.undo.push(structuredClone(S.track.channels));
  if(S.undo.length>60) S.undo.shift(); S.redo.length=0; refreshUndoButtons(); }
function afterEdit(){ markEdited(); buildChannelList(); buildInspector(); drawCurves(); drawPreview(); updateInspVal(); refreshUndoButtons(); }
function refreshUndoButtons(){ const u=$("#cvUndo"),r=$("#cvRedo"); if(u)u.disabled=!S.undo.length; if(r)r.disabled=!S.redo.length; }
function undoEdit(){ if(!S.undo.length||!S.track)return; S.redo.push(structuredClone(S.track.channels));
  S.track.channels=S.undo.pop(); afterEdit(); }
function redoEdit(){ if(!S.redo.length||!S.track)return; S.undo.push(structuredClone(S.track.channels));
  S.track.channels=S.redo.pop(); afterEdit(); }
/* upsert a time-sorted [t,value] key on a channel (find-or-create). Keys MUST
 * stay strictly ascending — exporters + edits assume it. Signed pose channels
 * aren't clamped to [0,1]. This is the shared write primitive (StudioBridge). */
function setChannelAt(name,value,t){ if(!S.track)return false;
  t=(t==null)?S.t:Math.max(0,Math.min(Math.max(S.duration,t),t));
  const signed=SIGNED_CH.test(name);
  value=signed?Math.max(-90,Math.min(90,+value||0)):Math.max(0,Math.min(1,+value||0));
  let c=chan(name);
  if(!c){ c={name,keys:[]}; S.track.channels.push(c);
    S.chan[name]={color:CURVE_COLORS[(S.track.channels.length-1)%CURVE_COLORS.length],visible:true,idx:S.track.channels.length-1}; }
  const keys=c.keys, EPS=1e-4; let i=0; while(i<keys.length && keys[i][0]<t-EPS) i++;
  if(i<keys.length && Math.abs(keys[i][0]-t)<=EPS) keys[i][1]=value;   // upsert existing key
  else keys.splice(i,0,[t,value]);                                     // insert time-sorted
  markChannelOwned(name); return true; }
function addKeyAtPlayhead(){ if(!S.track||!S.sel){ return; } const c=chan(S.sel); if(!c)return;
  snapshotUndo(); setChannelAt(S.sel, sample(c.keys,S.t), S.t); afterEdit(); }
function delKeyAtPlayhead(){ if(!S.track||!S.sel)return; const c=chan(S.sel); if(!c||c.keys.length<=1)return;
  snapshotUndo(); let bi=0,bd=1e9; for(let i=0;i<c.keys.length;i++){ const d=Math.abs(c.keys[i][0]-S.t); if(d<bd){bd=d;bi=i;} }
  c.keys.splice(bi,1); markChannelOwned(S.sel); afterEdit(); }
let curveDrag=null;
function wireCanvases(){
  const cv=$("#curves");
  if(cv){ cv.style.cursor="crosshair";
    cv.addEventListener("pointerdown",e=>{ if(!S.track)return; const {x,y,w,h}=canvasMetrics(cv,e); const g=curveGeom(w,h); const T=Math.max(.001,S.duration);
      if(S.sel){ const c=chan(S.sel); const ki=hitKeyframe(c,g,T,x,y);
        if(ki>=0){
          if(e.altKey||e.button===2){ if(c.keys.length>1){ snapshotUndo(); c.keys.splice(ki,1); markChannelOwned(c.name); afterEdit(); } return; }  // alt/right-click deletes
          snapshotUndo(); curveDrag={ki,c,g,T}; cv.setPointerCapture(e.pointerId); cv.style.cursor="grabbing"; return; } }
      const near=nearestChannel(g,T,x,y); if(near) selChannel(near);
      seekAtX(x,g.gw,g.padL,T); });
    cv.addEventListener("pointermove",e=>{ if(!curveDrag)return; const {x,y}=canvasMetrics(cv,e); const {ki,c,g,T}=curveDrag;
      const k=c.keys[ki]; const nv=valAtY(y,g); k[1]=SIGNED_CH.test(c.name)?Math.max(-90,Math.min(90,nv)):Math.max(0,Math.min(1,nv));
      const lo=ki>0?c.keys[ki-1][0]:0, hi=ki<c.keys.length-1?c.keys[ki+1][0]:T;
      k[0]=Math.min(hi,Math.max(lo,(x-g.padL)/g.gw*T));
      markEdited(); markChannelOwned(c.name); drawCurves(); drawPreview(); updateInspVal(); });
    const end=e=>{ if(curveDrag){ try{cv.releasePointerCapture(e.pointerId);}catch(_){}} curveDrag=null; cv.style.cursor="crosshair"; };
    cv.addEventListener("pointerup",end); cv.addEventListener("pointercancel",end);
    cv.addEventListener("contextmenu",e=>e.preventDefault());   // right-click is used to delete a key
    cv.addEventListener("dblclick",e=>{ if(!S.track)return; const {x,y,w,h}=canvasMetrics(cv,e); const g=curveGeom(w,h); const T=Math.max(.001,S.duration);
      let name=S.sel; if(!name||!chan(name)){ name=nearestChannel(g,T,x,y); if(name)selChannel(name); }
      if(!name)return; const t=Math.min(T,Math.max(0,(x-g.padL)/g.gw*T)); const nv=valAtY(y,g);
      const v=SIGNED_CH.test(name)?Math.max(-90,Math.min(90,nv)):Math.max(0,Math.min(1,nv));
      snapshotUndo(); setChannelAt(name,v,t); afterEdit(); });     // double-click adds a key
  }
  for(const id of ["#wave","#phonStrip"]){ const c=$(id); if(!c)continue; c.style.cursor="crosshair";
    c.addEventListener("pointerdown",e=>{ if(!S.duration)return; const {x,w}=canvasMetrics(c,e); seekAtX(x,w,0,Math.max(.001,S.duration)); }); }
  for(const id of ["#curveStrip","#eventStrip"]){ const cs=$(id); if(cs){ cs.style.cursor="crosshair";   // share the curve x-axis (padL 8, gw w-16)
    cs.addEventListener("pointerdown",e=>{ if(!S.duration)return; const {x,w}=canvasMetrics(cs,e); seekAtX(x,w-16,8,Math.max(.001,S.duration)); }); } }
  const etl=$("#eventsTl"); if(etl){ etl.style.cursor="crosshair";
    etl.addEventListener("pointerdown",e=>{ if(!S.duration)return; const {x,y,w}=canvasMetrics(etl,e);
      const hit=(S._evHit||[]).find(hh=>Math.hypot(x-hh.x,y-hh.y)<10);   // click a marker seeks to it
      if(hit){ S.t=Math.min(S.duration,Math.max(0,hit.e.t||0)); S.playClock=S.t; setScrub(); drawAll(); }
      else seekAtX(x,w-16,8,Math.max(.001,S.duration)); }); }
  const fg=$("#facegraph"); if(fg){ fg.style.cursor="pointer";
    fg.addEventListener("pointerdown",e=>{ const {x,y}=canvasMetrics(fg,e);
      const hit=(S.fgNodes||[]).find(n=>Math.abs(x-n.x)<=n.w/2+3 && Math.abs(y-n.y)<=n.h/2+3);
      if(hit){ S.inspectKind="node"; S.node=hit; S.sel=null; buildChannelList&&(S.track&&buildChannelList()); buildInspector(); drawFaceGraph(); } }); }
  // curve-edit toolbar + keyboard
  $("#cvUndo")&&($("#cvUndo").onclick=undoEdit);
  $("#cvRedo")&&($("#cvRedo").onclick=redoEdit);
  $("#cvAddKey")&&($("#cvAddKey").onclick=addKeyAtPlayhead);
  $("#cvDelKey")&&($("#cvDelKey").onclick=delKeyAtPlayhead);
  $("#qaRun")&&($("#qaRun").onclick=runQA);
  $("#evRun")&&($("#evRun").onclick=runEvents);
  // re-derive live when a toggle changes, but only once events already exist
  ["#evEmphasis","#evPhrase"].forEach(id=>{ const c=$(id); if(c) c.onchange=()=>{ if(S.events&&S.events.length) runEvents(); }; });
  $("#mapReset")&&($("#mapReset").onclick=resetMapping);
  $("#mapDownload")&&($("#mapDownload").onclick=downloadMapping);
  refreshUndoButtons(); wirePosePanel();
  addEventListener("keydown",e=>{ if(!(e.ctrlKey||e.metaKey))return; const t=e.target.tagName;
    if(t==="INPUT"||t==="TEXTAREA"||t==="SELECT")return;
    const k=e.key.toLowerCase();
    if(k==="z"){ e.preventDefault(); e.shiftKey?redoEdit():undoEdit(); }
    else if(k==="y"){ e.preventDefault(); redoEdit(); } });
}

/* ===================================================================== *
 *  On-face pose controls — head XY pad + expression/gesture sliders
 *  Write a key at the playhead through setChannelAt; drive3D applies head pose
 *  (signed degrees) + the emotion/gesture channels live. (backlog #12/#17)
 * ===================================================================== */
function wirePosePanel(){
  const panel=$("#posePanel"), pad=$("#posePad"), dot=$("#poseDot"), tog=$("#poseToggle"); if(!panel||!pad||!tog) return;
  const EXPR=[...panel.querySelectorAll('input[data-ch]')], roll=$("#poseRoll"), RANGE=25;   // degrees
  const gval=n=>{ const c=chan(n); return c?sample(c.keys,S.t):0; };
  const setDot=(nx,ny)=>{ dot.style.left=((nx+1)/2*100)+"%"; dot.style.top=((ny+1)/2*100)+"%"; };
  function sync(){ const yaw=gval("headYaw"), pitch=gval("headPitch");
    setDot(Math.max(-1,Math.min(1,yaw/RANGE)), Math.max(-1,Math.min(1,pitch/RANGE)));
    roll.value=gval("headRoll"); EXPR.forEach(inp=>{ inp.value=gval(inp.dataset.ch); }); }
  function need(){ if(!S.track){ alert("Generate a take first — pose controls write keys onto the take."); return false; } return true; }
  tog.onclick=()=>{ panel.hidden=!panel.hidden; if(!panel.hidden) sync(); };
  let padDrag=false;
  const padTo=e=>{ const r=pad.getBoundingClientRect();
    const nx=Math.max(-1,Math.min(1,(e.clientX-r.left)/r.width*2-1)), ny=Math.max(-1,Math.min(1,(e.clientY-r.top)/r.height*2-1));
    setDot(nx,ny); setChannelAt("headYaw",nx*RANGE,S.t); setChannelAt("headPitch",ny*RANGE,S.t); markEdited(); drawPreview(); };
  pad.addEventListener("pointerdown",e=>{ if(!need())return; snapshotUndo(); padDrag=true; try{pad.setPointerCapture(e.pointerId);}catch(_){} padTo(e); });
  pad.addEventListener("pointermove",e=>{ if(padDrag) padTo(e); });
  const padEnd=e=>{ padDrag=false; try{pad.releasePointerCapture(e.pointerId);}catch(_){} };
  pad.addEventListener("pointerup",padEnd); pad.addEventListener("pointercancel",padEnd);
  pad.addEventListener("contextmenu",e=>{ e.preventDefault(); if(!need())return; snapshotUndo();
    setChannelAt("headYaw",0,S.t); setChannelAt("headPitch",0,S.t); setDot(0,0); markEdited(); drawPreview(); });
  roll.addEventListener("pointerdown",()=>{ if(need()) snapshotUndo(); });
  roll.addEventListener("input",()=>{ if(!S.track)return; setChannelAt("headRoll",parseFloat(roll.value)||0,S.t); markEdited(); drawPreview(); });
  EXPR.forEach(inp=>{ inp.addEventListener("pointerdown",()=>{ if(need()) snapshotUndo(); });
    inp.addEventListener("input",()=>{ if(!S.track)return; setChannelAt(inp.dataset.ch,parseFloat(inp.value)||0,S.t); markEdited(); drawPreview(); }); });
  $("#poseReset")&&($("#poseReset").onclick=()=>{ if(!need())return; snapshotUndo();
    ["headYaw","headPitch","headRoll"].forEach(n=>setChannelAt(n,0,S.t));
    EXPR.forEach(inp=>setChannelAt(inp.dataset.ch,0,S.t)); sync(); afterEdit(); });
}

/* ===================================================================== *
 *  QA — deterministic timing / pronunciation check (qa.summarize)
 * ===================================================================== */
async function runQA(){
  const panel=$("#qaPanel"); if(!panel)return; panel.hidden=false;
  if(!S.track){ panel.innerHTML='<p class="dim">Generate a take first, then run QA.</p>'; return; }
  panel.innerHTML='<p class="dim">Running QA…</p>';
  try{ renderQA(await Pipe.qa()); }
  catch(err){ panel.innerHTML='<p class="dim">QA failed: '+esc(err.message)+'</p>'; }
}
function renderQA(r){
  const panel=$("#qaPanel"); if(!panel)return;
  const cues=r.cue_warnings||[], oov=r.oov_words||[], warns=r.warnings||[];
  let html=`<div class="qa-head"><span>QA · ${r.channels} channels · ${r.keyframes} keys · ${(+r.duration||0).toFixed(2)}s</span><button class="btn sm" id="qaClose" title="Close">✕</button></div>`;
  if(!cues.length && !warns.length){ html+='<p class="qa-ok">✓ No timing or pronunciation issues flagged.</p>'; }
  if(warns.length) html+='<ul class="qa-list">'+warns.map(w=>`<li class="qa-flag warn">⚠ ${esc(w)}</li>`).join("")+'</ul>';
  if(cues.length){ html+=`<div class="qa-sub">${cues.length} cue-timing outlier(s) — click to seek:</div><ul class="qa-list">`+
    cues.map(c=>`<li class="qa-flag ${c.kind==="short"?"short":"long"}" data-t="${c.start}">${c.kind==="short"?"⏱ short":"⏳ long"} · <b>${esc(c.phoneme)}</b> @ ${(+c.start).toFixed(2)}s · ${Math.round(c.duration*1000)}ms</li>`).join("")+"</ul>"; }
  panel.innerHTML=html;
  $("#qaClose")&&($("#qaClose").onclick=()=>{ panel.hidden=true; });
  panel.querySelectorAll(".qa-flag[data-t]").forEach(li=>li.onclick=()=>{
    S.t=Math.min(S.duration,Math.max(0,parseFloat(li.dataset.t)||0)); S.playClock=S.t; setScrub(); drawAll(); });
}

/* ===================================================================== *
 *  Events — auto-authored typed event layer (pipeline.derive_events, #14)
 * ===================================================================== */
const EV_COLOR=t=>t==="emphasis"?css("--accent"):t==="marker"?css("--accent-2"):css("--fg-dim");
async function runEvents(){
  const el=$("#eventList");
  if(!S.track){ if(el) el.innerHTML='<p class="dim">Generate a take first, then derive events.</p>'; S.events=[]; drawEvents(); return; }
  if(el) el.innerHTML='<p class="dim">Deriving…</p>';
  try{
    const r=await Pipe.events($("#evEmphasis").checked,$("#evPhrase").checked);
    S.events=r.events||[];
    // events ride along in the track JSON → exporters emit them as engine notifies
    S.track.events=S.events; const tk=curTake(); if(tk&&tk.track) tk.track.events=S.events;
  }catch(err){ if(el) el.innerHTML='<p class="dim">Derive failed: '+esc(err.message)+'</p>'; return; }
  renderEventList(); drawEvents(); drawEventStrip();
}
function renderEventList(){
  const el=$("#eventList"); const cnt=$("#evCount"); const evs=S.events||[];
  if(cnt) cnt.textContent=evs.length;
  if(!el) return;
  if(!evs.length){ el.innerHTML='<p class="dim">No events. Emphasis needs a stressed vowel; phrase markers need a <b>sil</b> pause.</p>'; return; }
  el.innerHTML=evs.map(e=>{
    const pay=(e.payload&&Object.keys(e.payload).length)?" · "+esc(JSON.stringify(e.payload)):"";
    const dur=(e.dur||0)>0?" · "+(+e.dur).toFixed(2)+"s":"";
    return `<div class="ev-row" data-t="${e.t||0}"><span class="ev-dot ev-${esc(e.type||"custom")}"></span>`+
      `<span class="ev-t">${(+e.t||0).toFixed(2)}s</span><span class="ev-type">${esc(e.type||"")}</span>`+
      `<span class="ev-name">${esc(e.name||"")}</span><span class="ev-pay dim">${dur}${pay}</span></div>`;
  }).join("");
  el.querySelectorAll(".ev-row[data-t]").forEach(r=>r.onclick=()=>{
    S.t=Math.min(S.duration,Math.max(0,parseFloat(r.dataset.t)||0)); S.playClock=S.t; setScrub(); drawAll(); });
}
function drawEventStrip(){ drawCurveStrip("#eventStrip"); }
function drawEvents(){
  const cv=$("#eventsTl"); if(!cv)return; const {x,w,h}=fitCanvas(cv); x.clearRect(0,0,w,h);
  x.fillStyle=css("--panel-2"); x.fillRect(0,0,w,h);
  const T=Math.max(.001,S.duration), padL=8, gw=w-16, X=t=>padL+gw*(t/T);
  // time grid
  x.font="10px "+css("--font-mono"); x.textBaseline="alphabetic";
  x.strokeStyle=css("--line"); x.fillStyle=css("--fg-mute"); x.lineWidth=1;
  for(let i=0;i<=8;i++){ const gx=padL+gw*i/8; x.globalAlpha=.4; x.beginPath();x.moveTo(gx,4);x.lineTo(gx,h-14);x.stroke(); x.globalAlpha=1; x.fillText((T*i/8).toFixed(1),gx+2,h-4); }
  const evs=S.events||[];
  if(!evs.length){ x.fillStyle=css("--fg-mute"); x.textAlign="center";
    x.fillText(S.track?"No events — click Derive to auto-author from the speech.":"Generate a take, then Derive events.",w/2,h/2); x.textAlign="left"; }
  // two lanes: emphasis above, markers/other below
  const laneY=t=>t==="emphasis"?h*0.36:h*0.64;
  S._evHit=[];
  x.textBaseline="alphabetic";
  for(const e of evs){ const ex=X(e.t||0), ty=e.type||"custom", y=laneY(ty), c=EV_COLOR(ty);
    if((e.dur||0)>0){ const ex2=X((e.t||0)+e.dur); x.fillStyle=c; x.globalAlpha=.22; x.fillRect(ex,y-10,Math.max(2,ex2-ex),20); x.globalAlpha=1; x.strokeStyle=c; x.strokeRect(ex,y-10,Math.max(2,ex2-ex),20); }
    x.fillStyle=c; x.beginPath(); x.moveTo(ex,y-8);x.lineTo(ex+6,y);x.lineTo(ex,y+8);x.lineTo(ex-6,y);x.closePath(); x.fill();
    S._evHit.push({x:ex,y,e});
    if(gw/Math.max(1,evs.length)>30){ x.fillStyle=css("--fg-dim"); x.font="9px "+css("--font-mono"); x.fillText(e.name||ty,ex+8,y-11); }
  }
  // playhead
  x.strokeStyle=css("--accent"); x.lineWidth=1.5; x.beginPath(); const hx=X(S.t); x.moveTo(hx,2);x.lineTo(hx,h-12);x.stroke();
}

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
  if(S.playing){ S.lastTs=0; S.playClock=S.t; requestAnimationFrame(loop); } else showFps(null); }
// spacebar toggles playback (ignored while typing in a field)
addEventListener("keydown",e=>{ if(e.code!=="Space")return;
  const t=e.target.tagName; if(t==="INPUT"||t==="TEXTAREA"||t==="SELECT")return;
  e.preventDefault(); togglePlay(); });

/* ---- FPS: a real playback frame rate + a measured live meter ---------- */
const fpsMeter={ el:()=>$("#fpsMeter"), n:0, acc:0 };
function showFps(measured){ const el=fpsMeter.el(); if(!el)return;
  if(measured==null){ el.classList.remove("live"); el.textContent=(S.fps||60)+" fps"; }
  else { el.classList.add("live"); el.textContent=Math.round(measured)+" fps"; } }
$("#fps").oninput=()=>{ S.fps=parseFloat($("#fps").value)||60; if(!S.playing) showFps(null); };
showFps(null);

function loop(ts){ if(!S.playing)return;
  if(S.lastTs){ const dt=(ts-S.lastTs)/1000; S.playClock+=dt;
    fpsMeter.acc+=dt; fpsMeter.n++;
    if(fpsMeter.acc>=0.4){ showFps(fpsMeter.n/fpsMeter.acc); fpsMeter.acc=0; fpsMeter.n=0; } }
  S.lastTs=ts;
  if(S.playClock>=S.duration) S.playClock=0;
  const fps=Math.max(1,S.fps||60);
  S.t=Math.round(S.playClock*fps)/fps;    // quantise the playhead → the fps is real
  setScrub(); drawAll(); requestAnimationFrame(loop); }

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
  const tools=$("#stageTools"); if(tools) tools.hidden=false;
  const P=()=>window.Preview3D;
  $("#reframeBtn") &&($("#reframeBtn").onclick =()=>P().reframe&&P().reframe());
  $("#zoomInBtn")  &&($("#zoomInBtn").onclick  =()=>P().zoom&&P().zoom(0.8));
  $("#zoomOutBtn") &&($("#zoomOutBtn").onclick =()=>P().zoom&&P().zoom(1.25));
  const cap=document.querySelector(".preview-readout .dim");
  if(cap) cap.textContent="ARKit-blendshape 3D head, driven by the take — drag to orbit · scroll or ＋ / − to zoom · ⟳ recenter";
  P().setActive(true); P().resize(); drawPreview();
});

wireIO(); wireCanvases();
bootstrap();
