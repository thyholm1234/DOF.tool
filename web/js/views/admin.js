import { api, state, h, fmtN, fmtDate, ago, seg, params, setParams, toast, errorBox, empty } from "../core.js";

export async function render(view) {
  if (!state.user || !["admin", "superadmin"].includes(state.user.role)) { view.append(errorBox(new Error("Kræver administratorrettigheder"))); return; }
  const tab = params().tab || "drift";
  const body = h("div", {});
  view.append(h("div", { class: "page-head" }, h("h1", {}, "Administration"), h("span", { class: "spacer" }),
    seg([["drift", "Drift"], ["brugere", "Brugere"], ["arter", "Arter"], ["indhold", "Nyheder & moderation"]], tab, (v) => { setParams({ tab: v }); body.replaceChildren(); clearInterval(timer); draw(v); })), body);
  let timer;
  const draw = (t) => ({ drift: () => { ops(body); timer = setInterval(() => { body.replaceChildren(); ops(body); }, 15000); }, brugere: () => users(body), arter: () => species(body), indhold: () => content(body) }[t] || ops)();
  draw(tab);
  return () => clearInterval(timer);
}

async function ops(body) {
  let d;
  try { d = await api("/api/admin/overview"); } catch (err) { body.append(errorBox(err)); return; }
  const g = d.gateway, c = d.counts, a = d.archive;
  const today = new Date().toISOString().slice(0, 10);
  const enqueueForm = () => {
    const kind = h("select", {}, ...[["day", "Dag (dato)"], ["month", "Måned"], ["history", "Observatørhistorik"], ["reference", "Referencedata (21 kald)"]].map(([v, l]) => h("option", { value: v }, l)));
    const p1 = h("input", { placeholder: "Dato, måned eller obserkode", style: { flex: 1 } });
    return h("form", { class: "row", onsubmit: async (e) => {
      e.preventDefault();
      const params = kind.value === "day" ? { date: p1.value } : kind.value === "month" ? { year: p1.value.split("-")[0], month: p1.value.split("-")[1] } : kind.value === "history" ? { obserkode: p1.value } : {};
      try { await api("/api/admin/gateway/enqueue", { method: "POST", body: { kind: kind.value, params } }); toast("Lagt i kø"); body.replaceChildren(); ops(body); } catch (err) { toast(err.message, "err"); }
    } }, kind, p1, h("button", { class: "btn" }, "Læg i kø"));
  };
  const setting = (key, label) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: !!d.settings[key], onchange: async (e) => { await api("/api/admin/settings", { method: "PUT", body: { [key]: e.target.checked } }); toast("Gemt"); } }), label);
  const tagInput = h("input", { value: d.settings.matrikel_tag, style: { width: "100px" } });
  body.append(
    h("div", { class: "stats" }, st(fmtN(c.users), "brugere"), st(fmtN(c.devices), "push-enheder"), st(fmtN(c.observations_est), "observationer (ca.)"), st(fmtN(c.notifications_24h), "notifikationer 24 t")),
    h("div", { class: "grid-2 mt" },
      h("div", { class: "card stack" }, h("h3", {}, "DOFbasen-gateway"),
        h("p", {}, `Højst ét kald pr. ${g.min_interval} s. Seneste: `, h("b", {}, g.last_kind || "–"), ` ${g.last_at ? ago(g.last_at) : ""} (${g.last_duration || "–"} s) `,
          g.last_ok === "0" ? h("span", { class: "tag SU" }, "fejl") : h("span", { class: "tag plain" }, "ok")),
        g.last_error ? h("div", { class: "notice err" }, g.last_error) : null,
        h("p", { class: "muted" }, `Kald i dag: ${g[`calls:${today}`] || 0} · i kø: ${g.queue_length}`),
        setting("live_enabled", "Live-hentning af dagens observationer"),
        enqueueForm(),
        d.queue.length ? h("div", { class: "table-wrap", style: { maxHeight: "260px" } }, h("table", { class: "data" }, h("tbody", {}, ...d.queue.map(j => h("tr", {},
          h("td", {}, j.kind), h("td", { class: "muted" }, JSON.stringify(j.params)), h("td", { class: "muted" }, `p${j.priority}`),
          h("td", { class: "right" }, h("button", { class: "btn sm ghost danger", onclick: async () => { await api(`/api/admin/gateway/jobs/${j.id}`, { method: "DELETE" }); body.replaceChildren(); ops(body); } }, "Annullér"))))))) : h("p", { class: "muted" }, "Køen er tom.")),
      h("div", { class: "card stack" }, h("h3", {}, "Data og beregninger"),
        h("p", {}, `Observationer fra ${fmtDate(c.first_date)} til ${fmtDate(c.last_date)}.`),
        h("p", {}, "Arkiv: ", `${a.files} filer`, a.pending.length ? `, ${a.pending.length} nye/ændrede` : ", alt importeret",
          a.progress?.status ? ` · import: ${a.progress.status} ${a.progress.files_done || 0}/${a.progress.files_total || "?"} filer, ${fmtN(Number(a.progress.rows_total || 0))} rækker` : ""),
        h("div", { class: "row" }, ...[["archive_import", "Importér arkiv"], ["compute", "Genberegn fænologi/lokale tal"], ["climate", "Hent DMI-klima"], ["aggregates", "Opdatér dagsaggregater"]]
          .map(([cmd, label]) => h("button", { class: "btn sm", onclick: async () => { await api(`/api/admin/worker/${cmd}`, { method: "POST", body: {} }); toast(`${label}: startet`); } }, label))),
        d.worker?.cmd ? h("p", { class: "muted" }, `Seneste worker-kommando: ${d.worker.cmd} ${ago(d.worker.at)}`) : null,
        h("h3", { class: "mt" }, "Indstillinger"),
        h("div", { class: "row" }, "Matrikel-tag:", tagInput, h("button", { class: "btn sm", onclick: async () => { await api("/api/admin/settings", { method: "PUT", body: { matrikel_tag: tagInput.value } }); toast("Gemt"); } }, "Gem"),
          h("small", { class: "muted" }, "årstal tilføjes automatisk (#BB → #BB26)")))),
    h("div", { class: "card mt" }, h("h3", {}, "Gateway-log (seneste 50 kald)"),
      h("div", { class: "table-wrap", style: { maxHeight: "360px" } }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Tid"), h("th", {}, "Job"), h("th", {}, "Parametre"), h("th", { class: "right" }, "Sek."), h("th", {}, "Resultat"))),
        h("tbody", {}, ...d.log.map(l => h("tr", {}, h("td", { class: "nowrap" }, new Date(l.at * 1000).toLocaleTimeString("da-DK")), h("td", {}, l.kind),
          h("td", { class: "muted" }, JSON.stringify(l.params || {})), h("td", { class: "right num" }, l.duration),
          h("td", {}, l.error ? h("span", { style: { color: "var(--err)" } }, l.error) : h("span", { class: "muted" }, JSON.stringify(l.result || {}))))))))));
}

