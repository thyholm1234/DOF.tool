// Moderation → Kommentarer, beskeder og udelukkelser.
import { api, state, h, icon, fmtDate, ago, seg, params, setParams, toast, errorBox, empty, modal } from "../../core.js";
import { conversation } from "../support.js";
import { banForm } from "../../moderation.js";
import { refreshUnread } from "../../app.js";

export async function moderation(body) {
  let vis = params().vis || "kommentarer";
  const inner = h("div", {});
  const draw = () => { inner.replaceChildren(); setParams({ vis: vis === "kommentarer" ? "" : vis }); ({ kommentarer: modComments, beskeder: modMessages, udelukkede: modBans }[vis] || modComments)(inner); };
  body.append(h("div", { class: "row mb" }, seg([["kommentarer", "Kommentarer"], ["beskeder", `Beskeder${state.adminSupportUnread ? ` (${state.adminSupportUnread})` : ""}`], ["udelukkede", "Udelukkede"]],
    vis, (v) => { vis = v; setParams({ t: "" }); draw(); })), inner);
  draw();
}

async function modComments(body) {
  let status = "alle", q = "";
  const list = h("div", { class: "stack" });
  const search = h("input", { type: "search", placeholder: "Søg i tekst, navn eller art…", style: { maxWidth: "280px" } });
  let t;
  search.oninput = () => { clearTimeout(t); t = setTimeout(() => { q = search.value.trim(); load(); }, 300); };
  body.append(h("div", { class: "card stack" },
    h("div", { class: "row between" }, seg([["alle", "Alle"], ["synlige", "Synlige"], ["skjulte", "Skjulte"]], status, (v) => { status = v; load(); }), search),
    h("p", { class: "muted", style: { margin: 0, fontSize: ".85rem" } }, "Skjulte kommentarer ses ikke af andre brugere. Udelukkede brugere kan ikke skrive nye kommentarer. Alle handlinger logges under Aktivitet."),
    list));
  const load = async () => {
    try {
      const rows = await api(`/api/admin/comments?status=${status}&q=${encodeURIComponent(q)}`);
      list.replaceChildren(...(rows.length ? rows.map(x => h("div", { class: `mod-item ${x.hidden ? "hidden" : ""}` },
        h("div", { style: { minWidth: 0 } },
          h("div", { class: "row", style: { gap: "6px" } }, h("b", {}, x.navn || x.obserkode), h("small", { class: "muted" }, `${x.obserkode} · ${ago(x.created_at)}`),
            x.hidden ? h("span", { class: "tag SU" }, "skjult") : null, x.banned ? h("span", { class: "tag SUB" }, "udelukket") : null,
            x.likes ? h("small", { class: "muted" }, `👍 ${x.likes}`) : null),
          h("p", { style: { margin: "4px 0", whiteSpace: "pre-wrap" } }, x.body),
          h("small", { class: "muted" }, `${x.art || "?"} · ${x.lok || ""} · ${fmtDate(x.dato)} · `, h("a", { href: `#/traad/${x.dato}/${x.artnr}/${x.loknr}` }, "åbn tråden"))),
        h("div", { class: "mod-actions" },
          h("button", { class: `btn sm ${x.hidden ? "" : "danger"}`, onclick: async () => {
            await api(`/api/admin/comments/${x.id}`, { method: "PATCH", body: { hidden: !x.hidden } }); toast(x.hidden ? "Kommentaren vises igen" : "Kommentaren er skjult"); load();
          } }, x.hidden ? "Vis igen" : "Skjul"),
          x.user_id !== state.user.id ? h("button", { class: "btn sm", html: `${icon("chat")} Skriv`, onclick: () => messageForm(x) }) : null,
          x.user_id !== state.user.id && !x.staff ? (x.banned
            ? h("button", { class: "btn sm", onclick: async () => { await api(`/api/admin/bans/${x.user_id}`, { method: "DELETE" }); toast("Udelukkelsen er ophævet"); load(); } }, "Ophæv udelukkelse")
            : h("button", { class: "btn sm danger", onclick: () => banForm(x, load) }, "Udeluk")) : null))) : [empty("Ingen kommentarer")]));
    } catch (err) { list.replaceChildren(errorBox(err)); }
  };
  await load();
}

