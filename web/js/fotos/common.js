// Fotomodulet: fælles hjælpere (CSS, adgang, datoer, fotokort, sideskift).
import { h, state } from "../core.js";

export const BASE = "#/fotos";
/** Oprydning når man forlader siden (SSE-lyttere, tastaturgenveje …); køres af skallen. */
export const cleanups = [];
export const ctx = () => state.user?.fotos || { features: {}, can_moderate: false, can_admin: false, privilegie: "standard" };
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
  const d = new Date(iso.length === 10 ? iso + "T12:00" : iso);
  const s = `${d.getDate()}. ${(long ? ML : MS)[d.getMonth()]} ${d.getFullYear()}`;
  return time ? `${s} kl. ${d.toLocaleTimeString("da-DK", { hour: "2-digit", minute: "2-digit" })}` : s;
}
export const nowrap = (n) => new Intl.NumberFormat("da-DK").format(n);
export const loadingBox = (t = "Henter …") => h("div", { class: "muted", style: { padding: "16px 0" } }, t);
export const notice = (t, kind = "") => h("div", { class: `notice ${kind}` }, t);
export const bytes = (n) => n >= 1e9 ? `${(n / 1e9).toFixed(2).replace(".", ",")} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1).replace(".", ",")} MB` : `${Math.round(n / 1e3)} kB`;

export function pager(page, pages, go) {
  if (pages <= 1) return null;
  const items = [];
  const from = Math.max(1, Math.min(page - 2, pages - 4)), to = Math.min(pages, from + 4);
  if (page > 1) items.push(h("a", { href: "javascript:void 0", onclick: () => go(page - 1) }, "‹ Forrige"));
  for (let p = from; p <= to; p++) items.push(p === page ? h("span", { class: "on" }, p) : h("a", { href: "javascript:void 0", onclick: () => go(p) }, p));
  if (page < pages) items.push(h("a", { href: "javascript:void 0", onclick: () => go(page + 1) }, "Næste ›"));
  return h("nav", { class: "fot-pager", "aria-label": "Sider" }, ...items);
}

export const STATUS = { afventer: "Afventer godkendelse", publiceret: "Publiceret", afvist: "Afvist", afpubliceret: "Afpubliceret" };

/** Kort i gitteret: miniature (lazy, med bredde/højde), hovedart (+N arter), dato, lokalitet, fotograf, likes og visninger. */
export function photoCard(p, { href = `${BASE}/${p.id}` } = {}) {
  const extra = Math.max(0, (p.arter?.length || 0) - 1);
  const img = h("img", { src: p.thumb, alt: p.arter?.[0]?.navn || p.titel || "Foto", loading: "lazy", decoding: "async" });
  if (p.w && p.h) { img.width = 400; img.height = Math.round(400 * p.h / p.w); }
  return h("a", { class: "fot-card", href },
    h("div", { class: "fot-img" }, img, p.gruppe ? h("span", { class: "fot-badge" }, `+${p.gruppe}`) : null,
      p.status && p.status !== "publiceret" ? h("span", { class: "fot-badge warn" }, STATUS[p.status] || p.status) : null),
    h("div", { class: "fot-card-body" },
      h("b", {}, p.arter?.[0]?.navn || p.titel || "Foto", extra ? h("small", { class: "muted" }, ` +${extra} ${extra === 1 ? "art" : "arter"}`) : null),
      h("div", { class: "fot-meta" }, [dkDate(p.dato), p.lokalitet].filter(Boolean).join(" · ")),
      h("div", { class: "fot-meta" }, p.fotograf?.navn || "", h("span", { class: "fot-stats" }, `♥ ${p.likes} · 👁 ${nowrap(p.views)}${p.kommentarer ? ` · 💬 ${p.kommentarer}` : ""}`))));
}
