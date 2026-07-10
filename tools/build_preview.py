"""Build a self-contained HTML previewer with a track embedded inline.

Usage:
    python tools/build_preview.py track.json out.html [--autoplay]
                                   [--wav voice.wav] [--segments segs.json]

Browsers block file:// fetch, so everything is baked into the page: the track,
and (optionally) the audio as a base64 data URI and a phoneme/word segment lane.

  ``--autoplay``  starts playback (looping) on load — used by the hosted demo.
  ``--wav``       embeds an audio file (also ``--audio``); the transport syncs
                  to it (play/pause/scrub drive audio time and vice-versa) and a
                  min/max waveform is drawn under the curve plot.
  ``--segments``  a phoneme/word timeline drawn above the transport and synced to
                  the playhead; clicking a segment seeks there (and plays just
                  that slice when audio is embedded). Accepts either a JSON file
                  or a Praat ``.TextGrid`` (Montreal Forced Aligner output).

Segments JSON format (also produced by ``openfacefx naive|mfa --emit-segments``):

    [{"phoneme": "HH", "start": 0.15, "end": 0.32, "confidence": 0.9}, ...]

or, to carry word groupings drawn as a second lane:

    {"segments": [ ...as above... ],
     "words":    [{"text": "hello", "start": 0.15, "end": 0.55}, ...]}

``confidence`` is optional in [0, 1]; when present, segments are tinted
red→green so low-confidence alignments stand out for QA. The page stays a single
self-contained file with no network requests (audio decoded client-side via the
Web Audio API), openable straight from file://.
"""
import argparse
import base64
import json
import os
import sys

TEMPLATE = open(__file__.replace("build_preview.py", "preview_template.html"),
                encoding="utf-8").read()

_AUDIO_MIME = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
               ".oga": "audio/ogg", ".m4a": "audio/mp4", ".flac": "audio/flac"}


def _audio_data_uri(path: str) -> str:
    """Read an audio file and return a ``data:`` URI embedding it verbatim."""
    mime = _AUDIO_MIME.get(os.path.splitext(path)[1].lower(), "audio/wav")
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _pick(d: dict, *keys) -> str:
    for k in keys:
        if d.get(k) is not None:
            return str(d[k])
    return ""


def _segments_from_textgrid(path: str):
    """Parse a Praat TextGrid into (phone segments, word segments) by reusing
    the package's MFA reader. Needs ``openfacefx`` importable."""
    try:
        from openfacefx.alignment import load_mfa_textgrid
    except ImportError:
        raise SystemExit("reading a .TextGrid needs openfacefx installed; "
                         "install it or pass a JSON segments file instead")
    segs = [{"phoneme": s.phoneme, "start": s.start, "end": s.end,
             **({"confidence": s.confidence} if s.confidence is not None else {})}
            for s in load_mfa_textgrid(path)]
    words = []
    try:  # optional: a "words" interval tier, if the TextGrid carries one
        words = [{"text": w.phoneme, "start": w.start, "end": w.end}
                 for w in load_mfa_textgrid(path, tier="words")]
    except ValueError:
        pass
    return segs, words


def _load_segments(path: str) -> dict:
    """Load and validate a segments source into ``{"segments": [...],
    "words": [...]}``, raising a clear error at this boundary on bad input."""
    if path.lower().endswith(".textgrid"):
        raw_segs, raw_words = _segments_from_textgrid(path)
    else:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            raw_segs, raw_words = data, []
        elif isinstance(data, dict):
            raw_segs = data.get("segments") or []
            raw_words = data.get("words") or []
        else:
            raise SystemExit("segments file must be a JSON array or an object "
                             "with a 'segments' key")

    segs = []
    for s in raw_segs:
        try:
            seg = {"phoneme": _pick(s, "phoneme", "label", "text", "name"),
                   "start": float(s["start"]), "end": float(s["end"])}
        except (KeyError, TypeError, ValueError):
            raise SystemExit("each segment needs numeric 'start' and 'end'")
        conf = s.get("confidence", s.get("score"))
        if conf is not None:
            seg["confidence"] = float(conf)
        segs.append(seg)
    words = []
    for w in raw_words:
        try:
            words.append({"text": _pick(w, "text", "word", "label"),
                          "start": float(w["start"]), "end": float(w["end"])})
        except (KeyError, TypeError, ValueError):
            raise SystemExit("each word needs numeric 'start' and 'end'")
    if not segs and not words:
        raise SystemExit(f"no segments found in {path}")
    return {"segments": segs, "words": words}


# ---------------------------------------------------------------------------
# Injected assets. The base template is left byte-for-byte untouched; these are
# spliced in only when audio/segments are supplied, so the no-extras output is
# identical to previous releases. Anchors (</style>, the transport div, the
# final render(0);) each occur exactly once in the template.
# ---------------------------------------------------------------------------

