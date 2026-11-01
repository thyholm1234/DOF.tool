// Kommentarer under et foto: arkivet fra netfugl.dk (kun til læsning), portalens egne (svar, ret, slet, anmeld), moderation og interne noter.
import { api, h, toast, modal, seg, state } from "../core.js";
import { live } from "../live.js";
import { banForm } from "../moderation.js";
import { dkDate, cleanups, on } from "./common.js";

const REASONS = [["chikane", "Chikane eller nedsættende"], ["spam", "Spam eller reklame"], ["forkert", "Forkert eller vildledende"], ["andet", "Andet"]];

export function commentsBox(pid, { open = true, canModerate = false, isMod = false, highlight = 0 } = {}) {
  const wrap = h("section", { class: "fot-comments", id: "kommentarer" });
  let data = null, replyTo = null;

  const compose = (parent = null, { intern = false } = {}) => {
    const ta = h("textarea", { rows: intern ? 3 : 4, maxlength: 5000, placeholder: intern ? "Intern note til moderatorerne …" : parent ? "Skriv et svar …" : "Skriv en kommentar …", "aria-label": "Kommentar" });
    const btn = h("button", { class: "btn primary", type: "submit" }, intern ? "Gem note" : parent ? "Svar" : "Send kommentar");
    return h("form", { class: "fot-compose stack", onsubmit: async (e) => {
      e.preventDefault();
      if (!ta.value.trim()) return;
      btn.disabled = true;
      try { await api(`/api/fotos/${pid}/kommentarer`, { method: "POST", body: { body: ta.value, parent_id: parent, intern } }); ta.value = ""; replyTo = null; await load(); }
      catch (err) { toast(err.message, "err"); }
      btn.disabled = false;
    } }, ta, h("div", { class: "row" }, btn, intern ? null : h("span", { class: "muted small" }, "Du kan bruge **fed**, _kursiv_ og [links](https://…).")));
  };
  const act = (label, fn) => h("button", { type: "button", onclick: fn }, label);
  function report(c) {
    let reason = "chikane";
    const note = h("input", { type: "text", maxlength: 500, placeholder: "Evt. en forklaring (valgfri)" });
    const m = modal("Anmeld kommentar", h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      try { await api(`/api/fotos/kommentarer/${c.id}/anmeld`, { method: "POST", body: { reason, note: note.value } }); m.close(); toast("Tak – en moderator ser på den"); await load(); }
      catch (err) { toast(err.message, "err"); }
    } }, h("p", { class: "muted" }, "Anmeldelser går til moderatorerne. Kommentaren skjules automatisk, hvis flere anmelder den."),
      seg(REASONS, reason, v => { reason = v; }), note, h("div", { class: "row" }, h("button", { class: "btn primary" }, "Send anmeldelse"))));
  }
  function edit(c) {
    const ta = h("textarea", { rows: 4, maxlength: 5000 }, "");
    ta.value = c.html.replace(/<[^>]+>/g, "").trim();
    const m = modal("Ret kommentar", h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      try { await api(`/api/fotos/kommentarer/${c.id}`, { method: "PATCH", body: { body: ta.value } }); m.close(); await load(); } catch (err) { toast(err.message, "err"); }
    } }, ta, h("div", { class: "row" }, h("button", { class: "btn primary" }, "Gem"))));
  }
  async function moderate(c, action) {
    let reason = "";
    if (action === "skjul" || action === "slet") {
      reason = prompt(action === "skjul" ? "Årsag til at skjule kommentaren (vises for forfatteren):" : "Årsag til at slette kommentaren:", "") ?? null;
      if (reason === null) return;
    }
    try { await api(`/api/admin/fotos/kommentarer/${c.id}/${action}`, { method: "POST", body: { reason } }); await load(); } catch (err) { toast(err.message, "err"); }
  }

  function item(c, reply = false) {
    const gone = c.status === "slettet", hidden = c.status === "skjult";
    const name = c.archive ? c.author.navn : h("span", {}, c.author.navn, c.author.obserkode ? h("small", { class: "muted" }, ` (${c.author.obserkode})`) : null);
    const txt = gone ? "Kommentaren er slettet." : hidden && !c.html ? "Kommentaren er skjult af en moderator." : null;
    return h("div", { class: `fot-comment ${reply ? "reply" : ""} ${hidden ? "hidden" : ""} ${gone ? "gone" : ""} ${c.id === highlight ? "hl" : ""}`, id: `k${c.id}` },
      h("div", {}, h("span", { class: "who" }, name), h("span", { class: "when" }, dkDate(c.created_at, { time: true }), c.edited_at ? " · redigeret" : "",
        hidden ? ` · skjult${c.hidden_reason ? ": " + c.hidden_reason : ""}` : "", canModerate && c.report_count ? ` · 🚩 ${c.report_count}` : "")),
      txt ? h("div", { class: "txt" }, txt) : h("div", { class: "txt", html: c.html }),
      !c.archive && !gone ? h("div", { class: "acts" },
        open && c.status === "synlig" && !reply && !c.intern ? act("Svar", () => { replyTo = replyTo === c.id ? null : c.id; draw(); }) : null,
        c.mine && c.status === "synlig" ? act("Ret", () => edit(c)) : null,
        c.mine ? act("Slet", async () => { if (confirm("Slet din kommentar?")) { try { await api(`/api/fotos/kommentarer/${c.id}`, { method: "DELETE" }); await load(); } catch (e) { toast(e.message, "err"); } } }) : null,
        !c.mine && c.status === "synlig" && !c.intern ? act(c.reported ? "Anmeldt" : "Anmeld", () => { if (!c.reported) report(c); }) : null,
        canModerate && c.status === "synlig" ? act("Skjul", () => moderate(c, "skjul")) : null,
        canModerate && hidden ? act("Gendan", () => moderate(c, "gendan")) : null,
        canModerate && c.report_count ? act("Afvis anmeldelser", () => moderate(c, "afvis")) : null,
        canModerate ? act("Slet (mod.)", () => moderate(c, "slet")) : null,
        canModerate && c.author.user_id && !c.mine ? act("Udeluk bruger", () => banForm({ user_id: c.author.user_id, navn: c.author.navn, obserkode: c.author.obserkode }, load)) : null) : null,
      replyTo === c.id ? compose(c.id) : null,
      ...(c.svar || []).map(r => item(r, true)));
  }

  function draw() {
    const count = (list) => list.reduce((s, c) => s + 1 + (c.svar?.length || 0), 0);
    const n = count(data.comments);
    wrap.replaceChildren(
      h("h3", {}, n ? `Kommentarer (${n})` : "Kommentarer"),
      data.archive.length ? h("details", { class: "fot-archive", open: data.archive.length <= 5 || !n },
        h("summary", {}, `Kommentarer fra netfugl.dk (${count(data.archive)})`), ...data.archive.map(c => item(c))) : null,
      ...data.comments.map(c => item(c)),
      !n && !data.archive.length ? h("p", { class: "muted" }, "Ingen kommentarer endnu.") : null,
      open && state.user ? h("div", { style: { marginTop: "16px" } }, compose(null)) : h("p", { class: "muted" }, "Kommentarer er lukket for dette foto."),
      isMod ? h("div", { class: "fot-intern" }, h("h4", {}, "Interne noter (kun moderatorer)"),
        ...(data.interne || []).map(c => item(c)), compose(null, { intern: true })) : null);
    if (highlight) wrap.querySelector(`#k${highlight}`)?.scrollIntoView({ block: "center" });
  }
  async function load() { data = await api(`/api/fotos/${pid}/kommentarer`); draw(); }

  load().catch(e => wrap.replaceChildren(h("p", { class: "muted" }, "Kommentarer kunne ikke hentes: " + e.message)));
  let timer;
  const off = live.on("comment", (d) => { if (d.thread === `foto:${pid}`) { clearTimeout(timer); timer = setTimeout(() => load().catch(() => {}), 500); } });
  cleanups.push(off);
  return wrap;
}
