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
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const consoleErrors = [];
page.on("console", m => { if (m.type() === "error") consoleErrors.push(m.text()); });
page.on("pageerror", e => consoleErrors.push("pageerror: " + e.message));

const report = { url: URL, tabs: [] };

try {
  /* ---------------- boot ---------------- */
  group("Boot");
  const t0 = Date.now();
  await page.goto(URL, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => !document.querySelector("#run")?.disabled, null, { timeout: 60000 });
  report.bootMs = Date.now() - t0;
  ok(true, `Studio became interactive (${report.bootMs} ms)`);
  ok(await page.title() === "OpenFaceFX Studio", "document title");
  const runtime = await page.$eval("#runtimeLabel", e => e.textContent.trim()).catch(() => "");
  ok(/native/.test(runtime), "native runtime detected", `runtime chip said: ${runtime || "(empty)"}`);

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

  /* ---------------- no console noise anywhere ---------------- */
  group("Console");
  report.consoleErrors = consoleErrors;
  ok(consoleErrors.length === 0, "no console errors across the whole session",
     consoleErrors.slice(0, 4).join(" | "));

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
