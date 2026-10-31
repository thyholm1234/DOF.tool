// Markdown-editor til artikler og nyheder: værktøjslinje, genveje, træk-og-slip/indsæt af billeder, tabeller, gallerier, faktabokse,
// indlejring, arter og artikellinks, forhåndsvisning renderet af serveren (så den er identisk med resultatet), ord/tegn/læsetid og
// kladde i browseren. Ingen afhængigheder. Formatet er CommonMark + tabeller + ::: galleri / ::: boks (se services/content_render.py).
import { api, h, modal, toast, seg } from "../core.js";
import { describeDialog, mediaPicker, uploadFile } from "./media-picker.js";

const mq = () => matchMedia("(min-width: 1000px)").matches;

/** Indsæt tekst, så browserens fortryd (Ctrl+Z) virker; fald tilbage til setRangeText. */
function insertText(ta, text) {
  ta.focus();
  if (document.execCommand?.("insertText", false, text)) return;
  ta.setRangeText(text, ta.selectionStart, ta.selectionEnd, "end");
  ta.dispatchEvent(new Event("input", { bubbles: true }));
}
const selected = (ta) => ta.value.slice(ta.selectionStart, ta.selectionEnd);

export function embedHtml(input) {
  const u = input.trim();
  let m;
  if ((m = u.match(/^https?:\/\/(?:www\.)?xeno-canto\.org\/(?:XC)?(\d+)/i))) return `<iframe src="https://xeno-canto.org/${m[1]}/embed?simple=1" width="100%" height="140" title="Xeno-canto XC${m[1]}" loading="lazy"></iframe>`;
  if ((m = u.match(/^https?:\/\/(?:www\.)?vimeo\.com\/(\d+)/i))) return `<iframe src="https://player.vimeo.com/video/${m[1]}" width="640" height="360" allowfullscreen title="Vimeo ${m[1]}" loading="lazy"></iframe>`;
  if ((m = u.match(/^https?:\/\/(?:www\.)?(?:youtube\.com\/watch\?v=|youtu\.be\/)([\w-]{6,})/i))) return `<iframe src="https://www.youtube-nocookie.com/embed/${m[1]}" width="640" height="360" allowfullscreen title="Video" loading="lazy"></iframe>`;
  if (/^https:\/\//i.test(u) && !/\s/.test(u)) return `<iframe src="${u.replace(/"/g, "&quot;")}" width="100%" height="360" title="Indlejret indhold" loading="lazy"></iframe>`;
  return u;  // rå HTML
}

export function mdEditor({ value = "", ownerKind = "artikel", getOwnerId = () => null, profile = "artikel", features = {}, draftKey = null, onChange = () => {} } = {}) {
  const ta = h("textarea", { class: "mde-ta", spellcheck: "true", "aria-label": "Brødtekst (markdown)", value: "" });
  ta.value = value;
  const preview = h("div", { class: "mde-pv art-body" });
  const status = h("div", { class: "mde-status" });
  let mode = "skriv", dirty = false, timer, ptimer, seq = 0, destroyed = false, lastSaved = value;
  const owner = () => ({ ownerKind, ownerId: getOwnerId() });

  // ------------------------------------------------------------ tekstoperationer
  function wrap(before, after = before, ph = "tekst") {
    const sel = selected(ta) || ph;
    insertText(ta, before + sel + after);
    if (!selected(ta) && sel === ph) { ta.setSelectionRange(ta.selectionEnd - after.length - ph.length, ta.selectionEnd - after.length); }
  }
  function lines(fn) {
    const start = ta.value.lastIndexOf("\n", ta.selectionStart - 1) + 1;
    let end = ta.value.indexOf("\n", ta.selectionEnd); if (end < 0) end = ta.value.length;
    ta.setSelectionRange(start, end);
    insertText(ta, fn(ta.value.slice(start, end).split("\n")).join("\n"));
  }
  const prefix = (p) => lines(ls => ls.every(l => l.startsWith(p)) ? ls.map(l => l.slice(p.length)) : ls.map(l => p + l.replace(/^(#{1,6} |[-*] |\d+\. |> )/, "")));
  const numbered = () => lines(ls => ls.map((l, i) => `${i + 1}. ${l.replace(/^(\d+\. |[-*] |> |#{1,6} )/, "")}`));
  const block = (text) => { const before = ta.value.slice(0, ta.selectionStart); insertText(ta, (before && !before.endsWith("\n\n") ? (before.endsWith("\n") ? "\n" : "\n\n") : "") + text + "\n\n"); };
  const heading = (n) => lines(ls => ls.map(l => { const t = l.replace(/^#{1,6} /, ""); return l.startsWith("#".repeat(n) + " ") ? t : "#".repeat(n) + " " + t; }));

  // ------------------------------------------------------------ dialoger
  function linkDialog() {
    const text = h("input", { type: "text", value: selected(ta), placeholder: "Linktekst" });
    const url = h("input", { type: "text", placeholder: "https://… eller søg efter en artikel nedenfor" });
    const results = h("div", { class: "stack small" });
    let t;
    const search = h("input", { type: "search", placeholder: "Søg efter en artikel at linke til …" });
    search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(async () => {
      if (search.value.trim().length < 2) { results.replaceChildren(); return; }
      const d = await api(`/api/artikler?q=${encodeURIComponent(search.value.trim())}&mode=praecis`).catch(() => ({ rows: [] }));
      results.replaceChildren(...d.rows.slice(0, 6).map(a => h("a", { href: "javascript:void 0", onclick: () => { url.value = `artikel:${a.id}`; if (!text.value) text.value = a.title; } }, a.title)));
    }, 250); });
    const m = modal("Indsæt link", h("form", { class: "stack", onsubmit: (e) => { e.preventDefault(); if (!url.value.trim()) return; m.close(); insertText(ta, `[${text.value || url.value}](${url.value.trim()})`); } },
      h("label", { class: "field" }, h("span", {}, "Tekst"), text), h("label", { class: "field" }, h("span", {}, "Adresse"), url),
      features.articleLinks === false ? null : [search, results], h("div", { class: "row" }, h("button", { class: "btn primary" }, "Indsæt"))));
    setTimeout(() => (text.value ? url : text).focus(), 50);
  }
  function tableDialog() {
    const r = h("input", { type: "number", min: 1, max: 30, value: 3, style: { width: "80px" } }), c = h("input", { type: "number", min: 1, max: 12, value: 3, style: { width: "80px" } });
    const m = modal("Indsæt tabel", h("form", { class: "stack", onsubmit: (e) => {
      e.preventDefault(); m.close();
      const cols = Math.max(1, +c.value), rows = Math.max(1, +r.value);
      const row = (f) => "| " + Array.from({ length: cols }, (_, i) => f(i)).join(" | ") + " |";
      block([row(i => `Kolonne ${i + 1}`), row(() => "---"), ...Array.from({ length: rows }, () => row(() => " "))].join("\n"));
    } }, h("div", { class: "row" }, h("label", {}, "Rækker ", r), h("label", {}, "Kolonner ", c)), h("div", { class: "row" }, h("button", { class: "btn primary" }, "Indsæt"))));
  }
  function embedDialog() {
    const inp = h("textarea", { rows: 4, placeholder: "Indsæt adressen på en lydfil fra xeno-canto, en Vimeo-/YouTube-video – eller rå HTML" });
    const m = modal("Indlejr", h("form", { class: "stack", onsubmit: (e) => { e.preventDefault(); if (!inp.value.trim()) return; m.close(); block(embedHtml(inp.value)); } },
      inp, h("p", { class: "muted small" }, "Indlejring virker kun fra de værter, der er tilladt under Artikler → Admin → Indstillinger. Alt andet HTML renses af serveren (ingen scripts)."),
      h("div", { class: "row" }, h("button", { class: "btn primary" }, "Indsæt"))));
    setTimeout(() => inp.focus(), 50);
  }
  function speciesDialog() {
    const q = h("input", { type: "search", placeholder: "Søg efter en art …", autocomplete: "off" }), out = h("div", { class: "stack" });
    let t;
    q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(async () => {
      if (q.value.trim().length < 2) { out.replaceChildren(); return; }
      const rows = await api(`/api/species?q=${encodeURIComponent(q.value.trim())}&limit=12`).catch(() => []);
      out.replaceChildren(...rows.map(s => h("a", { href: "javascript:void 0", onclick: () => { m.close(); insertText(ta, `[[art:${s.artnr}]]`); } }, `${s.navn} `, h("small", { class: "muted" }, s.latin || ""))));
    }, 200); });
    const m = modal("Indsæt art", h("div", { class: "stack" }, q, out));
    setTimeout(() => q.focus(), 50);
  }
  function boksDialog() {
    const sel = selected(ta);
    insertText(ta, `::: boks Fakta\n${sel || "Tekst i faktaboksen"}\n:::`);
  }
  // Lydfiler indsættes som billedsyntaks og bliver til en afspiller; andre filer som et link med type og størrelse
  const fileLine = (m) => m.kind === "lyd" ? `![${(m.caption || m.filename).replace(/[\[\]]/g, "")}](media:${m.id})`
    : `[${m.caption || m.filename} (${m.kind === "pdf" ? "PDF, " : ""}${m.size >= 1e6 ? (m.size / 1e6).toFixed(1).replace(".", ",") + " MB" : Math.max(1, Math.round(m.size / 1e3)) + " kB"})](media:${m.id})`;
  const mediaLine = (m) => `![${m.alt || ""}](media:${m.id}${m.caption ? ` "${m.caption.replace(/"/g, "'")}"` : ""})`;

  async function addFiles(files) {
    for (const f of files) {
      try {
        toast(`Uploader ${f.name} …`);
        const m = await uploadFile(f, owner());
        if (m.kind === "billede") describeDialog(m, { onDone: (u) => block(mediaLine(u)) });
        else block(fileLine(m));
      } catch (e) { toast(`${f.name}: ${e.message}`, "err"); }
    }
  }
  const pickImage = () => mediaPicker({ ...owner(), kinds: ["billede"], title: "Indsæt billede", onPick: ([m]) => m && describeDialog(m, { onDone: (u) => block(mediaLine(u)) }) });
  const pickGallery = () => mediaPicker({ ...owner(), kinds: ["billede"], multi: true, title: "Vælg billeder til galleriet", onPick: (ms) => ms.length && block(`::: galleri\n${ms.map(mediaLine).join("\n\n")}\n:::`) });
  const pickFile = () => mediaPicker({ ...owner(), kinds: ["pdf", "lyd", "fil"], title: "Vedhæft PDF, lydfil eller anden fil", onPick: ([m]) => m && block(fileLine(m)) });

  // ------------------------------------------------------------ værktøjslinje
  const T = (label, title, fn, cls = "") => h("button", { type: "button", class: `mde-btn ${cls}`, title, "aria-label": title, onmousedown: (e) => e.preventDefault(), onclick: fn }, label);
  const sep = () => h("span", { class: "mde-sep" });
  const toolbar = h("div", { class: "mde-bar", role: "toolbar", "aria-label": "Formatering" },
    T("H2", "Overskrift 2", () => heading(2)), T("H3", "Overskrift 3", () => heading(3)), T("H4", "Overskrift 4", () => heading(4)), sep(),
    T("B", "Fed (Ctrl+B)", () => wrap("**"), "b"), T("I", "Kursiv (Ctrl+I)", () => wrap("_"), "i"), T("S", "Gennemstreget", () => wrap("~~"), "s"), sep(),
    T("🔗", "Link (Ctrl+K)", linkDialog), T("❝", "Citat", () => prefix("> ")), T("• Liste", "Punktliste", () => prefix("- ")), T("1. Liste", "Nummereret liste", numbered),
    T("▦ Tabel", "Tabel", tableDialog), T("―", "Vandret streg", () => block("---")), T("</>", "Kode", () => selected(ta).includes("\n") ? wrap("```\n", "\n```") : wrap("`")), sep(),
    T("🖼 Billede", "Indsæt billede", pickImage), T("▤ Galleri", "Galleri af flere billeder", pickGallery), T("▢ Faktaboks", "Faktaboks", boksDialog),
    T("📎 Fil/lyd", "Vedhæft PDF, lydfil eller anden fil", pickFile), T("⧉ Indlejr", "Indlejr lyd, video eller HTML", embedDialog),
    features.species === false ? null : T("🐦 Art", "Indsæt art", speciesDialog));
  const modes = seg([["skriv", "Skriv"], ["vis", "Vis"], ["side", "Side om side"]], mode, (v) => setMode(v));
  const full = T("⤢", "Fuld skærm", () => { root.classList.toggle("full"); document.body.classList.toggle("mde-lock", root.classList.contains("full")); }, "full");
  const head = h("div", { class: "mde-head" }, toolbar, h("div", { class: "mde-modes" }, modes, full));

  function setMode(v) {
    if (v === "side" && !mq()) v = "vis";
    mode = v;
    root.dataset.mode = v;
    modes.querySelectorAll("button").forEach((b, i) => b.classList.toggle("on", ["skriv", "vis", "side"][i] === v));
    if (v !== "skriv") renderPreview();
  }

  // ------------------------------------------------------------ forhåndsvisning og status
  async function renderPreview() {
    if (mode === "skriv") return;
    const my = ++seq;
    try {
      const r = await api("/api/content/render", { method: "POST", body: { md: ta.value, profile } });
      if (my !== seq || destroyed) return;
      preview.innerHTML = r.html || '<p class="muted">Intet at vise endnu.</p>';
      stats(r);
    } catch (e) { if (my === seq) preview.replaceChildren(h("div", { class: "notice err" }, e.message)); }
  }
  function stats(r) {
    const words = r?.words ?? (ta.value.trim() ? ta.value.trim().split(/\s+/).length : 0);
    status.replaceChildren(`${words} ord · ${ta.value.length} tegn · ca. ${Math.max(1, Math.round(words / 200))} min. læsning`,
      r?.species?.length ? h("span", {}, " · Arter: ", r.species.slice(0, 8).map(s => s.navn).join(", ")) : null,
      ...(r?.warnings || []).map(w => h("div", { class: "mde-warn" }, "⚠ " + w)));
  }

  // ------------------------------------------------------------ hændelser
  ta.addEventListener("input", () => {
    dirty = ta.value !== lastSaved;
    onChange(ta.value);
    stats(null);
    clearTimeout(ptimer); ptimer = setTimeout(renderPreview, 400);
  });
  ta.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "b") { e.preventDefault(); wrap("**"); }
    else if (mod && e.key.toLowerCase() === "i") { e.preventDefault(); wrap("_"); }
    else if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); linkDialog(); }
    else if (e.key === "Tab") {
      const ls = ta.value.slice(0, ta.selectionStart).split("\n").pop();
      if (/^\s*([-*]|\d+\.) /.test(ls) || selected(ta).includes("\n")) { e.preventDefault(); lines(arr => arr.map(l => e.shiftKey ? l.replace(/^( {1,2}|\t)/, "") : "  " + l)); }
    } else if (e.key === "Enter" && !e.shiftKey && !mod && ta.selectionStart === ta.selectionEnd) {
      const cur = ta.value.slice(0, ta.selectionStart).split("\n").pop();
      const m = cur.match(/^(\s*)([-*]|\d+\.|>) (.*)$/);
      if (m) {
        e.preventDefault();
        if (!m[3]) { lines(() => [""]); return; }  // tomt punkt afslutter listen
        const next = /\d/.test(m[2]) ? `${parseInt(m[2], 10) + 1}.` : m[2];
        insertText(ta, `\n${m[1]}${next} `);
      }
    }
  });
  ta.addEventListener("paste", (e) => { const files = [...(e.clipboardData?.files || [])].filter(f => f.type.startsWith("image/")); if (files.length) { e.preventDefault(); addFiles(files); } });
  ta.addEventListener("dragover", (e) => { if ([...(e.dataTransfer?.items || [])].some(i => i.kind === "file")) { e.preventDefault(); root.classList.add("drop"); } });
  ta.addEventListener("dragleave", () => root.classList.remove("drop"));
  ta.addEventListener("drop", (e) => { root.classList.remove("drop"); const files = [...(e.dataTransfer?.files || [])]; if (files.length) { e.preventDefault(); addFiles(files); } });
  const leave = (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
  addEventListener("beforeunload", leave);

  // kladde i browseren hvert 5. sekund
  if (draftKey) timer = setInterval(() => { if (dirty && ta.value !== lastSaved) { try { localStorage.setItem("mde:" + draftKey, JSON.stringify({ at: Date.now(), value: ta.value })); } catch { /* fuld lagring */ } } }, 5000);

  const root = h("div", { class: "mde", "data-mode": "skriv" }, head, h("div", { class: "mde-panes" }, ta, preview), status);
  setMode("skriv");
  stats(null);

  return {
    el: root,
    get value() { return ta.value; },
    set value(v) { ta.value = v; lastSaved = v; dirty = false; stats(null); if (mode !== "skriv") renderPreview(); },
    get dirty() { return dirty; },
    markClean() { lastSaved = ta.value; dirty = false; if (draftKey) { try { localStorage.removeItem("mde:" + draftKey); } catch { /* ignorér */ } } },
    insert(text) { insertText(ta, text); },
    focus() { ta.focus(); },
    setMode,
    draft() { try { const d = JSON.parse(localStorage.getItem("mde:" + draftKey) || "null"); return d && d.value !== ta.value ? d : null; } catch { return null; } },
    destroy() { destroyed = true; clearInterval(timer); clearTimeout(ptimer); removeEventListener("beforeunload", leave); document.body.classList.remove("mde-lock"); },
  };
}
