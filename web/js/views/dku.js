import { api, state, h, icon, fmtDate, ago, toast, errorBox, empty, modal, seg, params, setParams, requireLogin } from "../core.js";
import { refreshUser } from "../app.js";

// DKU: tip om tvivlsomme observationer og sagsbehandling (se server/portal/services/dku.py)

const REASONS = [["dokumentation", "Mangler dokumentation"], ["usandsynlig", "Usandsynlig – mulig fejlobservation"],
  ["fejl", "Sandsynligvis fejl i indtastningen"], ["andet", "Andet"]];
const OUTCOMES = [["godkendt", "Godkendt – observationen står"], ["rettet", "Rettet af observatøren"],
  ["afvist", "Afvist – fejlobservation"], ["henlagt", "Henlagt – ikke grundlag for at gå videre"]];
const STATUS_TAG = { ny: "SU", behandles: "SUB", afventer: "bemaerk", afgjort: "plain" };

/** Er modulet synligt for den indloggede? */
export const dkuVisible = () => !!state.user?.dku?.visible;

const isDku = () => !!(state.user?.dku?.member_of?.length || state.user?.dku?.admin);

/** Dialog: tip DKU om en observation – eller, for DKU-ansvarlige, åbn en sag direkte.
 *  source = "dofbasen" (obsid) eller "portal" (feltmelding). */
export function tipDku(source, id, label) {
  const dku = isDku();
  const reason = h("select", {}, ...REASONS.map(([v, t]) => h("option", { value: v }, t)));
  const body = h("textarea", { maxlength: 4000, required: true, minlength: 10, style: { minHeight: "110px" },
    placeholder: dku ? "Hvad er DKU i tvivl om? (ses kun af DKU)" : "Hvad er du i tvivl om? Fx manglende beskrivelse, usandsynlig dato, antal eller lokalitet …" });
  const toObserver = h("textarea", { maxlength: 4000, style: { minHeight: "90px" }, placeholder: "Fx: Kan du beskrive fuglen nærmere og fortælle, hvordan den blev bestemt?" });
  const { close } = modal(dku ? "DKU-sag" : "Tip DKU", h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/dku/cases", { method: "POST", body: { source, id, reason: reason.value, body: body.value, observer_note: dku ? toObserver.value : "" } });
      close();
      toast(r.origin === "dku" ? (r.joined ? "Der var allerede en sag – din note er tilføjet" : "DKU-sagen er oprettet")
        : r.joined ? "Tilføjet til en sag, DKU allerede har om observationen" : "Tak – DKU har fået dit tip");
      location.hash = `#/dku/${r.id}`;
    } catch (err) { toast(err.message, "err"); }
  } },
    h("p", { class: "muted", style: { margin: 0 } }, label),
    h("label", { class: "field" }, h("span", {}, "Hvorfor"), reason),
    h("label", { class: "field" }, h("span", {}, dku ? "Intern note (kun DKU)" : "Din begrundelse til DKU"), body),
    dku ? h("label", { class: "field" }, h("span", {}, "Skriv til observatøren nu (valgfrit)"), toObserver) : null,
    h("p", { class: "muted", style: { fontSize: ".85rem", margin: 0 } }, dku
      ? "Er du DKU for observationens lokalafdeling, åbnes sagen direkte under behandling – ellers sendes den som tip til de ansvarlige. Observatøren kan aldrig se, om en sag startede med et tip."
      : "Dit tip går kun til DKU. Observatøren får aldrig at vide, at der er tippet – eller af hvem."),
    h("div", { class: "row" }, h("button", { class: "btn primary" }, dku ? "Opret sag" : "Send til DKU"))));
}

/** Knap til observationskort; null hvis modulet er slået fra eller det er brugerens egen observation. */
export function tipButton(source, id, { own = false, label = "" } = {}) {
  if (!dkuVisible() || own) return null;
  const dku = isDku();
  return h("button", { class: "btn sm ghost", type: "button", title: dku ? "Åbn en DKU-sag om observationen" : "Tip DKU om en tvivlsom observation",
    onclick: () => tipDku(source, id, label), html: `${icon("flag")} ${dku ? "DKU-sag" : "Tip DKU"}` });
}

