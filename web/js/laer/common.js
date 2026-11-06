// Læring 2.0: fælles hjælpere (CSS, medier, kort, procenter).
import { h } from "../core.js";
import { silhouette, hueOf } from "./silhouettes.js";

export const BASE = "#/laer";
const loaded = new Set();

/** Hent modulets CSS første gang (samme versionerede sti som js-filerne). */
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
export const pct = (a, b) => (b ? Math.round((100 * a) / b) : 0);
export const LEVELS = { begynder: "Begynder", "øvet": "Øvet", erfaren: "Erfaren", ekspert: "Erfaren" };
export const KINDS = { kursus: "Kursus", ovelse: "Øvelse", quiz: "Quiz", spil: "Spil" };

/** SVG-silhuet som element. */
export function silEl(key, { label = "" } = {}) {
  const d = h("span", { class: "lr-sil-wrap" });
  d.innerHTML = silhouette(key, { label });
  return d;
}

/** Kortets billedflade: silhuet på farveflade. */
export function art(c, cls = "") {
  if (c.cover?.url) {  // forsidebillede fra fotobiblioteket
    return h("div", { class: `lr-art lr-art-photo ${cls}`, style: { background: hueOf(c.hue) } },
      h("img", { src: c.cover.url, alt: "", loading: "lazy" }), c.cover.credit ? h("small", { class: "lr-credit" }, `Foto: ${c.cover.credit}`) : null);
  }
  const el = h("div", { class: `lr-art ${cls}`, style: { background: hueOf(c.hue) } });
  el.append(silEl(c.sil));
  return el;
}

/** Bredt bannerbillede med kreditering (kursusside). */
export function banner(cover, alt = "") {
  return h("figure", { class: "lr-banner" }, h("img", { src: cover.url, alt }), cover.credit ? h("figcaption", { class: "lr-credit" }, `Foto: ${cover.credit}`) : null);
}

/** Medie til et spørgsmål eller en blok: foto, lyd, silhuet, to fotos side om side. */
/** Klik på et foto i læringsmodulet åbner det stort (Esc, klik eller ✕ lukker). Én delegeret lytter dækker alle fotos. */
let lightboxOn = false;
function initLightbox() {
  if (lightboxOn) return;
  lightboxOn = true;
  const close = () => { document.querySelector(".lr-lightbox")?.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = e => { if (e.key === "Escape") close(); };
  document.addEventListener("click", e => {
    const img = e.target instanceof Element ? e.target.closest(".lr-media img") : null;
    if (!img) return;
    close();
    const cap = img.closest("figure")?.querySelector("figcaption")?.textContent;
    const box = h("div", { class: "lr-lightbox", role: "dialog", "aria-label": "Foto i stort format", onclick: close },
      h("button", { class: "lr-lightbox-x", "aria-label": "Luk", onclick: close }, "✕"),
      h("img", { src: img.currentSrc || img.src, alt: img.alt }), cap ? h("p", {}, cap) : null);
    document.body.append(box);
    document.addEventListener("keydown", onKey);
  });
}

export function mediaEl(m, { onHidden } = {}) {
  if (!m || !m.kind) return null;
  initLightbox();
  if (m.kind === "silhouette") {
    const f = h("figure", { class: "lr-media lr-sil", style: { background: hueOf(m.hue) } });
    f.append(silEl(m.value, { label: "Silhuet af en fugl" }));
    return f;
  }
  if (m.kind === "image") {
    return h("figure", { class: "lr-media" }, h("img", { src: m.value, alt: "Foto til spørgsmålet", loading: "lazy", "data-photo": m.photo_id || null }),
      m.credit ? h("figcaption", {}, `Foto: ${m.credit}`) : null);
  }
  if (m.kind === "images") {
    return h("div", { class: "lr-pair" }, ...m.urls.map((u, i) => h("figure", { class: "lr-media" }, h("img", { src: u.url, alt: `Foto ${i + 1}`, loading: "lazy" }),
      h("figcaption", {}, `Foto ${i + 1}${u.credit ? ` · ${u.credit}` : ""}`))));
  }
  if (m.kind === "audio") {
    return h("figure", { class: "lr-media lr-audio" }, h("audio", { controls: true, preload: "auto", src: m.value }), m.credit ? h("figcaption", {}, m.credit) : null);
  }
  return null;
}

export function bar(done, total, label) {
  return h("div", { class: "lr-bar", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": total, "aria-valuenow": done, title: label || null },
    h("i", { style: { width: `${pct(done, total)}%` } }));
}

export function chip(text, cls = "") { return h("span", { class: `lr-chip ${cls}` }, text); }

export const backLink = (href, text) => h("p", { class: "lr-back" }, h("a", { href }, `← ${text}`));
