// Snak: fælles hjælpere (navn, rettigheder, CSS, avatar, kategori-tag, indlæsning). Navnet «Snak» står kun her og i funktionsflagets tekst.
import { api, h, icon, state, trustedHTML } from "../core.js";

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
/** Rund avatar: profilbilledet (lille variant), ellers initialer; farven følger navnet, så den er stabil. */
export function avatar(author, size = 40) {
  if (author.avatar) return h("img", { class: "sn-avatar", src: author.avatar, alt: "", width: size, height: size, loading: "lazy", decoding: "async" });
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
export const plain = (html) => { const d = new DOMParser().parseFromString(trustedHTML(html), "text/html"); return (d.body.textContent || "").trim(); };

/** Billeder i et opslag eller en kommentar: kontaktark, der åbner en lysboks (native <dialog>). Bredde/højde er sat, så siden ikke hopper. */
export function gallery(images = [], { single = false } = {}) {
  if (!images.length) return null;
  const open = (i) => {
    let at = i;
    const img = h("img", { class: "sn-lb-img", alt: images[at].alt || "" });
    const show = () => { img.src = images[at].url; img.alt = images[at].alt || ""; counter.textContent = images.length > 1 ? `${at + 1} / ${images.length}` : ""; };
    const step = (d) => { at = (at + d + images.length) % images.length; show(); };
    const counter = h("span", { class: "muted small" });
    const dlg = h("dialog", { class: "sn-lightbox", "aria-label": "Billede", onclick: (e) => { if (e.target === dlg) dlg.close(); },
      onkeydown: (e) => { if (e.key === "ArrowLeft") step(-1); else if (e.key === "ArrowRight") step(1); } },
    h("button", { type: "button", class: "icon-btn sn-lb-close", "aria-label": "Luk", onclick: () => dlg.close() }, "✕"),
    images.length > 1 ? h("button", { type: "button", class: "icon-btn sn-lb-prev", "aria-label": "Forrige billede", onclick: () => step(-1) }, "‹") : null,
    img, counter,
    images.length > 1 ? h("button", { type: "button", class: "icon-btn sn-lb-next", "aria-label": "Næste billede", onclick: () => step(1) }, "›") : null);
    dlg.addEventListener("close", () => dlg.remove());
    document.body.append(dlg);
    show();
    dlg.showModal();
  };
  // Browseren vælger selv mellem miniaturen (≤400 px) og mellemstørrelsen (≤1200 px) ud fra den viste bredde og skærmens pixeltæthed.
  // Afledningerne er skaleret til at passe i en kvadrat, så den reelle bredde afhænger af billedets format.
  const n = Math.min(images.length, 4);
  const sizes = single ? "260px" : n === 1 ? "(max-width: 760px) 100vw, 730px" : n === 3 ? "(max-width: 760px) 33vw, 240px" : "(max-width: 760px) 50vw, 365px";
  const w = (im, box) => im.width && im.height ? Math.round(im.width * Math.min(1, box / Math.max(im.width, im.height))) : box;  // forstørres aldrig
  const srcset = (im) => im.thumb && im.medium && im.thumb !== im.medium ? `${im.thumb} ${w(im, 400)}w, ${im.medium} ${w(im, 1200)}w` : undefined;
  return h("div", { class: `sn-gallery n${n} ${single ? "single" : ""}` },
    ...images.map((im, i) => h("button", { type: "button", class: "sn-thumb", "aria-label": im.alt || `Åbn billede ${i + 1}`, onclick: () => open(i) },
      h("img", { loading: "lazy", decoding: "async", srcset: srcset(im), sizes: srcset(im) ? sizes : undefined, src: im.medium || im.thumb, alt: im.alt || "", width: im.width || undefined, height: im.height || undefined }))));
}

const ATT_LABEL = { artikel: "Artikel", foto: "Foto", traad: "Observationer", art: "Art", lokalitet: "Lokalitet", link: "Link" };
/** Vedhæftet kort (artikel, foto, tråd, art, lokalitet eller eksternt link). Uden url (modul slået fra eller mål væk) vises et neutralt kort. */
export function attachmentCard(a, { onRemove } = {}) {
  if (!a) return null;
  const external = a.type === "link";
  const body = [
    a.image ? h("img", { class: "sn-att-img", src: a.image, alt: "", loading: "lazy", decoding: "async", referrerpolicy: "no-referrer" }) : null,
    h("span", { class: "sn-att-text" },
      h("span", { class: "sn-att-kind muted small" }, ATT_LABEL[a.type] || "", external && a.host ? ` · ${a.host}` : ""),
      h("b", {}, a.unavailable ? "Ikke tilgængeligt" : (a.title || a.host || "Link")),
      a.teaser ? h("span", { class: "muted small" }, a.teaser) : null,
      a.meta ? h("span", { class: "muted small" }, a.meta) : null)];
  const card = a.url && !a.unavailable
    ? h("a", { class: "sn-att", href: a.url, ...(external ? { target: "_blank", rel: "noopener noreferrer nofollow ugc" } : {}) }, ...body)
    : h("div", { class: "sn-att off" }, ...body);
  return onRemove ? h("div", { class: "sn-att-wrap" }, card, h("button", { type: "button", class: "icon-btn", "aria-label": "Fjern vedhæftning", onclick: onRemove }, "✕")) : card;
}

/** Arter og lokalitet som små chips under teksten. */
export function extras(post) {
  const arter = post.arter_navn || [];
  if (!arter.length && !post.lok_navn) return null;
  return h("div", { class: "sn-extras" },
    ...arter.map(a => h("a", { class: "sn-chip", href: `#/art/${a.artnr}` }, h("span", { class: "mi", html: icon("bird") }), a.navn)),
    post.lok_navn && post.loknr ? h("a", { class: "sn-chip", href: `#/lok/${post.loknr}` }, h("span", { class: "mi", html: icon("pin") }), post.lok_navn) : null);
}

/** Upload ét billede til opslag (kind «post») eller kommentar («comment»). Giver {id, url, thumb, medium, …}. */
export async function uploadImage(file, kind = "post") {
  const form = new FormData();
  form.append("file", file);
  form.append("kind", kind);
  return api("/api/snak/media", { method: "POST", form });
}