export async function render(view, [sub]) {
  if (requireLogin(view)) return;
  if (!dkuVisible()) { view.append(errorBox(new Error("DKU-modulet er ikke slået til"))); return; }
  if (sub && /^\d+$/.test(sub)) return caseView(view, +sub);
  const d = state.user.dku;
  const tabs = [["mine", "Mine tip"], ["observer", "Mine observationer"], ...(d.member_of.length || d.admin ? [["dku", "DKU-sager"]] : []),
    ...(d.member_of.length || d.admin ? [["ansvarlige", "Ansvarlige"]] : [])];
  let tab = params().tab || (d.member_of.length ? "dku" : "mine");
  if (!tabs.some(t => t[0] === tab)) tab = tabs[0][0];
  let st = params().st || "open";
  const body = h("div", { class: "mt" });
  view.append(h("div", { class: "page-head" }, h("h1", {}, "DKU"), h("span", { class: "spacer" }),
    seg(tabs, tab, (v) => { tab = v; setParams({ tab: v }); draw(); })), body);
  const draw = async () => {
    body.replaceChildren();
    if (tab === "ansvarlige") return membersPanel(body);
    const filter = seg([["open", "Åbne"], ["closed", "Afgjorte"], ["all", "Alle"]], st, (v) => { st = v; setParams({ tab, st: v }); draw(); });
    body.append(h("div", { class: "row between" }, h("p", { class: "muted", style: { margin: 0 } }, {
      mine: "Observationer, du har tippet DKU om.",
      observer: "Dine observationer, som DKU har spørgsmål til.",
      dku: d.admin && !d.member_of.length ? "Alle sager (du administrerer DKU)." : "Sager fra de lokalafdelinger, du er DKU-ansvarlig for.",
    }[tab]), filter));
    const list = h("div", { class: "stack mt" }, h("div", { class: "skeleton", style: { height: "120px" } }));
    body.append(list);
    let rows;
    try { rows = await api(`/api/dku/cases?view=${tab}&state=${st}`); } catch (err) { list.replaceChildren(errorBox(err)); return; }
    if (!rows.length) {
      list.replaceChildren(empty(st === "open" ? "Ingen åbne sager" : "Ingen sager",
        tab === "mine" ? "Tip DKU fra en observation i en tråd (knappen «Tip DKU»)." : ""));
      return;
    }
    list.replaceChildren(...rows.map(r => h("a", { class: "card", href: `#/dku/${r.id}`, style: { display: "block", color: "inherit" } },
      h("div", { class: "row between" },
        h("div", {}, h("b", {}, `${r.antal} ${r.art}`), h("span", { class: "muted" }, ` · ${r.lok || "–"} · ${fmtDate(r.dato)}`),
          r.unread ? h("span", { class: "badge-inline", style: { marginLeft: "6px" } }, "nyt") : null),
        h("span", { class: `tag ${STATUS_TAG[r.status]}` }, r.outcome_label || r.status_label)),
      h("div", { class: "muted", style: { fontSize: ".86rem" } },
        [r.reason, r.observer ? `observatør: ${r.observer}` : null, (r.afdelinger || []).join(", "),
         r.origin === "dku" ? "oprettet af DKU" : r.n_tippers > 1 ? `${r.n_tippers} tip` : null,
         r.assignee ? `hos ${r.assignee}` : null, `opdateret ${ago(r.updated_at)}`]
          .filter(Boolean).join(" · ")))));
  };
  draw();
}

// ---------------------------------------------------------------- én sag

