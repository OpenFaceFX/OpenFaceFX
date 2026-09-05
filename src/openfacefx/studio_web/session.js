/* ===================================================================== *
 *  OpenFaceFX Studio — session persistence
 *
 *  1. Theme: the light/dark choice is remembered (localStorage "offx_theme";
 *     index.html applies it before first paint so there is no flash).
 *  2. Session restore: the workspace (actors → takes: transcript, params,
 *     track, edits, audio) is autosaved to localStorage "offx_session" —
 *     after every generate/edit, when the tab is hidden, and on unload — and
 *     restored on the next boot. Closing the tab no longer loses the work;
 *     saving a *project* (Account & projects) remains the durable, named copy.
 *     Audio is large: if the browser's quota refuses the snapshot it is retried
 *     without the clips, and the restore notice says so.
 *  3. "Start a fresh workspace" in the ⋯ menu clears the saved session.
 *
 *  Classic script after studio.js: uses window.StudioBridge, notice(), S.
 * ===================================================================== */
"use strict";
(function () {
  const $ = s => document.querySelector(s);
  const KEY = "offx_session", THEME = "offx_theme";
  const bridge = () => window.StudioBridge || {};
  const store = { get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
                  set(k, v) { localStorage.setItem(k, v); },          // may throw (quota / private mode) — callers catch
                  del(k) { try { localStorage.removeItem(k); } catch (_) {} } };

  /* ---- theme ---------------------------------------------------------- */
  function wireTheme() {
    const t = $("#themeToggle"); if (!t) return;
    // studio.js's onclick flips data-theme first; this listener runs after it
    t.addEventListener("click", () => { try { store.set(THEME, document.documentElement.dataset.theme || "dark"); } catch (_) {} });
  }

  /* ---- session autosave --------------------------------------------- */
  let last = "", timer = 0;
  function snapshot() {
    const b = bridge(); if (!b.getWorkspace) return null;
    const w = b.getWorkspace(); if (!w || !Array.isArray(w.actors)) return null;
    return w;
  }
  const hasWork = w => w.actors.some(a => (a.takes || []).some(t => t && t.track));
  function save() {
    const w = snapshot(); if (!w) return false;
    if (!hasWork(w)) return false;                       // never overwrite a saved session with an empty one
    let json = JSON.stringify(w); if (json === last) return true;
    try { store.set(KEY, json); last = json; return true; }
    catch (_) {                                            // quota: keep everything but the audio clips
      for (const a of w.actors) for (const t of a.takes || []) { delete t.wavB64; delete t.peaks; }
      w.audioDropped = true; json = JSON.stringify(w);
      try { store.set(KEY, json); last = json; return true; } catch (__) { return false; }
    }
  }
  const saveSoon = () => { clearTimeout(timer); timer = setTimeout(save, 400); };

  /* ---- restore on boot ------------------------------------------------ */
  function restore() {
    const raw = store.get(KEY); if (!raw) return;
    let w; try { w = JSON.parse(raw); } catch (_) { store.del(KEY); return; }
    if (!w || !hasWork(w)) return;
    if (window.S && S.track) return;                       // the user already has a take (e.g. a project opened first)
    const b = bridge(); if (!b.setWorkspace) return;
    let ok = false; try { ok = b.setWorkspace(w); } catch (_) { ok = false; }
    if (!ok) { store.del(KEY); return; }
    last = raw;
    const n = w.actors.reduce((k, a) => k + (a.takes || []).filter(t => t && t.track).length, 0);
    const msg = `Restored your last session — ${n} take${n === 1 ? "" : "s"}.` +
      (w.audioDropped ? " The audio clips were too large to keep; load them again if you need the waveform." : "");
    if (typeof notice === "function") notice(msg, "info");
  }
  function ready() { return !!($("#run") && !$("#run").disabled); }

  /* ---- start fresh ------------------------------------------------------ */
  function fresh() {
    const b = bridge(); if (!b.setWorkspace) return;
    const w = snapshot();
    if (w && hasWork(w) && !confirm("Start a fresh workspace? The current takes will be discarded (a saved project is not affected).")) return;
    store.del(KEY); last = "";
    b.setWorkspace({ v: 1, actors: [{ name: "Untitled", takes: [] }], actorIdx: 0, takeIdx: -1 });
    if (typeof notice === "function") notice("Fresh workspace. The previous session is gone.", "info");
  }

  function init() {
    wireTheme();
    const item = $('#ioMenu [data-act="ws-reset"]'); if (item) item.addEventListener("click", fresh);
    document.addEventListener("offx:take", saveSoon);
    addEventListener("pagehide", save);
    addEventListener("beforeunload", save);
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") save(); });
    setInterval(save, 15000);                                // cheap: skipped when nothing changed
    // restore once the pipeline is up (native: instant; browser: after Pyodide)
    const t0 = Date.now();
    const tick = () => { if (ready()) restore(); else if (Date.now() - t0 < 120000) setTimeout(tick, 150); };
    tick();
  }
  window.StudioSession = { save, restore, fresh, KEY };
  if (document.readyState !== "loading") init(); else addEventListener("DOMContentLoaded", init);
})();
