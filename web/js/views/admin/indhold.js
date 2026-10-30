// Moderation → Nyheder: nyheder på forsiden (« Nyt fra portalen »), skrevet i den fælles markdown-editor med billeder, manchet og planlagt publicering.
import { api, h, fmtDate, toast, modal } from "../../core.js";

const STATUS = { kladde: "Kladde", planlagt: "Planlagt", publiceret: "Publiceret" };
const local = (iso) => { if (!iso) return ""; const d = new Date(iso); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16); };

export async function content(body) {
  const { loadCss } = await import("../../artikler/common.js");
  loadCss("artikler", "editor");
  const { mdEditor } = await import("../../editor/md-editor.js");
  const { mediaPicker } = await import("../../editor/media-picker.js");
  const list = h("div", { class: "stack" });
  const holder = h("div", {});
  body.append(h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, "Nyheder"), h("button", { class: "btn primary", type: "button", onclick: () => open(null) }, "+ Ny nyhed")), holder, list);

  async function open(id) {
    const n = id ? await api(`/api/admin/news/${id}`) : { title: "", lead: "", body: "", status: "publiceret", publish_at: null, version: null, cover: null };
    const S = { id, version: n.version, cover: n.cover_media_id || null, coverUrl: n.cover?.thumb || null };
    const title = h("input", { type: "text", maxlength: 200, placeholder: "Overskrift", value: n.title, "aria-label": "Overskrift", class: "art-title-input" });
    const lead = h("textarea", { rows: 2, maxlength: 2000, placeholder: "Manchet (valgfri – vises på forsiden)" }); lead.value = n.lead || "";
    const status = h("select", {}, ...Object.entries(STATUS).map(([v, t]) => h("option", { value: v, selected: v === n.status }, t)));
    const when = h("input", { type: "datetime-local", value: local(n.publish_at) });
    const whenRow = h("label", { class: "field" }, h("span", {}, "Publiceres"), when);
    const sync = () => { whenRow.style.display = status.value === "planlagt" ? "" : "none"; }; status.onchange = sync; sync();
    const ed = mdEditor({ value: n.body, ownerKind: "nyhed", getOwnerId: () => S.id, profile: "nyhed", features: { species: false, articleLinks: false }, draftKey: `nyhed-${id || "ny"}` });
    const coverBox = h("div", { class: "row" });
    const drawCover = () => coverBox.replaceChildren(S.coverUrl ? h("img", { src: S.coverUrl, alt: "", style: { height: "56px", borderRadius: "6px" } }) : h("span", { class: "muted small" }, "Intet forsidebillede"),
      h("button", { class: "btn sm", type: "button", onclick: () => mediaPicker({ ownerKind: "nyhed", ownerId: S.id, kinds: ["billede"], title: "Vælg billede", onPick: ([m]) => { if (m) { S.cover = m.id; S.coverUrl = m.thumb || m.url; drawCover(); } } }) }, "Vælg billede …"),
      S.cover ? h("button", { class: "btn sm ghost", type: "button", onclick: () => { S.cover = null; S.coverUrl = null; drawCover(); } }, "Fjern") : null);
    drawCover();
    const close = () => { ed.destroy(); holder.replaceChildren(); };
    const save = async () => {
      try {
        const r = await api(S.id ? `/api/admin/news/${S.id}` : "/api/admin/news", { method: S.id ? "PUT" : "POST", body: {
          title: title.value, body: ed.value || " ", lead: lead.value.trim() || null, cover_media_id: S.cover, status: status.value,
          publish_at: when.value ? new Date(when.value).toISOString() : null, version: S.version } });
        S.id = r.id; S.version = r.version; ed.markClean(); toast("Gemt"); close(); load();
      } catch (e) { toast(e.status === 409 ? "Nyheden er ændret af en anden – åbn den igen" : e.message, "err"); }
    };
    holder.replaceChildren(h("div", { class: "card stack", style: { margin: "12px 0" } }, title, lead, ed.el,
      h("div", { class: "row" }, h("label", { class: "field" }, h("span", {}, "Status"), status), whenRow, h("div", {}, coverBox)),
      h("div", { class: "row" }, h("button", { class: "btn primary", type: "button", onclick: save }, "Gem"), h("button", { class: "btn", type: "button", onclick: close }, "Annullér"))));
    holder.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function load() {
    const rows = await api("/api/admin/news");
    list.replaceChildren(...(rows.length ? rows.map(x => h("div", { class: "row between card" }, h("div", {}, h("b", {}, x.title), h("small", { class: "muted" }, ` ${fmtDate(x.created_at)} · ${STATUS[x.status] || x.status}${x.status === "planlagt" ? " " + fmtDate(x.publish_at) : ""}`),
      h("div", { class: "muted small" }, x.teaser)),
      h("div", { class: "row" }, h("a", { class: "btn sm ghost", href: `#/nyheder/${x.id}` }, "Vis"), h("button", { class: "btn sm", type: "button", onclick: () => open(x.id) }, "Redigér"),
        h("button", { class: "btn sm ghost danger", type: "button", onclick: async () => { if (!confirm(`Slet «${x.title}»?`)) return; await api(`/api/admin/news/${x.id}`, { method: "DELETE" }); load(); } }, "Slet")))) : [h("p", { class: "muted" }, "Ingen nyheder endnu.")]));
  }
  await load();
  void modal;
}
