/* ===================================================================== *
 *  OpenFaceFX Studio — system voice (Web Speech API)
 *
 *  The browser can't run Piper (no process to spawn) and the cloud voices
 *  need a key, so the one natural voice available to a purely in-browser
 *  Studio is the one the operating system already ships. speechSynthesis
 *  gives us that for free — and, crucially, fires `boundary` events, so we
 *  learn where every WORD lands in time. Those become word anchors, which the
 *  existing align path (`anchored_segments`) turns into real phoneme segments.
 *  So the lip-sync is genuinely timed, not guessed.
 *
 *  THE LIMITATION, stated plainly: browsers do not expose the synthesized
 *  audio. There is no MediaStream, no buffer, no way to capture it. A take
 *  made this way therefore carries timing but NO audio clip — nothing to
 *  export, no spectrogram, no waveform. That is a platform restriction, not
 *  an oversight; capturing it would need the voice to come from somewhere we
 *  control (Piper on the desktop, or a cloud provider).
 *
 *  Self-contained on purpose: no Studio state in here, so it can be tested on
 *  its own and studio.js only has to orchestrate.
 * ===================================================================== */
(function(){
"use strict";

/* Chrome silently stops long utterances after ~15s unless nudged. */
const KEEPALIVE_MS = 10000;

function synth(){ return (typeof window !== "undefined" && window.speechSynthesis) || null; }

/** Is a usable speech synthesizer present? */
function available(){
  return !!(synth() && typeof window.SpeechSynthesisUtterance === "function");
}

/** Installed voices. Populated asynchronously, hence onVoices() below. */
function voices(){
  const s = synth();
  if(!s) return [];
  try{ return s.getVoices() || []; }catch(_){ return []; }
}

/** Call back once the voice list is actually populated (it starts empty). */
function onVoices(cb){
  const s = synth();
  if(!s) return;
  if(voices().length){ cb(voices()); return; }
  const fire = () => cb(voices());
  try{ s.addEventListener("voiceschanged", fire, {once:true}); }
  catch(_){ s.onvoiceschanged = fire; }
  setTimeout(fire, 1000);          // some browsers never fire the event
}

/**
 * Speak `text` aloud and record when each word starts.
 *
 * Times come from performance.now() rather than the event's `elapsedTime`:
 * the spec says seconds but implementations have shipped milliseconds, and a
 * wall-clock delta is unambiguous in every browser.
 *
 * Resolves {marks:[{char,len,t}], duration, spoke:true}.
 */
function speakAndTime(text, opts){
  opts = opts || {};
  const s = synth();
  if(!available()) return Promise.reject(new Error(
    "This browser has no speech synthesis. Use the built-in voice, or run the desktop Studio for Piper."));
  s.cancel();                       // clear anything still queued
  return new Promise((resolve, reject) => {
    const u = new window.SpeechSynthesisUtterance(text);
    if(opts.voiceURI){
      const v = voices().find(v => v.voiceURI === opts.voiceURI);
      if(v) u.voice = v;
    }
    if(opts.rate) u.rate = Math.max(0.1, Math.min(10, Number(opts.rate) || 1));
    if(opts.pitch) u.pitch = Math.max(0, Math.min(2, Number(opts.pitch) || 1));

    const marks = [];
    let t0 = 0, settled = false;
    const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());
    const keep = setInterval(() => {
      try{ if(s.speaking && !s.paused){ s.pause(); s.resume(); } }catch(_){ }
    }, KEEPALIVE_MS);
    const finish = fn => (...a) => { if(settled) return; settled = true; clearInterval(keep); fn(...a); };

    u.onstart = () => { if(!t0) t0 = now(); };
    u.onboundary = e => {
      if(e.name && e.name !== "word") return;      // skip sentence/punctuation marks
      if(!t0) t0 = now();                          // some voices boundary before onstart
      marks.push({ char: e.charIndex|0, len: e.charLength|0, t: (now() - t0) / 1000 });
    };
    u.onerror = finish(e => {
      const kind = (e && (e.error || e.name)) || "unknown";
      reject(kind === "not-allowed"
        ? new Error("The browser blocked speech synthesis — it needs a direct click. Press Generate voice again.")
        : new Error("Speech synthesis failed (" + kind + ")."));
    });
    u.onend = finish(() => {
      const duration = t0 ? (now() - t0) / 1000 : 0;
      resolve({ marks, duration, spoke: true });
    });

    try{ s.speak(u); }
    catch(err){ finish(() => reject(err))(); }
  });
}

/**
 * Boundary marks -> word anchors for `parse_word_anchors`
 * ({"words":[{text,start,end}]}). Each word's end is the next word's start;
 * the last one gets the measured clip length so the track spans the speech.
 *
 * Returns null when nothing usable came back — some voices never fire
 * `boundary`, and the caller should fall back to text timing at the measured
 * duration rather than inventing word positions.
 */
function marksToWords(text, marks, duration){
  const toks = [];
  const re = /\S+/g;
  let m;
  while((m = re.exec(text)) !== null) toks.push({ text: m[0], at: m.index });
  if(!toks.length || !marks || !marks.length) return null;

  const timeOf = new Map();                     // token index -> first time seen
  for(const mk of marks){
    let ti = toks.findIndex(t => t.at === mk.char);
    if(ti < 0){                                 // charIndex mid-token: take the token it falls in
      for(let i = 0; i < toks.length; i++){
        if(toks[i].at <= mk.char) ti = i; else break;
      }
    }
    if(ti >= 0 && !timeOf.has(ti)) timeOf.set(ti, Math.max(0, mk.t));
  }
  if(!timeOf.size) return null;

  const idx = [...timeOf.keys()].sort((a, b) => a - b);
  const words = idx.map(i => ({ text: toks[i].text, start: +timeOf.get(i).toFixed(4) }));
  // monotonic starts — a stray out-of-order boundary must not invert the track
  for(let i = 1; i < words.length; i++)
    if(words[i].start < words[i-1].start) words[i].start = words[i-1].start;
  const end = Math.max(duration || 0, words[words.length-1].start + 0.12);
  words[words.length-1].end = +end.toFixed(4);
  return words;
}

window.WebSpeechTTS = { available, voices, onVoices, speakAndTime, marksToWords };
})();
