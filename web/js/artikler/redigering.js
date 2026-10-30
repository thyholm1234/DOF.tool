// Redigering af en artikel: titel + markdown-editor til venstre, metadata-panel til højre (manchet, forfattere, dato, emneord, forsidebillede,
// status/planlagt publicering, fremhævning, kommentarer, kilde). Optimistisk låsning, revisioner og kladde i browseren.
import { api, h, toast, modal } from "../core.js";
import { BASE, cleanups, dkDate, ctx, loadCss, loadingBox } from "./common.js";
import { mdEditor } from "../editor/md-editor.js";
import { mediaPicker } from "../editor/media-picker.js";

const local = (iso) => { if (!iso) return ""; const d = new Date(iso); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16); };
const toIso = (v) => v ? new Date(v).toISOString() : null;

async function load(id) {
  if (!id) return { id: null, title: "", lead: "", body_md: "", authors: [], tags_all: [], status: "kladde", comments_open: true, kind: "artikel", version: null, images: [], dato: null };
  return api(`/api/artikler/${id}`);
}

export async function ny() { return form(null); }
export async function ret(parts) { return form(+parts[0]); }

async function form(id) {
  loadCss("editor");
  if (!ctx().can_edit) return h("div", { class: "notice err" }, "Du har ikke adgang til at redigere artikler.");
  const a = await load(id);
  if (id && !a.body_md && a.body_md !== "") return h("div", { class: "notice err" }, "Artiklen kan ikke redigeres.");
  const all = await api("/api/artikler/emneord").catch(() => []);
  document.title = (id ? "Redigér: " + a.title : "Ny artikel") + " – Artikler";
  const S = { id, version: a.version, cover: a.cover?.id || a.cover_media_id || null, coverUrl: a.cover?.thumb || null, tags: (a.tags_all || a.tags || []).map(t => t.navn), authors: (a.authors || []).map(x => x.navn) };

  const title = h("input", { class: "art-title-input", type: "text", maxlength: 300, placeholder: "Titel", value: a.title, "aria-label": "Titel" });
  const ed = mdEditor({ value: a.body_md || "", ownerKind: "artikel", getOwnerId: () => S.id, draftKey: `artikel-${id || "ny"}`, onChange: () => touch() });
  cleanups.push(() => ed.destroy());
  const banner = h("div", {});
  const d = ed.draft();
  if (d) banner.append(h("div", { class: "notice" }, `Der er en ikke-gemt kladde fra ${dkDate(new Date(d.at).toISOString(), { time: true })} i denne browser. `,
    h("button", { class: "btn sm", type: "button", onclick: () => { ed.value = d.value; ed.insert(""); banner.replaceChildren(); toast("Kladden er gendannet"); } }, "Gendan"), " ",
    h("button", { class: "btn sm ghost", type: "button", onclick: () => { ed.markClean(); banner.replaceChildren(); } }, "Kassér")));

  const field = (label, el, hint) => h("label", { class: "field" }, h("span", {}, label), el, hint ? h("small", { class: "muted" }, hint) : null);
  const lead = h("textarea", { rows: 3, maxlength: 2000, placeholder: "Kort manchet under overskriften (valgfri; ellers bruges første afsnit)" }); lead.value = a.lead || "";
  const dato = h("input", { type: "datetime-local", value: local(a.dato) });
  const status = h("select", {}, ...[["kladde", "Kladde"], ["publiceret", "Publiceret"], ["planlagt", "Planlagt"], ["skjult", "Skjult"]].map(([v, t]) => h("option", { value: v, selected: v === a.status }, t)));
  const publishAt = h("input", { type: "datetime-local", value: local(a.publish_at) });
  const planRow = h("div", {}, field("Publiceres", publishAt, "Tidspunktet skal ligge i fremtiden"));
  const featured = h("input", { type: "datetime-local", value: local(a.featured_until) });
  const comments = h("input", { type: "checkbox", checked: a.comments_open });
  const source = h("input", { type: "url", maxlength: 400, value: a.source_url || "", placeholder: "https://…" });
  const kind = h("select", {}, h("option", { value: "artikel", selected: a.kind !== "pdf" }, "Artikel"), h("option", { value: "pdf", selected: a.kind === "pdf" }, "PDF-artikel"));
  const capt = h("input", { type: "text", maxlength: 300, value: a.cover_caption || "", placeholder: "Billedtekst til forsidebilledet" });
  const syncPlan = () => { planRow.style.display = status.value === "planlagt" ? "" : "none"; }; status.onchange = syncPlan; syncPlan();

  const chipBox = (key, ph, listId, options) => {
    const box = h("div", { class: "art-chips" }), inp = h("input", { type: "text", placeholder: ph, list: listId, autocomplete: "off" });
    const draw = () => box.replaceChildren(...S[key].map((t, i) => h("button", { type: "button", class: "art-chip", title: "Fjern", onclick: () => { S[key].splice(i, 1); draw(); touch(); } }, `${t} ×`)));
    const add = () => { const v = inp.value.trim(); if (v && !S[key].some(x => x.toLowerCase() === v.toLowerCase())) { S[key].push(v); draw(); touch(); } inp.value = ""; };
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); } });
    inp.addEventListener("change", add);
    draw();
    return h("div", { class: "stack" }, box, inp, options ? h("datalist", { id: listId }, ...options.map(o => h("option", { value: o }))) : null);
  };
  const tagsUi = chipBox("tags", "Tilføj emneord (Enter)", "art-tags-dl", all.filter(t => t.kind !== "aar").map(t => t.navn));
  const authorsUi = chipBox("authors", "Tilføj forfatter (Enter)", "art-authors-dl", null);
  const coverBox = h("div", { class: "stack" });
  const drawCover = () => coverBox.replaceChildren(S.coverUrl ? h("img", { src: S.coverUrl, alt: "", style: { width: "100%", borderRadius: "6px" } }) : h("p", { class: "muted small" }, "Intet valgt – første billede i artiklen bruges."),
    h("div", { class: "row" }, h("button", { class: "btn sm", type: "button", onclick: () => mediaPicker({ ownerKind: "artikel", ownerId: S.id, kinds: ["billede"], title: "Vælg forsidebillede",
      onPick: ([m]) => { if (m) { S.cover = m.id; S.coverUrl = m.thumb || m.url; drawCover(); touch(); } } }) }, "Vælg billede …"),
      S.cover ? h("button", { class: "btn sm ghost", type: "button", onclick: () => { S.cover = null; S.coverUrl = null; drawCover(); touch(); } }, "Fjern") : null));
  drawCover();

  const saved = h("span", { class: "muted small" }, id ? "Gemt" : "Ikke gemt endnu");
  function touch() { saved.textContent = "Ændringer ikke gemt"; }
  [title, lead, dato, status, publishAt, featured, comments, source, kind, capt].forEach(x => x.addEventListener("input", touch));

  const body = (overrideStatus) => ({
    title: title.value.trim(), lead: lead.value.trim() || null, body_md: ed.value, authors: S.authors.map(navn => ({ navn })), dato: toIso(dato.value), tags: S.tags,
    cover_media_id: S.cover, cover_caption: capt.value.trim() || null, status: overrideStatus || status.value, publish_at: toIso(publishAt.value),
    featured_until: toIso(featured.value), comments_open: comments.checked, source_url: source.value.trim() || null, kind: kind.value, version: S.version,
  });
  async function save(overrideStatus) {
    if (!title.value.trim()) { toast("Skriv en titel", "err"); title.focus(); return; }
    try {
      const r = await api(S.id ? `/api/admin/artikler/${S.id}` : "/api/admin/artikler", { method: S.id ? "PUT" : "POST", body: body(overrideStatus) });
      S.id = r.id; S.version = r.version; if (overrideStatus) status.value = overrideStatus; syncPlan();
      ed.markClean(); saved.textContent = "Gemt " + new Date().toLocaleTimeString("da-DK", { hour: "2-digit", minute: "2-digit" });
      toast("Gemt");
      if (!id) history.replaceState(null, "", `${BASE}/${r.id}/rediger`);
    } catch (e) {
      if (e.status === 409) conflict(); else toast(e.message, "err");
    }
  }
  function conflict() {
    const m = modal("Artiklen er ændret af en anden", h("div", { class: "stack" },
      h("p", {}, "Siden du åbnede artiklen, har en anden gemt en nyere version. Din tekst er ikke tabt – den ligger stadig her og i kladden i din browser."),
      h("div", { class: "row" },
        h("button", { class: "btn danger", type: "button", onclick: async () => { const fresh = await api(`/api/artikler/${S.id}`); S.version = fresh.version; m.close(); save(); } }, "Overskriv med min version"),
        h("button", { class: "btn", type: "button", onclick: () => { m.close(); window.open(`${BASE}/${S.id}`, "_blank"); } }, "Se den nye version i en ny fane"))));
  }
  async function revisions() {
    if (!S.id) return;
    const list = await api(`/api/admin/artikler/${S.id}/revisioner`);
    const pane = h("div", { class: "stack" });
    const m = modal("Tidligere versioner", h("div", { class: "stack" }, list.length ? h("ul", {}, ...list.map(r => h("li", {}, h("a", { href: "javascript:void 0", onclick: async () => {
      const v = await api(`/api/admin/artikler/${S.id}/revisioner/${r.id}`);
      pane.replaceChildren(h("h4", {}, `Version ${v.version}: ${v.title}`), h("div", { class: "art-body", style: { maxHeight: "40vh", overflow: "auto", border: "1px solid var(--line)", padding: "8px 12px" }, html: v.html }),
        h("button", { class: "btn danger", type: "button", onclick: async () => { if (!confirm("Gendan denne version? Den nuværende bliver gemt som en revision.")) return;
          try { await api(`/api/admin/artikler/${S.id}/revisioner/${r.id}/gendan`, { method: "POST" }); m.close(); ed.markClean(); location.reload(); } catch (e) { toast(e.message, "err"); } } }, "Gendan denne version"));
    } }, `Version ${r.version} – ${r.title}`), ` · ${dkDate(r.created_at, { time: true })}${r.af ? " · " + r.af : ""}`))) : h("p", { class: "muted" }, "Der er ingen tidligere versioner endnu."), pane), { wide: true });
  }

  const panel = h("aside", { class: "panel card stack" },
    h("div", { class: "row" }, h("button", { class: "btn primary", type: "button", onclick: () => save() }, "Gem"),
      status.value !== "publiceret" ? h("button", { class: "btn", type: "button", onclick: () => save("publiceret") }, "Publicér") : null, saved),
    field("Status", status), planRow, field("Manchet", lead), field("Forfattere", authorsUi), field("Dato", dato), field("Emneord", tagsUi, "Arter findes automatisk i teksten"),
    field("Forsidebillede", coverBox), field("Billedtekst", capt), field("Type", kind), field("Fremhæv på forsiden til", featured),
    h("label", { class: "check" }, comments, h("span", {}, "Kommentarer er åbne")), field("Kilde", source),
    S.id ? h("div", { class: "row" }, h("button", { class: "btn sm", type: "button", onclick: revisions }, "Versioner …"), h("a", { class: "btn sm", href: `${BASE}/${S.id}` }, "Vis artikel")) : null,
    a.source === "netfugl" ? h("p", { class: "muted small" }, "Importeret fra netfugl.dk. Når du gemmer, overskrives dine rettelser ikke af senere importer (medmindre importen bedes om det).") : null);
  return h("div", {}, banner, h("div", { class: "art-edit" }, h("div", { class: "stack" }, title, ed.el), panel));
}
export { loadingBox };
