/* ===================================================================== *
 *  OpenFaceFX Studio — AI Assistant + zero-knowledge BYO-key vault
 *
 *  Keys are encrypted client-side with a master password:
 *    masterPassword ─PBKDF2-SHA256(600k, salt)─▶ AES-256-GCM vault key
 *    (non-extractable, in-memory only) ── encrypts each provider API key.
 *  Persisted (localStorage here; a SaaS backend stores the same ciphertext):
 *    { v, kdf, iterations, salt, items:[{provider,label,iv,ciphertext}] }
 *  The master password and vault key NEVER leave the browser.  (OWASP /
 *  Bitwarden model; params stored so the work factor is upgradeable.)
 * ===================================================================== */
"use strict";
(() => {
const enc = new TextEncoder(), dec = new TextDecoder();
const b64   = u8 => btoa(String.fromCharCode(...new Uint8Array(u8)));
const unb64 = s  => Uint8Array.from(atob(s), c => c.charCodeAt(0));
const VAULT_KEY = "offx.studio.vault", ITER = 600_000;

/* ---- crypto ---------------------------------------------------------- */
async function deriveKey(password, salt) {
  const km = await crypto.subtle.importKey("raw", enc.encode(password),
    { name:"PBKDF2" }, false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name:"PBKDF2", salt, iterations:ITER, hash:"SHA-256" },
    km, { name:"AES-GCM", length:256 }, false, ["encrypt","decrypt"]); // non-extractable
}
async function encryptSecret(key, text) {
  const iv = crypto.getRandomValues(new Uint8Array(12));           // fresh 96-bit IV
  const ct = await crypto.subtle.encrypt({ name:"AES-GCM", iv }, key, enc.encode(text));
  return { iv:b64(iv), ciphertext:b64(ct) };
}
async function decryptSecret(key, { iv, ciphertext }) {
  const pt = await crypto.subtle.decrypt({ name:"AES-GCM", iv:unb64(iv) }, key, unb64(ciphertext));
  return dec.decode(pt);
}

/* ---- vault (persisted ciphertext) ----------------------------------- */
const Vault = {
  load(){ try { return JSON.parse(localStorage.getItem(VAULT_KEY)); } catch { return null; } },
  save(v){ localStorage.setItem(VAULT_KEY, JSON.stringify(v)); },
  wipe(){ localStorage.removeItem(VAULT_KEY); },
  exists(){ return !!this.load(); }
};
let VKEY = null;                       // in-memory AES key (cleared on lock)
let ITEMS = [];                        // decrypted provider entries, memory only

/* ---- provider adapters --------------------------------------------- *
 *  Two shapes cover everything: Anthropic-native + OpenAI-compatible
 *  (OpenAI, Gemini-compat, Ollama, vLLM, LM Studio).  CORS: Anthropic +
 *  local call direct; OpenAI/Gemini route through the native /api relay
 *  when the studio runs under `openfacefx studio`.
 * -------------------------------------------------------------------- */
const PROVIDERS = {
  anthropic: { label:"Anthropic (Claude)", kind:"llm", shape:"anthropic", model:"claude-haiku-4-5",
    url:"https://api.anthropic.com/v1/messages", direct:true, needsKey:true },
  openai:    { label:"OpenAI", kind:"llm", shape:"openai", model:"gpt-5-mini",
    url:"https://api.openai.com/v1/chat/completions", direct:false, needsKey:true,
    tts:true },                       // one OpenAI key also serves Generate voice
  gemini:    { label:"Google Gemini", kind:"llm", shape:"openai", model:"gemini-flash-latest",
    url:"https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", direct:false, needsKey:true },
  ollama:    { label:"Ollama (local)", kind:"llm", shape:"openai", model:"llama3.1",
    url:"http://localhost:11434/v1/chat/completions", direct:true, needsKey:false },
  custom:    { label:"OpenAI-compatible (vLLM / LM Studio / …)", kind:"llm", shape:"openai", model:"",
    url:"", direct:true, needsKey:false },
  // Voice (TTS) providers live in the same vault so there is ONE place to put a
  // key. They are not LLMs, so callLLM/the director never select them.
  elevenlabs:{ label:"ElevenLabs (voice)", kind:"tts", needsKey:true, tts:true,
    model:"eleven_multilingual_v2" },
};

const isLLM = p => (PROVIDERS[p]?.kind || "llm") === "llm";
/* The director's provider: the first stored entry that is actually an LLM. */
const llmEntry = () => ITEMS.find(i => isLLM(i.provider)) || null;

async function nativeAvailable(){
  try { const r = await fetch("/api/health",{signal:AbortSignal.timeout(500)}); return r.ok; } catch { return false; }
}

async function callLLM(entry, { system, user, json }) {
  const p = PROVIDERS[entry.provider];
  const model = entry.model || p.model;
  const key = entry.key;
  // route via native relay when the provider blocks browser CORS
  if (p.shape === "openai" && !p.direct && await nativeAvailable()) {
    const r = await fetch("/api/llm", { method:"POST", headers:{"content-type":"application/json"},
      body: JSON.stringify({ url: entry.url||p.url, key, model, system, user, json }) });
    if(!r.ok) throw new Error("relay error "+r.status); const j = await r.json();
    if(j.error) throw new Error(j.error); return j.text;
  }
  if (p.shape === "anthropic") {
    const r = await fetch(p.url, { method:"POST", headers:{
        "content-type":"application/json", "x-api-key":key,
        "anthropic-version":"2023-06-01", "anthropic-dangerous-direct-browser-access":"true" },
      body: JSON.stringify({ model, max_tokens:1500, system,
        messages:[{ role:"user", content:user }] }) });
    if(!r.ok) throw new Error(await errText(r));
    const j = await r.json(); return (j.content||[]).map(b=>b.text||"").join("");
  }
  // OpenAI-compatible (direct: Gemini-simple, Ollama, custom)
  const url = entry.url || p.url;
  const headers = { "content-type":"application/json" };
  if (key) headers["authorization"] = "Bearer "+key;
  const body = { model, messages:[ system?{role:"system",content:system}:null, {role:"user",content:user} ].filter(Boolean) };
  if (json) body.response_format = { type:"json_object" };
  const r = await fetch(url, { method:"POST", headers, body: JSON.stringify(body) });
  if(!r.ok) throw new Error(await errText(r));
  const j = await r.json(); return j.choices?.[0]?.message?.content || "";
}
async function errText(r){ try{ const j=await r.json(); return (j.error&&(j.error.message||j.error))||JSON.stringify(j); }catch{ return r.status+" "+r.statusText+" (browser CORS may block this provider — run `openfacefx studio` to use the relay)"; } }

function extractJSON(s){ const a=s.indexOf("{"), b=s.lastIndexOf("}"); if(a<0||b<0) throw new Error("no JSON in reply"); return JSON.parse(s.slice(a,b+1)); }
const clamp=(v,lo,hi)=>Math.max(lo,Math.min(hi,+v||0));

/* ===================================================================== *
 *  Assist actions
 * ===================================================================== */
const B = () => window.StudioBridge || {};
const ACTIONS = {
  clean: {
    label:"Clean transcript", hint:"Normalize punctuation, numbers & abbreviations, then regenerate the take.",
    async run(entry){
      const t = B().transcript?.() || "";
      // 1) deterministic, keyless Unicode→ASCII folds (works with no API key)
      let text=t, folds=[];
      try{ const n=await B().normalize?.(t); if(n&&n.text!=null){ text=n.text; folds=n.subs||[]; } }catch(_){}
      // 2) LLM expands numbers/abbreviations + fixes casing (must keep every word)
      let via="deterministic folds";
      try{
        const out=await callLLM(entry,{ json:true,
          system:"You normalize text for text-to-speech and lip-sync. Expand numbers and abbreviations to spoken words and fix casing/obvious punctuation. KEEP every word — never summarize or drop content. Reply JSON {\"normalized\": string}.",
          user:text });
        const j=extractJSON(out); const wc=s=>s.trim().split(/\s+/).filter(Boolean).length;
        if(j.normalized && j.normalized.trim() && wc(j.normalized)>=wc(text)*0.6){ text=j.normalized; via="normalized + expanded"; }
      }catch(err){ via="deterministic folds (LLM step skipped: "+err.message+")"; }
      B().setTranscript?.(text);
      let msg="Applied ("+via+")"+(folds.length?" · folds: "+folds.map(f=>`${f.count}× ${JSON.stringify(f.from)}→${JSON.stringify(f.to)}`).join(", "):"");
      if(B().regenerate){ await B().regenerate(); msg+=" · regenerated ✓"; }
      return msg+"\n\n"+text;
    }
  },
  pronounce: {
    label:"Pronounce OOV words", hint:"ARPAbet for names/brands → applied as a pronunciation override + regenerate.",
    async run(entry){
      if(!B().hasTake?.()) return "Generate a take first, then apply pronunciations.";
      const t = B().transcript?.() || "";
      const out = await callLLM(entry, { json:true,
        system:"You are a grapheme-to-phoneme engine. For each unusual/proper-noun word in the text, give an ARPAbet pronunciation (space-separated ARPAbet phones, stress digits on vowels). Reply JSON {\"words\":[{\"word\":UPPER,\"arpabet\":\"...\"}]}. Only include words a CMU dictionary would likely miss.",
        user:t });
      const j = extractJSON(out); const rows=(j.words||[]).filter(r=>r.word&&r.arpabet);
      if(!rows.length) return "No out-of-vocabulary words detected.";
      const dict = rows.map(r=>`${(r.word||"").toUpperCase()}  ${String(r.arpabet).trim().toUpperCase()}`).join("\n");
      if(!B().setCmudict?.(dict)) return "CMUdict lines (no active take to apply to):\n\n"+dict;
      let msg = "Applied "+rows.length+" pronunciation(s):\n"+dict;
      if(B().regenerate){ await B().regenerate(); msg += "\n\nRegenerated ✓"; }
      return msg;
    }
  },
  emotion: {
    label:"Direct emotion", hint:"Valence/arousal → baked onto the take (brow, cheeks, mouth corners).",
    async run(entry){
      if(!B().hasTake?.()) return "Generate a take first, then apply emotion.";
      const t = B().transcript?.() || "";
      const out = await callLLM(entry, { json:true,
        system:"You are a performance director. For the line, return JSON {\"valence\":-1..1,\"arousal\":-1..1,\"emotion\":str,\"intensity\":0..1}. valence: unpleasant..pleasant; arousal: calm..excited.",
        user:t });
      const j = extractJSON(out);
      const v=clamp(j.valence,-1,1), a=clamp(j.arousal,-1,1), inten=clamp(j.intensity==null?0.8:j.intensity,0,1);
      // v1: one emotion held across the take — a constant valence/arousal envelope
      // (bake_emotion resamples it onto the base curve's own time range)
      const env={ format:"openfacefx.emotion", version:1, mode:"valence_arousal", fps:60,
        va:{ valence:[[0,v],[600,v]], arousal:[[0,a],[600,a]] } };
      const res=await B().bakeEmotion(env, inten);
      if(res.error) throw new Error(res.error);
      if(res.track) B().applyTrack(res.track);
      return `Baked ${j.emotion||"emotion"} — valence ${v.toFixed(2)}, arousal ${a.toFixed(2)}, ${Math.round(inten*100)}% — onto the take ✓ (see it on the head + the smile/brow/cheek curves)`;
    }
  },
  direct: {
    label:"Direct the performance", hint:"Free-form notes → talking style + gestures, then regenerate.",
    async run(entry, note){
      const chans = (B().track?.()?.channels||[]).map(c=>c.name);
      const out = await callLLM(entry, { json:true,
        system:"You are a facial-animation director for OpenFaceFX. Turn the note into pipeline settings. Reply JSON {\"style\":one of [neutral,whisper,mumble,broadcast,tense,exaggerated,broad,shout],\"gestures\":bool,\"breath\":bool,\"note\":short rationale}. gestures=true for lively/expressive delivery; breath=true for calm/idle pauses.",
        user:"NOTE: "+(note||"")+"\n\nCurrent channels: "+JSON.stringify(chans) });
      const j = extractJSON(out);
      const STY=["neutral","whisper","mumble","broadcast","tense","exaggerated","broad","shout"];
      const p = { style: STY.includes(j.style)?j.style:"", gestures: !!j.gestures, breath: !!j.breath };
      if(!B().setParams?.(p)) return out;
      let msg=`Applied → style: ${p.style||"default"}, gestures: ${p.gestures?"on":"off"}, breath: ${p.breath?"on":"off"}`;
      if(j.note) msg+=`\n(${j.note})`;
      if(B().regenerate){ await B().regenerate(); msg+="\nRegenerated ✓"; }
      return msg;
    }
  },
};

/* ===================================================================== *
 *  UI
 * ===================================================================== */
let MOUNT, PILL;
function esc(s){ return (s||"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }

function render(){
  if(!MOUNT) return;
  if(!Vault.exists()){ renderSetup(); return; }
  if(!VKEY){ renderUnlock(); return; }
  renderConsole();
  notifyKeys();
}

/* Let the rest of the Studio (the voice engine) know the vault changed. */
function notifyKeys(){
  try{ window.dispatchEvent(new CustomEvent("offx-keys")); }catch(_){ }
}

function renderSetup(){
  PILL.textContent="set up a key"; PILL.className="pill";
  const opts = providerOptions();
  MOUNT.innerHTML = `
    <div class="assist-msg">
      <b>AI director for your take.</b> Describe what you want in plain language and it makes real,
      validated edits — clean the transcript, fix name/brand pronunciations, direct emotion, and set
      the talking style &amp; gestures — then regenerates. It configures the deterministic pipeline; it
      never renders the animation itself.
      <div class="assist-help" style="margin-top:8px">
        <div class="assist-help-row">Runs on <b>your own LLM key</b> — Anthropic, OpenAI, Gemini, or a local model (Ollama / vLLM / LM Studio) for fully offline use.</div>
      </div>
    </div>
    <div class="assist-msg">
      <b>Bring your own key.</b> Your provider API key is encrypted in your browser with a master
      password (PBKDF2-SHA256 · 600k → AES-256-GCM). Only ciphertext is stored — the key and
      password never leave this machine.
    </div>
    <form class="keyform" id="setupForm">
      <div class="keyrow">
        <select id="kProvider">${opts}</select>
        <input id="kModel" placeholder="model (optional)" style="flex:1">
      </div>
      <input id="kUrl" placeholder="base URL (only for OpenAI-compatible / custom)" style="display:none">
      <input id="kKey" type="password" placeholder="provider API key (blank for local Ollama)">
      <input id="kPass" type="password" placeholder="master password (protects your keys)">
      <div class="keyrow">
        <button class="btn primary" type="submit">Encrypt &amp; save key</button>
        <span class="enc-note"><b>🔒 zero-knowledge</b> — encrypted locally, never uploaded in plaintext.</span>
      </div>
    </form>`;
  const prov=$("#kProvider"), url=$("#kUrl");
  const syncProv=()=>{ url.style.display = (prov.value==="custom"||prov.value==="ollama")?"block":"none";
    $("#kModel").placeholder = "model (default: "+(PROVIDERS[prov.value].model||"—")+")"; };
  prov.onchange=syncProv; syncProv();
  $("#setupForm").onsubmit=async e=>{ e.preventDefault();
    const pass=$("#kPass").value; if(pass.length<6){ (window.notice||alert)("Choose a master password (6+ chars).","error"); return; }
    const salt=crypto.getRandomValues(new Uint8Array(16));
    VKEY=await deriveKey(pass,salt);
    const item={ provider:prov.value, label:PROVIDERS[prov.value].label,
      model:$("#kModel").value.trim(), url:$("#kUrl").value.trim(),
      ...(await encryptSecret(VKEY,$("#kKey").value)) };
    Vault.save({ v:1, kdf:"PBKDF2-SHA256", iterations:ITER, salt:b64(salt), items:[item] });
    ITEMS=[{...item, key:$("#kKey").value}]; render();
  };
}

function renderUnlock(){
  PILL.textContent="locked"; PILL.className="pill";
  MOUNT.innerHTML=`
    <div class="assist-msg">Your key vault is locked. Enter your master password to decrypt it (in memory only).</div>
    <form class="keyform" id="unlockForm">
      <input id="uPass" type="password" placeholder="master password" autofocus>
      <div class="keyrow">
        <button class="btn primary" type="submit">Unlock</button>
        <button class="btn" type="button" id="forget">Forget stored keys…</button>
      </div>
    </form>`;
  $("#forget").onclick=()=>{ if(confirm("Delete the encrypted vault from this browser? You'll re-enter your API key.")){ Vault.wipe(); VKEY=null; ITEMS=[]; render(); } };
  $("#unlockForm").onsubmit=async e=>{ e.preventDefault();
    const v=Vault.load(), salt=unb64(v.salt);
    try{ const key=await deriveKey($("#uPass").value, salt);
      const items=[]; for(const it of v.items){ items.push({...it, key: it.ciphertext?await decryptSecret(key,it):""}); }
      VKEY=key; ITEMS=items; render();
    }catch(err){ (window.notice||alert)("Wrong master password (decryption failed).","error"); }
  };
}

/* Add another provider key to an already-unlocked vault — this is the one place
 * in the Studio where API keys are entered, voice keys included. */
function renderAddKey(){
  const opts = providerOptions();
  MOUNT.innerHTML=`
    <div class="assist-msg"><b>Add a key.</b> Same vault, same master password — encrypted here in the
      browser. Voice providers (ElevenLabs, OpenAI) are used by <b>Generate voice</b>; LLM providers
      power the director actions.</div>
    <form class="keyform" id="addForm">
      <div class="keyrow">
        <select id="aProvider">${opts}</select>
        <input id="aModel" placeholder="model / voice id (optional)" style="flex:1">
      </div>
      <input id="aKey" type="password" placeholder="provider API key">
      <div class="keyrow">
        <button class="btn primary" type="submit">Encrypt &amp; add</button>
        <button class="btn" type="button" id="addCancel">Cancel</button>
      </div>
    </form>`;
  $("#addCancel").onclick=()=>render();
  $("#addForm").onsubmit=async e=>{ e.preventDefault();
    const p=$("#aProvider").value, raw=$("#aKey").value;
    const v=Vault.load();
    const item={ provider:p, label:PROVIDERS[p].label, model:$("#aModel").value.trim(), url:"",
      ...(await encryptSecret(VKEY, raw)) };
    v.items=[...(v.items||[]).filter(i=>i.provider!==p), item];      // one entry per provider
    Vault.save(v);
    ITEMS=[...ITEMS.filter(i=>i.provider!==p), {...item, key:raw}];
    render();
  };
}

function providerOptions(){
  const grp=(kind,label)=>`<optgroup label="${label}">`+Object.entries(PROVIDERS)
    .filter(([,p])=>(p.kind||"llm")===kind)
    .map(([k,p])=>`<option value="${k}">${p.label}</option>`).join("")+`</optgroup>`;
  return grp("llm","AI director (LLM)")+grp("tts","Voice (text-to-speech)");
}

function renderConsole(){
  const e=llmEntry();
  const voice=ITEMS.filter(i=>PROVIDERS[i.provider]?.tts).map(i=>i.provider);
  PILL.textContent=e?e.label.split(" ")[0].toLowerCase():"voice key only"; PILL.className="pill ok";
  const actionBtns=Object.entries(ACTIONS).map(([k,a])=>`<button class="btn" data-act="${k}" title="${esc(a.hint)}">${a.label}</button>`).join("");
  MOUNT.innerHTML=`
    <div class="assist-actions">${actionBtns}
      <button class="btn" id="addKeyBtn" title="Add another provider key — LLM or voice">＋ key</button>
      <button class="btn ghost" id="lockBtn" title="Clear keys from memory">lock 🔒</button></div>
    <div class="assist-msg dim" style="font-size:12.5px">Keys stored: ${
      ITEMS.map(i=>esc(i.label)).join(" · ")||"none"}${
      voice.length?` — <b>Generate voice</b> can use ${voice.map(esc).join(" / ")}.`
                 :" — no voice key yet; add one to use a neural voice in <b>Generate voice</b>."}</div>
    <div id="assistLog" class="assistant" style="flex:1;overflow:auto"></div>
    <form class="assist-input" id="assistForm">
      <textarea id="assistNote" placeholder="Direct the performance… e.g. “tired, trailing off, slight head shake”"></textarea>
      <button class="btn primary" type="submit">Send</button>
    </form>`;
  $("#lockBtn").onclick=()=>{ VKEY=null; ITEMS=[]; render(); notifyKeys(); };
  $("#addKeyBtn").onclick=()=>renderAddKey();
  $$("#assistant .assist-actions [data-act]")?.forEach; // no-op guard
  MOUNT.querySelectorAll("[data-act]").forEach(b=>b.onclick=()=>runAction(b.dataset.act, b));
  $("#assistForm").onsubmit=e=>{ e.preventDefault(); const n=$("#assistNote").value.trim(); if(n){ $("#assistNote").value=""; runAction("direct",null,n); } };
  // seed the log with a "what this does" card so the actions aren't a guessing game
  const rows=Object.entries(ACTIONS).map(([k,a])=>`<div class="assist-help-row"><b>${a.label}</b> — ${esc(a.hint)}</div>`).join("");
  log(`<b>AI director.</b> Describe what you want and it turns that into <b>real edits on the current take</b> — it never replaces the pipeline, it sets the inputs (transcript, pronunciation, emotion, style) and regenerates, so results stay deterministic.
    <div class="assist-help">${rows}</div>
    <span class="dim">Type a free-form note below (e.g. “tired, trailing off, slight head shake”) → <b>Direct the performance</b>. <b>Direct emotion</b> and <b>Pronounce</b> need a generated take first. ${
      e ? `Running on ${esc(e.label)}${e.model?" · "+esc(e.model):""} — nothing is sent until you click an action.`
        : `<b>No LLM key stored</b> — the director actions need one; use <b>＋ key</b> to add it. Your voice key still works for <b>Generate voice</b>.`}</span>`, "intro");
}

function log(html, cls=""){ const el=$("#assistLog"); if(!el) return;
  const d=document.createElement("div"); d.className="assist-msg "+cls; d.innerHTML=html; el.appendChild(d); el.scrollTop=el.scrollHeight; }

async function runAction(key, btn, note){
  const a=ACTIONS[key]; if(!a) return; const entry=llmEntry();
  if(!entry){ log(`<b class="dim">${a.label} — needs an LLM key.</b> Use <b>＋ key</b> above to add Anthropic, OpenAI, Gemini or a local model. A voice-only key can't run the director.`); return; }
  if(note!==undefined) log(esc(note),"user");
  log(`<span class="dim">${a.label}…</span>`);
  const busy=$("#assistLog").lastChild;
  try{ const out=await a.run(entry, note);
    busy.innerHTML=`<b>${a.label}</b><br><pre style="white-space:pre-wrap;margin:6px 0 0;font-family:var(--font-mono);font-size:12.5px">${esc(out)}</pre>`;
  }catch(err){ busy.innerHTML=`<b class="dim">${a.label} — failed:</b> ${esc(err.message)}`; }
}

/* ---- public API ------------------------------------------------------ *
 *  The vault is the Studio's single home for API keys, so the voice engine
 *  reads its key from here instead of keeping its own copy. Decrypted keys
 *  live in memory only (ITEMS) and only while the vault is unlocked, so
 *  getKey() returns null when locked — the caller must say so, not guess.
 * --------------------------------------------------------------------- */
window.Assistant = {
  mount(){ MOUNT=document.getElementById("assistantMount"); PILL=document.getElementById("assistProvider"); render(); },
  /* 'none' (nothing stored) | 'locked' (ciphertext present, key needed) | 'unlocked' */
  vaultState(){ return !Vault.exists() ? "none" : (VKEY ? "unlocked" : "locked"); },
  hasKey(provider){ const i=ITEMS.find(x=>x.provider===provider); return !!(i && i.key); },
  getKey(provider){ const i=ITEMS.find(x=>x.provider===provider); return (i && i.key) || null; },
  /* extra per-provider setting: model for an LLM, default voice id for TTS */
  getModel(provider){ const i=ITEMS.find(x=>x.provider===provider); return (i && i.model) || ""; },
  /* which stored providers can speak — drives the voice-engine dropdown */
  ttsProviders(){ return ITEMS.filter(i=>PROVIDERS[i.provider]?.tts && i.key).map(i=>i.provider); },
  open(){ const t=document.querySelector('.tab[data-view="assistant"]'); if(t) t.click(); },
};
document.addEventListener("DOMContentLoaded", ()=>window.Assistant.mount());
// tiny local $ helpers (module scope)
function $(s){ return document.querySelector(s); }
function $$(s){ return [...document.querySelectorAll(s)]; }
})();
