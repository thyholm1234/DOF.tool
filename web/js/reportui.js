// Fælles visning af feltmeldinger (egne indtastninger): status, sikkerhed, medier og statusknapper.
import { api, state, h, icon, fmtN, fmtTime, ago, tag, toast, KAT } from "./core.js";

export const STATUS = { til_stede: "Under observation", "fløjet": "Fløjet/forsvundet", ikke_genfundet: "Ikke genfundet", ukendt: "Ikke angivet" };
export const SIKKERHED = { sikker: "Sikker", sandsynlig: "Sandsynlig", mulig: "Mulig" };
export const GRUNDLAG = { set: "Set", hoert: "Hørt", foto: "Foto", lyd: "Lydoptagelse", haand: "I hånden/ringmærket", beskrevet: "Detaljeret beskrevet" };

export function statusBadge(status, live, at) {
  if (status === "til_stede") {
    return h("span", { class: `status-badge ${live ? "live" : "stale"}`, title: at ? `Senest bekræftet ${fmtTime(at)}` : "" },
      h("i"), live ? "Under observation" : `Sidst set ${at ? fmtTime(at) : ""}`);
  }
  if (status === "fløjet" || status === "ikke_genfundet") return h("span", { class: "status-badge gone" }, STATUS[status]);
  return null;
}

export const certaintyTag = (s) => s && s !== "sikker" ? h("span", { class: `tag certainty ${s}`, title: "Bestemmelsessikkerhed" }, SIKKERHED[s]) : null;

/** Kort for én feltmelding. onChange(report) kaldes efter statusændring. */
export function reportCard(r, { onChange, onEdit, showObserver = true } = {}) {
  const tid = [r.obstidfra || r.turtidfra, r.obstidtil].filter(Boolean).join("–");
  const details = [r.adf, r.koen === "M" ? "han" : r.koen === "F" ? "hun" : null, r.alderkode, r.dragt, r.metode && r.metode !== "Uspecificeret/tilfældig" ? r.metode : null].filter(Boolean);
  const canStatus = state.user && !(r.hemmelig && !r.own);
  const setStatus = async (status) => {
    const note = status === "til_stede" ? "" : (prompt(`${STATUS[status]} – evt. kommentar (fx retning, tidspunkt):`) ?? null);
    if (note === null) return;
    try { const upd = await api(`/api/reports/${r.id}/status`, { method: "POST", body: { status, note: note || null } }); toast(`Status: ${STATUS[status]}`); onChange?.(upd); }
    catch (err) { toast(err.message, "err"); }
  };
  return h("div", { class: `report ${r.kategori}` },
    h("div", { class: "row between", style: { alignItems: "flex-start" } },
      h("div", { style: { minWidth: 0 } },
        h("div", { class: "row", style: { gap: "6px" } }, h("b", { class: "num" }, fmtN(r.antal)), h("b", {}, r.art),
          r.kategori !== "alm" ? tag(r.kategori, KAT[r.kategori]) : null, certaintyTag(r.sikkerhed),
          r.hemmelig ? h("span", { class: "tag plain", html: `${icon("lock")} Hemmelig` }) : null),
        h("div", { class: "muted", style: { fontSize: ".86rem" } },
          [details.join(", "), showObserver ? r.observer : null, tid].filter(Boolean).join(" · "))),
      h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, statusBadge(r.status, r.live, r.status_at),
        onEdit ? h("button", { class: "btn sm danger solid", onclick: onEdit, html: `${icon("edit")} Redigér` }) : null)),
    r.grundlag?.length ? h("div", { class: "row", style: { gap: "4px", marginTop: "6px" } },
      h("small", { class: "muted" }, "Grundlag:"), ...r.grundlag.map(g => h("span", { class: "tag plain" }, GRUNDLAG[g] || g))) : null,
    r.fuglnoter ? h("p", { style: { margin: "6px 0 0", whiteSpace: "pre-wrap" } }, r.fuglnoter) : null,
    r.media?.length ? h("div", { class: "report-media" }, ...r.media.map(m => m.kind === "audio"
      ? h("audio", { controls: true, src: m.url, preload: "none" })
      : h("a", { href: m.url, target: "_blank", rel: "noopener" }, h("img", { src: m.url, loading: "lazy", alt: `Foto af ${r.art}` })))) : null,
    r.updates?.length > 1 || (r.updates?.length === 1 && r.updates[0].note) ? h("ul", { class: "timeline" },
      ...r.updates.map(u => h("li", {}, h("b", {}, STATUS[u.status] || u.status), ` · ${u.navn} · ${fmtTime(u.at)}`, u.note ? h("div", { class: "muted" }, u.note) : null))) : null,
    r.i_dofbasen ? h("small", { class: "muted" }, "✓ Også indtastet i DOFbasen") : null,
    canStatus ? h("div", { class: "row", style: { gap: "6px", marginTop: "8px", flexWrap: "wrap" } },
      h("button", { class: "btn sm", onclick: () => setStatus("til_stede"), html: `${icon("bino")} Stadig til stede` }),
      h("button", { class: "btn sm", onclick: () => setStatus("fløjet") }, "Fløjet"),
      h("button", { class: "btn sm", onclick: () => setStatus("ikke_genfundet") }, "Ikke genfundet")) : null,
    h("small", { class: "muted", style: { display: "block", marginTop: "4px" } }, `Indtastet ${ago(r.created_at)}`));
}