_EXTRA_CSS = """
  /* --- injected: audio waveform + phoneme/word lane --- */
  #ofx-audio{display:none}
  .ofx-strip{margin-top:18px;background:var(--panel);border:1px solid var(--line);
    border-radius:4px}
  .ofx-strip .cap{font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;
    color:var(--dim);padding:10px 12px;border-bottom:1px solid var(--line)}
  #ofx-track{position:relative;padding:10px 12px}
  .ofx-row{position:relative;height:22px;margin-bottom:4px}
  .ofx-seg{position:absolute;top:0;height:100%;display:flex;align-items:center;
    justify-content:center;overflow:hidden;white-space:nowrap;
    font:11px var(--mono);color:var(--txt);
    background:rgba(244,185,66,.14);border:1px solid var(--amber-dim);
    border-radius:2px;cursor:pointer;padding:0 2px;min-width:2px}
  .ofx-seg:hover{background:rgba(244,185,66,.30)}
  .ofx-seg:focus-visible{outline:2px solid var(--amber);outline-offset:1px}
  .ofx-word{background:rgba(74,144,217,.16);border-color:#31527a;color:#cfe0f4;
    letter-spacing:.04em}
  .ofx-word:hover{background:rgba(74,144,217,.32)}
  #ofx-wave{display:block;width:100%;height:56px;margin-top:2px;
    background:var(--panel2);border-radius:3px}
  #ofx-head{position:absolute;top:8px;bottom:8px;width:1px;left:0;
    background:var(--hot);box-shadow:0 0 4px rgba(224,108,91,.6);pointer-events:none}
"""

# Behaviour is parameterised by a single injected ``OFX`` config object, so this
# stays one fixed blob. It runs *before* the template's final render(0); (and so
# before any --autoplay btn.click()), reassigning the transport handlers when
# audio is present and wrapping render() to move the lane playhead. It must never
# contain the literal "render(0);" — that anchor belongs to the template alone.
_OFX_LOGIC = r"""
(function(){
  const seg = OFX.segments || [], words = OFX.words || [];
  const audioEl = document.getElementById('ofx-audio');
  const lane = document.getElementById('ofx-lane');
  const wordRow = document.getElementById('ofx-words');
  const wave = document.getElementById('ofx-wave');
  const head = document.getElementById('ofx-head');

  function tint(c){                       // confidence in [0,1] -> red..green
    if(c==null) return '';
    const h = Math.round(120*Math.max(0,Math.min(1,c)));
    return 'background:hsl('+h+',52%,40%);border-color:hsl('+h+',52%,52%)';
  }
  function block(row, label, s, e, title, style){
    const b = document.createElement('button');
    b.type='button'; b.className='ofx-seg'; b.textContent=label; b.title=title;
    b.style.left=(s/DUR*100)+'%'; b.style.width=Math.max((e-s)/DUR*100,0)+'%';
    if(style) b.setAttribute('style', b.getAttribute('style')+';'+style);
    row.appendChild(b); return b;
  }
  if(lane) seg.forEach(p=>{
    const t = p.phoneme+'  '+p.start.toFixed(2)+'-'+p.end.toFixed(2)+'s'
      + (p.confidence!=null ? '  conf '+p.confidence.toFixed(2) : '');
    block(lane, p.phoneme, p.start, p.end, t, tint(p.confidence))
      .onclick = ()=> playSpan(p.start, p.end);
  });
  if(wordRow) words.forEach(w=>{
    const b = block(wordRow, w.text, w.start, w.end,
      w.text+'  '+w.start.toFixed(2)+'-'+w.end.toFixed(2)+'s', '');
    b.className='ofx-seg ofx-word';
    b.onclick = ()=> playSpan(w.start, w.end);
  });

  function drawWave(buf){                 // simple min/max peaks
    const g = wave.getContext('2d'), W=wave.width, H=wave.height, mid=H/2;
    const data = buf.getChannelData(0), n=data.length, step=n/W;
    g.clearRect(0,0,W,H);
    g.strokeStyle='#26344a'; g.beginPath(); g.moveTo(0,mid); g.lineTo(W,mid); g.stroke();
    g.fillStyle='rgba(74,144,217,.55)';
    for(let x=0;x<W;x++){
      let lo=1, hi=-1; const a=(x*step)|0, b=((x+1)*step)|0;
      for(let i=a;i<b;i++){ const v=data[i]; if(v<lo)lo=v; if(v>hi)hi=v; }
      if(hi<lo){ lo=hi=0; }
      const y1=mid-hi*mid*0.95, y2=mid-lo*mid*0.95;
      g.fillRect(x, y1, 1, Math.max(1, y2-y1));
    }
  }
  if(audioEl && wave){
    try{
      const bytes = Uint8Array.from(atob(audioEl.src.split(',')[1]), c=>c.charCodeAt(0));
      const AC = window.AudioContext || window.webkitAudioContext;
      new AC().decodeAudioData(bytes.buffer, drawWave, ()=>{});
    }catch(e){}
  }

  const _render = render;                 // wrap to move the lane playhead
  render = function(t){ _render(t); if(head) head.style.left=(t/DUR*100)+'%'; };

  let sliceEnd = null;
  function playSpan(s, e){
    if(audioEl){
      sliceEnd = e; audioEl.currentTime = s;
      const pr = audioEl.play(); if(pr && pr.catch) pr.catch(()=>{});
      render(s);
    } else { playing=false; btn.textContent='▶ play'; render(s); }
  }
  if(audioEl){                            // audio is the master clock
    function aloop(){
      if(audioEl.paused) return;
      let t = audioEl.currentTime;
      if(sliceEnd!=null && t>=sliceEnd){ audioEl.pause(); sliceEnd=null; }
      render(Math.min(t, DUR));
      if(!audioEl.paused) requestAnimationFrame(aloop);
    }
    btn.onclick = ()=>{
      if(audioEl.paused){ sliceEnd=null; const pr=audioEl.play(); if(pr&&pr.catch) pr.catch(()=>{}); }
      else audioEl.pause();
    };
    audioEl.addEventListener('play', ()=>{ btn.textContent='❚❚ pause'; requestAnimationFrame(aloop); });
    audioEl.addEventListener('pause', ()=>{ btn.textContent='▶ play'; });
    audioEl.addEventListener('ended', ()=>{ sliceEnd=null; audioEl.currentTime=0; render(0.0); });
    scrub.oninput = ()=>{ sliceEnd=null; const t=(+scrub.value/1000)*DUR; audioEl.currentTime=t; render(t); };
  }
})();
"""


