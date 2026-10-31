// Artikler → Admin: artikler (alle statusser), import, emneord, kommentarer til moderation, indstillinger og vedligehold.
import { api, h, toast, seg } from "../core.js";
import { BASE, ctx, dkDate, nowrap, loadingBox, cleanups } from "./common.js";
import { results, readState } from "./soeg.js";

const TABS = [["artikler", "Artikler"], ["import", "Import"], ["emneord", "Emneord"], ["kommentarer", "Kommentarer"], ["indstillinger", "Indstillinger"], ["vedligehold", "Vedligehold"]];

export async function page(parts) {
  const c = ctx();
  const tabs = TABS.filter(([k]) => k === "kommentarer" ? c.can_moderate : true);
  const tab = tabs.some(t => t[0] === parts[1]) ? parts[1] : tabs[0][0];
  const body = h("div", {}, loadingBox());
  const bar = h("div", { class: "row", style: { margin: "0 0 14px" } }, ...tabs.map(([k, t]) => h("a", { class: `btn sm ${k === tab ? "primary" : ""}`, href: `${BASE}/admin/${k}` }, t)));
  (async () => {
    try { body.replaceChildren(await TAB[tab]()); } catch (e) { body.replaceChildren(h("div", { class: "notice err" }, e.message)); }
  })();
  return h("div", { class: "art-adm" }, h("h2", {}, "Administration af artikler"), bar, body);
}

const TAB = {
  async artikler() {
    const st = { ...readState({}), sort: "nyeste" };
    const out = h("div", {});
    const q = h("input", { type: "search", placeholder: "Søg i alle artikler (inkl. kladder) …", style: { flex: 1, minWidth: "220px" } });
    const run = () => { st.side = 1; st.q = q.value.trim(); out.replaceChildren(results(st).el); };
    run();
    return h("div", {}, h("form", { class: "row", onsubmit: (e) => { e.preventDefault(); run(); } }, q, h("button", { class: "btn primary" }, "Søg"),
      ctx().can_edit ? h("a", { class: "btn", href: `${BASE}/ny` }, "+ Ny artikel") : null), out);
  },
  async import() { return (await import("./import.js")).page(); },

  async emneord() { return tagAdmin(); },

  async kommentarer() { return commentQueue(); },

  async indstillinger() {
    const s = await api("/api/admin/artikler/indstillinger");
    const hosts = h("textarea", { rows: 6 }); hosts.value = s.embed_hosts.join("\n");
    const amb = h("textarea", { rows: 3 }); amb.value = s.ambiguous_species.join(", ");
    const num = (v, min, max) => h("input", { type: "number", value: v, min, max, style: { width: "100px" } });
    const thr = num(s.report_threshold, 1, 50), mins = num(s.comment_edit_minutes, 0, 1440), sec = num(s.front.secondary_count, 0, 6);
    const ce = h("input", { type: "checkbox", checked: s.comments_enabled }), otd = h("input", { type: "checkbox", checked: s.front.on_this_day });
    return h("form", { class: "card stack", style: { maxWidth: "720px" }, onsubmit: async (e) => {
      e.preventDefault();
      try {
        await api("/api/admin/artikler/indstillinger", { method: "PUT", body: { embed_hosts: hosts.value.split(/[\s,]+/).filter(Boolean), ambiguous_species: amb.value.split(",").map(x => x.trim()).filter(Boolean),
          report_threshold: +thr.value, comment_edit_minutes: +mins.value, comments_enabled: ce.checked, front: { ...s.front, secondary_count: +sec.value, on_this_day: otd.checked } } });
        toast("Gemt – indlejringer fra nye værter virker straks (siden hentes igen)");
      } catch (err) { toast(err.message, "err"); }
    } },
      h("label", { class: "field" }, h("span", {}, "Værter, der må indlejres (iframe) – ét domæne pr. linje"), hosts, h("small", { class: "muted" }, "Fx xeno-canto.org, player.vimeo.com. Alt andet i en indlejring fjernes.")),
      h("label", { class: "field" }, h("span", {}, "Tvetydige artsnavne"), amb, h("small", { class: "muted" }, "Navne der også er almindelige ord eller efternavne tæller kun i teksten, når de står med stort bogstav midt i en sætning. Kør «Genberegn arter» bagefter.")),
      h("label", { class: "field" }, h("span", {}, "Anmeldelser før en kommentar skjules automatisk"), thr),
      h("label", { class: "field" }, h("span", {}, "Minutter man kan rette sin kommentar"), mins),
      h("label", { class: "check" }, ce, h("span", {}, "Kommentarer er slået til")),
      h("label", { class: "field" }, h("span", {}, "Antal artikler i forsidens tre kolonner"), sec),
      h("label", { class: "check" }, otd, h("span", {}, "Vis «På denne dag» på forsiden")),
      h("div", { class: "row" }, h("button", { class: "btn primary" }, "Gem")));
  },

  async vedligehold() {
    const run = (path, label) => h("button", { class: "btn", type: "button", onclick: async () => { try { await api(path, { method: "POST" }); toast(`${label} er sat i gang (se Admin → Drift → Job)`); } catch (e) { toast(e.message, "err"); } } }, label);
    return h("div", { class: "card stack", style: { maxWidth: "720px" } },
      h("div", {}, h("h3", {}, "Genberegn arter i alle artikler"), h("p", { class: "muted" }, "Efter ændringer i artslisten, emneord eller listen over tvetydige navne. Redaktørens egne valg og fravalg bevares."), run("/api/admin/artikler/genberegn-arter", "Genberegn arter")),
      h("div", {}, h("h3", {}, "Render alle tekster igen"), h("p", { class: "muted" }, "Artikler, nyheder og kommentarer renderes igen med de nyeste regler (efter en opdatering af renderen eller ændret tilladelsesliste)."), run("/api/admin/artikler/render-alle", "Render alt igen")));
  },
};