async function caseView(view, id) {
  view.replaceChildren(h("div", { class: "skeleton", style: { height: "240px" } }));
  let c;
  try { c = await api(`/api/dku/cases/${id}`); } catch (err) { view.replaceChildren(errorBox(err), h("p", {}, h("a", { href: "#/dku" }, "← DKU"))); return; }
  refreshUser().catch(() => {});  // ulæst-tælleren i menuen
  const reload = () => caseView(view, id);
  const roleText = { dku: c.origin === "dku" ? "Sagen er oprettet af DKU" : "Sagen startede med et tip", tipper: "Du har tippet DKU",
    observer: "DKU har et spørgsmål til din observation" }[c.role];
  const changed = new Set(c.changes.map(x => x.field));
  const fieldTable = (rows) => h("table", { class: "data dku-fields" }, h("tbody", {}, ...rows.map(f => h("tr", { class: changed.has(f.key) ? "changed" : "" },
    h("th", {}, f.label), h("td", { style: { whiteSpace: "pre-wrap" } }, f.value)))));

  const obsCard = h("div", { class: "card stack" },
    h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, c.source === "dofbasen" ? "Observation i DOFbasen" : "Feltmelding i DOF.tool"),
      h("span", { class: "row", style: { gap: "6px" } },
        c.links.thread ? h("a", { class: "btn sm ghost", href: c.links.thread }, "Tråden") : null,
        c.links.dofbasen ? h("a", { class: "btn sm ghost", href: c.links.dofbasen, target: "_blank", rel: "noopener", html: `${icon("external")} DOFbasen` }) : null)),
    h("div", { class: "muted", style: { fontSize: ".88rem" } },
      `Observatør: ${c.observer.name || c.observer.obserkode}${c.observer.obserkode ? ` (${c.observer.obserkode})` : ""}`,
      c.role === "dku" && !c.observer.in_portal ? " · bruger ikke DOF.tool – kontakt via DOFbasen" : ""),
    c.deleted ? h("div", { class: "notice warn" }, "Observationen findes ikke længere (slettet eller rettet væk). Data herunder er fra, da tippet blev sendt.") : null,
    c.changes.length ? h("div", { class: "notice" }, h("b", {}, "Rettet siden tippet: "),
      ...c.changes.flatMap((x, i) => [i ? "; " : "", `${x.label}: `, h("s", { class: "muted" }, x.before || "–"), " → ", h("b", {}, x.after || "–")])) : null,
    fieldTable(c.current || c.fields),
    c.media.length ? h("div", { class: "report-media" }, ...c.media.map(m => m.kind === "audio"
      ? h("audio", { controls: true, src: m.url, preload: "none" })
      : h("a", { href: m.url, target: "_blank", rel: "noopener" }, h("img", { src: m.url, loading: "lazy", alt: "Foto" })))) : null,
    c.links.edit ? h("div", { class: "row" }, h("a", { class: "btn primary", href: c.links.edit, html: `${icon("edit")} Ret observationen` }),
      h("small", { class: "muted" }, "Dine rettelser vises automatisk i sagen.")) : null,
    c.links.edit_dofbasen ? h("div", { class: "row" }, h("a", { class: "btn primary", href: c.links.edit_dofbasen, target: "_blank", rel: "noopener", html: `${icon("external")} Ret i DOFbasen` }),
      h("small", { class: "muted" }, "Observationen rettes i DOFbasen; ændringen ses her, når DOF.tool har hentet den.")) : null);

  const msgs = h("div", { class: "support-msgs" }, ...c.messages.map(m => h("div", {
    class: `support-msg ${m.role === "system" ? "system" : m.mine ? "admin" : "user"} ${m.visibility === "dku" && c.role === "dku" ? "internal" : ""}` },
    m.role === "system" ? null : h("div", { class: "row between" }, h("b", {}, m.author),
      h("small", { class: "muted" }, `${m.channel ? `${m.channel} · ` : ""}${ago(m.created_at)}`)),
    h("p", { style: { margin: m.role === "system" ? 0 : "4px 0 0", whiteSpace: "pre-wrap" } }, m.body),
    m.role === "system" ? h("small", { class: "muted" }, ago(m.created_at)) : null)));

  // Kanaler: DKU vælger (observatøren, tipperen eller intern note); tipper og observatør skriver altid kun til DKU
  const CH = { observer: "Til observatøren", tipper: "Til tipperen", dku: "Intern note (kun DKU)" };
  const text = h("textarea", { maxlength: 4000, placeholder: c.role === "dku" ? "Skriv til observatøren, tipperen eller en intern note…" : "Skriv til DKU…", style: { minHeight: "80px" } });
  const ch = h("select", {}, ...c.channels.map(k => h("option", { value: k }, CH[k])));
  const composer = c.channels.length ? h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    if (!text.value.trim()) return;
    try { await api(`/api/dku/cases/${id}/messages`, { method: "POST", body: { body: text.value, channel: ch.value } }); reload(); }
    catch (err) { toast(err.message, "err"); }
  } }, text, h("div", { class: "row" }, c.channels.length > 1 ? ch : h("small", { class: "muted" }, "Til DKU"), h("button", { class: "btn primary" }, "Send")))
    : h("p", { class: "muted" }, "Sagen er afgjort.");

  view.replaceChildren(
    h("div", { class: "page-head" }, h("div", {}, h("a", { href: "#/dku", class: "muted" }, "← DKU"),
      h("h1", { style: { margin: "2px 0 0" } }, `Sag #${c.id}: ${(c.current || c.fields).find(f => f.key === "art")?.value || ""}`)),
      h("span", { class: "spacer" }), h("span", { class: `tag ${STATUS_TAG[c.status]}` }, c.outcome_label || c.status_label)),
    h("p", { class: "muted" }, [roleText, c.reason_label, (c.afdelinger || []).join(", "), c.assignee ? `behandles af ${c.assignee}` : null,
      `oprettet ${fmtDate(c.created_at)}`].filter(Boolean).join(" · ")),
    c.outcome ? h("div", { class: "notice" }, h("b", {}, c.outcome_label), c.outcome_note ? h("p", { style: { margin: "4px 0 0", whiteSpace: "pre-wrap" } }, c.outcome_note) : null) : null,
    h("div", { class: "grid-2 mt" }, obsCard,
      h("div", { class: "stack" }, c.can.manage ? actionsCard(c, reload) : null,
        h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "Beskeder"), msgs, composer))));
  msgs.scrollTop = msgs.scrollHeight;
}