def _extra_html(audio_uri, seg) -> str:
    has_words = bool(seg and seg["words"])
    has_lane = bool(seg and seg["segments"])
    if has_lane and audio_uri:
        cap = "phoneme lane + waveform · click a segment to play it"
    elif has_lane:
        cap = "phoneme lane · click a segment to seek"
    else:
        cap = "audio waveform"
    rows = ['  <div class="ofx-strip">',
            f'    <div class="cap">{cap}</div>',
            '    <div id="ofx-track">']
    if has_words:
        rows.append('      <div id="ofx-words" class="ofx-row"></div>')
    if has_lane:
        rows.append('      <div id="ofx-lane" class="ofx-row"></div>')
    if audio_uri:
        rows.append('      <canvas id="ofx-wave" width="960" height="56"></canvas>')
    rows += ['      <div id="ofx-head"></div>', '    </div>', '  </div>']
    if audio_uri:
        rows.append(f'  <audio id="ofx-audio" preload="auto" src="{audio_uri}"></audio>')
    return "\n".join(rows) + "\n  "


def _inject(html, audio_uri, seg) -> str:
    cfg = {"segments": seg["segments"] if seg else [],
           "words": seg["words"] if seg else [],
           "hasAudio": bool(audio_uri)}
    extra_js = ("\n// ---- injected: audio + phoneme lane ----\n"
                "const OFX = " + json.dumps(cfg) + ";\n" + _OFX_LOGIC + "\n")
    assert "render(0);" not in extra_js  # never shadow the template's anchor
    html = html.replace("</style>", _EXTRA_CSS + "</style>")
    html = html.replace('<div class="transport">',
                        _extra_html(audio_uri, seg) + '<div class="transport">')
    html = html.replace("render(0);", extra_js + "render(0);")
    return html


def main(track_path: str, out_path: str, autoplay: bool = False,
         audio_path: str = None, segments_path: str = None) -> None:
    track = json.load(open(track_path, encoding="utf-8"))
    html = TEMPLATE.replace("/*__TRACK__*/null", json.dumps(track))
    audio_uri = _audio_data_uri(audio_path) if audio_path else None
    seg = _load_segments(segments_path) if segments_path else None
    if audio_uri or seg:
        html = _inject(html, audio_uri, seg)
    if autoplay:
        html = html.replace("render(0);", "render(0);btn.click();")
    open(out_path, "w", encoding="utf-8").write(html)
    print("wrote", out_path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Build a self-contained HTML track previewer.")
    ap.add_argument("track", help="input track JSON")
    ap.add_argument("out", help="output HTML path")
    ap.add_argument("--autoplay", action="store_true",
                    help="start playback (looping) on load — used by the demo")
    ap.add_argument("--wav", "--audio", dest="wav", metavar="AUDIO",
                    help="embed this audio file; the transport syncs to it and a "
                         "waveform is drawn (WAV/MP3/OGG/M4A/FLAC)")
    ap.add_argument("--segments", metavar="FILE",
                    help="phoneme/word timeline: a segments JSON (see module "
                         "docstring) or a Praat .TextGrid")
    a = ap.parse_args()
    main(a.track, a.out, autoplay=a.autoplay,
         audio_path=a.wav, segments_path=a.segments)
