// Moderatorens boks på fotosiden: godkend/afvis, afpublicér, lås, gruppér, ret, slet og bannere.
import { api, h, toast, modal } from "../core.js";
import { BASE } from "./common.js";

/** Afvis/afpublicér med standardgrunde + fritekst. onDone(grund) kaldes med den valgte tekst. */
export async function reasonDialog(title, onDone) {
  const cfg = await api("/api/admin/fotos/indstillinger").catch(() => ({ afvis_grunde: [] }));
  const picks = new Set();
  const free = h("textarea", { rows: 3, placeholder: "Egen begrundelse (valgfri)", style: { width: "100%" } });
  const m = modal(title, h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    const grund = [...picks, free.value.trim()].filter(Boolean).join(". ");
    try { await onDone(grund); m.close(); } catch (x) { toast(x.message, "err"); }
  } }, ...(cfg.afvis_grunde || []).map(g => h("label", { class: "check" }, h("input", { type: "checkbox", onchange: (e) => { e.target.checked ? picks.add(g) : picks.delete(g); } }), h("span", {}, g))),
    free, h("p", { class: "muted small" }, "Fotografen får en besked med begrundelsen."), h("div", { class: "row" }, h("button", { class: "btn primary" }, "Fortsæt"))));
}

export const setStatus = (id, status, grund) => api(`/api/admin/fotos/${id}/status`, { method: "PUT", body: { status, grund } });

export function adminBox(p, reload) {
  const act = (label, fn, cls = "btn sm") => h("button", { class: cls, type: "button", onclick: fn }, label);
  const run = async (fn, msg) => { try { await fn(); toast(msg); reload(); } catch (e) { toast(e.message, "err"); } };
  async function group() {
    const { ids } = await api(`/api/admin/fotos/${p.id}/gruppeforslag`);
    const extra = prompt(`Foto-id'er der skal grupperes med dette (komma-adskilt)${ids.length ? `. Forslag fra samme fotograf og fund: ${ids.join(", ")}` : ""}`, ids.join(", "));
    if (extra === null) return;
    const list = extra.split(/[,\s]+/).map(Number).filter(Boolean);
    if (!list.length) return;
    await run(() => api(`/api/fotos/${p.id}/gruppe`, { method: "POST", body: { ids: list } }), "Fotoerne er grupperet");
  }
  return h("section", { class: "fot-admin" }, h("h4", {}, "Administration"),
    h("div", { class: "row" },
      p.status !== "publiceret" ? act("Godkend", () => run(() => setStatus(p.id, "publiceret"), "Fotoet er publiceret"), "btn sm primary") : null,
      p.status === "publiceret" ? act("Afpublicér…", () => reasonDialog("Afpublicér foto", async (g) => { await setStatus(p.id, "afpubliceret", g); toast("Afpubliceret"); reload(); })) : null,
      p.status === "afventer" ? act("Afvis…", () => reasonDialog("Afvis foto", async (g) => { await setStatus(p.id, "afvist", g); toast("Afvist"); reload(); })) : null,
      act(p.laast ? "Oplås" : "Lås", () => run(() => api(`/api/admin/fotos/${p.id}/laas`, { method: "PUT", body: { locked: !p.laast } }), p.laast ? "Oplåst" : "Låst")),
      act("Gruppér…", group),
      p.gruppe.length > 1 ? act("Fjern fra gruppe", () => run(() => api(`/api/fotos/${p.id}/gruppe`, { method: "DELETE" }), "Fjernet fra gruppen")) : null,
      act("Ret", () => { location.hash = `${BASE}/${p.id}/ret`; }),
      p.kan.bannere ? act("Brug som banner…", async () => (await import("./bannere.js")).bannerDialog(p)) : null,
      act("Slet", async () => { if (confirm("Slet fotoet for altid?")) { try { await api(`/api/fotos/${p.id}`, { method: "DELETE" }); toast("Slettet"); location.hash = BASE; } catch (e) { toast(e.message, "err"); } } }, "btn sm danger")));
}
