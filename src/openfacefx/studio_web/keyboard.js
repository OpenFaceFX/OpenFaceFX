/* ===================================================================== *
 *  OpenFaceFX Studio — keyboard operability for the canvas editors
 *  (WCAG 2.1.1). Every pointer gesture on a canvas has a key here, so the
 *  Studio can be driven without a mouse and by assistive tech.
 *
 *  Curves (#curves, Workspace #ws_curves) — focus the canvas (Tab), then
 *    ← →              select the previous / next key (the playhead follows)
 *    Home / End       first / last key            Ctrl+A   select every key
 *    Shift+← →        nudge selected keys 1 frame (Shift+Alt: 10 frames)
 *    ↑ ↓              nudge value 0.01 (Shift: 0.1); pose channels 1° / 10°
 *    Enter            add a key at the playhead    Delete   remove the selection
 *    PageUp / Down    previous / next channel      Ctrl+Z / Y   undo / redo
 *  Phoneme strip (#phonStrip, #ws_phonStrip)
 *    ← → Home End     select a boundary;  Shift+← →  move it 1 frame (re-solves)
 *  Face Graph (#facegraph, #ws_facegraph)   ← → Home End  select a node
 *  Events (#eventsTl)                       ← →  jump to the previous / next event
 *  Channel lists (#channelList, #ws_rail)   ↑ ↓ Home End move, Enter selects,
 *                                           V shows / hides the channel
 *  Anywhere outside a field                 , .  step the playhead one frame
 *
 *  Classic script, loaded after studio.js: it shares studio.js's globals
 *  (S, chan, setChannelAt, snapshotUndo, redrawCurves, announce, …).
 * ===================================================================== */
