/* ===================================================================== *
 *  OpenFaceFX Studio — accounts & projects (the SaaS surface)
 *  Talks to the /api/auth + /api/projects backend when the studio runs as a
 *  server (openfacefx studio / the SaaS image). On a static host (no backend)
 *  it degrades to a "local workspace": projects saved in this browser, with a
 *  note on how to get accounts + cloud sync. Passwords go straight to the
 *  backend over same-origin; the session is an httpOnly cookie this script
 *  never sees. The BYO-key vault stays zero-knowledge (see assistant.js).
 * ===================================================================== */
"use strict";
(function () {
  const $ = s => document.querySelector(s);
  const A = { native: false, user: null, curId: null, view: "projects" };
  const LS_KEY = "offx.studio.projects";
  const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const bridge = () => window.StudioBridge || {};

  async function api(path, opts) {
    const r = await fetch(path, { credentials: "same-origin", ...opts });
    let j = {}; try { j = await r.json(); } catch (_) {}
    if (!r.ok) throw new Error(j.error || (r.status + " " + r.statusText));
    return j;
  }

  /* ---- backend detection --------------------------------------------- */
  async function detect() {
    try {
      const r = await fetch("/api/auth/me", { credentials: "same-origin" });
      if (r.ok) { const j = await r.json(); if ("user" in j) { A.native = true; A.user = j.user; return; } }
    } catch (_) {}
    A.native = false;      // static host → local workspace
  }

  /* ---- local (no-backend) project store ------------------------------ */
  const local = {
    all() { try { return JSON.parse(localStorage.getItem(LS_KEY) || "[]"); } catch (_) { return []; } },
    save(list) { localStorage.setItem(LS_KEY, JSON.stringify(list)); },
    upsert(id, name, data) {
      const list = local.all(); const now = Date.now();
      if (id) { const p = list.find(x => x.id === id);
        if (p) { p.name = name; p.data = data; p.updated = now; local.save(list); return p; } }
      const p = { id: "loc_" + now.toString(36), name, data, created: now, updated: now };
      list.push(p); local.save(list); return p;
    },
    get(id) { return local.all().find(x => x.id === id) || null; },
    del(id) { local.save(local.all().filter(x => x.id !== id)); },
  };

  /* ---- chip in the top bar ------------------------------------------- */
  function renderChip() {
    const chip = $("#acctChip"); if (!chip) return;
    if (!A.native) { chip.textContent = "◇ Local workspace"; chip.title = "Projects are saved in this browser"; }
    else if (A.user) { chip.textContent = "◉ " + A.user.email; chip.title = "Account & projects"; }
    else { chip.textContent = "Sign in"; chip.title = "Sign in to save projects in the cloud"; }
  }

  /* ---- modal --------------------------------------------------------- */
  function openModal(view) { A.view = view || (A.native && !A.user ? "auth" : "projects"); $("#acctModal").hidden = false; render(); }
  function closeModal() { $("#acctModal").hidden = true; }

  function render() {
    const body = $("#acctBody"); if (!body) return;
    if (A.view === "auth") return renderAuth(body);
    return renderProjects(body);
  }

  function renderAuth(body) {
    body.innerHTML = `
      <div class="acct-tabs">
        <button class="acct-tab ${A.mode !== "register" ? "on" : ""}" data-mode="login">Sign in</button>
        <button class="acct-tab ${A.mode === "register" ? "on" : ""}" data-mode="register">Create account</button>
      </div>
      <form class="acct-form" id="authForm" autocomplete="on">
        <label class="lbl">Email</label>
        <input type="email" id="authEmail" required autocomplete="email" spellcheck="false">
        <label class="lbl">Password</label>
        <input type="password" id="authPass" required minlength="8"
          autocomplete="${A.mode === "register" ? "new-password" : "current-password"}">
        <p class="acct-err" id="authErr" hidden></p>
        <button class="btn primary block" type="submit">${A.mode === "register" ? "Create account" : "Sign in"}</button>
      </form>
      <p class="enc-note"><b>🔒</b><span>Your password is hashed on the server; sessions are httpOnly cookies.
        Provider API keys stay <b>zero-knowledge</b> — encrypted in your browser, only ciphertext ever syncs.</span></p>`;
    body.querySelectorAll(".acct-tab").forEach(t => t.onclick = () => { A.mode = t.dataset.mode; render(); });
    $("#authForm").onsubmit = async e => {
      e.preventDefault(); const err = $("#authErr"); err.hidden = true;
      const email = $("#authEmail").value.trim(), password = $("#authPass").value;
      const path = A.mode === "register" ? "/api/auth/register" : "/api/auth/login";
      try {
        const j = await api(path, { method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ email, password }) });
        A.user = j.user; renderChip(); A.view = "projects"; render(); loadProjects();
      } catch (ex) { err.textContent = ex.message; err.hidden = false; }
    };
  }

  async function renderProjects(body) {
    const wsName = () => { const p = (bridge().getWorkspace && bridge().getWorkspace()); return (p && p.actors && p.actors[0] && p.actors[0].name) || "Untitled"; };
    const banner = !A.native
      ? `<div class="acct-banner">Local workspace — projects live in this browser.
           Run <code>openfacefx studio</code> or deploy the SaaS image for accounts &amp; cloud sync.</div>`
      : (A.user ? `<div class="acct-who">Signed in as <b>${esc(A.user.email)}</b>
           <button class="btn ghost sm" id="signOut">Sign out</button></div>`
        : `<div class="acct-banner">You're signed out. <button class="btn sm" id="toAuth">Sign in</button> to sync projects.</div>`);
    body.innerHTML = `${banner}
      <div class="acct-save">
        <input type="text" id="projName" placeholder="Project name" value="${esc(wsName())}" spellcheck="false">
        <button class="btn primary" id="projSave">${A.curId ? "Save" : "Save new"}</button>
        ${A.curId ? '<button class="btn sm" id="projSaveAs">Save as new</button>' : ""}
      </div>
      <p class="acct-err" id="projErr" hidden></p>
      <h4 class="acct-h">Your projects</h4>
      <ul class="acct-list" id="projList"><li class="dim">Loading…</li></ul>`;
    const so = $("#signOut"); if (so) so.onclick = signOut;
    const ta = $("#toAuth"); if (ta) ta.onclick = () => { A.view = "auth"; render(); };
    $("#projSave").onclick = () => saveProject(A.curId);
    const sa = $("#projSaveAs"); if (sa) sa.onclick = () => saveProject(null);
    loadProjects();
  }

  function projErr(msg) { const e = $("#projErr"); if (e) { e.textContent = msg; e.hidden = !msg; } }

  async function listProjects() {
    if (!A.native) return local.all().sort((a, b) => b.updated - a.updated);
    if (!A.user) return [];
    return (await api("/api/projects")).projects || [];
  }

  async function loadProjects() {
    const ul = $("#projList"); if (!ul) return;
    let items = [];
    try { items = await listProjects(); }
    catch (ex) { ul.innerHTML = `<li class="dim">${esc(ex.message)}</li>`; return; }
    if (!items.length) { ul.innerHTML = '<li class="dim">No saved projects yet.</li>'; return; }
    ul.innerHTML = items.map(p => `<li class="acct-item ${p.id === A.curId ? "cur" : ""}" data-id="${esc(p.id)}">
        <span class="acct-name">${esc(p.name)}</span>
        <span class="acct-when dim">${new Date(p.updated).toLocaleString()}</span>
        <button class="btn sm" data-act="open">Open</button>
        <button class="btn sm danger" data-act="del">Delete</button></li>`).join("");
    ul.querySelectorAll(".acct-item").forEach(li => {
      const id = li.dataset.id;
      li.querySelector('[data-act="open"]').onclick = () => openProject(id);
      li.querySelector('[data-act="del"]').onclick = () => delProject(id);
    });
  }

  async function saveProject(id) {
    projErr("");
    const name = ($("#projName").value || "Untitled").trim() || "Untitled";
    const data = bridge().getWorkspace ? bridge().getWorkspace() : null;
    if (!data) return projErr("Nothing to save.");
    try {
      if (!A.native) { const p = local.upsert(id, name, data); A.curId = p.id; }
      else {
        if (!A.user) { A.view = "auth"; render(); return; }
        const meta = await api("/api/projects", { method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ id: id || undefined, name, data }) });
        A.curId = meta.id;
      }
      render();
    } catch (ex) { projErr(ex.message); }
  }

  async function openProject(id) {
    projErr("");
    try {
      let data;
      if (!A.native) { const p = local.get(id); data = p && p.data; }
      else { data = (await api("/api/projects/" + id)).data; }
      if (data && bridge().setWorkspace && bridge().setWorkspace(data)) { A.curId = id; closeModal(); }
      else projErr("Couldn't open that project.");
    } catch (ex) { projErr(ex.message); }
  }

  async function delProject(id) {
    projErr("");
    try {
      if (!A.native) local.del(id);
      else await api("/api/projects/" + id, { method: "DELETE" });
      if (A.curId === id) A.curId = null;
      loadProjects(); render();
    } catch (ex) { projErr(ex.message); }
  }

  async function signOut() {
    try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {}
    A.user = null; A.curId = null; renderChip(); A.view = "auth"; render();
  }

  /* ---- boot ---------------------------------------------------------- */
  async function init() {
    await detect(); renderChip();
    $("#acctChip").onclick = () => openModal();
    $("#acctClose").onclick = closeModal;
    $("#acctModal").addEventListener("click", e => { if (e.target.id === "acctModal") closeModal(); });
    addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });
  }
  if (document.readyState !== "loading") init(); else addEventListener("DOMContentLoaded", init);
})();