export async function commentQueue() {
  let tabK = "anmeldte";
  const out = h("div", { class: "stack" });
  const draw = async () => {
    out.replaceChildren(loadingBox());
    const rows = await api(`/api/admin/artikler/kommentarer?tab=${tabK}`);
    const act = async (r, action) => {
      let reason = "";
      if (action === "skjul" || action === "slet") { reason = prompt("Årsag (vises for forfatteren):", "") ?? null; if (reason === null) return; }
      try { await api(`/api/admin/artikler/kommentarer/${r.id}/${action}`, { method: "POST", body: { reason, notify: false } }); draw(); } catch (e) { toast(e.message, "err"); }
    };
    out.replaceChildren(...(rows.length ? rows.map(r => h("div", { class: "card stack" },
      h("div", {}, h("a", { href: `${BASE}/${r.article_id}?k=${r.id}` }, r.article), " · ", h("b", {}, r.navn), r.archive ? " (netfugl.dk-arkiv)" : "", ` · ${dkDate(r.created_at, { time: true })} · `, h("span", { class: "art-chip" }, r.status), r.report_count ? h("span", { class: "art-chip warn" }, `🚩 ${r.report_count}`) : null),
      h("div", { style: { whiteSpace: "pre-wrap" } }, r.body),
      r.hidden_reason ? h("div", { class: "muted small" }, "Årsag: " + r.hidden_reason) : null,
      r.reports?.length ? h("ul", { class: "small" }, ...r.reports.map(x => h("li", {}, `${x.af}: ${x.reason}${x.note ? " – " + x.note : ""}`))) : null,
      h("div", { class: "row" }, r.status === "synlig" ? h("button", { class: "btn sm", onclick: () => act(r, "skjul") }, "Skjul") : null, r.status === "skjult" ? h("button", { class: "btn sm", onclick: () => act(r, "gendan") }, "Gendan") : null,
        r.report_count ? h("button", { class: "btn sm", onclick: () => act(r, "afvis") }, "Afvis anmeldelser") : null, r.status !== "slettet" ? h("button", { class: "btn sm danger", onclick: () => act(r, "slet") }, "Slet") : null))) : [h("p", { class: "muted" }, "Ingen kommentarer her.")]));
  };
  draw();
  return h("div", {}, seg([["anmeldte", "Anmeldte"], ["skjulte", "Skjulte"], ["seneste", "Seneste"]], tabK, v => { tabK = v; draw(); }), h("div", { style: { marginTop: "12px" } }, out));
}

// ------------------------------------------------------------------ emneord

const TAG_KINDS = [["emne", "Emne"], ["sted", "Sted"], ["publikation", "Publikation"], ["aar", "År"], ["art", "Art"], ["type", "Type"]];
const AI_PROMPT = `Du får en CSV med emneord fra et arkiv af danske fugleartikler (netfugl.dk). Klassificér hvert emneord ved at rette kolonnerne «slags», «artnr»/«art» og «skjult».

slags (præcis én af):
- emne: et fagligt emne (fx Træk, Ringmærkning, Ynglefugle, Fotografering)
- sted: en lokalitet, et område eller et land (fx Skagen, Vejlerne, Bornholm, Spanien)
- art: en fugleart – skriv det danske artsnavn i «art» (fx Dværgterne); lad «artnr» stå tomt, hvis du ikke kender DOF's artsnummer
- publikation: et blad eller en serie (fx Pandion, DOF NYT, Fugle og Natur)
- aar: et årstal
- type: artikeltypen (Nyhed, Artikel, Kort nyt)
skjult: «ja» for emneord, der ikke giver mening for læserne (støj, interne mærker, dubletter med stavefejl), ellers «nej».

Kolonnen «eksempler» viser op til tre artikeltitler med emneordet, så du kan se, hvad det bruges til. Behold kolonnerne id, navn, slags, artnr, art og skjult og rækkefølgen; ret ikke id. Du må rette stavefejl i «navn». Svar kun med CSV (semikolon som skilletegn), uden forklaring. Du må udelade rækker, du ikke ændrer.`;

