// Feedback og support: beskeder til administratorerne, med svar i samme samtale.
import { api, state, h, icon, ago, fmtDate, params, setParams, requireLogin, toast, empty, errorBox, seg } from "../core.js";
import { refreshUnread } from "../app.js";

export const KINDS = { feedback: "Feedback", support: "Hjælp", fejl: "Fejl", ide: "Idé", moderation: "Moderation" };
const NEW_KINDS = Object.entries(KINDS).filter(([k]) => k !== "moderation");

export async function render(view) {
  if (requireLogin(view)) return;
  const list = h("div", { class: "stack" });
  const detail = h("div", {});
  view.append(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Feedback og support"),
      h("p", { class: "lead" }, "Skriv til portalens administratorer – ros, fejl, ønsker eller spørgsmål. Du får svar her og som notifikation. Beskeder fra moderatorerne lander også her."))),
    h("div", { class: "report-layout" }, h("div", { class: "stack" }, newForm(() => load()), list), detail));

  const load = async () => {
    try {
      const rows = await api("/api/support");
      list.replaceChildren(h("h2", { style: { margin: "8px 0 0" } }, "Mine samtaler"),
        ...(rows.length ? rows.map(r => h("button", { type: "button", class: `loc-item ${String(r.id) === params().t ? "active" : ""}`, onclick: () => open(r.id) },
          h("span", {}, r.unread ? h("span", { class: "badge-inline", style: { marginLeft: 0, marginRight: "6px" } }, "nyt") : null,
            h("b", {}, r.subject), h("small", { class: "muted" }, ` · ${KINDS[r.kind] || r.kind}${r.status === "closed" ? " · lukket" : ""}`)),
          h("small", { class: "muted" }, ago(r.updated_at))))
          : [empty("Ingen samtaler endnu")]));
    } catch (err) { list.replaceChildren(errorBox(err)); }
  };
  const open = async (id) => {
    setParams({ t: id });
    detail.replaceChildren(h("div", { class: "skeleton", style: { height: "200px" } }));
    try { detail.replaceChildren(conversation(await api(`/api/support/${id}`), () => { load(); open(id); })); refreshUnread(); load(); }
    catch (err) { detail.replaceChildren(errorBox(err)); }
  };
  await load();
  if (params().t) open(params().t);
}

function context() {
  return { path: location.hash, version: document.querySelector('script[type=module]')?.src?.split("/")[4] || "", screen: `${innerWidth}×${innerHeight}` };
}

function newForm(done) {
  let kind = "feedback";
  const subject = h("input", { maxlength: 160, placeholder: "Kort overskrift", required: true });
  const body = h("textarea", { maxlength: 5000, placeholder: "Beskriv gerne hvad du gjorde, og hvad du forventede…", required: true, style: { minHeight: "110px" } });
  return h("form", { class: "card stack", onsubmit: async (e) => {
    e.preventDefault();
    try {
      const t = await api("/api/support", { method: "POST", body: { subject: subject.value, kind, body: body.value, context: context() } });
      subject.value = ""; body.value = "";
      toast("Tak – beskeden er sendt til administratorerne");
      setParams({ t: t.id }); done();
    } catch (err) { toast(err.message, "err"); }
  } },
    h("h2", { style: { margin: 0 } }, "Ny besked"),
    seg(NEW_KINDS, kind, (v) => { kind = v; }),
    h("label", { class: "field" }, h("span", {}, "Emne"), subject),
    h("label", { class: "field" }, h("span", {}, "Besked"), body),
    h("small", { class: "muted" }, "Siden du er på, appversion og browser sendes med, så vi lettere kan finde fejl."),
    h("div", { class: "row" }, h("button", { class: "btn primary", html: `${icon("chat")} Send` })));
}

/** Samtalevisning – bruges også i admin-panelet. */
export function conversation(t, onChange, { admin = false } = {}) {
  const reply = h("textarea", { maxlength: 5000, placeholder: admin ? "Svar brugeren…" : "Skriv et svar…", style: { minHeight: "80px" } });
  return h("div", { class: "card stack" },
    h("div", { class: "row between" }, h("div", {}, h("h2", { style: { margin: 0 } }, t.subject),
      h("small", { class: "muted" }, `${KINDS[t.kind] || t.kind} · startet ${fmtDate(t.created_at)}${admin ? ` af ${t.user.navn || t.user.obserkode} (${t.user.obserkode})` : ""}`)),
      h("button", { class: "btn sm", onclick: async () => {
        try { await api(`/api/support/${t.id}`, { method: "PATCH", body: { status: t.status === "closed" ? "open" : "closed" } }); onChange(); }
        catch (err) { toast(err.message, "err"); }
      } }, t.status === "closed" ? "Genåbn" : "Luk samtalen")),
    h("div", { class: "support-msgs" }, ...t.messages.map(m => h("div", { class: `support-msg ${m.from_admin ? "admin" : "user"}` },
      h("div", { class: "row between" }, h("b", {}, m.from_admin ? `${m.navn || "Admin"} (${t.kind === "moderation" ? "moderator" : "admin"})` : (m.navn || "Bruger")), h("small", { class: "muted" }, ago(m.created_at))),
      m.context?.comment ? h("blockquote", { class: "quote" }, h("small", { class: "muted" }, "Om din kommentar"), h("div", {}, m.context.comment),
        m.context.path ? h("a", { href: m.context.path }, "åbn tråden") : null) : null,
      h("p", { style: { margin: "4px 0 0", whiteSpace: "pre-wrap" } }, m.body),
      admin && m.context && !m.context.comment ? h("small", { class: "muted", style: { display: "block", marginTop: "4px", wordBreak: "break-all" } },
        [m.context.path, m.context.version && `v ${m.context.version}`, m.context.screen, m.context.browser].filter(Boolean).join(" · ")) : null))),
    h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      if (!reply.value.trim()) return;
      try { await api(`/api/support/${t.id}/messages`, { method: "POST", body: { body: reply.value } }); reply.value = ""; onChange(); }
      catch (err) { toast(err.message, "err"); }
    } }, reply, h("div", { class: "row" }, h("button", { class: "btn primary" }, "Send svar"))));
}
