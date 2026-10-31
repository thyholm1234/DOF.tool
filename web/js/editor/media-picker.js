// Medievælger til editoren: upload (med skalering i browseren), vælg et eller flere medier, ret alt-tekst/billedtekst/kreditering, slet.
// Medier hører til en artikel eller nyhed (ownerKind/ownerId); før indlægget er gemt har de ingen ejer og tilhører den, der uploadede dem.
import { api, h, modal, toast, compressImage } from "../core.js";

const sizeLabel = (n) => n >= 1e6 ? `${(n / 1e6).toFixed(1).replace(".", ",")} MB` : `${Math.max(1, Math.round(n / 1e3))} kB`;

export async function uploadFile(file, { ownerKind, ownerId, alt = "", caption = "", credit = "" }) {
  const f = file.type?.startsWith("image/") ? await compressImage(file, { max: 2560, quality: 0.85, minBytes: 1_500_000 }) : file;
  const form = new FormData();
  form.append("file", f, f.name);
  form.append("owner_kind", ownerKind);
  if (ownerId) form.append("owner_id", ownerId);
  form.append("alt", alt); form.append("caption", caption); form.append("credit", credit);
  return api("/api/content/media", { method: "POST", form });
}

export const listMedia = (ownerKind, ownerId) => api(`/api/content/media?owner_kind=${ownerKind}${ownerId ? `&owner_id=${ownerId}` : ""}`);

/** Dialog hvor alt-tekst er påkrævet (tilgængelighed og søgning), før et billede indsættes. */
export function describeDialog(media, { onDone }) {
  const alt = h("input", { type: "text", maxlength: 300, value: media.alt || "", placeholder: "Beskriv billedet, fx «Dværgterne på Langli»", required: true });
  const caption = h("input", { type: "text", maxlength: 300, value: media.caption || "", placeholder: "Billedtekst (valgfri)" });
  const credit = h("input", { type: "text", maxlength: 160, value: media.credit || "", placeholder: "Fotograf / kreditering (valgfri)" });
  const m = modal("Indsæt billede", h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    try {
      const upd = await api(`/api/content/media/${media.id}`, { method: "PATCH", body: { alt: alt.value, caption: caption.value, credit: credit.value } });
      m.close(); onDone(upd);
    } catch (err) { toast(err.message, "err"); }
  } },
    h("img", { src: media.thumb || media.url, alt: "", style: { maxWidth: "100%", maxHeight: "220px", objectFit: "contain", borderRadius: "6px" } }),
    h("label", { class: "field" }, h("span", {}, "Alt-tekst (påkrævet)"), alt), h("label", { class: "field" }, h("span", {}, "Billedtekst"), caption), h("label", { class: "field" }, h("span", {}, "Kreditering"), credit),
    h("div", { class: "row" }, h("button", { class: "btn primary" }, "Indsæt"))));
  setTimeout(() => alt.focus(), 50);
}

/** Vælg medier. multi: flere (galleri). kinds: hvilke slags der vises. onPick får en liste af medier. */
export function mediaPicker({ ownerKind, ownerId, multi = false, kinds = ["billede", "pdf", "lyd", "fil"], title = "Medier", onPick }) {
  const grid = h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(130px, 1fr))", gap: "10px", maxHeight: "52vh", overflow: "auto" } });
  const chosen = new Map();
  const file = h("input", { type: "file", multiple: true, accept: kinds.includes("billede") && kinds.length === 1 ? "image/*" : "", hidden: true });
  const status = h("span", { class: "muted small" });
  const pickBtn = h("button", { class: "btn primary", type: "button", disabled: true }, "Vælg");
  const draw = (items) => {
    items = items.filter(m => kinds.includes(m.kind));
    grid.replaceChildren(...(items.length ? items.map(m => {
      const on = chosen.has(m.id);
      return h("div", { style: { border: `2px solid ${on ? "var(--brand)" : "var(--line)"}`, borderRadius: "8px", padding: "4px", cursor: "pointer", background: "var(--surface)" }, title: m.filename,
        onclick: () => { if (!multi) chosen.clear(); chosen.has(m.id) ? chosen.delete(m.id) : chosen.set(m.id, m); pickBtn.disabled = !chosen.size; draw(items); } },
        m.thumb ? h("img", { src: m.thumb, alt: m.alt || "", loading: "lazy", style: { width: "100%", aspectRatio: "4 / 3", objectFit: "cover", borderRadius: "4px" } })
          : h("div", { style: { aspectRatio: "4 / 3", display: "flex", alignItems: "center", justifyContent: "center", background: "var(--bg-2)", borderRadius: "4px", fontSize: "1.6rem" } }, m.kind === "lyd" ? "🔊" : "📎"),
        h("div", { class: "small", style: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, m.filename), h("div", { class: "muted small" }, sizeLabel(m.size)),
        h("button", { class: "btn sm ghost danger", type: "button", onclick: async (e) => { e.stopPropagation(); if (!confirm("Slet filen?")) return;
          try { await api(`/api/content/media/${m.id}`, { method: "DELETE" }); chosen.delete(m.id); load(); } catch (err) { toast(err.message, "err"); } } }, "Slet"));
    }) : [h("p", { class: "muted" }, "Ingen medier endnu – upload nogle.")]));
  };
  async function load() { draw(await listMedia(ownerKind, ownerId)); }
  file.addEventListener("change", async () => {
    const files = [...file.files]; file.value = "";
    for (const [i, f] of files.entries()) {
      status.textContent = `Uploader ${i + 1} af ${files.length} …`;
      try { const m = await uploadFile(f, { ownerKind, ownerId }); if (!multi) chosen.clear(); chosen.set(m.id, m); pickBtn.disabled = false; } catch (e) { toast(`${f.name}: ${e.message}`, "err"); }
    }
    status.textContent = ""; load();
  });
  const m = modal(title, h("div", { class: "stack" },
    h("div", { class: "row" }, h("button", { class: "btn", type: "button", onclick: () => file.click() }, "⬆ Upload …"), file, status),
    grid, h("div", { class: "row" }, pickBtn, h("button", { class: "btn", type: "button", onclick: () => m.close() }, "Annullér"))), { wide: true });
  pickBtn.onclick = () => { m.close(); onPick([...chosen.values()]); };
  load().catch(e => toast(e.message, "err"));
}
