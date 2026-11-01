// Krydslister: fælles hjælpere (CSS, adgang, datoer, tabeller med søgning/CSV/print, artsvælger).
import { api, h, fmtN, state, searchSpecies } from "../core.js";

export const BASE = "#/lister";  // krydslisterne er faner under Lister (views/lister.js)
export const cleanups = [];
export const ctx = () => state.user?.krydslister || { features: {}, can_admin: false };
export const on = (f) => !!ctx().features?.[f];
export const link = (path, text, attrs = {}) => h("a", { href: BASE + path, ...attrs }, text);

const loaded = new Set();
export function loadCss(...names) {
  for (const name of names) {
    if (loaded.has(name) || document.querySelector(`link[data-css="${name}"]`)) continue;
    const path = new URL(import.meta.url).pathname;
    const href = path.startsWith("/v/") ? `/v/${path.split("/")[2]}/css/${name}.css` : `/css/${name}.css`;
    document.head.append(h("link", { rel: "stylesheet", href, "data-css": name }));
    loaded.add(name);
  }
}
export const loadingBox = (t = "Henter …") => h("div", { class: "muted", style: { padding: "16px 0" } }, t);
export const notice = (t, kind = "") => h("div", { class: `notice ${kind}` }, t);
export const title = (t) => h("h3", { class: "kl-title" }, t);
export const dkDate = (iso, time = false) => !iso ? "" : new Date(iso).toLocaleString("da-DK", time ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" });

/** Tabel med søgning, CSV og Print. rows: [{text, cells, cls?}] hvor text er det, søgningen matcher på. */
export function table(head, rows, opts = {}) {
  const filter = h("input", { type: "search", placeholder: "Søg …", "aria-label": "Søg i tabellen", style: { width: "200px" } });
  const tbody = h("tbody", {});
  const draw = () => {
    const f = filter.value.trim().toLowerCase();
    tbody.replaceChildren(...rows.filter(r => !f || r.text.toLowerCase().includes(f)).map(r => h("tr", { class: r.cls || "" },
      ...r.cells.map(c => h("td", { class: typeof c === "number" ? "num" : "" }, typeof c === "number" ? fmtN(c) : c)))));
  };
  filter.oninput = draw;
  draw();
  const csv = () => {
    const cell = (c) => `"${(c instanceof Node ? c.textContent : String(c ?? "")).replaceAll('"', '""')}"`;
    const lines = [head.join(";"), ...rows.map(r => r.cells.map(cell).join(";"))];
    const a = h("a", { href: URL.createObjectURL(new Blob(["﻿" + lines.join("\n")], { type: "text/csv" })), download: `${opts.name || "krydsliste"}.csv` });
    document.body.append(a); a.click(); a.remove();
  };
  return h("div", {}, h("div", { class: "kl-tools" }, h("button", { type: "button", class: "btn sm", onclick: csv }, "CSV"), h("button", { type: "button", class: "btn sm", onclick: () => print() }, "Print"),
    h("span", { class: "spacer" }), filter),
    h("div", { class: "kl-scroll" }, h("table", { class: "kl-table" }, h("thead", {}, h("tr", {}, ...head.map(t => h("th", {}, t)))), tbody)));
}

/** Søg i World Bird List (evt. kun en artsliste); onPick(art|null). */
export function taxonPicker({ liste = null, placeholder = "Artsnavn …", onPick }) {
  const out = h("div", { class: "kl-pick" }), sug = h("div", { class: "kl-suggest" });
  const inp = h("input", { type: "search", placeholder, autocomplete: "off", "aria-label": placeholder });
  let t;
  inp.oninput = () => {
    onPick(null);
    clearTimeout(t);
    if (inp.value.trim().length < 2) { sug.replaceChildren(); return; }
    t = setTimeout(async () => {
      const rows = await api(`/api/krydslister/arter?q=${encodeURIComponent(inp.value.trim())}${liste ? `&liste=${liste}` : ""}`).catch(() => []);
      sug.replaceChildren(...rows.map(r => h("button", { type: "button", onclick: () => { inp.value = `${r.su ? "* " : ""}${r.navn}`; sug.replaceChildren(); onPick(r); } },
        r.su ? h("b", { class: "kl-su" }, "* ") : null, r.navn, h("small", { class: "muted" }, ` · ${r.latin}`))));
    }, 200);
  };
  out.append(inp, sug);
  return out;
}
export { api, searchSpecies };