function actionsCard(c, reload) {
  const run = async (action, extra = {}) => {
    try { await api(`/api/dku/cases/${c.id}/actions`, { method: "POST", body: { action, ...extra } }); toast("Gemt"); reload(); }
    catch (err) { toast(err.message, "err"); }
  };
  const involve = () => {
    const note = h("textarea", { required: true, minlength: 10, maxlength: 4000, style: { minHeight: "120px" }, placeholder: "Hvad er DKU i tvivl om, og hvilken dokumentation mangler der?" });
    const { close } = modal("Inddrag observatøren", h("form", { class: "stack", onsubmit: (e) => { e.preventDefault(); close(); run("involve", { note: note.value }); } },
      h("p", { class: "muted", style: { margin: 0 } }, "Observatøren får besked og kan se sagen, skrive og rette observationen. Tipperen forbliver anonym."),
      note, h("div", { class: "row" }, h("button", { class: "btn primary" }, "Send til observatøren"))));
  };
  const decide = () => {
    const outcome = h("select", {}, ...OUTCOMES.map(([v, t]) => h("option", { value: v }, t)));
    const note = h("textarea", { maxlength: 4000, style: { minHeight: "100px" }, placeholder: "Begrundelse (ses af tipper og observatør)" });
    const { close } = modal("Afgør sagen", h("form", { class: "stack", onsubmit: (e) => { e.preventDefault(); close(); run("close", { outcome: outcome.value, note: note.value }); } },
      h("label", { class: "field" }, h("span", {}, "Afgørelse"), outcome), note,
      c.source === "dofbasen" ? h("small", { class: "muted" }, "Godkendt og rettet markerer observationen som behandlet af DKU i DOFbasen-modulet.") : null,
      h("div", { class: "row" }, h("button", { class: "btn primary" }, "Afgør"))));
  };
  const closed = c.status === "afgjort";
  return h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "Behandling"),
    c.tippers.length ? h("small", { class: "muted" }, `Tippet af: ${c.tippers.join(", ")} (kun synligt for DKU)`) : null,
    h("div", { class: "row" },
      !closed && c.status === "ny" ? h("button", { class: "btn", onclick: () => run("take") }, "Tag sagen") : null,
      !closed && c.observer.in_portal && !c.observer_involved ? h("button", { class: "btn", onclick: involve }, "Inddrag observatøren") : null,
      !closed ? h("button", { class: "btn primary", onclick: decide }, "Afgør") : h("button", { class: "btn", onclick: () => run("reopen") }, "Genåbn")),
    !c.observer.in_portal && !closed ? h("small", { class: "muted" }, "Observatøren bruger ikke DOF.tool. Kontakt vedkommende via DOFbasen og noter svaret her som intern note.") : null);
}

// ---------------------------------------------------------------- DKU-ansvarlige

async function membersPanel(body) {
  let d;
  try { d = await api("/api/dku/members"); } catch (err) { body.append(errorBox(err)); return; }
  const redraw = () => { body.replaceChildren(); membersPanel(body); };
  body.append(h("p", { class: "muted" }, d.can_edit
    ? "Vælg DKU-ansvarlige for hver lokalafdeling. De får besked om nye tip fra afdelingen. Landsdækkende ansvarlige ser alle sager; er der ingen ansvarlige for en afdeling, går tippet til dem, der administrerer DKU."
    : "DKU-ansvarlige for hver lokalafdeling. Administratorer med modulet DKU kan ændre dem."),
    h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, h("th", {}, "Lokalafdeling"), h("th", {}, "DKU-ansvarlige"), d.can_edit ? h("th", {}, "Tilføj") : null)),
      h("tbody", {}, ...d.afdelinger.map(a => {
        const kode = h("input", { placeholder: "Obserkode", maxlength: 16, style: { width: "110px" } });
        return h("tr", {}, h("td", {}, h("b", {}, a.navn)),
          h("td", {}, a.members.length ? h("div", { class: "row", style: { gap: "6px" } }, ...a.members.map(m => h("span", { class: "tag plain" }, `${m.navn} (${m.obserkode})`,
            d.can_edit ? h("button", { class: "btn sm ghost", type: "button", title: "Fjern", style: { padding: "0 4px" }, html: icon("x"), onclick: async () => {
              if (!confirm(`Fjern ${m.navn} som DKU-ansvarlig for ${a.navn}?`)) return;
              try { await api(`/api/dku/members/${a.id}/${m.user_id}`, { method: "DELETE" }); redraw(); } catch (err) { toast(err.message, "err"); }
            } }) : null))) : h("span", { class: "muted" }, "ingen")),
          d.can_edit ? h("td", {}, h("form", { class: "row", style: { gap: "4px", flexWrap: "nowrap" }, onsubmit: async (e) => {
            e.preventDefault();
            try { await api("/api/dku/members", { method: "POST", body: { afdeling_id: a.id, obserkode: kode.value } }); redraw(); }
            catch (err) { toast(err.message, "err"); }
          } }, kode, h("button", { class: "btn sm" }, "Tilføj"))) : null);
      })))));
}