const st = (v, l) => h("div", { class: "stat" }, h("b", {}, v), h("span", {}, l));

async function users(body) {
  const q = h("input", { type: "search", placeholder: "Søg obserkode/navn…", style: { maxWidth: "280px" } });
  const out = h("div", { class: "card table-wrap mt" });
  body.append(q, out);
  const load = async () => {
    const rows = await api(`/api/admin/users?q=${encodeURIComponent(q.value)}`);
    out.replaceChildren(h("table", { class: "data stack-sm", style: { "--cols": 3 } },
      h("thead", {}, h("tr", {}, h("th", {}, "Bruger"), h("th", {}, "Rolle"), h("th", {}, "Sidst set"), h("th", { class: "right" }, "Enheder/regler"), h("th", {}))),
      h("tbody", {}, ...rows.map(u => h("tr", {},
        h("td", {}, h("b", {}, u.navn || u.obserkode), h("div", { class: "muted" }, u.obserkode, u.blocked ? " · spærret" : "")),
        h("td", { class: "wide", "data-label": "Rolle" }, h("select", { style: { width: "auto", height: "32px" }, disabled: u.role === "superadmin" && state.user.role !== "superadmin", onchange: async (e) => {
          try { await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { role: e.target.value } }); toast("Rolle ændret"); } catch (err) { toast(err.message, "err"); load(); }
        } }, ...["user", "editor", "admin", "superadmin"].map(r => h("option", { selected: r === u.role }, r)))),
        h("td", { class: "muted", "data-label": "Sidst set" }, u.last_seen ? ago(u.last_seen) : "aldrig"),
        h("td", { class: "right num", "data-label": "Enh./regler" }, `${u.devices} / ${u.rules}`),
        h("td", { class: "right nowrap wide" }, u.role !== "superadmin" ? [
          h("button", { class: "btn sm", onclick: async () => { await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { blocked: !u.blocked } }); load(); } }, u.blocked ? "Ophæv spærring" : "Spær"),
          state.user.role === "superadmin" ? h("button", { class: "btn sm ghost danger", onclick: async () => { if (confirm(`Slet ${u.obserkode}?`)) { await api(`/api/admin/users/${u.id}`, { method: "DELETE" }); load(); } } }, "Slet") : null] : null))))));
  };
  let t; q.oninput = () => { clearTimeout(t); t = setTimeout(load, 250); };
  await load();
}