/** Skriv til en bruger – samtalen lander i brugerens 'Feedback og support', svar ses under Moderation → Beskeder. */
function messageForm(x) {
  const subject = h("input", { maxlength: 160, required: true, value: x.body ? `Din kommentar om ${x.art || "en observation"}` : "" });
  const text = h("textarea", { maxlength: 5000, required: true, placeholder: "Skriv venligt og konkret – hvad er problemet, og hvad ønsker vi?", style: { minHeight: "120px" } });
  const m = modal(`Skriv til ${x.navn || x.obserkode}`, h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    try {
      const r = await api(`/api/admin/users/${x.user_id}/message`, { method: "POST", body: { subject: subject.value, body: text.value, comment_id: x.body ? x.id : null } });
      m.close(); toast("Beskeden er sendt");
      location.hash = `#/moderation?tab=moderation&vis=beskeder&t=${r.thread}`;
    } catch (err) { toast(err.message, "err"); }
  } },
    x.body ? h("blockquote", { class: "quote" }, x.body) : null,
    h("label", { class: "field" }, h("span", {}, "Emne"), subject),
    h("label", { class: "field" }, h("span", {}, "Besked"), text),
    h("small", { class: "muted" }, "Brugeren får en notifikation og finder samtalen under Feedback og support. Alle moderatorer kan se og besvare den."),
    h("div", { class: "row" }, h("button", { class: "btn primary", html: `${icon("chat")} Send` }))));
}

async function modMessages(body) {
  let status = "open";
  const list = h("div", { class: "stack" });
  const detail = h("div", {});
  body.append(h("div", { class: "row mb" }, seg([["open", "Åbne"], ["closed", "Lukkede"], ["all", "Alle"]], status, (v) => { status = v; load(); })),
    h("p", { class: "muted", style: { fontSize: ".85rem", marginTop: 0 } }, "Samtaler som moderatorer har startet med brugere. Start en ny fra en kommentar (Skriv) eller under Udelukkede."),
    h("div", { class: "report-layout" }, list, detail));
  const load = async () => {
    try {
      const rows = await api(`/api/admin/support?status=${status}&kind=moderation`);
      list.replaceChildren(...(rows.length ? rows.map(r => h("button", { type: "button", class: "loc-item", onclick: () => open(r.id) },
        h("span", {}, r.unread ? h("span", { class: "badge-inline", style: { marginLeft: 0, marginRight: "6px" } }, "nyt") : null, h("b", {}, r.subject),
          h("small", { class: "muted" }, ` · ${r.navn} · ${r.n} besked${r.n === 1 ? "" : "er"}`)),
        h("small", { class: "muted" }, ago(r.updated_at)))) : [empty("Ingen samtaler")]));
    } catch (err) { list.replaceChildren(errorBox(err)); }
  };
  const open = async (id) => {
    setParams({ t: id });
    try { detail.replaceChildren(conversation(await api(`/api/support/${id}`), () => { load(); open(id); }, { admin: true })); load(); refreshUnread(); }
    catch (err) { detail.replaceChildren(errorBox(err)); }
  };
  await load();
  if (params().t) open(params().t);
}

async function modBans(body) {
  const list = h("div", { class: "stack" });
  body.append(h("div", { class: "card stack" },
    h("p", { class: "muted", style: { margin: 0, fontSize: ".85rem" } }, "Brugere der er udelukket fra at kommentere. Udløbne udelukkelser bliver stående som historik."), list));
  const load = async () => {
    try {
      const rows = await api("/api/admin/bans");
      list.replaceChildren(...(rows.length ? rows.map(b => h("div", { class: `mod-item ${b.active ? "" : "hidden"}` },
        h("div", { style: { minWidth: 0 } },
          h("div", { class: "row", style: { gap: "6px" } }, h("b", {}, b.navn || b.obserkode), h("small", { class: "muted" }, b.obserkode),
            b.active ? h("span", { class: "tag SUB" }, b.until ? `til ${fmtDate(b.until)}` : "indtil videre") : h("span", { class: "tag plain" }, "udløbet"),
            b.hide_comments ? h("small", { class: "muted" }, "kommentarer skjult") : null),
          h("p", { style: { margin: "4px 0" } }, b.reason),
          b.evidence ? h("blockquote", { class: "quote" }, b.evidence) : null,
          h("small", { class: "muted" }, `Af ${b.by_navn || "?"} · ${ago(b.created_at)} · ${b.n_comments} kommentar${b.n_comments === 1 ? "" : "er"} i alt`)),
        h("div", { class: "mod-actions" },
          h("button", { class: "btn sm", html: `${icon("chat")} Skriv`, onclick: () => messageForm({ user_id: b.user_id, navn: b.navn, obserkode: b.obserkode }) }),
          h("button", { class: "btn sm", onclick: async () => {
            if (!confirm(b.active ? "Ophæv udelukkelsen?" : "Fjern fra historikken?")) return;
            await api(`/api/admin/bans/${b.user_id}`, { method: "DELETE" }); toast(b.active ? "Udelukkelsen er ophævet" : "Fjernet"); load();
          } }, b.active ? "Ophæv" : "Fjern")))) : [empty("Ingen udelukkede brugere")]));
    } catch (err) { list.replaceChildren(errorBox(err)); }
  };
  await load();
}

// ---------------------------------------------------------------- portalens egne ture (moderator og op)
