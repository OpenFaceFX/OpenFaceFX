/* ===================================================================== *
 *  OpenFaceFX Studio — live end-to-end / UX tests
 *
 *  Drives the REAL Studio in a real Chrome against a running native server,
 *  so it catches what the Python suite structurally cannot: markup that never
 *  renders, controls that don't respond, canvases that paint nothing, and
 *  accessibility defects that only exist in the composed page.
 *
 *  Run:
 *      openfacefx studio --port 8801 --no-open &      # or any port
 *      node tests/e2e/studio.e2e.mjs                  # OFFX_URL to override
 *
 *  Requires `playwright-core` and a local Chrome (channel:"chrome"), so no
 *  browser download. Deliberately NOT wired into CI: it needs a browser and a
 *  live server. `--json <path>` writes the full report for diffing between runs.
 * ===================================================================== */
import { chromium } from "playwright-core";
import fs from "fs";
import path from "path";
import os from "os";
import { fileURLToPath } from "url";

const URL = process.env.OFFX_URL || "http://127.0.0.1:8801";
const JSON_OUT = (() => { const i = process.argv.indexOf("--json"); return i > 0 ? process.argv[i+1] : null; })();
const HEADED = process.argv.includes("--headed");

let pass = 0, fail = 0;
const failures = [];
const ok = (cond, name, detail) => {
  if (cond) { pass++; console.log(`  ✓ ${name}`); }
  else { fail++; failures.push({ name, detail }); console.log(`  ✗ ${name}${detail ? "\n      " + detail : ""}`); }
};
const group = n => console.log(`\n${n}`);

const browser = await chromium.launch({ channel: "chrome", headless: !HEADED });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
const consoleErrors = [];
// native confirm()s (delete a take, start fresh) are accepted; every dialog is recorded
const dialogs = [];
page.on("dialog", async d => { dialogs.push(d.message()); await d.accept(); });
const HEADLESS_AUDIO = /AudioContext encountered an error from the audio device/;   // no output device in headless Chrome
page.on("console", m => { if (m.type() === "error" && !HEADLESS_AUDIO.test(m.text())) consoleErrors.push(m.text() + (m.location()?.url ? " @ " + m.location().url : "")); });
page.on("pageerror", e => consoleErrors.push("pageerror: " + e.message));

const report = { url: URL, tabs: [] };