async function tagAdmin() {
  const tags = await api("/api/admin/artikler/emneord");
  const byId = new Map(tags.map(t => [t.id, t]));
  const pending = new Map();  // id → { navn, kind, artnr, hidden } (kun felter, der afviger)
  let filter = "", kindF = "", onlyChanged = false;
  const FIELDS = ["navn", "kind", "artnr", "hidden"];
  const val = (t, f) => pending.get(t.id)?.[f] ?? t[f];
  const setField = (t, f, v) => {
    const p = { ...(pending.get(t.id) || {}) };
    if ((v ?? null) === (t[f] ?? null)) delete p[f]; else p[f] = v;
    Object.keys(p).length ? pending.set(t.id, p) : pending.delete(t.id);
    updateBar();
  };

  const body = h("tbody");
  const count = h("span", { class: "muted small" });
  const saveBtn = h("button", { class: "btn primary", type: "button", onclick: () => save() }, "Gem");
  const undoBtn = h("button", { class: "btn", type: "button", onclick: () => { if (confirm("Fortryd alle ugemte ændringer?")) { pending.clear(); updateBar(); draw(); } } }, "Fortryd");
  const barText = h("span", {});
  const bar = h("div", { class: "row art-tagbar" }, barText, saveBtn, undoBtn);
  const warnBox = h("div", {});
  function updateBar() {
    const n = pending.size;
    barText.textContent = n ? `${nowrap(n)} ugemte ændringer` : "Ingen ugemte ændringer";
    saveBtn.disabled = undoBtn.disabled = !n;
    bar.classList.toggle("dirty", n > 0);
  }

  const draw = () => {
    const rows = tags.filter(t => (!filter || val(t, "navn").toLowerCase().includes(filter) || t.navn.toLowerCase().includes(filter))
      && (!kindF || val(t, "kind") === kindF) && (!onlyChanged || pending.has(t.id)));
    count.textContent = rows.length === tags.length ? `${nowrap(tags.length)} emneord` : `${nowrap(rows.length)} af ${nowrap(tags.length)} emneord`;
    body.replaceChildren(...rows.map(t => {
      const tr = h("tr", {});
      const mark = () => {
        const p = pending.get(t.id) || {};
        tr.classList.toggle("changed", pending.has(t.id));
        for (const [f, el] of Object.entries(els)) el.classList.toggle("changed", f in p);
      };
      const name = h("input", { type: "text", value: val(t, "navn"), maxlength: 120, style: { width: "100%" }, oninput: () => { setField(t, "navn", name.value.trim() || t.navn); mark(); } });
      const kind = h("select", { onchange: () => { setField(t, "kind", kind.value); artnr.disabled = kind.value !== "art"; mark(); } },
        ...TAG_KINDS.map(([v, l]) => h("option", { value: v, selected: v === val(t, "kind") }, l)));
      const artnr = h("input", { type: "number", value: val(t, "artnr") ?? "", placeholder: "artnr", style: { width: "80px" }, disabled: val(t, "kind") !== "art",
        oninput: () => { setField(t, "artnr", artnr.value ? +artnr.value : null); mark(); } });
      const hidden = h("input", { type: "checkbox", checked: val(t, "hidden"), title: "Skjul i oversigter og filtre", onchange: () => { setField(t, "hidden", hidden.checked); mark(); } });
      const els = { navn: name, kind, artnr, hidden };
      mark();
      tr.append(h("td", {}, name), h("td", {}, kind), h("td", {}, artnr), h("td", {}, hidden), h("td", { class: "num" }, nowrap(t.n)),
        h("td", {}, h("button", { class: "btn sm", type: "button", onclick: () => merge(t) }, "Flet …")));
      return tr;
    }));
  };

  async function merge(t) {
    if (pending.size && !confirm("Du har ugemte ændringer. Flet gemmes med det samme – de ugemte ændringer bevares. Fortsæt?")) return;
    const to = prompt(`Flet «${t.navn}» ind i hvilket emneord? Skriv det nøjagtige navn:`);
    const target = tags.find(x => x.id !== t.id && x.navn.toLowerCase() === (to || "").trim().toLowerCase());
    if (!target) { if (to) toast("Emneordet findes ikke", "err"); return; }
    if (!confirm(`Flyt ${t.n} artikler fra «${t.navn}» til «${target.navn}» og slet «${t.navn}»?`)) return;
    try {
      const r = await api(`/api/admin/artikler/emneord/${t.id}/flet`, { method: "POST", body: { into: target.id } });
      toast(`${r.flyttet} artikler flyttet`);
      tags.splice(tags.indexOf(t), 1); byId.delete(t.id); pending.delete(t.id); target.n += r.flyttet; updateBar(); draw();
    } catch (e) { toast(e.message, "err"); }
  }

  async function save() {
    const changes = [...pending].map(([id, p]) => {
      const t = byId.get(id);
      const c = { id, ...p };
      if ("kind" in p && p.kind === "art" && !("artnr" in p)) c.artnr = t.artnr;
      return c;
    });
    saveBtn.disabled = true;
    try {
      const r = await api("/api/admin/artikler/emneord", { method: "PUT", body: { changes } });
      for (const [id, p] of pending) Object.assign(byId.get(id), p);
      pending.clear(); updateBar(); draw();
      toast(`${nowrap(r.gemt)} emneord gemt`);
    } catch (e) { toast(e.message, "err"); updateBar(); }
  }

  const file = h("input", { type: "file", accept: ".csv,.tsv,.txt,.json,text/csv", hidden: true, onchange: async () => {
    const f = file.files[0]; file.value = "";
    if (!f) return;
    const form = new FormData(); form.append("file", f, f.name);
    try {
      const r = await api("/api/admin/artikler/emneord/import", { method: "POST", form });
      for (const c of r.changes) {
        const t = byId.get(c.id);
        if (t) for (const fld of FIELDS) if (fld in c) setField(t, fld, c[fld]);
      }
      onlyChanged = r.changes.length > 0; onlyBox.checked = onlyChanged;
      warnBox.replaceChildren(r.warnings.length ? h("details", { class: "notice warn" }, h("summary", {}, `${r.warnings.length} advarsler fra importen`), h("ul", { class: "small" }, ...r.warnings.map(w => h("li", {}, w)))) : "");
      toast(`${nowrap(r.changes.length)} ændringer indlæst fra ${nowrap(r.rows)} rækker – gennemse og tryk Gem`);
      updateBar(); draw();
    } catch (e) { toast(e.message, "err"); }
  } });
  const onlyBox = h("input", { type: "checkbox", onchange: () => { onlyChanged = onlyBox.checked; draw(); } });
  const promptBox = h("textarea", { rows: 10, readonly: true, style: { width: "100%", fontSize: ".85rem" } }); promptBox.value = AI_PROMPT;

  const leave = (e) => { if (pending.size) { e.preventDefault(); e.returnValue = ""; } };
  addEventListener("beforeunload", leave);
  const onHash = () => { if (pending.size && !location.hash.startsWith(`${BASE}/admin/emneord`)) toast(`${pending.size} ændringer til emneord blev ikke gemt`, "err"); };
  addEventListener("hashchange", onHash);
  cleanups.push(() => { removeEventListener("beforeunload", leave); removeEventListener("hashchange", onHash); });

  updateBar(); draw();
  return h("div", {},
    h("div", { class: "row", style: { marginBottom: "10px" } },
      h("input", { type: "search", placeholder: "Filtrér …", oninput: (e) => { filter = e.target.value.toLowerCase(); draw(); } }),
      seg([["", "Alle"], ...TAG_KINDS], kindF, v => { kindF = v; draw(); }),
      h("label", { class: "small" }, onlyBox, " Kun ændrede"), count),
    h("div", { class: "row", style: { marginBottom: "10px" } },
      h("a", { class: "btn sm", href: "/api/admin/artikler/emneord/eksport", download: "emneord.csv" }, "⬇ Eksportér CSV"),
      h("button", { class: "btn sm", type: "button", onclick: () => file.click() }, "⬆ Importér CSV …"), file,
      h("details", {}, h("summary", { class: "small" }, "Prompt til AI-klassificering"),
        h("p", { class: "muted small" }, "Eksportér, giv filen og teksten herunder til en AI, og importér svaret. Ændringerne gemmes ikke, før du har gennemset dem og trykket Gem."),
        promptBox, h("button", { class: "btn sm", type: "button", onclick: async () => { try { await navigator.clipboard.writeText(AI_PROMPT); toast("Kopieret"); } catch { promptBox.select(); } } }, "Kopiér prompt"))),
    warnBox,
    h("p", { class: "muted small" }, "Slags emneord styrer, hvor de vises. «Art» kobler emneordet til en art (artnr), så artiklerne også kommer med under arten. «Type» (Nyhed, Artikel) bruges som filter. Skjulte emneord vises ikke i oversigter. Rettelser markeres og gemmes samlet med «Gem»."),
    bar,
    h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Navn"), h("th", {}, "Slags"), h("th", {}, "Artnr"), h("th", {}, "Skjult"), h("th", { class: "num" }, "Artikler"), h("th", {}))), body));
}
