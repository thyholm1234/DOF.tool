// Artikelmodulet: fælles hjælpere (CSS, links, datoer, billedkort med pladsholder, emneords- og artschips, sideskift).
import { api, h, icon, state, params } from "../core.js";

export const BASE = "#/artikler";
/** Oprydning når man forlader siden (SSE-lyttere, tastaturgenveje …); køres af skallen. */
export const cleanups = [];
export const link = (path, text, attrs = {}) => h("a", { href: BASE + path, ...attrs }, text);
export const ctx = () => state.user?.artikler || { features: {}, can_edit: false, can_moderate: false, admin: false };
export const on = (f) => !!ctx().features?.[f];

const loaded = new Set();
/** Hent modulets CSS første gang (samme versionerede sti som js-filerne, så den cache'es sikkert). */
export function loadCss(...names) {
  for (const name of names) {
    if (loaded.has(name) || document.querySelector(`link[data-css="${name}"]`)) continue;
    const path = new URL(import.meta.url).pathname;
    const href = path.startsWith("/v/") ? `/v/${path.split("/")[2]}/css/${name}.css` : `/css/${name}.css`;
    document.head.append(h("link", { rel: "stylesheet", href, "data-css": name }));
    loaded.add(name);
  }
}

const MS = ["jan.", "feb.", "mar.", "apr.", "maj", "jun.", "jul.", "aug.", "sep.", "okt.", "nov.", "dec."];
const ML = ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december"];
/** «9. maj 2001» (med klokkeslæt: «9. maj 2001 kl. 20:27») */
export function dkDate(iso, { time = false, long = false } = {}) {
  if (!iso) return "";
  const d = new Date(iso);
  const s = `${d.getDate()}. ${(long ? ML : MS)[d.getMonth()]} ${d.getFullYear()}`;
  return time ? `${s} kl. ${d.toLocaleTimeString("da-DK", { hour: "2-digit", minute: "2-digit" })}` : s;
}
export const nowrap = (n) => new Intl.NumberFormat("da-DK").format(n);

/** Billede til et kort: valgt forsidebillede → første billede → PDF-forside → typografisk pladsholder (aldrig et tomt hul). */
export function cover(a, { size = "thumb", ratio = "3 / 2", cls = "" } = {}) {
  const c = a.cover;
  if (c && (c.thumb || c.medium)) {
    const src = size === "medium" ? (c.medium || c.thumb) : (c.thumb || c.medium);
    const img = h("img", { src, alt: c.alt || "", loading: "lazy", decoding: "async" });
    if (c.w && c.h && size === "medium") { img.width = c.w; img.height = c.h; }
    return h("div", { class: `art-img ${c.kind === "pdf" ? "pdf" : ""} ${cls}`, style: { aspectRatio: ratio } }, img, c.kind === "pdf" ? h("span", { class: "art-badge" }, "PDF") : null);
  }
  const hue = (a.id * 47) % 360;
  const word = a.arter?.[0]?.navn || a.tags?.[0]?.navn || (a.title || "?").trim().slice(0, 1).toUpperCase();
  return h("div", { class: `art-img art-ph ${cls}`, style: { aspectRatio: ratio, "--h": String(hue) }, "aria-hidden": "true" },
    h("span", { class: "ph-word" }, word), h("span", { class: "ph-ic", html: icon("bird") }));
}

export const authors = (a) => (a.authors || []).join(", ");
export const byline = (a) => [authors(a), dkDate(a.dato)].filter(Boolean).join(" · ");
export const meta = (a) => h("div", { class: "art-meta" }, byline(a), a.kommentarer ? ` · 💬 ${a.kommentarer}` : "", a.status && a.status !== "publiceret" ? h("span", { class: "art-chip warn" }, a.status) : null);

export const tagChips = (tags = [], limit = 99) => tags.length ? h("div", { class: "art-chips" }, ...tags.slice(0, limit).map(t => h("a", { class: "art-chip", href: `${BASE}/emneord/${t.id}` }, t.navn))) : null;
export const speciesChips = (arter = [], limit = 99) => arter.length ? h("div", { class: "art-chips" }, ...arter.slice(0, limit).map(s => h("a", { class: `art-chip art ${s.source === "ocr" ? "weak" : ""}`, href: `${BASE}/arter/${s.artnr}`, title: s.source === "ocr" ? "Nævnt i PDF'ens tekst" : "" }, s.navn))) : null;

export function pager(page, pages, go) {
  if (pages <= 1) return null;
  const items = [];
  const from = Math.max(1, Math.min(page - 2, pages - 4)), to = Math.min(pages, from + 4);
  if (page > 1) items.push(h("a", { href: "javascript:void 0", onclick: () => go(page - 1) }, "‹ Forrige"));
  for (let p = from; p <= to; p++) items.push(p === page ? h("span", { class: "on" }, p) : h("a", { href: "javascript:void 0", onclick: () => go(p) }, p));
  if (page < pages) items.push(h("a", { href: "javascript:void 0", onclick: () => go(page + 1) }, "Næste ›"));
  return h("nav", { class: "art-pager", "aria-label": "Sider" }, ...items);
}

/** Resultatkort (søgning, emneord, arter, relaterede): billede til venstre, tekst til højre. */
export function listCard(a) {
  return h("article", { class: "art-row" },
    h("a", { class: "art-row-img", href: `${BASE}/${a.id}`, tabindex: "-1", "aria-hidden": "true" }, cover(a, { ratio: "4 / 3" })),
    h("div", { class: "art-row-body" },
      h("h3", {}, h("a", { href: `${BASE}/${a.id}` }, a.title)),
      meta(a),
      a.headline ? h("p", { class: "art-snip", html: (a.headline_fra === "pdf" ? '<span class="art-chip weak">fra PDF</span> ' : "") + a.headline }) : h("p", { class: "art-snip" }, a.teaser),
      h("div", { class: "art-chips-row" }, tagChips(a.tags, 3), speciesChips(a.arter, 4))));
}

export const loadingBox = (t = "Henter …") => h("div", { class: "muted", style: { padding: "16px 0" } }, t);
export const notice = (t, kind = "") => h("div", { class: `notice ${kind}` }, t);
export { api, params };
