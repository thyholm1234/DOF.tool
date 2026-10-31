// Artikelsiden: titel, manchet, byline, forsidebillede, brødtekst (renset HTML fra serveren), PDF-visning, galleri med lysboks,
// kommentarer lige under artiklen, derefter emneord og relaterede artikler. Redaktører får redigér/slet og kan rette arterne
// (arterne vises ikke på siden).
import { api, h, toast, modal, state } from "../core.js";
import { BASE, cover, dkDate, nowrap, tagChips, listCard, ctx, on, cleanups } from "./common.js";
import { commentsBox } from "./kommentarer.js";

const sizeLabel = (n) => n >= 1e6 ? `${(n / 1e6).toFixed(1).replace(".", ",")} MB` : `${Math.max(1, Math.round(n / 1e3))} kB`;
const touch = () => matchMedia("(pointer: coarse)").matches || /Android|iPhone|iPad/i.test(navigator.userAgent);

export async function fraNetfugl(parts) {
  const r = await api(`/api/artikler/n/${parts[1]}`);
  location.replace(`${BASE}/${r.id}`);
  return h("p", { class: "muted" }, "Åbner artiklen …");
}

function lightbox(images, start) {
  let i = start;
  const img = h("img", { alt: "" }), cap = h("div", { class: "cap" });
  const show = () => { const m = images[i]; img.src = m.url; img.alt = m.alt || ""; cap.textContent = [m.caption, m.credit].filter(Boolean).join(" · ") || m.alt || ""; };
  const close = () => { box.remove(); document.removeEventListener("keydown", key); };
  const go = (d) => { i = (i + d + images.length) % images.length; show(); };
  const key = (e) => { if (e.key === "Escape") close(); else if (e.key === "ArrowLeft") go(-1); else if (e.key === "ArrowRight") go(1); };
  let x0 = null;
  const box = h("div", { class: "art-lightbox", role: "dialog", "aria-modal": "true", onclick: (e) => { if (e.target === box) close(); },
    ontouchstart: (e) => { x0 = e.touches[0].clientX; }, ontouchend: (e) => { if (x0 !== null && Math.abs(e.changedTouches[0].clientX - x0) > 50) go(e.changedTouches[0].clientX < x0 ? 1 : -1); x0 = null; } },
    h("button", { class: "x", type: "button", "aria-label": "Luk", onclick: close }, "×"),
    images.length > 1 ? [h("button", { class: "p", type: "button", "aria-label": "Forrige", onclick: () => go(-1) }, "‹"), h("button", { class: "n", type: "button", "aria-label": "Næste", onclick: () => go(1) }, "›")] : null,
    img, cap);
  document.body.append(box);
  document.addEventListener("keydown", key);
  cleanups.push(close);
  show();
}

function speciesEditor(a, done) {
  const list = h("div", { class: "art-chips" }), out = h("div", { class: "stack" });
  const q = h("input", { type: "search", placeholder: "Tilføj art …", list: "art-sp", autocomplete: "off", style: { width: "100%" } });
  const dl = h("datalist", { id: "art-sp" });
  let found = [];
  const save = async (body) => { try { await api(`/api/admin/artikler/${a.id}/arter`, { method: "PUT", body }); toast("Gemt"); m.close(); done(); } catch (e) { toast(e.message, "err"); } };
  list.replaceChildren(...a.species.map(s => h("button", { type: "button", class: `art-chip art ${s.source === "ocr" ? "weak" : ""}`, title: "Fjern (falsk positiv)", onclick: () => save({ remove: [s.artnr] }) }, `${s.navn} ×`)));
  q.addEventListener("input", async () => {
    if (q.value.trim().length < 2) return;
    found = await api(`/api/species?q=${encodeURIComponent(q.value.trim())}&limit=15`).catch(() => []);
    dl.replaceChildren(...found.map(s => h("option", { value: s.navn })));
    const hit = found.find(s => s.navn.toLowerCase() === q.value.trim().toLowerCase());
    if (hit) save({ add: [hit.artnr] });
  });
  out.append(h("p", { class: "muted small" }, "Arterne findes automatisk i teksten. Klik på en art for at fjerne den, hvis den er forkert – den kommer ikke igen ved genberegning."),
    list, q, dl);
  const m = modal("Ret arter i artiklen", out);
}