async function species(body) {
  const q = h("input", { type: "search", placeholder: "Søg art…", style: { maxWidth: "280px" } });
  const out = h("div", { class: "card table-wrap mt" });
  body.append(h("p", { class: "muted" }, "Klassifikation og listeregler. 'Tæller som' bestemmer hvilken art en underart tæller som på årslister; tom = tæller ikke. 'Udeluk' fjerner arten fra lister og notifikationer."), q, out);
  const load = async () => {
    const rows = await api(`/api/admin/species?q=${encodeURIComponent(q.value)}`);
    out.replaceChildren(h("table", { class: "data stack-sm", style: { "--cols": 3 } }, h("thead", {}, h("tr", {}, h("th", {}, "Art"), h("th", {}, "Nr."), h("th", {}, "Type"), h("th", {}, "Status"), h("th", {}, "Tæller som"), h("th", {}, "Udeluk"))),
      h("tbody", {}, ...rows.map(s => h("tr", {}, h("td", {}, s.navn), h("td", { class: "num muted", "data-label": "Nr." }, s.artnr), h("td", { class: "muted", "data-label": "Type" }, s.art_type),
        h("td", { "data-label": "Status" }, h("select", { style: { width: "auto", height: "30px" }, onchange: async (e) => { await api(`/api/admin/species/${s.artnr}`, { method: "PATCH", body: { klass: e.target.value } }); toast("Gemt"); } },
          ...["Alm", "SUB", "SU"].map(k => h("option", { selected: k === s.klass }, k)))),
        h("td", { class: "muted span2", "data-label": "Tæller som" }, s.list_navn || "–"),
        h("td", { "data-label": "Udeluk" }, h("input", { type: "checkbox", checked: s.excluded, onchange: async (e) => { await api(`/api/admin/species/${s.artnr}`, { method: "PATCH", body: { excluded: e.target.checked } }); toast("Gemt"); } })))))));
  };
  let t; q.oninput = () => { clearTimeout(t); t = setTimeout(load, 250); };
  await load();
}

async function content(body) {
  const title = h("input", { placeholder: "Overskrift" });
  const text = h("textarea", { placeholder: "Tekst" });
  const news = h("div", { class: "stack" });
  const comments = h("div", { class: "table-wrap" });
  body.append(h("div", { class: "grid-2" },
    h("form", { class: "card stack", onsubmit: async (e) => { e.preventDefault(); try { await api("/api/admin/news", { method: "POST", body: { title: title.value, body: text.value } }); title.value = text.value = ""; load(); toast("Publiceret"); } catch (err) { toast(err.message, "err"); } } },
      h("h3", {}, "Ny nyhed"), title, text, h("button", { class: "btn primary" }, "Publicér"), news),
    h("div", { class: "card" }, h("h3", {}, "Seneste kommentarer"), comments)));
  const load = async () => {
    const [n, c] = await Promise.all([api("/api/news?limit=20"), api("/api/admin/comments")]);
    news.replaceChildren(...n.map(x => h("div", { class: "row between" }, h("span", {}, h("b", {}, x.title), h("small", { class: "muted" }, ` ${fmtDate(x.created_at)}`)),
      h("button", { class: "btn sm ghost danger", onclick: async () => { await api(`/api/admin/news/${x.id}`, { method: "DELETE" }); load(); } }, "Slet"))));
    comments.replaceChildren(c.length ? h("table", { class: "data" }, h("tbody", {}, ...c.map(x => h("tr", { style: { opacity: x.hidden ? .5 : 1 } },
      h("td", {}, h("b", {}, x.navn || x.obserkode), h("div", {}, x.body), h("small", { class: "muted" }, `${x.art} · ${fmtDate(x.dato)} · `, h("a", { href: `#/traad/${x.dato}/${x.artnr}/${x.loknr}` }, "tråd"))),
      h("td", { class: "right" }, h("button", { class: "btn sm", onclick: async () => { await api(`/api/admin/comments/${x.id}`, { method: "PATCH", body: { hidden: !x.hidden } }); load(); } }, x.hidden ? "Vis" : "Skjul")))))) : empty("Ingen kommentarer"));
  };
  await load();
}