try {
  /* ---------------- boot ---------------- */
  group("Boot");
  const t0 = Date.now();
  await page.goto(URL, { waitUntil: "domcontentloaded" });
  // the browser runtime downloads Pyodide + the openfacefx wheel first: allow 3 min
  await page.waitForFunction(() => !document.querySelector("#run")?.disabled, null, { timeout: 180000 });
  report.bootMs = Date.now() - t0;
  ok(true, `Studio became interactive (${report.bootMs} ms)`);
  ok(await page.title() === "OpenFaceFX Studio", "document title");
  const runtime = await page.$eval("#runtimeLabel", e => e.textContent.trim()).catch(() => "");
  // a native server stamps the page; a static host (the live site) boots Pyodide in-browser
  const RUNTIME = /native/.test(runtime) ? "native" : /browser/.test(runtime) ? "browser" : "";
  report.runtime = RUNTIME;
  ok(!!RUNTIME, `runtime detected: ${RUNTIME || "?"}`, `runtime chip said: ${runtime || "(empty)"}`);

  /* ---------------- document structure ---------------- */
  group("Document structure");
  const doc = await page.evaluate(() => ({
    lang: document.documentElement.lang,
    h1: [...document.querySelectorAll("h1")].length,
    main: document.querySelectorAll("main,[role=main]").length,
    viewport: !!document.querySelector("meta[name=viewport]"),
    tabs: document.querySelectorAll("[role=tab]").length,
    panels: document.querySelectorAll("[role=tabpanel]").length,
    selected: document.querySelectorAll("[role=tab][aria-selected=true]").length,
    controls: [...document.querySelectorAll("[role=tab]")].filter(t => t.getAttribute("aria-controls")).length,
  }));
  report.doc = doc;
  ok(doc.lang === "en", "html[lang] is set");
  ok(doc.h1 === 1, "exactly one <h1>", `found ${doc.h1}`);
  ok(doc.main === 1, "a <main> landmark exists");
  ok(doc.viewport, "viewport meta present");
  ok(doc.panels === doc.tabs, "every tab has a tabpanel", `${doc.tabs} tabs / ${doc.panels} panels`);
  ok(doc.selected === 1, "exactly one tab is aria-selected", `found ${doc.selected}`);
  ok(doc.controls === doc.tabs, "every tab aria-controls its panel", `${doc.controls}/${doc.tabs}`);

  /* ---------------- keyboard: WAI-ARIA tabs pattern ---------------- */
  group("Keyboard (WAI-ARIA tabs pattern)");
  await page.focus('.tab[data-view="preview"]');
  await page.keyboard.press("ArrowRight");
  ok(await page.evaluate(() => document.activeElement?.dataset?.view) === "workspace",
     "ArrowRight moves to the next tab");
  await page.keyboard.press("End");
  const last = await page.evaluate(() => ({
    focused: document.activeElement?.dataset?.view,
    lastTab: [...document.querySelectorAll("#tabs .tab")].pop()?.dataset.view,
  }));
  ok(last.focused === last.lastTab, "End jumps to the last tab", JSON.stringify(last));
  await page.keyboard.press("Home");
  ok(await page.evaluate(() => document.activeElement?.dataset?.view) === "preview", "Home returns to the first tab");
  const roving = await page.evaluate(() =>
    [...document.querySelectorAll("#tabs .tab")].filter(t => t.tabIndex === 0).length);
  ok(roving === 1, "roving tabindex — only the active tab is in the tab order", `${roving} tabs tabbable`);

  /* ---------------- every tab renders and is labelled ---------------- */
  group("Per-tab render + labelling");
  const views = await page.$$eval("#tabs .tab", ts => ts.map(t => t.dataset.view));
  for (const v of views) {
    await page.click(`.tab[data-view="${v}"]`);
    await page.waitForTimeout(200);
    const r = await page.evaluate(view => {
      const visible = el => { const s = getComputedStyle(el), b = el.getBoundingClientRect();
        return s.display !== "none" && s.visibility !== "hidden" && b.width > 0 && b.height > 0; };
      const pane = document.querySelector(`.view[data-view="${view}"]`);
      const ctl = [...pane.querySelectorAll("button,input,select,textarea,[tabindex]")].filter(visible);
      const named = e => {
        if (e.type === "hidden") return true;
        return !!((e.textContent || "").trim() || e.getAttribute("aria-label") || e.getAttribute("title") ||
          (e.id && document.querySelector(`label[for="${e.id}"]`)) || e.closest("label") || e.getAttribute("placeholder"));
      };
      // effective target: a checkbox inside a label is toggled by the whole label
      const target = e => (e.tagName === "INPUT" && e.closest("label")) || e;
      return {
        view,
        visible: visible(pane),
        controls: ctl.length,
        unnamed: ctl.filter(e => !named(e)).map(e => e.tagName + "." + (e.className || "")),
        tiny: ctl.filter(e => { const b = target(e).getBoundingClientRect(); return b.height < 24 || b.width < 24; })
                .map(e => { const b = target(e).getBoundingClientRect();
                  return `${e.id || e.tagName}[${(e.className||"").split(" ")[0]}] ${Math.round(b.width)}x${Math.round(b.height)}`; }),
        canvasUnnamed: [...pane.querySelectorAll("canvas")].filter(visible)
          .filter(c => !c.getAttribute("aria-label") && c.getAttribute("aria-hidden") !== "true")
          .map(c => c.id || "(anon)"),
      };
    }, v);
    report.tabs.push(r);
    ok(r.visible, `${v}: panel renders`);
    ok(r.unnamed.length === 0, `${v}: every control has an accessible name`,
       r.unnamed.length ? `${r.unnamed.length} unnamed: ${[...new Set(r.unnamed)].slice(0,3).join(", ")}` : "");
    ok(r.canvasUnnamed.length === 0, `${v}: every visible canvas is named or marked decorative`,
       r.canvasUnnamed.join(", "));
    ok(r.tiny.length === 0, `${v}: no target under 24x24 (WCAG 2.5.8)`,
       [...new Set(r.tiny)].slice(0, 5).join(" | "));
  }

  /* ---------------- the core workflow actually works ---------------- */
  group("Generate a take (the primary workflow)");
  await page.click('.tab[data-view="preview"]');
  await page.fill("#text", "Hello world, this is a live end to end test.");
  await page.click("#run");
  await page.waitForFunction(() => window.StudioBridge?.track?.()?.channels?.length > 0, null, { timeout: 60000 });
  const take = await page.evaluate(() => {
    const t = window.StudioBridge.track();
    return { channels: t.channels.length, keys: t.channels.reduce((n, c) => n + c.keys.length, 0),
             duration: window.StudioBridge.duration?.() ?? null };
  });
  report.take = take;
  ok(take.channels > 0, `track generated (${take.channels} channels, ${take.keys} keys)`);
  ok(take.keys > 0, "channels carry keyframes");

  group("Canvases actually paint");
  for (const [view, id] of [["curves","curves"], ["phonemes","wave"], ["facegraph","facegraph"]]) {
    await page.click(`.tab[data-view="${view}"]`);
    await page.waitForTimeout(320);
    const lit = await page.evaluate(cid => {
      const c = document.getElementById(cid);
      if (!c || !c.width) return -1;
      const x = c.getContext("2d");
      if (!x) return -2;                                 // webgl canvas — skip
      const d = x.getImageData(0, 0, c.width, c.height).data;
      let n = 0;
      for (let i = 3; i < d.length; i += 4 * 37) if (d[i] > 8) n++;   // sparse alpha sample
      return n;
    }, id);
    ok(lit === -2 || lit > 0, `${view}: #${id} has painted pixels`, `sampled ${lit}`);
  }

  /* ---------------- colour contrast, both themes (WCAG 1.4.3) ---------------- */
  group("Colour contrast (WCAG 1.4.3 AA)");
  const CONTRAST = `(() => {
    const lum = c => { const [r,g,b] = c.map(v => { v/=255; return v<=0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055,2.4); });
      return 0.2126*r + 0.7152*g + 0.0722*b; };
    const parse = s => { const m = s.match(/rgba?\\(([^)]+)\\)/); if(!m) return null;
      const p = m[1].split(",").map(Number); return {rgb:[p[0],p[1],p[2]], a: p.length>3?p[3]:1}; };
    const ratio = (f,bg) => { const L1=lum(f), L2=lum(bg); const [a,b]=L1>L2?[L1,L2]:[L2,L1]; return (a+0.05)/(b+0.05); };
    const bgOf = el => { let n = el;
      while(n && n !== document.documentElement){ const c = parse(getComputedStyle(n).backgroundColor);
        if(c && c.a > 0.85) return c.rgb; n = n.parentElement; }
      return parse(getComputedStyle(document.body).backgroundColor)?.rgb || [0,0,0]; };
    const vis = el => { const s=getComputedStyle(el), r=el.getBoundingClientRect();
      return s.display!=="none" && s.visibility!=="hidden" && +s.opacity>0.05 && r.width>0 && r.height>0; };
    const out = [];
    for(const el of document.querySelectorAll("body *")){
      if(!vis(el)) continue;
      const txt = [...el.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent.trim()).join("");
      if(!txt) continue;
      const s = getComputedStyle(el), fg = parse(s.color); if(!fg) continue;
      const size = parseFloat(s.fontSize), bold = (parseInt(s.fontWeight)||400) >= 700;
      const need = (size >= 24 || (size >= 18.66 && bold)) ? 3 : 4.5;
      const r = ratio(fg.rgb, bgOf(el));
      if(r < need) out.push(\`\${el.tagName.toLowerCase()}\${el.id?"#"+el.id:""} \${r.toFixed(2)}:1 need \${need} "\${txt.slice(0,22)}"\`);
    }
    return [...new Set(out)];
  })()`;
  for (const theme of ["dark", "light"]) {
    await page.evaluate(t => document.documentElement.setAttribute("data-theme", t), theme);
    await page.waitForTimeout(200);
    const bad = [];
    for (const v of views) {
      await page.click(`.tab[data-view="${v}"]`); await page.waitForTimeout(120);
      bad.push(...await page.evaluate(CONTRAST));
    }
    const uniq = [...new Set(bad)];
    report[`contrast_${theme}`] = uniq;
    ok(uniq.length === 0, `${theme} theme: all text meets 4.5:1 (3:1 large)`, uniq.slice(0, 6).join(" | "));
  }
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));

  /* ---------------- focus is always visible (WCAG 2.4.7) ---------------- */
  group("Focus visibility (WCAG 2.4.7)");
  // Drive real Tab presses: :focus-visible does NOT match a programmatic
  // .focus() in Chrome, so scripted focus would report a false failure on
  // markup that is perfectly fine for an actual keyboard user.
  await page.evaluate(() => document.body.focus());
  const noRing = [];
  const seenStops = new Set();
  for (let i = 0; i < 45; i++) {
    await page.keyboard.press("Tab");
    const r = await page.evaluate(() => {
      const e = document.activeElement;
      if (!e || e === document.body) return null;
      const snap = el => { const s = getComputedStyle(el);
        return s.outlineWidth + s.outlineStyle + s.outlineColor + s.boxShadow + s.borderColor + s.backgroundColor; };
      const focused = snap(e);
      const key = (e.id ? "#" + e.id : e.tagName.toLowerCase() + "." + String(e.className || "").split(" ")[0]);
      e.blur();
      return { key, changed: snap(e) !== focused };
    });
    if (!r) continue;
    if (seenStops.has(r.key)) continue;
    seenStops.add(r.key);
    if (!r.changed) noRing.push(r.key);
  }
  report.focusStops = seenStops.size;
  ok(seenStops.size > 10, `Tab reaches the controls (${seenStops.size} distinct stops)`);
  ok(noRing.length === 0, "every keyboard-focused control shows a visible focus indicator",
     noRing.slice(0, 8).join(", "));

  /* ---------------- status messages (WCAG 4.1.3) ---------------- */
  group("Status messages (WCAG 4.1.3)");
  const hasLive = await page.evaluate(() =>
    !!document.querySelector("[role=status],[aria-live=polite],[aria-live=assertive]"));
  ok(hasLive, "a live region exists for async results");
  await page.click('.tab[data-view="preview"]');
  await page.evaluate(() => { document.getElementById("srStatus").textContent = ""; });
  await page.click("#run");
  await page.waitForFunction(() => (document.getElementById("srStatus")?.textContent || "").length > 0,
                             null, { timeout: 60000 });
  const spoken = await page.$eval("#srStatus", e => e.textContent);
  report.announced = spoken;
  ok(/take ready|generating/i.test(spoken), "generating a take is announced", `said: "${spoken}"`);

  /* ---------------- reflow (WCAG 1.4.10) ---------------- */
  group("Reflow");
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.waitForTimeout(250);
  const reflow = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }));
  report.reflow1024 = reflow;
  ok(reflow.overflow <= 1, "no horizontal scrolling at 1024px", `overflows by ${reflow.overflow}px`);
  await page.setViewportSize({ width: 1440, height: 900 });

  /* ---------------- controls outside the tab panels ---------------- */
  // The per-tab audit only sees the panels; the header, both rails and the
  // transport hold ~30 more controls (this is where the unnamed slider hid).
  group("Header, rails and transport");
  await page.click('.tab[data-view="preview"]');
  const outer = await page.evaluate(() => {
    const visible = el => { const s = getComputedStyle(el), b = el.getBoundingClientRect();
      return s.display !== "none" && s.visibility !== "hidden" && b.width > 0 && b.height > 0; };
    const ctl = [...document.querySelectorAll("button,input,select,textarea,[tabindex]")].filter(e => visible(e) && !e.closest(".view"));
    const named = e => e.type === "hidden" || !!((e.textContent || "").trim() || e.getAttribute("aria-label") || e.getAttribute("aria-labelledby") ||
      e.getAttribute("title") || (e.id && document.querySelector(`label[for="${e.id}"]`)) || e.closest("label") || e.getAttribute("placeholder"));
    const target = e => (e.tagName === "INPUT" && e.closest("label")) || e;
    return { n: ctl.length,
      unnamed: ctl.filter(e => !named(e)).map(e => e.tagName + "#" + e.id),
      tiny: ctl.filter(e => { const b = target(e).getBoundingClientRect(); return b.height < 24 || b.width < 24; })
        .map(e => { const b = target(e).getBoundingClientRect(); return `${e.id || e.tagName} ${Math.round(b.width)}x${Math.round(b.height)}`; }) };
  });
  report.outer = outer;
  ok(outer.unnamed.length === 0, `every control outside the panels has an accessible name (${outer.n} checked)`, outer.unnamed.join(", "));
  ok(outer.tiny.length === 0, "no header/rail/transport target under 24x24 (WCAG 2.5.8)", outer.tiny.join(" | "));
  const vt = await page.$eval("#scrub", e => e.getAttribute("aria-valuetext") || "");
  ok(/\d\d:\d\d\.\d{3} of \d\d:\d\d\.\d{3}/.test(vt), "the playhead slider speaks time, not 0–1000", `aria-valuetext="${vt}"`);

  /* ---------------- the ⋯ menu is a menu button (WAI-ARIA APG) ---------------- */
  group("Menu button (⋯ actor/take actions)");
  const mb = await page.evaluate(() => { const b = document.getElementById("ioMenuBtn"), m = document.getElementById("ioMenu");
    return { haspopup: b.getAttribute("aria-haspopup"), controls: b.getAttribute("aria-controls"),
             role: m.getAttribute("role"), items: m.querySelectorAll("[role=menuitem]").length }; });
  ok(mb.haspopup === "menu" && mb.controls === "ioMenu" && mb.role === "menu" && mb.items >= 5,
     "button and menu carry the ARIA menu pattern", JSON.stringify(mb));
  await page.focus("#ioMenuBtn"); await page.keyboard.press("Enter"); await page.waitForTimeout(80);
  const opened = await page.evaluate(() => ({ expanded: document.getElementById("ioMenuBtn").getAttribute("aria-expanded"),
    hidden: document.getElementById("ioMenu").hidden,
    focus: document.activeElement?.getAttribute("role") + ":" + document.activeElement?.textContent.trim() }));
  ok(opened.expanded === "true" && !opened.hidden && /^menuitem:/.test(opened.focus),
     "Enter opens the menu and focuses the first item", JSON.stringify(opened));
  await page.keyboard.press("ArrowDown");
  const second = await page.evaluate(() => document.activeElement?.textContent.trim());
  ok(second === "Rename take…", "ArrowDown moves to the next item", `focused "${second}"`);
  await page.keyboard.press("End");
  const lastItem = await page.evaluate(() => document.activeElement?.textContent.trim());
  ok(lastItem === "Start a fresh workspace…", "End jumps to the last item", `focused "${lastItem}"`);
  await page.keyboard.press("Escape"); await page.waitForTimeout(80);
  const closed = await page.evaluate(() => ({ expanded: document.getElementById("ioMenuBtn").getAttribute("aria-expanded"),
    hidden: document.getElementById("ioMenu").hidden, focus: document.activeElement?.id }));
  ok(closed.hidden && closed.expanded === "false" && closed.focus === "ioMenuBtn",
     "Escape closes the menu and returns focus to the button", JSON.stringify(closed));

  /* ---------------- the account modal is a modal dialog ---------------- */
  group("Modal dialog (Account & projects)");
  const dlg = await page.evaluate(() => { const m = document.getElementById("acctModal");
    return { role: m.getAttribute("role"), modal: m.getAttribute("aria-modal"),
             label: document.getElementById(m.getAttribute("aria-labelledby") || "")?.textContent.trim() }; });
  ok(dlg.role === "dialog" && dlg.modal === "true" && !!dlg.label, "role=dialog, aria-modal, labelled by its heading", JSON.stringify(dlg));
  await page.focus("#acctChip"); await page.keyboard.press("Enter"); await page.waitForTimeout(150);
  const inDlg = await page.evaluate(() => ({ open: !document.getElementById("acctModal").hidden,
    inside: !!document.activeElement?.closest("#acctModal"), focus: document.activeElement?.id || document.activeElement?.tagName }));
  ok(inDlg.open && inDlg.inside, "opening moves focus into the dialog", JSON.stringify(inDlg));
  let leaked = 0;
  for (let i = 0; i < 25; i++) { await page.keyboard.press("Tab");
    if (!(await page.evaluate(() => !!document.activeElement?.closest("#acctModal")))) leaked++; }
  ok(leaked === 0, "Tab never leaves the dialog (focus is trapped)", `${leaked}/25 presses escaped`);
  for (let i = 0; i < 6; i++) { await page.keyboard.press("Shift+Tab");
    if (!(await page.evaluate(() => !!document.activeElement?.closest("#acctModal")))) leaked++; }
  ok(leaked === 0, "Shift+Tab never leaves the dialog either", `${leaked} presses escaped`);
  const pwLabel = await page.evaluate(() => document.getElementById("authPass") ? !!document.querySelector('label[for="authPass"]') : "n/a");
  ok(pwLabel === true || pwLabel === "n/a", "the password field has a real <label>", `labelled: ${pwLabel}`);
  await page.keyboard.press("Escape"); await page.waitForTimeout(80);
  const back = await page.evaluate(() => ({ closed: document.getElementById("acctModal").hidden, focus: document.activeElement?.id }));
  ok(back.closed && back.focus === "acctChip", "Escape closes the dialog and returns focus to the opener", JSON.stringify(back));

  /* ---------------- the 3D preview renders on demand ---------------- */
  // It used to run a free 60 fps loop — on every tab, even with its canvas hidden.
  group("Idle rendering");
  await page.click('.tab[data-view="preview"]'); await page.waitForTimeout(600);
  const has3d = await page.evaluate(() => !!(window.Preview3D && window.Preview3D.ready));
  if (has3d) {
    const idle = await page.evaluate(async () => { const a = window.Preview3D.frames;
      await new Promise(r => setTimeout(r, 1500)); return window.Preview3D.frames - a; });
    ok(idle <= 2, `an idle 3D preview draws no frames (${idle} in 1.5 s)`);
    await page.click('.tab[data-view="curves"]'); await page.waitForTimeout(300);
    const hidden = await page.evaluate(async () => { const a = window.Preview3D.frames;
      const s = document.getElementById("scrub"); s.value = 400; s.dispatchEvent(new Event("input"));   // a redraw while hidden
      await new Promise(r => setTimeout(r, 500)); return window.Preview3D.frames - a; });
    ok(hidden === 0, `a hidden 3D canvas is never rendered (${hidden} frames drawn on the Curves tab)`);
    const before = await page.evaluate(() => window.Preview3D.frames);
    await page.click('.tab[data-view="preview"]'); await page.waitForTimeout(300);
    const after = await page.evaluate(() => window.Preview3D.frames);
    ok(after > before, "returning to the Preview tab draws a frame", `${before} → ${after}`);
    report.idleFrames = { idle, hidden, shown: after - before };
  } else ok(true, "no WebGL preview in this browser — skipped");

  /* ---------------- narrow windows: nothing pushed off-screen ---------------- */
  // body is overflow:hidden, so a control past the right edge is simply gone
  // (this is what 200% zoom on a laptop looks like). A control inside a
  // horizontally scrollable strip (the tab bar) counts as reachable.
  group("Reflow at tablet width / 200% zoom");
  for (const w of [768, 1024]) {
    await page.setViewportSize({ width: w, height: 800 }); await page.waitForTimeout(300);
    // below 860px the Generate panel is a drawer over the workspace — fold it away the
    // way a user would; everything behind it must then be reachable
    const drawer = await page.evaluate(() => { const t = document.getElementById("railToggle");
      return !!t && getComputedStyle(t).display !== "none"; });
    if (drawer) { await page.click("#railToggle"); await page.waitForTimeout(250); }
    const r = await page.evaluate(() => {
      const de = document.documentElement;
      const scrollable = el => { for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
        const o = getComputedStyle(n).overflowX; if ((o === "auto" || o === "scroll") && n.scrollWidth > n.clientWidth + 1) return true; } return false; };
      // a point outside a clipping ancestor is scrolled away, not covered
      const clipped = (e, x, y) => { for (let n = e.parentElement; n && n !== document.body; n = n.parentElement) {
        const o = getComputedStyle(n); if (o.overflowX !== "visible" || o.overflowY !== "visible") { const c = n.getBoundingClientRect();
          if (x < c.left || x > c.right || y < c.top || y > c.bottom) return true; } } return false; };
      const ctl = [...document.querySelectorAll("button,select,input,textarea")].filter(e => { const s = getComputedStyle(e), b = e.getBoundingClientRect();
        return s.display !== "none" && s.visibility !== "hidden" && b.width > 0 && b.height > 0; });
      const lost = ctl.filter(e => { const b = e.getBoundingClientRect(); return (b.right > de.clientWidth + 1 || b.left < -1) && !scrollable(e); })
        .map(e => e.id || e.className);
      // covered: something else sits on top of the control's centre (an overlay, a drawer)
      const covered = [];
      for (const e of ctl) { const b = e.getBoundingClientRect(), x = b.left + b.width / 2, y = b.top + b.height / 2;
        if (x < 0 || y < 0 || x > de.clientWidth || y > de.clientHeight || clipped(e, x, y)) continue;
        const hit = document.elementFromPoint(x, y);
        if (hit && !(e.contains(hit) || hit.contains(e))) covered.push((e.id || e.className) + " under " + (hit.id ? "#" + hit.id : hit.tagName + "." + String(hit.className).split(" ")[0])); }
      return { lost: [...new Set(lost)], covered: [...new Set(covered)] };
    });
    report[`reach${w}`] = r;
    ok(r.lost.length === 0, `${w}px: every control stays on screen`, r.lost.slice(0, 8).join(", "));
    ok(r.covered.length === 0, `${w}px: no control is hidden under another element`, r.covered.slice(0, 8).join(", "));
    if (drawer) { await page.click("#railToggle"); await page.waitForTimeout(150); }
  }
  await page.setViewportSize({ width: 1440, height: 900 }); await page.waitForTimeout(200);

  /* ---------------- every button does something without throwing ---------------- */
  // Destructive ones (delete/remove/✕/−) are skipped; browser dialogs are
  // auto-dismissed by Playwright. Elements are re-resolved before every click
  // because several panels re-render themselves.
  group("Every visible button survives a click");
  const sweepErrs = [];
  let clicked = 0;
  for (const v of views) {
    await page.click(`.tab[data-view="${v}"]`); await page.waitForTimeout(150);
    const n = await page.evaluate(view => document.querySelector(`.view[data-view="${view}"]`).querySelectorAll("button").length, v);
    for (let i = 0; i < n; i++) {
      const info = await page.evaluate(([view, idx]) => {
        const b = document.querySelector(`.view[data-view="${view}"]`).querySelectorAll("button")[idx];
        if (!b) return null;
        const s = getComputedStyle(b), r = b.getBoundingClientRect();
        const vis = s.display !== "none" && s.visibility !== "hidden" && r.width > 0 && r.height > 0 && !b.disabled;
        const text = (b.textContent || b.title || "").trim();
        if (!vis || (/delete|remove|✕|−/i.test(text) && !/key/i.test(text))) return null;
        b.scrollIntoView({ block: "nearest" });
        const q = b.getBoundingClientRect();
        return { text, x: q.left + q.width / 2, y: q.top + q.height / 2 };
      }, [v, i]);
      if (!info) continue;
      const e0 = consoleErrors.length;
      await page.mouse.click(info.x, info.y); await page.waitForTimeout(250); clicked++;
      if (consoleErrors.length > e0) sweepErrs.push(`${v}: "${info.text}" → ${consoleErrors[e0]}`);
      await page.evaluate(() => { document.getElementById("acctModal").hidden = true; document.getElementById("ioMenu").hidden = true; });
      if (await page.evaluate(view => !document.querySelector(`.view[data-view="${view}"]`).classList.contains("active"), v)) {
        await page.click(`.tab[data-view="${v}"]`); await page.waitForTimeout(100); }
    }
  }
  report.sweep = { clicked, errors: sweepErrs };
  ok(sweepErrs.length === 0, `${clicked} buttons clicked across ${views.length} tabs without a console error`, sweepErrs.slice(0, 5).join(" | "));

  /* ---------------- the canvas editors work from the keyboard (WCAG 2.1.1) ---------------- */
  // Every pointer gesture (select / drag / add / delete a key, drag a phoneme
  // boundary, pick a graph node, seek to an event, turn the head) has a key.
  group("Keyboard operability of the canvas editors (WCAG 2.1.1)");
  await page.click('.tab[data-view="curves"]'); await page.waitForTimeout(250);
  const rowsTabbable = await page.evaluate(() => [...document.querySelectorAll("#channelList li")].filter(l => l.tabIndex === 0).length);
  ok(rowsTabbable === 1, "the channel list is a roving-tabindex listbox (one row in the tab order)", `${rowsTabbable} rows tabbable`);
  await page.focus("#channelList li[tabindex='0']");
  await page.keyboard.press("ArrowDown"); await page.keyboard.press("Enter"); await page.waitForTimeout(120);
  const rowSel = await page.evaluate(() => ({ sel: S.sel, focused: document.activeElement?.dataset?.name, aria: document.activeElement?.getAttribute("aria-selected") }));
  ok(!!rowSel.sel && rowSel.sel === rowSel.focused && rowSel.aria === "true",
     "ArrowDown + Enter selects a channel and focus stays on its row", JSON.stringify(rowSel));
  // the Workspace rail is the same listbox; Enter there also solos the channel
  await page.click('.tab[data-view="workspace"]'); await page.waitForTimeout(400);
  await page.focus("#ws_rail li[tabindex='0']");
  await page.keyboard.press("ArrowDown"); await page.keyboard.press("Enter"); await page.waitForTimeout(150);
  const railSel = await page.evaluate(() => ({ sel: S.sel, solo: S.solo, focused: document.activeElement?.dataset?.name }));
  ok(!!railSel.sel && railSel.sel === railSel.focused && railSel.solo === railSel.sel,
     "Workspace rail: ArrowDown + Enter selects and solos a channel, focus stays on its row", JSON.stringify(railSel));
  await page.keyboard.press("Enter"); await page.waitForTimeout(150);          // toggle the solo back off
  await page.click('.tab[data-view="curves"]'); await page.waitForTimeout(250);
  await page.focus("#curves");
  await page.evaluate(() => { document.getElementById("srStatus").textContent = ""; S.t = 0; S.playClock = 0; setScrub(); drawAll(); });
  let k0;                                              // walk right from the start to a key with a free frame after it
  for (let tries = 0; tries < 8; tries++) {
    await page.keyboard.press("ArrowRight"); await page.waitForTimeout(80);
    k0 = await page.evaluate(() => { const c = chan(S.sel), k = S.selKeys[0], i = c.keys.indexOf(k), next = c.keys[i + 1];
      return { n: S.selKeys.length, t: k?.[0], v: k?.[1], keys: c.keys.length, fps: S.fps || 60, playhead: S.t,
               room: next ? next[0] - k[0] : S.duration - k[0], signed: SIGNED_CH.test(S.sel) }; });
    if (k0.room > 2 / k0.fps) break;
  }
  ok(k0.n === 1 && Math.abs(k0.playhead - k0.t) < 1e-6, "ArrowRight selects a key and moves the playhead to it", JSON.stringify(k0));
  await page.keyboard.press("Shift+ArrowRight"); await page.waitForTimeout(60);
  const k1 = await page.evaluate(() => S.selKeys[0]?.[0]);
  ok(Math.abs(k1 - k0.t - 1 / k0.fps) < 1e-6, "Shift+ArrowRight nudges the key one frame later", `${k0.t} → ${k1} (frame ${1 / k0.fps})`);
  const step = k0.signed ? 1 : 0.01, up = k0.signed || k0.v < 0.5;
  await page.keyboard.press(up ? "ArrowUp" : "ArrowDown"); await page.waitForTimeout(60);
  const v1 = await page.evaluate(() => S.selKeys[0]?.[1]);
  ok(Math.abs(v1 - k0.v - (up ? step : -step)) < 1e-6, `${up ? "ArrowUp" : "ArrowDown"} nudges the value by ${step}`, `${k0.v} → ${v1}`);
  await page.waitForTimeout(500);                      // the nudge burst settles into one undo entry
  await page.keyboard.press("."); await page.waitForTimeout(60);      // step the playhead off the key
  await page.keyboard.press("Enter"); await page.waitForTimeout(120);
  const added = await page.evaluate(() => chan(S.sel).keys.length);
  ok(added === k0.keys + 1, "Enter adds a key at the playhead (after '.' stepped it one frame)", `${k0.keys} → ${added}`);
  await page.keyboard.press("Delete"); await page.waitForTimeout(120);
  const deleted = await page.evaluate(() => chan(S.sel).keys.length);
  ok(deleted === k0.keys, "Delete removes the selected key", `${added} → ${deleted}`);
  await page.keyboard.press("Control+z"); await page.waitForTimeout(120);
  const undone = await page.evaluate(() => chan(S.sel).keys.length);
  ok(undone === k0.keys + 1, "Ctrl+Z restores it", `${deleted} → ${undone}`);
  const spokenKey = await page.$eval("#srStatus", e => e.textContent);
  ok(/key \d+ of \d+/.test(spokenKey), "key selection and edits are announced", `said: "${spokenKey}"`);
  // phoneme strip: select a boundary, move it a frame, the take re-solves
  await page.click('.tab[data-view="phonemes"]'); await page.waitForTimeout(250);
  await page.focus("#phonStrip");
  await page.keyboard.press("Home"); await page.waitForTimeout(100);
  const b0 = await page.evaluate(() => ({ ends: S.segments.map(s => s.end), spoken: document.getElementById("srStatus").textContent, fps: S.fps || 60 }));
  await page.keyboard.press("Shift+ArrowRight"); await page.waitForTimeout(80);
  const b1 = await page.evaluate(() => S.segments.map(s => s.end));
  const moved = b1.map((e, i) => Math.abs(e - b0.ends[i]) > 1e-9 ? i : -1).filter(i => i >= 0);
  ok(moved.length === 1 && Math.abs(b1[moved[0]] - b0.ends[moved[0]] - 1 / b0.fps) < 1e-6,
     "Shift+ArrowRight moves exactly one phoneme boundary one frame later", JSON.stringify({ moved, from: b0.ends[moved[0]], to: b1[moved[0]] }));
  await page.waitForTimeout(900);                      // the re-solve fires once the burst settles
  const resolved = await page.evaluate(() => S.track.channels.length);
  ok(resolved > 0 && /boundary \d+ of \d+/.test(b0.spoken), "the boundary is announced and the take re-solves afterwards", `spoken: "${b0.spoken}", channels: ${resolved}`);
  // face graph: arrows pick a node
  await page.click('.tab[data-view="facegraph"]'); await page.waitForTimeout(400);
  await page.focus("#facegraph"); await page.keyboard.press("ArrowRight"); await page.waitForTimeout(150);
  const node = await page.evaluate(() => ({ kind: S.inspectKind, label: S.node?.label, inspector: !!S.node && document.getElementById("inspector")?.textContent.includes(S.node.label) }));
  ok(node.kind === "node" && !!node.label && node.inspector, "ArrowRight on the Face Graph selects a node and the Inspector shows it", JSON.stringify(node));
  // events: arrows jump between markers
  await page.click('.tab[data-view="events"]'); await page.waitForTimeout(200);
  if (!(await page.evaluate(() => (S.events || []).length))) { await page.click("#evRun"); await page.waitForFunction(() => (S.events || []).length > 0, null, { timeout: 20000 }).catch(() => {}); }
  await page.evaluate(() => { S.t = 0; S.playClock = 0; setScrub(); drawAll(); });
  await page.focus("#eventsTl"); await page.keyboard.press("ArrowRight"); await page.waitForTimeout(100);
  const evJump = await page.evaluate(() => ({ n: (S.events || []).length, t: S.t }));
  ok(evJump.n > 0 && evJump.t > 0, "ArrowRight on the Events timeline jumps the playhead to the next event", JSON.stringify(evJump));
  // pose pad: arrows turn the head
  await page.click('.tab[data-view="preview"]'); await page.waitForTimeout(200);
  if (await page.evaluate(() => document.getElementById("posePanel").hidden)) await page.click("#poseToggle");
  await page.waitForTimeout(100);
  const yaw0 = await page.evaluate(() => { const c = chan("headYaw"); return c ? sample(c.keys, S.t) : 0; });
  await page.focus("#posePad"); await page.keyboard.press("ArrowRight"); await page.waitForTimeout(100);
  const yaw1 = await page.evaluate(() => { const c = chan("headYaw"); return c ? sample(c.keys, S.t) : null; });
  ok(yaw1 !== null && Math.abs(yaw1 - yaw0 - 1) < 1e-6, "ArrowRight on the pose pad turns the head 1° (a headYaw key at the playhead)", `${yaw0} → ${yaw1}`);
  await page.keyboard.press("Home"); await page.waitForTimeout(80);

  /* ---------------- the core workflows, end to end ---------------- */
  // Real downloads, a real WAV, import/align/batch/QA round-trips, take CRUD,
  // what survives a reload, error recovery. Fixtures live in a temp dir.
  group("Workflows: export, audio, import, align, batch, QA, takes");
  const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "offx-e2e-"));
  const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
  const takesInfo = () => page.evaluate(() => ({ n: curActor().takes.length, idx: S.takeIdx, names: curActor().takes.map(t => t.name) }));
  const generate = async (text) => {
    await page.click('.tab[data-view="preview"]'); await page.fill("#text", text);
    await page.evaluate(() => { window.__prevTrack = S.track; }); await page.click("#run");
    await page.waitForFunction(() => S.track && S.track !== window.__prevTrack && !document.getElementById("run").disabled, null, { timeout: 60000 });
    await page.waitForTimeout(150);
  };
  const toasts = () => page.evaluate(() => [...document.querySelectorAll("#toasts .toast")].map(t => (t.classList.contains("error") ? "error: " : "") + t.textContent));
  // exports: every button yields a real file; .fuz refuses without audio (a toast, not a dialog)
  await generate("Hello world, this is a live end to end test of every exporter.");
  await page.click('.tab[data-view="export"]'); await page.waitForTimeout(200);
  const fmts = await page.$$eval("#exportGrid button", bs => bs.map(b => b.dataset.fmt));
  const exported = {}, noFile = [];
  for (const f of fmts) {
    const [dl] = await Promise.all([page.waitForEvent("download", { timeout: 8000 }).catch(() => null), page.click(`#exportGrid button[data-fmt="${f}"]`)]);
    if (!dl) { noFile.push(f); await page.waitForTimeout(150); continue; }
    const p = path.join(TMP, dl.suggestedFilename()); await dl.saveAs(p);
    exported[f] = { file: dl.suggestedFilename(), size: fs.statSync(p).size };
    await page.waitForTimeout(100);
  }
  report.exports = exported;
  ok(fmts.length >= 15 && Object.values(exported).every(e => e.size > 0), `${Object.keys(exported).length} of ${fmts.length} exporters produced a non-empty download`,
     Object.entries(exported).filter(([, e]) => !e.size).map(([f]) => f).join(", "));
  ok(noFile.length === 1 && noFile[0] === "fuz", "only .fuz refuses before a voice clip is loaded", `no download: ${noFile.join(", ")}`);
  const dExp = dialogs.length;                       // click it again: the notice must be visible right away, with no dialog
  await page.click('#exportGrid button[data-fmt="fuz"]'); await page.waitForTimeout(400);
  ok((await toasts()).some(t => /^error: Export failed.*audio/i.test(t)) && dialogs.length === dExp, "that refusal is a visible notice, not a blocking dialog",
     JSON.stringify({ toasts: await toasts(), dialogs: dialogs.slice(dExp) }));
  const trackJson = JSON.parse(fs.readFileSync(path.join(TMP, exported.json.file), "utf8"));
  ok(Array.isArray(trackJson.channels) && trackJson.channels.length > 0, "the Track JSON download parses and carries channels", `${trackJson.channels?.length} channels`);
  // audio: a real WAV → spectrogram, energy engine, audio-clocked playback, .fuz
  await page.click('.tab[data-view="preview"]');
  await page.setInputFiles("#wav", path.join(ROOT, "examples", "speech.wav"));
  await page.waitForFunction(() => !!S.wavBytes && !!S.wavSpec, null, { timeout: 15000 });
  const wavState = await page.evaluate(() => ({ name: document.getElementById("wavName").textContent, engine: document.getElementById("engine").value, dur: +document.getElementById("dur").value }));
  ok(/speech\.wav/.test(wavState.name) && wavState.engine === "energy" && Math.abs(wavState.dur - 3.27) < 0.05, "loading a WAV names it, sets the duration and switches to the energy engine", JSON.stringify(wavState));
  await generate("The quick brown fox jumps over the lazy dog.");
  await page.click('.tab[data-view="phonemes"]'); await page.waitForTimeout(400);
  const specLit = await page.evaluate(() => { const c = document.getElementById("wave"), x = c.getContext("2d"); const d = x.getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < d.length; i += 4 * 37) if (d[i] > 8) n++; return n; });
  ok(specLit > 500, "the spectrogram paints from the loaded audio", `sampled ${specLit} lit pixels`);
  ok(await page.$eval("#tpMute", e => !e.hidden), "the mute button appears once audio is loaded");
  await page.click("#tpPlay"); await page.waitForTimeout(1200);
  const playing = await page.evaluate(() => ({ playing: S.playing, t: S.t }));
  await page.click("#tpPlay"); await page.waitForTimeout(100);
  ok(playing.playing && playing.t > 0.2, "Play advances the playhead (even if the audio device stalls, the frame clock takes over)", JSON.stringify(playing));
  ok(await page.evaluate(() => !S.playing && !audioPlaying()), "Pause stops playback and the audio source");
  await page.click('.tab[data-view="export"]'); await page.waitForTimeout(150);
  const [fuz] = await Promise.all([page.waitForEvent("download", { timeout: 20000 }).catch(() => null), page.click('#exportGrid button[data-fmt="fuz"]')]);
  ok(!!fuz && fs.statSync(await fuz.path()).size > 1000, "with audio loaded, .fuz exports (lip + clip)", fuz ? fuz.suggestedFilename() : "no download");
  // import the exported track back as a new take
  const before = await takesInfo();
  await page.click('.tab[data-view="preview"]');
  await page.setInputFiles("#importFile", path.join(TMP, exported.json.file));
  await page.waitForFunction(n => curActor().takes.length > n, before.n, { timeout: 15000 }).catch(() => {}); await page.waitForTimeout(250);
  const imported = await takesInfo();
  ok(imported.n === before.n + 1 && await page.evaluate(n => S.track?.channels?.length === n, trackJson.channels.length),
     "Import track… adds a take with the same channels", `${before.n} → ${imported.n}, hint: ${await page.$eval("#importHint", e => e.textContent)}`);
  // align from a words JSON
  fs.writeFileSync(path.join(TMP, "words.json"), JSON.stringify([{ word: "hello", start: 0.1, end: 0.45 }, { word: "brave", start: 0.5, end: 0.9 }, { word: "new", start: 0.95, end: 1.2 }, { word: "world", start: 1.25, end: 1.8 }]));
  await page.fill("#text", "hello brave new world");
  await page.selectOption("#alignFmt", "words");
  await page.setInputFiles("#alignFile", path.join(TMP, "words.json"));
  await page.waitForFunction(n => curActor().takes.length > n, imported.n, { timeout: 15000 }).catch(() => {}); await page.waitForTimeout(250);
  const aligned = await page.evaluate(() => ({ segs: S.segments.length, dur: S.duration, hint: document.getElementById("alignHint").textContent }));
  ok(aligned.segs > 4 && Math.abs(aligned.dur - 1.8) < 0.01, "Align from… words makes a take timed by the word anchors", JSON.stringify(aligned));
  // batch: one take per line, reported by a notice
  const batchBefore = await takesInfo();
  await page.fill("#text", "First line of the batch.\nSecond line here.\nThird and last line."); await page.waitForTimeout(150);
  ok(await page.$eval("#batch", b => !b.hidden), "a multi-line transcript reveals the Batch button");
  await page.click("#batch"); await page.waitForFunction(n => curActor().takes.length >= n + 3, batchBefore.n, { timeout: 60000 }).catch(() => {}); await page.waitForTimeout(300);
  ok((await takesInfo()).n === batchBefore.n + 3 && (await toasts()).some(t => /^Batched 3 takes/.test(t)), "Batch makes one take per line and says so in a notice", JSON.stringify({ takes: (await takesInfo()).n, toasts: await toasts() }));
  // QA + pronunciation editor round-trip
  await generate("The xylozor hummed softly.");
  await page.click('.tab[data-view="phonemes"]'); await page.waitForTimeout(150);
  await page.click("#qaRun"); await page.waitForFunction(() => !!document.querySelector("#qaPanel .pw-in"), null, { timeout: 20000 }).catch(() => {}); await page.waitForTimeout(200);
  const oovBefore = await page.$$eval("#qaPanel .pw-in", is => is.map(i => i.dataset.word));
  ok(oovBefore.includes("xylozor"), "Run QA flags the made-up word as out-of-vocabulary with an editable pronunciation", oovBefore.join(", "));
  await page.fill('#qaPanel .pw-in[data-word="xylozor"]', "Z AY1 L OW0 Z ER0");
  await page.click("#pwAll"); await page.waitForTimeout(1500);
  await page.click("#qaRun"); await page.waitForTimeout(1200);
  const oovAfter = await page.evaluate(() => ({ oov: [...document.querySelectorAll("#qaPanel .pw-in")].map(i => i.dataset.word), dict: curTake().cmudict }));
  ok(!oovAfter.oov.includes("xylozor") && /XYLOZOR\s+Z AY1 L OW0 Z ER0/.test(oovAfter.dict), "Save all & re-generate writes the dictionary and clears the flag", JSON.stringify(oovAfter));
  // takes: duplicate, rename, delete (with a confirmation)
  const t0n = (await takesInfo()).n;
  await page.click("#ioMenuBtn"); await page.click('#ioMenu [data-act="take-dup"]'); await page.waitForTimeout(200);
  ok((await takesInfo()).n === t0n + 1, "Duplicate take adds a copy");
  await page.click("#ioMenuBtn"); await page.click('#ioMenu [data-act="take-rename"]'); await page.waitForTimeout(100);
  await page.keyboard.type("Renamed take"); await page.keyboard.press("Enter"); await page.waitForTimeout(150);
  const tr = await takesInfo();
  ok(tr.names[tr.idx] === "Renamed take", "Rename take… renames it inline", tr.names[tr.idx]);
  const d0 = dialogs.length;
  await page.click("#ioMenuBtn"); await page.click('#ioMenu [data-act="take-del"]'); await page.waitForTimeout(200);
  ok(dialogs.length === d0 + 1 && /Delete take/.test(dialogs[d0] || "") && (await takesInfo()).n === t0n, "Delete take asks for confirmation first, then deletes", dialogs[d0]);
  // a bad import is a notice, and the app keeps working
  fs.writeFileSync(path.join(TMP, "garbage.json"), "{not json at all");
  const dBad = dialogs.length;
  await page.click('.tab[data-view="preview"]'); await page.setInputFiles("#importFile", path.join(TMP, "garbage.json")); await page.waitForTimeout(1200);
  ok(dialogs.length === dBad && (await toasts()).some(t => /^error: Import failed/.test(t)), "a broken import file yields an error notice, not a dialog", JSON.stringify(await toasts()));
  await generate("Still working after a bad import.");
  ok(await page.evaluate(() => !!S.track), "…and Generate still works afterwards");
  // an empty transcript says what it made
  await generate("");
  ok((await toasts()).some(t => /Empty transcript/.test(t)), "an empty transcript explains it made a silent take", JSON.stringify(await toasts()));
  // built-in voice
  await page.fill("#text", "Generate a voice for me."); await page.evaluate(() => { S.wavBytes = null; });
  await page.click("#ttsBtn"); await page.waitForFunction(() => !!S.wavBytes && !document.getElementById("ttsBtn").disabled, null, { timeout: 60000 }).catch(() => {});
  const tts = await page.evaluate(() => ({ bytes: S.wavBytes?.length || 0, spec: !!S.wavSpec, name: document.getElementById("wavName").textContent }));
  ok(tts.bytes > 10000 && tts.spec && /AI voice/.test(tts.name), "Generate voice synthesises a clip and drives the spectrogram", JSON.stringify(tts));

  /* ---------------- what survives a reload ---------------- */
  group("Reload: theme and session persist");
  const themeBefore = await page.evaluate(() => document.documentElement.dataset.theme);
  await page.click("#themeToggle"); const themeSet = await page.evaluate(() => document.documentElement.dataset.theme);
  const takesBefore = await page.evaluate(() => S.actors.reduce((n, a) => n + a.takes.filter(t => t.track).length, 0));
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => !document.querySelector("#run")?.disabled, null, { timeout: 60000 }); await page.waitForTimeout(700);
  ok(themeSet !== themeBefore && await page.evaluate(() => document.documentElement.dataset.theme) === themeSet, `the ${themeSet} theme survives a reload`);
  const restored = await page.evaluate(() => ({ takes: S.actors.reduce((n, a) => n + a.takes.filter(t => t.track).length, 0), toasts: [...document.querySelectorAll("#toasts .toast")].map(t => t.textContent) }));
  ok(restored.takes === takesBefore && takesBefore > 0, `the session is restored after a reload (${restored.takes} takes) and announced`, JSON.stringify(restored));
  const dFresh = dialogs.length;
  await page.click("#ioMenuBtn"); await page.click('#ioMenu [data-act="ws-reset"]'); await page.waitForTimeout(300);
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => !document.querySelector("#run")?.disabled, null, { timeout: 60000 }); await page.waitForTimeout(500);
  ok(dialogs.length === dFresh + 1 && await page.evaluate(() => !S.track && S.actors.every(a => !a.takes.some(t => t.track))), "Start a fresh workspace… (confirmed) clears the saved session for good");
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));

  /* ---------------- accounts (opt-in: needs a throwaway DB) ---------------- */
  // Registers a user, so it only runs when OFFX_E2E_ACCOUNTS=1 — start the server
  // with OFFX_STUDIO_DB pointing at a scratch file first (see README).
  if (process.env.OFFX_E2E_ACCOUNTS === "1") {
    group("Accounts: register → save project → reload → open → delete → sign out");
    await generate("Persist me across a reload please.");
    const email = `e2e_${Date.now()}@example.test`;
    await page.click("#acctChip"); await page.waitForTimeout(200);
    if (await page.$("#authForm")) {
      await page.click('#acctBody .acct-tab[data-mode="register"]'); await page.waitForTimeout(100);
      await page.fill("#authEmail", email); await page.fill("#authPass", "correct-horse-battery");
      await page.click('#authForm button[type="submit"]');
      await page.waitForFunction(() => !!document.getElementById("projSave"), null, { timeout: 15000 }).catch(() => {});
    }
    ok((await page.$eval("#acctChip", e => e.textContent)).includes(email), "registering signs the user in (chip shows the email)");
    await page.fill("#projName", "E2E project"); await page.click("#projSave");
    await page.waitForFunction(() => document.querySelectorAll("#projList .acct-item").length > 0, null, { timeout: 15000 }).catch(() => {});
    ok((await page.$$eval("#projList .acct-item .acct-name", es => es.map(e => e.textContent))).includes("E2E project"), "Save new lists the project");
    await page.keyboard.press("Escape");
    await page.reload({ waitUntil: "domcontentloaded" }); await page.waitForFunction(() => !document.querySelector("#run")?.disabled, null, { timeout: 60000 }); await page.waitForTimeout(600);
    ok((await page.$eval("#acctChip", e => e.textContent)).includes(email), "the session cookie keeps the user signed in across a reload");
    await page.click("#ioMenuBtn"); await page.click('#ioMenu [data-act="ws-reset"]'); await page.waitForTimeout(300);   // drop the autosaved copy so Open is what restores it
    await page.click("#acctChip"); await page.waitForFunction(() => document.querySelectorAll("#projList .acct-item").length > 0, null, { timeout: 15000 }).catch(() => {});
    await page.click('#projList .acct-item [data-act="open"]'); await page.waitForTimeout(800);
    const opened = await page.evaluate(() => ({ closed: document.getElementById("acctModal").hidden, text: document.getElementById("text").value, track: !!S.track }));
    ok(opened.closed && opened.track && /Persist me/.test(opened.text), "Open restores the saved take and closes the dialog", JSON.stringify(opened));
    await page.click("#acctChip"); await page.waitForTimeout(300);
    await page.click('#projList .acct-item [data-act="del"]'); await page.waitForTimeout(500);
    ok(/No saved projects/.test(await page.$eval("#projList", e => e.textContent)), "Delete removes the project");
    await page.click("#signOut"); await page.waitForTimeout(300);
    ok(/Sign in/.test(await page.$eval("#acctChip", e => e.textContent)), "Sign out returns the chip to Sign in");
    await page.keyboard.press("Escape");
  }
  fs.rmSync(TMP, { recursive: true, force: true });

  /* ---------------- no console noise anywhere ---------------- */
  group("Console");
  // a static host that isn't the Pages deploy (no data-offx-static stamp) is
  // probed for a backend once per boot; those two 404s are expected there
  const PROBE = /\/api\/(health|auth\/me)\b/;
  const realErrors = RUNTIME === "browser" ? consoleErrors.filter(e => !(/Failed to load resource/.test(e) && PROBE.test(e))) : consoleErrors;
  report.consoleErrors = realErrors;
  ok(realErrors.length === 0, "no console errors across the whole session",
     realErrors.slice(0, 4).join(" | "));

} catch (err) {
  fail++; failures.push({ name: "suite crashed", detail: err.message });
  console.log("\n✗ SUITE CRASHED: " + err.message);
} finally {
  await browser.close();
}

report.pass = pass; report.fail = fail; report.failures = failures;
if (JSON_OUT) fs.writeFileSync(JSON_OUT, JSON.stringify(report, null, 2));
console.log(`\n${fail ? "FAIL" : "PASS"} — ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