export async function side(parts) {
  const id = +parts[0];
  const a = await api(`/api/artikler/${id}`);
  document.title = `${a.title} – Artikler`;
  const reload = async () => { const fresh = await side(parts); root.replaceWith(fresh); };
  const images = a.images.map(i => ({ url: i.medium || i.url, full: i.url, thumb: i.thumb, alt: i.alt, caption: i.caption, credit: i.credit }));
  const gallery = a.images.filter(i => !i.inline && !(a.cover && a.cover.id === i.id));
  const pdf = a.pdf;
  const body = h("div", { class: "art-body", html: a.body_html });
  // klik på et billede i teksten åbner lysboksen (alle billeder, i rækkefølge)
  body.addEventListener("click", (e) => {
    const link = e.target.closest?.("a.art-fig a, .art-fig a");
    const url = link?.getAttribute("href");
    const idx = url ? a.images.findIndex(i => i.url === url) : -1;
    if (idx >= 0) { e.preventDefault(); lightbox(images, idx); }
  });
  const editBar = ctx().can_edit ? h("div", { class: "art-toolbar" },
    h("a", { class: "btn sm", href: `${BASE}/${id}/rediger` }, "Redigér"),
    h("button", { class: "btn sm", type: "button", onclick: () => speciesEditor(a, reload) }, "Ret arter"),
    h("button", { class: "btn sm danger", type: "button", onclick: async () => {
      if (!confirm(`Slet «${a.title}» med alle medier og kommentarer?`)) return;
      try { await api(`/api/admin/artikler/${id}`, { method: "DELETE" }); toast("Slettet"); location.hash = BASE; } catch (e) { toast(e.message, "err"); } } }, "Slet"),
    a.status !== "publiceret" ? h("span", { class: "art-chip warn" }, a.status === "planlagt" ? `Planlagt ${dkDate(a.publish_at, { time: true })}` : a.status) : null,
    a.locally_edited ? h("span", { class: "art-chip" }, "rettet i portalen") : null) : null;
  const follow = on("artikler_notifikationer") ? h("button", { class: "btn sm", type: "button", onclick: async () => {
    try { await api(`/api/artikler/${id}/foelg`, { method: a.following ? "DELETE" : "PUT" }); a.following = !a.following; fb.textContent = a.following ? "🔔 Følger" : "🔕 Følg"; toast(a.following ? "Du får besked om nye kommentarer" : "Du følger ikke længere"); } catch (e) { toast(e.message, "err"); } } },
    a.following ? "🔔 Følger" : "🔕 Følg") : null;
  const fb = follow;
  const authorLinks = a.authors.map((x, i) => [i ? ", " : "", h("a", { href: `${BASE}/soeg?forfatter=${encodeURIComponent(x.navn)}` }, x.navn)]);

  const root = h("article", { class: "art-page" },
    editBar,
    h("h1", {}, a.title),
    a.lead ? h("p", { class: "lead" }, a.lead) : null,
    h("div", { class: "art-byline" }, authorLinks.length ? h("span", {}, "Af ", authorLinks) : null, h("span", {}, dkDate(a.dato, { long: true })),
      h("span", {}, `Læst ${nowrap(a.views)} gange`), a.kommentarer ? h("a", { href: "#kommentarer", onclick: (e) => { e.preventDefault(); document.getElementById("kommentarer")?.scrollIntoView({ behavior: "smooth" }); } }, `💬 ${a.kommentarer}`) : null, follow),
    a.cover && !a.cover.inline && a.kind !== "pdf" ? h("figure", { class: "art-cover" }, h("img", { src: a.cover.medium || a.cover.url, alt: a.cover.alt || "", width: a.cover.w || undefined, height: a.cover.h || undefined,
      style: { width: "100%", height: "auto", cursor: "zoom-in" }, onclick: () => lightbox(images, a.images.findIndex(i => i.id === a.cover.id)) }),
      (a.cover_caption || a.cover.caption) ? h("figcaption", {}, a.cover_caption || a.cover.caption) : null) : null,
    pdf ? h("div", { class: "art-block" },
      touch() ? null : h("iframe", { class: "art-pdfview", src: pdf.url + "#view=FitH", title: pdf.filename }),
      h("div", { class: "art-toolbar" }, h("a", { class: "btn primary", href: pdf.url, target: "_blank", rel: "noopener" }, "Åbn PDF"),
        h("a", { class: "btn", href: pdf.url, download: pdf.filename }, `Hent (${pdf.pages ? pdf.pages + " sider, " : ""}${sizeLabel(pdf.size)})`)),
      pdf.text_md ? h("details", {}, h("summary", {}, "Tekst fra PDF'en (maskinlæst – kan indeholde fejl)"),
        h("div", { style: { whiteSpace: "pre-wrap", fontSize: ".9rem", maxHeight: "60vh", overflow: "auto", padding: "8px 0" } }, pdf.text_md)) : null) : null,
    a.body_html ? body : null,
    gallery.length ? h("div", { class: "art-gallery" }, ...gallery.map(i => h("button", { type: "button", "aria-label": i.alt || "Åbn billede", onclick: () => lightbox(images, a.images.findIndex(x => x.id === i.id)) },
      h("img", { src: i.thumb, alt: i.alt || "", loading: "lazy", style: { width: "100%", aspectRatio: "4 / 3", objectFit: "cover", borderRadius: "4px" } })))) : null,
    (a.kind !== "pdf" ? a.pdfs : []).length || a.files.length ? h("div", { class: "art-block" }, h("h4", {}, "Vedhæftninger"),
      h("ul", {}, ...[...(a.kind !== "pdf" ? a.pdfs : []), ...a.files].map(f => h("li", {}, h("a", { href: f.url, target: "_blank", rel: "noopener" }, f.caption || f.filename), ` (${sizeLabel(f.size)}${f.pages ? `, ${f.pages} sider` : ""})`)))) : null,
    a.comments_on ? commentsBox(id, { open: a.comments_open && a.status === "publiceret", canModerate: ctx().can_moderate }) : null,
    a.tags.length ? h("div", { class: "art-block" }, h("h4", {}, "Emneord"), tagChips(a.tags)) : null,
    a.source_url ? h("p", { class: "muted small" }, "Oprindeligt bragt på netfugl.dk: ", h("a", { href: a.source_url, target: "_blank", rel: "noopener noreferrer" }, a.source_url.replace(/^https?:\/\/(www\.)?/, ""))) : null,
    ctx().can_edit && a.import_errors?.length ? h("details", { class: "muted small" }, h("summary", {}, `Importnoter (${a.import_errors.length})`), h("ul", {}, ...a.import_errors.map(e => h("li", {}, typeof e === "string" ? e : JSON.stringify(e))))) : null,
    a.related.length ? h("div", { class: "art-block" }, h("h4", {}, "Relaterede artikler"), ...a.related.map(listCard)) : null);
  void state;
  return root;
}
export const page = side;