"use strict";
(function () {
  const $ = s => document.querySelector(s);
  const frame = () => 1 / Math.max(1, S.fps || 60);
  const T = () => Math.max(.001, S.duration);
  const seek = t => { S.t = Math.max(0, Math.min(T(), t)); S.playClock = S.t; setScrub(); audioSeek(); drawAll(); };
  const say = m => { try { announce(m); } catch (_) {} };
  const isSigned = n => SIGNED_CH.test(n);
  const fmtV = (n, v) => isSigned(n) ? v.toFixed(1) + "°" : v.toFixed(2);
  const arrowOf = e => e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0;

  /* ---- coalesced undo: one snapshot per burst of nudges ---------------- */
  let lastNudge = 0, settle = 0;
  function nudgeBegin() { const now = performance.now(); if (now - lastNudge > 700) snapshotUndo(); lastNudge = now; }
  function nudgeEnd(fn) { clearTimeout(settle); settle = setTimeout(fn, 400); }

  /* ---- curves ------------------------------------------------------------ */
  function curveKeys(e) {
    if (!S.track) return;
    const visible = S.track.channels.filter(ch => S.chan[ch.name] && S.chan[ch.name].visible !== false);
    let c = S.sel && chan(S.sel);
    if (e.key === "PageUp" || e.key === "PageDown") {
      e.preventDefault(); if (!visible.length) return;
      const i = Math.max(0, visible.indexOf(c)), n = (i + (e.key === "PageDown" ? 1 : -1) + visible.length) % visible.length;
      selChannel(visible[n].name); say(visible[n].name + " selected, " + visible[n].keys.length + " keys"); return;
    }
    if (!c) { if (!visible.length) return; c = visible[0]; selChannel(c.name); }
    const keys = c.keys; if (!keys.length) return;
    const sel = S.selKeys.filter(k => keys.includes(k));
    const cur = sel.length ? keys.indexOf(sel[sel.length - 1]) : -1;
    const tell = k => say(c.name + " key " + (keys.indexOf(k) + 1) + " of " + keys.length + ", " + fmt(k[0]) + ", " + fmtV(c.name, k[1]));
    const pick = i => { i = Math.max(0, Math.min(keys.length - 1, i)); const k = keys[i]; S.selKeys = [k]; seek(k[0]); redrawCurves(); tell(k); };
    const arrow = arrowOf(e);
    if (arrow && !e.shiftKey) {
      e.preventDefault();
      if (cur >= 0) { pick(cur + arrow); return; }
      // nothing selected yet: the next key after (or last key before) the playhead
      let i = -1;
      if (arrow > 0) i = keys.findIndex(k => k[0] >= S.t - 1e-6); else keys.forEach((k, q) => { if (k[0] <= S.t + 1e-6) i = q; });
      pick(i < 0 ? (arrow > 0 ? keys.length - 1 : 0) : i); return;
    }
    if (e.key === "Home" || e.key === "End") { e.preventDefault(); pick(e.key === "Home" ? 0 : keys.length - 1); return; }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
      e.preventDefault(); S.selKeys = keys.slice(); redrawCurves(); say("all " + keys.length + " keys of " + c.name + " selected"); return;
    }
    if (e.key === "Enter" || e.key === "Insert") {
      e.preventDefault(); addKeyAtPlayhead();
      const k = keys.find(kk => Math.abs(kk[0] - S.t) <= 1e-4); if (k) { S.selKeys = [k]; redrawCurves(); tell(k); } return;
    }
    if (!sel.length) return;                                   // the nudges below need a selection
    const vert = e.key === "ArrowUp" ? 1 : e.key === "ArrowDown" ? -1 : 0;
    if (arrow && e.shiftKey) {
      e.preventDefault(); nudgeBegin();
      const dt = arrow * frame() * (e.altKey ? 10 : 1);
      if (sel.length === 1) {                                    // a single key stays between its neighbours (as the drag does)
        const k = sel[0], ki = keys.indexOf(k), lo = ki > 0 ? keys[ki - 1][0] : 0, hi = ki < keys.length - 1 ? keys[ki + 1][0] : T();
        k[0] = Math.min(hi, Math.max(lo, k[0] + dt));
      } else { for (const k of sel) k[0] = Math.max(0, Math.min(T(), k[0] + dt)); keys.sort((a, b) => a[0] - b[0]); }
      markEdited(); markChannelOwned(c.name); redrawCurves(); drawPreview(); updateInspVal();
      if (sel.length === 1) { seek(sel[0][0]); tell(sel[0]); } else say(sel.length + " keys moved " + (dt > 0 ? "later" : "earlier"));
      nudgeEnd(afterEdit); return;
    }
    if (vert) {
      e.preventDefault(); nudgeBegin();
      const dv = vert * (isSigned(c.name) ? 1 : 0.01) * (e.shiftKey ? 10 : 1);
      for (const k of sel) k[1] = clampV(c.name, k[1] + dv);
      markEdited(); markChannelOwned(c.name); redrawCurves(); drawPreview(); updateInspVal();
      if (sel.length === 1) tell(sel[0]); else say(sel.length + " keys " + (dv > 0 ? "raised" : "lowered"));
      nudgeEnd(afterEdit);
    }
  }

  /* ---- phoneme strip: a selectable, nudgeable boundary --------------------- */
  let bcur = -1;                                   // boundary i sits between S.segments[i] and [i+1]
  function stripKeys(e) {
    if (!S.segments || S.segments.length < 2 || !S.duration) return;
    const n = S.segments.length - 1, arrow = arrowOf(e);
    const tell = () => { const a = S.segments[bcur], b = S.segments[bcur + 1];
      say("boundary " + (bcur + 1) + " of " + n + ", " + phLabel(a.phoneme) + " to " + phLabel(b.phoneme) + ", " + fmt(a.end || 0)); };
    const pick = i => { bcur = Math.max(0, Math.min(n - 1, i)); seek(S.segments[bcur].end || 0); tell(); };
    if (arrow && !e.shiftKey) {
      e.preventDefault();
      if (bcur >= 0) { pick(bcur + arrow); return; }
      let i = -1;
      if (arrow > 0) { for (let q = 0; q < n; q++) if ((S.segments[q].end || 0) >= S.t - 1e-6) { i = q; break; } }
      else for (let q = 0; q < n; q++) if ((S.segments[q].end || 0) <= S.t + 1e-6) i = q;
      pick(i < 0 ? (arrow > 0 ? n - 1 : 0) : i); return;
    }
    if (e.key === "Home" || e.key === "End") { e.preventDefault(); pick(e.key === "Home" ? 0 : n - 1); return; }
    if (arrow && e.shiftKey && bcur >= 0) {
      e.preventDefault(); nudgeBegin();
      const a = S.segments[bcur], b = S.segments[bcur + 1], eps = Math.max(.008, T() * 0.004);
      let t = (a.end || 0) + arrow * frame() * (e.altKey ? 10 : 1);
      t = Math.min((b.end || T()) - eps, Math.max((a.start || 0) + eps, t));
      a.end = t; b.start = t; seek(t); tell();
      nudgeEnd(() => resolvePhonemes());              // the viseme curves re-solve once the burst settles
    }
  }
  // draw the keyboard cursor on the strip whenever a strip has focus
  const _drawStrip = drawStrip;
  drawStrip = function (cid) {
    _drawStrip(cid);
    const cv = $("#" + (cid || "phonStrip"));
    if (!cv || document.activeElement !== cv || bcur < 0 || !S.segments || bcur >= S.segments.length - 1) return;
    const x = cv.getContext("2d"), dpr = devicePixelRatio || 1, w = cv.width / dpr, h = cv.height / dpr;
    const X = w * ((S.segments[bcur].end || 0) / T());
    x.save(); x.strokeStyle = css("--accent"); x.fillStyle = css("--accent"); x.lineWidth = 2;
    x.beginPath(); x.moveTo(X, 0); x.lineTo(X, h); x.stroke();
    x.beginPath(); x.moveTo(X - 6, 0); x.lineTo(X + 6, 0); x.lineTo(X, 8); x.closePath(); x.fill(); x.restore();
  };

  /* ---- face graph: cycle the nodes ----------------------------------------- */
  function graphKeys(e) {
    const nodes = S.fgNodes || []; if (!nodes.length) return;
    const arrow = arrowOf(e), home = e.key === "Home", end = e.key === "End"; if (!arrow && !home && !end) return;
    e.preventDefault();
    const i = nodes.findIndex(nn => S.node && nn.label === S.node.label && nn.kind === S.node.kind);
    const n = home ? 0 : end ? nodes.length - 1 : ((i < 0 ? (arrow > 0 ? -1 : nodes.length) : i) + arrow + nodes.length) % nodes.length;
    const hit = nodes[n];
    S.inspectKind = "node"; S.node = hit; S.sel = null; if (S.track) buildChannelList(); buildInspector();
    if (S.view === "workspace") drawWorkspace(); else drawFaceGraph();
    say((hit.kind || "node") + " " + hit.label + ", " + (n + 1) + " of " + nodes.length);
  }

  /* ---- events: jump between markers --------------------------------------- */
  function eventKeys(e) {
    const arrow = arrowOf(e); if (!arrow) return;
    const ts = (S.events || []).map(x => +x.t || 0).sort((a, b) => a - b); if (!ts.length) return;
    e.preventDefault();
    const t = arrow > 0 ? ts.find(v => v > S.t + 1e-3) : ts.slice().reverse().find(v => v < S.t - 1e-3);
    if (t == null) return;
    seek(t); say("event " + (ts.indexOf(t) + 1) + " of " + ts.length + ", " + fmt(t));
  }

  /* ---- channel lists: roving tabindex listbox ------------------------------ */
  function listKeys(e) {
    const list = e.currentTarget, items = [...list.querySelectorAll("li[tabindex]")], i = items.indexOf(document.activeElement);
    if (i < 0) return;
    const go = n => { e.preventDefault(); items[(n + items.length) % items.length].focus(); };
    const refocus = (name, sub) => { const li = list.querySelector(`li[data-name="${CSS.escape(name)}"]`); if (li) (sub ? li.querySelector(sub) || li : li).focus(); };
    if (e.key === "ArrowDown") go(i + 1); else if (e.key === "ArrowUp") go(i - 1);
    else if (e.key === "Home") go(0); else if (e.key === "End") go(items.length - 1);
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); const name = items[i].dataset.name; items[i].click(); refocus(name); }
    else if (e.key.toLowerCase() === "v") { const name = items[i].dataset.name, v = items[i].querySelector(".vis"); if (v) { e.preventDefault(); v.click(); refocus(name); } }
  }

  /* ---- global frame step ---------------------------------------------------- */
  addEventListener("keydown", e => {
    if ((e.key !== "," && e.key !== ".") || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target.tagName; if (t === "INPUT" || t === "TEXTAREA" || t === "SELECT" || !S.duration) return;
    e.preventDefault(); seek(S.t + (e.key === "." ? 1 : -1) * frame());
  });

  /* ---- wiring ---------------------------------------------------------------- */
  function editor(id, desc, keys, handler, describedBy) {
    const cv = $("#" + id); if (!cv) return;
    cv.tabIndex = 0; cv.setAttribute("role", "application"); cv.setAttribute("aria-roledescription", desc);
    cv.setAttribute("aria-keyshortcuts", keys);
    if (describedBy && $("#" + describedBy)) cv.setAttribute("aria-describedby", describedBy);
    cv.addEventListener("keydown", handler);
  }
  function init() {
    const CK = "ArrowLeft ArrowRight Home End Shift+ArrowLeft Shift+ArrowRight ArrowUp ArrowDown Enter Delete PageUp PageDown Control+A";
    editor("curves", "curve editor", CK, curveKeys, "curvesKeys");
    editor("ws_curves", "curve editor", CK, curveKeys, "curvesKeys");
    const PK = "ArrowLeft ArrowRight Home End Shift+ArrowLeft Shift+ArrowRight";
    editor("phonStrip", "phoneme timing editor", PK, stripKeys, "phonKeys");
    editor("ws_phonStrip", "phoneme timing editor", PK, stripKeys, "phonKeys");
    editor("facegraph", "face graph", "ArrowLeft ArrowRight Home End", graphKeys, "fgKeys");
    editor("ws_facegraph", "face graph", "ArrowLeft ArrowRight Home End", graphKeys, "fgKeys");
    editor("eventsTl", "event timeline", "ArrowLeft ArrowRight", eventKeys, "evKeys");
    for (const id of ["channelList", "ws_rail"]) { const l = $("#" + id); if (l) l.addEventListener("keydown", listKeys); }
    for (const id of ["phonStrip", "ws_phonStrip"]) { const cv = $("#" + id); if (cv) { cv.addEventListener("focus", () => drawStrip(id)); cv.addEventListener("blur", () => drawStrip(id)); } }
  }
  if (document.readyState !== "loading") init(); else addEventListener("DOMContentLoaded", init);
})();
