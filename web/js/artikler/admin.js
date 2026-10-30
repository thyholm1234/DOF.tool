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

  async emneord() {
    const tags = await api("/api/admin/artikler/emneord");
    let filter = "", kindF = "";
    const KINDS = [["emne", "Emne"], ["sted", "Sted"], ["publikation", "Publikation"], ["aar", "År"], ["art", "Art"], ["type", "Type"]];
    const body = h("tbody");
    const draw = () => {
      const rows = tags.filter(t => (!filter || t.navn.toLowerCase().includes(filter)) && (!kindF || t.kind === kindF)).slice(0, 300);
      body.replaceChildren(...rows.map(t => {
        const name = h("input", { type: "text", value: t.navn, maxlength: 120, style: { width: "100%" } });
        const kind = h("select", {}, ...KINDS.map(([v, l]) => h("option", { value: v, selected: v === t.kind }, l)));
        const artnr = h("input", { type: "number", value: t.artnr ?? "", placeholder: "artnr", style: { width: "80px" } });
        const hidden = h("input", { type: "checkbox", checked: t.hidden, title: "Skjul i oversigter og filtre" });
        return h("tr", {}, h("td", {}, name), h("td", {}, kind), h("td", {}, artnr), h("td", {}, hidden), h("td", { class: "num" }, nowrap(t.n)),
          h("td", {}, h("button", { class: "btn sm", type: "button", onclick: async () => {
            try { await api(`/api/admin/artikler/emneord/${t.id}`, { method: "PUT", body: { navn: name.value, kind: kind.value, artnr: artnr.value ? +artnr.value : null, hidden: hidden.checked } }); toast("Gemt"); Object.assign(t, { navn: name.value, kind: kind.value, hidden: hidden.checked }); } catch (e) { toast(e.message, "err"); } } }, "Gem"), " ",
            h("button", { class: "btn sm", type: "button", onclick: async () => {
              const to = prompt(`Flet «${t.navn}» ind i hvilket emneord? Skriv det nøjagtige navn:`);
              const target = tags.find(x => x.navn.toLowerCase() === (to || "").trim().toLowerCase());
              if (!target) { if (to) toast("Emneordet findes ikke", "err"); return; }
              if (!confirm(`Flyt ${t.n} artikler fra «${t.navn}» til «${target.navn}» og slet «${t.navn}»?`)) return;
              try { const r = await api(`/api/admin/artikler/emneord/${t.id}/flet`, { method: "POST", body: { into: target.id } }); toast(`${r.flyttet} artikler flyttet`); tags.splice(tags.indexOf(t), 1); target.n += r.flyttet; draw(); } catch (e) { toast(e.message, "err"); } } }, "Flet …")));
      }));
    };
    draw();
    return h("div", {}, h("div", { class: "row", style: { marginBottom: "10px" } },
      h("input", { type: "search", placeholder: "Filtrér …", oninput: (e) => { filter = e.target.value.toLowerCase(); draw(); } }),
      seg([["", "Alle"], ...KINDS], kindF, v => { kindF = v; draw(); }), h("span", { class: "muted small" }, `${nowrap(tags.length)} emneord`)),
      h("p", { class: "muted small" }, "Slags emneord styrer, hvor de vises. «Art» kobler emneordet til en art (artnr), så artiklerne også kommer med under arten. «Type» (Nyhed, Artikel) bruges som filter. Skjulte emneord vises ikke i oversigter."),
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Navn"), h("th", {}, "Slags"), h("th", {}, "Artnr"), h("th", {}, "Skjult"), h("th", { class: "num" }, "Artikler"), h("th", {}))), body));
  },

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
