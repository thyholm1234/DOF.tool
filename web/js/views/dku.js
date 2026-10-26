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
  const tabs = [["observer", "Mine observationer"], ...(d.member_of.length || d.admin ? [["dku", "DKU-sager"]] : []),
    ...(d.member_of.length || d.admin ? [["kandidater", "Kandidater"], ["ansvarlige", "Ansvarlige"]] : [])];
  let tab = params().tab || (d.member_of.length ? "dku" : "observer");
  if (!tabs.some(t => t[0] === tab)) tab = tabs[0][0];
  let st = params().st || "open";
  const body = h("div", { class: "mt" });
  view.append(h("div", { class: "page-head" }, h("h1", {}, "DKU"), h("span", { class: "spacer" }),
    seg(tabs, tab, (v) => { tab = v; setParams({ tab: v }); draw(); })), body);
  const draw = async () => {
    body.replaceChildren();
    if (tab === "ansvarlige") return membersPanel(body);
    if (tab === "kandidater") return candidatesPanel(body);
    const filter = seg([["open", "Åbne"], ["closed", "Afgjorte"], ["all", "Alle"]], st, (v) => { st = v; setParams({ tab, st: v }); draw(); });
    body.append(h("div", { class: "row between" }, h("p", { class: "muted", style: { margin: 0 } }, {
      observer: "Dine observationer, som DKU har spørgsmål til.",
      dku: d.admin && !d.member_of.length ? "Alle sager (du administrerer DKU)." : "Sager fra de lokalafdelinger, du er DKU-ansvarlig for.",
    }[tab]), filter));
    const list = h("div", { class: "stack mt" }, h("div", { class: "skeleton", style: { height: "120px" } }));
    body.append(list);
    let rows;
    try { rows = await api(`/api/dku/cases?view=${tab}&state=${st}`); } catch (err) { list.replaceChildren(errorBox(err)); return; }
    if (!rows.length) {
      list.replaceChildren(empty(st === "open" ? "Ingen åbne sager" : "Ingen sager"));
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

// ---------------------------------------------------------------- kandidater: lokale afvigelser

const CRITERIA = [["antal", "Antal over lokalt normalt", "Antal ≥ minimum og ≥ faktor × den lokale 95-percentil for arten"],
  ["ny", "Ny/sjælden art lokalt", "Arten er set højst så mange gange lokalt i historikken"],
  ["saeson", "Uden for lokal sæson", "Arten er set lokalt før, men aldrig inden for ± dage af datoen"],
  ["dok", "Mangler dokumentation", "Bemærk-art uden fuglenoter, fotos og lyd"]];
const num = (v, w = "78px", min = 0, step = 1) => h("input", { type: "number", value: v, min, step, style: { width: w } });
const lab = (t, el) => h("label", { class: "field" }, h("span", {}, t), el);
const JOB_STATUS = { queued: ["I kø", "plain"], running: ["Kører", "SUB"], done: ["Færdig", "bemaerk"], error: ["Fejlede", "SU"] };

/** Afdelingsvælger med de afdelinger, brugeren er DKU-ansvarlig for. */
function afdelingSelect(onchange) {
  const sel = h("select", { onchange }, h("option", { value: "" }, "Alle mine afdelinger"));
  const d = state.user.dku;
  api("/api/dku/members").then(m => { for (const a of m.afdelinger) if (a.id && (d.admin || d.member_of.includes(0) || d.member_of.includes(a.id)))
    sel.append(h("option", { value: a.id }, a.navn)); }).catch(() => {});
  return sel;
}

/** Filterfelterne. get() giver filtret som JSON (som API'et og workeren bruger det), set() udfylder dem. */
function filterForm(f = {}) {
  const on = Object.fromEntries(CRITERIA.map(([k]) => [k, !f.kriterier || f.kriterier.includes(k)]));
  const boxes = CRITERIA.map(([k, label, tip]) => h("label", { class: "check", title: tip },
    h("input", { type: "checkbox", checked: on[k], onchange: (e) => { on[k] = e.target.checked; } }), ` ${label}`));
  const notable = h("input", { type: "checkbox", checked: !!f.kun_bemaerk });
  const days = num(f.days ?? 7, "64px", 1), min = num(f.min ?? 1, "64px", 1), hist = num(f.hist_years ?? 10, "64px", 1);
  const antalMin = num(f.antal_min ?? 5), factor = num(f.faktor ?? 2, "70px", 1, 0.5), nyMax = num(f.ny_max ?? 0), sd = num(f.saeson_dage ?? 14, "70px", 1);
  const level = h("select", {}, h("option", { value: "lok" }, "lokaliteten"), h("option", { value: "kommune" }, "kommunen"));
  level.value = f.niveau || "lok";
  const get = () => {
    const kriterier = CRITERIA.map(c => c[0]).filter(k => on[k]);
    if (!kriterier.length) { toast("Vælg mindst ét kriterium", "err"); return null; }
    return { kriterier, days: +days.value, min: Math.min(+min.value || 1, kriterier.length), niveau: level.value, hist_years: +hist.value,
      antal_min: +antalMin.value, faktor: +factor.value, ny_max: +nyMax.value, saeson_dage: +sd.value, kun_bemaerk: notable.checked };
  };
  return { get, boxes: [...boxes, h("label", { class: "check", title: "Kun observationer med en anden kategori end alm" }, notable, " Kun bemærkelsesværdige")],
    fields: [lab("Seneste dage", days), lab("Mindst antal kriterier", min), lab("Sammenlign med", level), lab("Historik (år)", hist),
      lab("Antal ≥", antalMin), lab("× lokal 95-percentil", factor), lab("Ny art: set højst (gange)", nyMax), lab("Sæson ± dage", sd)] };
}

const filterText = (f) => [`${f.days} dage`, f.kriterier.map(k => CRITERIA.find(c => c[0] === k)?.[1].toLowerCase()).join(", "),
  f.min > 1 ? `mindst ${f.min} kriterier` : null, `sammenlignet med ${f.niveau === "kommune" ? "kommunen" : "lokaliteten"} (${f.hist_years} år)`,
  f.kriterier.includes("antal") ? `antal ≥ ${f.antal_min} og ≥ ${f.faktor} × 95-percentil` : null,
  f.kriterier.includes("ny") ? `ny art: set højst ${f.ny_max} gange` : null, f.kriterier.includes("saeson") ? `sæson ± ${f.saeson_dage} dage` : null,
  f.kun_bemaerk ? "kun bemærkelsesværdige" : null].filter(Boolean).join(" · ");

/** Kandidatlisten (fra søgning, løbende filter eller analyse) med godkend/start sag. reload henter listen igen. */
function candidateList(out, r, reload) {
  const picked = new Set();
  const approve = async (ids) => {
    try { const x = await api("/api/dku/review", { method: "POST", body: { obsids: ids } }); toast(`${x.approved} godkendt`); reload(); }
    catch (err) { toast(err.message, "err"); }
  };
  if (!r.rows.length) { out.replaceChildren(empty("Ingen afvigelser", "Alt er gennemgået, eller ingen observationer rammer kriterierne. Prøv færre kriterier eller løsere grænser.")); return; }
  const bulk = h("button", { class: "btn sm", type: "button", disabled: true, onclick: () => approve([...picked]) }, "Godkend valgte");
  const card = (x) => {
    const pick = h("input", { type: "checkbox", disabled: !!x.case || x.reviewed, onchange: (e) => {
      e.target.checked ? picked.add(x.obsid) : picked.delete(x.obsid); bulk.disabled = !picked.size; bulk.textContent = picked.size ? `Godkend valgte (${picked.size})` : "Godkend valgte"; } });
    return h("div", { class: "card stack" + (x.reviewed ? " dim" : "") },
      h("div", { class: "row between" },
        h("label", { class: "check" }, pick, h("span", {}, h("b", {}, `${x.antal} ${x.art}`),
          h("span", { class: "muted" }, ` · ${x.lok || "–"}${x.kommune ? `, ${x.kommune}` : ""} · ${fmtDate(x.dato)}`))),
        h("span", { class: "row", style: { gap: "6px" } }, ...x.matched.map(k => h("span", { class: "tag bemaerk" }, r.criteria[k])))),
      h("div", { class: "muted", style: { fontSize: ".86rem" } }, [x.observer ? `observatør: ${x.observer}` : null,
        x.kategori !== "alm" ? `kategori ${x.kategori}` : null,
        x.historik ? `lokalt set ${x.historik} gange${x.p95 != null ? ` (95-percentil ${x.p95})` : ""}, ${x.i_saesonen} i sæsonen` : "aldrig set lokalt i perioden",
        x.found_at ? `fundet ${ago(x.found_at)}` : null]
        .filter(Boolean).join(" · ")),
      h("div", { class: "row" },
        x.case ? h("a", { class: "btn sm", href: `#/dku/${x.case.id}` }, `Sag #${x.case.id}`)
          : x.reviewed ? h("button", { class: "btn sm", type: "button", onclick: async () => {
              try { await api(`/api/dku/review/${x.obsid}`, { method: "DELETE" }); reload(); } catch (err) { toast(err.message, "err"); } } }, "Fortryd godkendelse")
          : [h("button", { class: "btn sm", type: "button", html: `${icon("check")} Godkend`, onclick: () => approve([x.obsid]) }),
             h("button", { class: "btn sm primary", type: "button", html: `${icon("flag")} Start DKU-sag`,
               onclick: () => tipDku("dofbasen", x.obsid, `${x.antal} ${x.art} · ${x.lok || "–"} · ${fmtDate(x.dato)}`) })],
        x.thread ? h("a", { class: "btn sm ghost", href: x.thread }, "Tråden") : null,
        h("a", { class: "btn sm ghost", href: x.url, target: "_blank", rel: "noopener", html: `${icon("external")} DOFbasen` })));
  };
  // Oversigt pr. lokalafdeling
  const groups = new Map();
  for (const x of r.rows) { const g = x.afdelinger_navne[0] || "Uden afdeling"; (groups.get(g) || groups.set(g, []).get(g)).push(x); }
  out.replaceChildren(h("div", { class: "row between" }, h("p", { class: "muted", style: { margin: 0 } }, `${r.rows.length} observationer til gennemsyn`), bulk),
    ...[...groups].flatMap(([g, rows]) => [h("h3", { style: { margin: "12px 0 0" } }, `${g} `, h("span", { class: "muted" }, `(${rows.length})`)), ...rows.map(card)]));
}

function candidatesPanel(body) {
  let mode = params().k || "loebende";
  const inner = h("div", {});
  body.append(h("div", { class: "row between" },
    h("p", { class: "muted", style: { margin: 0 } }, "Observationer, der afviger fra det lokale billede, i de afdelinger, du er DKU-ansvarlig for: godkend dem, der er i orden, eller start en DKU-sag. Hemmelige observationer indgår ikke."),
    seg([["loebende", "Løbende"], ["soeg", "Søg og analysér"]], mode, (v) => { mode = v; setParams({ tab: "kandidater", k: v }); draw(); })), inner);
  const draw = () => { inner.replaceChildren(); (mode === "soeg" ? searchPanel : standingPanel)(inner); };
  draw();
}

/** Det løbende filter: workeren vurderer nye observationer, efterhånden som de kommer ind fra DOFbasen. */
function standingPanel(body) {
  const reviewed = h("input", { type: "checkbox", onchange: () => load() });
  const afdSel = afdelingSelect(() => load());
  const head = h("div", {}), out = h("div", { class: "stack mt" }, h("div", { class: "skeleton", style: { height: "120px" } }));
  body.append(h("div", { class: "card stack mt" }, head,
    h("div", { class: "row", style: { gap: "12px", alignItems: "flex-end" } }, lab("Afdeling", afdSel),
      h("label", { class: "check" }, reviewed, " Vis også godkendte"))), out);
  const load = async () => {
    const q = new URLSearchParams({ vis_behandlede: reviewed.checked });
    if (afdSel.value) q.set("afdeling", afdSel.value);
    let r;
    try { r = await api(`/api/dku/candidates/standing?${q}`); } catch (err) { out.replaceChildren(errorBox(err)); return; }
    const f = r.filter, st = r.status;
    head.replaceChildren(h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, "Løbende filter ",
      h("span", { class: `tag ${f.on ? "bemaerk" : "plain"}` }, f.on ? "slået til" : "slået fra")),
      r.can_edit ? h("button", { class: "btn sm", type: "button", html: `${icon("edit")} Ret filter`, onclick: () => editStanding(f, load) }) : null),
      h("small", { class: "muted" }, f.on ? filterText(f) : r.can_edit
        ? "Slå filtret til, så vurderer workeren nye observationer, efterhånden som de hentes fra DOFbasen."
        : "Det løbende filter er ikke slået til. Landsdækkende DKU-ansvarlige og administratorer kan slå det til."),
      f.on ? h("small", { class: "muted" }, !st ? "Venter på workerens første gennemgang …"
        : [`Opdateret ${ago(st.at)}`, st.full ? "hele perioden gennemgået" : `${st.checked} nye/ændrede observationer vurderet`,
           st.checked_at ? `sidst tjekket ${ago(st.checked_at)}` : null].filter(Boolean).join(" · ")) : null);
    if (!f.on) { out.replaceChildren(); return; }
    candidateList(out, r, load);
  };
  load();
}

