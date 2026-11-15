// Snak: fælles hjælpere (navn, rettigheder, CSS, avatar, kategori-tag, indlæsning). Navnet «Snak» står kun her og i funktionsflagets tekst.
import { h, state } from "../core.js";

export const NAME = "Snak";
export const BASE = "#/snak";
/** Oprydning når man forlader siden (SSE-lyttere m.m.); køres af skallen. */
export const cleanups = [];
export const ctx = () => state.user?.snak || { features: {}, can_moderate: false, can_admin: false };
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

export const loadingBox = () => h("div", { class: "skeleton", style: { height: "120px" }, role: "status", "aria-label": "Henter" });
export const notice = (text, kind = "") => h("div", { class: `notice ${kind}` }, text);

export const initials = (navn = "?") => navn.trim().split(/\s+/).slice(0, 2).map(w => w[0]?.toUpperCase() || "").join("") || "?";
/** Rund avatar med initialer; farven følger navnet, så den er stabil. */
export function avatar(author, size = 40) {
  const hue = [...(author.navn || "?")].reduce((s, c) => s + c.charCodeAt(0), 0) % 360;
  return h("span", { class: "sn-avatar", style: { "--h": String(hue), width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.4)}px` }, "aria-hidden": "true" }, initials(author.navn));
}

export function categoryTag(cat) {
  return h("a", { class: "sn-cat", href: `${BASE}/k/${cat.slug}`, style: { "--c": cat.farve } }, cat.navn);
}

export function authorLink(author) {
  if (!author.obserkode || !state.user?.profiler) return h("b", {}, author.navn);
  return h("a", { href: `#/observator/${author.obserkode}` }, h("b", {}, author.navn));
}

/** Titel i fanen: opslagets titel eller de første 60 tegn af teksten. */
export const plain = (html) => { const d = new DOMParser().parseFromString(html || "", "text/html"); return (d.body.textContent || "").trim(); };
