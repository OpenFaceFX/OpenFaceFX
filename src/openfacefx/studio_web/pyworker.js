/* ===================================================================== *
 *  OpenFaceFX Studio — Pyodide host for the browser runtime
 *
 *  Runs CPython + numpy + the openfacefx wheel in a Web Worker, so a long
 *  solve never freezes the page: the main thread keeps painting, the
 *  "Generating…" state shows, the 3D head keeps moving. studio.js talks to
 *  it with a tiny RPC:
 *      {id, type:"boot",  pyodideVer, version, bridge}  → installs + runs the bridge source, returns the wheel version
 *      {id, type:"call",  fn, args}                     → calls bridge global `fn(...args)`, returns its (JSON string) result
 *      {id, type:"write", path, bytes}                  → drops bytes into the Pyodide FS (the energy engine's WAV)
 *  Every reply is {id, result} or {id, error}; boot also streams
 *  {type:"progress", msg, frac} for the boot overlay. If this worker cannot
 *  start (no Worker, a CSP, an old browser) studio.js falls back to running
 *  the interpreter in-page, exactly as before.
 * ===================================================================== */
"use strict";
let py = null;
const post = m => self.postMessage(m);

async function boot({ pyodideVer, version, bridge }) {
  const step = (msg, frac) => post({ type: "progress", msg, frac });
  step("Loading the WebAssembly runtime (~24 MB first visit, then cached)…", 0.08);
  importScripts(`https://cdn.jsdelivr.net/pyodide/${pyodideVer}/full/pyodide.js`);
  step("Starting CPython…", 0.28); py = await loadPyodide();
  step("Loading numpy (wasm)…", 0.5); await py.loadPackage(["micropip", "numpy"]);
  step(`Installing openfacefx ${version}…`, 0.72);
  const mp = py.pyimport("micropip");
  try { await mp.install(`openfacefx==${version}`); }
  catch (e) {
    // PyPI's JSON API is CDN-cached per edge: right after a release the pinned
    // version may not resolve everywhere yet. Retry through the lag, then take
    // whatever is available so the Studio still boots.
    let ok = false;
    for (let i = 0; i < 3 && !ok; i++) {
      step(`Fetching openfacefx ${version}… (retry ${i + 1}/3)`, 0.74);
      await new Promise(r => setTimeout(r, 3000));
      try { await mp.install(`openfacefx==${version}`); ok = true; } catch (_) {}
    }
    if (!ok) { step("Installing openfacefx (latest available)…", 0.8); await mp.install("openfacefx"); }
  }
  step("Wiring the studio bridge…", 0.9); await py.runPythonAsync(bridge);
  let ver = version;
  try { ver = await py.runPythonAsync("import openfacefx as _o; _o.__version__"); } catch (_) {}
  return String(ver);
}

self.onmessage = async e => {
  const { id, type } = e.data;
  try {
    if (type === "boot") post({ id, result: await boot(e.data) });
    else if (type === "write") { py.FS.writeFile(e.data.path, new Uint8Array(e.data.bytes)); post({ id, result: true }); }
    else if (type === "call") {
      const fn = py.globals.get(e.data.fn);
      try { post({ id, result: await fn(...e.data.args) }); } finally { fn.destroy(); }
    } else post({ id, error: "unknown message type " + type });
  } catch (err) { post({ id, error: String((err && err.message) || err) }); }
};