function editStanding(f, done) {
  const form = filterForm(f);
  const on = h("input", { type: "checkbox", checked: true });
  const { close } = modal("Løbende filter", h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    const v = form.get();
    if (!v) return;
    try { await api("/api/dku/candidates/standing", { method: "PUT", body: { ...v, on: on.checked } }); close();
      toast(on.checked ? "Gemt – workeren gennemgår perioden med det nye filter" : "Det løbende filter er slået fra"); done(); }
    catch (err) { toast(err.message, "err"); }
  } },
    h("p", { class: "muted", style: { margin: 0 } }, "Filtret gælder alle afdelinger; hver DKU-ansvarlig ser kandidaterne fra sine egne afdelinger."),
    h("label", { class: "check" }, on, " Slået til"),
    h("div", { class: "row", style: { gap: "14px" } }, ...form.boxes),
    h("div", { class: "row", style: { gap: "12px", alignItems: "flex-end" } }, ...form.fields),
    h("div", { class: "row" }, h("button", { class: "btn primary" }, "Gem"))), { wide: true });
}

/** Søg selv – i browseren, eller send søgningen til workeren og hent resultatet, når den er færdig. */
function searchPanel(body) {
  const form = filterForm();
  const reviewed = h("input", { type: "checkbox" });
  const afdSel = afdelingSelect();
  const out = h("div", { class: "stack mt" }), jobs = h("div", { class: "stack" });
  let shown = null, timer = null;
  const query = () => {
    const f = form.get();
    if (!f) return null;
    return { f, q: new URLSearchParams({ ...f, kriterier: f.kriterier.join(","), vis_behandlede: reviewed.checked, ...(afdSel.value ? { afdeling: afdSel.value } : {}) }) };
  };
  const run = async () => {
    const x = query();
    if (!x) return;
    shown = null;
    out.replaceChildren(h("div", { class: "skeleton", style: { height: "120px" } }));
    try { candidateList(out, await api(`/api/dku/candidates?${x.q}`), run); } catch (err) { out.replaceChildren(errorBox(err)); }
  };
  const send = async () => {
    const x = query();
    if (!x) return;
    try { await api("/api/dku/candidates/analyses", { method: "POST", body: { ...x.f, afdeling: afdSel.value ? +afdSel.value : null } });
      toast("Sendt til workeren – resultatet kommer under Analyser"); loadJobs(); }
    catch (err) { toast(err.message, "err"); }
  };
  const show = async (id) => {
    shown = id;
    out.replaceChildren(h("div", { class: "skeleton", style: { height: "120px" } }));
    let r;
    try { r = await api(`/api/dku/candidates/analyses/${id}?vis_behandlede=${reviewed.checked}`); } catch (err) { out.replaceChildren(errorBox(err)); return; }
    const list = h("div", { class: "stack" });
    out.replaceChildren(h("p", { class: "muted", style: { margin: 0 } }, h("b", {}, "Analyse: "), filterText(r.filter), ` · kørt ${ago(r.finished_at)}`), list);
    candidateList(list, r, () => show(id));
  };
  const loadJobs = async () => {
    clearTimeout(timer);
    if (!jobs.isConnected && timer) return;  // fanen er forladt
    let rows;
    try { rows = await api("/api/dku/candidates/analyses"); } catch (err) { jobs.replaceChildren(errorBox(err)); return; }
    jobs.replaceChildren(...(rows.length ? [h("h3", { style: { margin: "4px 0 0" } }, "Analyser i workeren")] : []), ...rows.map(j => {
      const [label, tag] = JOB_STATUS[j.status] || [j.status, "plain"];
      return h("div", { class: "row between", style: { gap: "8px" } },
        h("span", {}, h("span", { class: `tag ${tag}` }, label), " ", j.label,
          h("span", { class: "muted" }, ` · ${ago(j.created_at)}${j.status === "done" ? ` · ${j.n} kandidater` : ""}${j.error ? ` · ${j.error}` : ""}`)),
        j.status === "done" ? h("button", { class: `btn sm ${shown === j.id ? "primary" : ""}`, type: "button", onclick: () => show(j.id) }, "Vis") : null);
    }));
    if (rows.some(j => j.status === "queued" || j.status === "running")) timer = setTimeout(loadJobs, 4000);
  };
  body.append(h("div", { class: "card stack mt" },
    h("div", { class: "row", style: { gap: "14px" } }, ...form.boxes, h("label", { class: "check" }, reviewed, " Vis også godkendte")),
    h("div", { class: "row", style: { gap: "12px", alignItems: "flex-end" } }, lab("Afdeling", afdSel), ...form.fields,
      h("button", { class: "btn primary", type: "button", onclick: run }, "Find afvigelser"),
      h("button", { class: "btn", type: "button", title: "Kør søgningen i workeren – godt til mange dage eller lang historik. Resultatet gemmes en uge.",
        onclick: send, html: `${icon("db")} Send til worker` })),
    jobs), out);
  loadJobs();
}
