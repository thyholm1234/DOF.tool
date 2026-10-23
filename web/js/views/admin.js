import { api, state, h, icon, fmtN, fmtDate, fmtTime, ago, seg, params, setParams, toast, errorBox, empty, modal, can, hasTab, ADMIN_TABS, adminLink, searchSpecies } from "../core.js";
import { lineChart, seriesLegend, PALETTE } from "../charts.js";
import { conversation, KINDS } from "./support.js";
import { eventForm } from "./events.js";
import { banForm } from "../moderation.js";
import { refreshUnread, refreshUser } from "../app.js";

export async function render(view) {
  // Fanerne følger brugerens moduler (rollens standard eller individuelle rettigheder)
  const tabs = ADMIN_TABS.filter(([m]) => hasTab(m));
  if (!tabs.length) { view.append(errorBox(new Error("Du har ikke adgang til nogen admin-moduler"))); return; }
  let tab = params().tab || tabs[0][0];
  if (!tabs.some(t => t[0] === tab)) tab = tabs[0][0];
  const body = h("div", {});
  view.append(h("div", { class: "page-head" }, h("h1", {}, adminLink().long), h("span", { class: "spacer" }),
    seg(tabs, tab, (v) => { setParams({ tab: v, t: "" }); body.replaceChildren(); stop(); draw(v); })), body);
  let timers = [];
  const stop = () => { timers.forEach(clearInterval); timers = []; };
  const every = (fn, ms) => { fn(); timers.push(setInterval(fn, ms)); };
  const draw = (t) => ({
    drift: () => {
      const jobsBox = h("div", { class: "card stack" }), uploadBox = h("div", { class: "card stack mt" }), opsBox = h("div", { class: "mt" });
      body.append(jobsBox, uploadBox, opsBox);
      archiveUpload(uploadBox);
      every(() => jobsCard(jobsBox), 3000);
      every(() => { opsBox.replaceChildren(); ops(opsBox); }, 15000);
    },
    data: () => dataPanel(body, "dof", (f) => every(f, 20000)),
    vejr: () => dataPanel(body, "weather", (f) => every(f, 5000)),
    aktivitet: () => activityPanel(body), support: () => supportPanel(body),
    moderation: () => moderation(body), ture: () => portalEvents(body), kategorier: () => categoriesPanel(body),
    brugere: () => users(body), roller: () => rolesPanel(body), arter: () => species(body), indhold: () => content(body),
    funktioner: () => featuresPanel(body),
  }[t] || ops)();
  draw(tab);
  return stop;
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
        h("div", { class: "row between" },
          h("p", { class: "muted", style: { margin: 0 } }, `Kald i dag: ${g[`calls:${today}`] || 0} · i kø: ${g.queue_length} · opslag i dag: ${g[`lookups:${today}`] || 0}${g.lookup_queue || g.lookups_running ? ` (${(g.lookup_queue || 0) + (g.lookups_running || 0)} i gang)` : ""}`, g.queue_length > d.queue.length ? ` (viser de første ${d.queue.length})` : ""),
          g.queue_length ? h("button", { class: "btn sm ghost danger", onclick: async () => {
            if (!confirm(`Fjern alle ${g.queue_length} ventende kald til DOFbasen? Kaldet, der kører nu, gøres færdigt, og login-forsøg bevares.`)) return;
            try { const r = await api("/api/admin/gateway/queue", { method: "DELETE" }); toast(`${r.removed} job fjernet fra køen`); body.replaceChildren(); ops(body); } catch (err) { toast(err.message, "err"); }
          } }, "Tøm kø") : null),
        g.running ? h("p", {}, h("span", { class: "tag local" }, "kører"), " ", h("b", {}, g.running.kind), " ", h("span", { class: "muted" }, JSON.stringify(g.running.params))) : null,
        setting("live_enabled", "Live-hentning af dagens observationer"),
        enqueueForm(),
        d.queue.length ? h("div", { class: "table-wrap", style: { maxHeight: "260px" } }, h("table", { class: "data" }, h("tbody", {}, ...d.queue.map(j => h("tr", {},
          h("td", {}, j.kind), h("td", { class: "muted" }, JSON.stringify(j.params)), h("td", { class: "muted" }, `p${j.priority}`),
          h("td", { class: "right" }, h("button", { class: "btn sm ghost danger", onclick: async () => { await api(`/api/admin/gateway/jobs/${j.id}`, { method: "DELETE" }); body.replaceChildren(); ops(body); } }, "Annullér"))))))) : h("p", { class: "muted" }, "Køen er tom.")),
      h("div", { class: "card stack" }, h("h3", {}, "Data og beregninger"),
        h("p", {}, `Observationer fra ${fmtDate(c.first_date)} til ${fmtDate(c.last_date)}.`),
        h("p", {}, "Arkiv: ", `${a.files} filer`, a.pending.length ? `, ${a.pending.length} nye/ændrede` : ", alt importeret",
          a.progress?.status ? ` · import: ${a.progress.status} ${a.progress.files_done || 0}/${a.progress.files_total || "?"} filer, ${fmtN(Number(a.progress.rows_total || 0))} rækker` : ""),
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

// ---------------------------------------------------------------- upload af månedsfiler (gentegnes ikke af 15 s-opdateringen)

const FILE_RE = /^dofbasen_\d{4}_\d{2}\.csv$/;
const fmtMB = (b) => `${(b / 1e6).toLocaleString("da-DK", { maximumFractionDigits: 1 })} MB`;

/** POST med upload-fremdrift (fetch kan ikke rapportere hvor meget der er sendt). */
function uploadFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);
    form.append("mtime", String(file.lastModified / 1000));
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/admin/archive/upload");
    xhr.setRequestHeader("X-Requested-With", "fetch");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* ikke JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(data?.detail || `${file.name}: fejl ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error(`${file.name}: forbindelsen blev afbrudt`));
    xhr.send(form);
  });
}

async function archiveUpload(box) {
  const input = h("input", { type: "file", accept: ".csv", multiple: true, class: "hide" });
  const drop = h("label", { class: "dropzone", onclick: () => input.click() },
    h("b", {}, "Vælg eller træk månedsfiler hertil"), h("span", { class: "muted" }, "dofbasen_ÅÅÅÅ_MM.csv – flere ad gangen"));
  const status = h("div", { class: "stack" });
  const list = h("div", {});
  let busy = false;

  const loadList = async () => {
    let files;
    try { files = await api("/api/admin/archive/files"); } catch (err) { list.replaceChildren(errorBox(err)); return; }
    const uploaded = files.filter(f => f.uploaded), pending = files.filter(f => f.pending);
    list.replaceChildren(h("details", {},
      h("summary", {}, `${files.length} filer i arkivet · ${uploaded.length ? `${uploaded.length} uploadede venter på import` : "ingen uploadede filer venter"} · ${pending.length ? `${pending.length} venter i alt` : "alt importeret"}`),
      h("div", { class: "table-wrap mt", style: { maxHeight: "320px" } }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Fil"), h("th", { class: "right" }, "Størrelse"), h("th", { class: "right" }, "Rækker"), h("th", {}, "Status"), h("th", {}))),
        h("tbody", {}, ...[...files].reverse().map(f => h("tr", {},
          h("td", { class: "nowrap" }, f.name), h("td", { class: "right num" }, fmtMB(f.size)), h("td", { class: "right num" }, fmtN(f.rows)),
          h("td", {}, f.pending ? h("span", { class: "tag bemaerk" }, "venter") : h("span", { class: "tag plain" }, "importeret"), " ",
            h("span", { class: "muted", style: { fontSize: ".8rem" } }, f.uploaded ? "upload" : "arkiv")),
          h("td", { class: "right" }, f.uploaded ? h("button", { class: "btn sm ghost danger", html: icon("trash"), "aria-label": "Fjern", onclick: async () => {
            if (!confirm(`Fjern den uploadede ${f.name}, før den importeres?`)) return;
            try { await api(`/api/admin/archive/upload/${f.name}`, { method: "DELETE" }); loadList(); } catch (err) { toast(err.message, "err"); }
          } }) : null))))))));
  };

  const run = async (all) => {
    if (busy) return;
    const files = [...all].filter(f => f.name.endsWith(".csv"));
    const bad = files.filter(f => !FILE_RE.test(f.name));
    const good = files.filter(f => FILE_RE.test(f.name)).sort((a, b) => a.name.localeCompare(b.name));
    if (!good.length) { toast(bad.length ? "Filnavnene skal være dofbasen_ÅÅÅÅ_MM.csv" : "Ingen CSV-filer valgt", "err"); return; }
    busy = true; drop.classList.add("busy");
    const total = good.reduce((n, f) => n + f.size, 0);
    const bar = h("progress", { max: 1, value: 0, style: { width: "100%" } });
    const line = h("div", { class: "muted" });
    const errors = h("div", { class: "stack" }, ...bad.map(f => h("div", { class: "notice err" }, `${f.name}: springes over – navnet skal være dofbasen_ÅÅÅÅ_MM.csv`)));
    status.replaceChildren(line, bar, errors);
    let sent = 0, ok = 0, newOrChanged = 0;
    for (const [i, f] of good.entries()) {
      try {
        const r = await uploadFile(f, (frac) => {
          bar.value = (sent + frac * f.size) / total;
          line.textContent = `${i + 1}/${good.length}: ${f.name} · ${fmtMB(sent + frac * f.size)} af ${fmtMB(total)}`;
        });
        ok++; if (r?.pending) newOrChanged++;
      } catch (err) { errors.append(h("div", { class: "notice err" }, err.message)); }
      sent += f.size; bar.value = sent / total;
    }
    line.textContent = `${ok} af ${good.length} filer uploadet${newOrChanged ? ` – ${newOrChanged} nye/ændrede importeres nu og slettes bagefter` : ok ? " – de var allerede importeret og er slettet igen" : ""}.`;
    if (newOrChanged) {
      try { await api("/api/admin/worker/archive_import", { method: "POST", body: {} }); } catch (err) { errors.append(errorBox(err)); }
    }
    busy = false; drop.classList.remove("busy"); input.value = "";
    loadList();
  };

  input.onchange = () => run(input.files);
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); run(e.dataTransfer.files); };

  box.append(h("h3", { style: { margin: 0 } }, "Upload af DOFbasen-månedsfiler"),
    h("p", { class: "muted", style: { margin: 0 } }, "Månedsudtræk (dofbasen_ÅÅÅÅ_MM.csv) importeres direkte uden kald til dofbasen.dk. Dage der er dækket af en fil, hentes ikke for at fylde huller. Uploadede filer slettes fra serveren, så snart de er importeret; filer der allerede er importeret uændret, slettes med det samme."),
    drop, input, status, list);
  await loadList();
}

// ---------------------------------------------------------------- worker-opgaver (status opdateres hvert 3. sekund)

const RUNNABLE = ["compute", "archive_import", "aggregates", "climate", "events", "forecast", "regions", "gaps", "location_years"];
const JOB_STATUS = { idle: ["plain", "ikke kørt"], queued: ["bemaerk", "i kø"], running: ["local", "kører"], done: ["plain", "ok"], error: ["SU", "fejl"], interrupted: ["SUB", "afbrudt"] };
const jobTag = (status) => { const [k, l] = JOB_STATUS[status] || ["plain", status]; return h("span", { class: `tag ${k}` }, l); };
const dur = (s) => s == null ? "–" : s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} t`;

async function jobsCard(box) {
  let d;
  try { d = await api("/api/admin/jobs"); } catch (err) { box.replaceChildren(errorBox(err)); return; }
  const histOpen = box.querySelector("details")?.open;
  const run = async (j) => {
    try { await api(`/api/admin/worker/${j.name}`, { method: "POST", body: {} }); toast(`${j.label}: lagt i kø`); jobsCard(box); } catch (err) { toast(err.message, "err"); }
  };
  const progress = (j) => {
    if (j.status === "running") {
      const pct = j.total ? Math.round(100 * (j.done || 0) / j.total) : null;
      return [h("div", {}, j.step || "Starter…", pct != null ? h("span", { class: "muted" }, ` · ${j.done}/${j.total} (${pct} %)`) : null,
        h("span", { class: "muted" }, ` · startet ${ago(j.started_at)}`)),
        pct != null ? h("div", { class: "progress", style: { marginTop: "5px" } }, h("i", { style: { width: `${pct}%` } })) : null];
    }
    if (j.status === "queued") return h("span", { class: "muted" }, `Venter på worker (lagt i kø ${ago(j.queued_at)})`);
    if (j.status === "interrupted") return h("span", { class: "muted" }, `Afbrudt ved genstart${j.step ? ` under: ${j.step}` : ""}`);
    const last = j.last;
    if (!last) return h("span", { class: "muted" }, "–");
    return [h("span", { class: "muted" }, `Sidst ${ago(last.finished_at)} · ${dur(last.duration)}`),
      last.error ? h("div", { style: { color: "var(--err)" } }, last.error) : null];
  };
  const jobs = d.jobs.filter(j => j.status !== "idle" || RUNNABLE.includes(j.name));
  box.replaceChildren(h("h3", {}, "Worker-opgaver"),
    h("p", { class: "muted" }, "Workeren kører kommandoer én ad gangen; en opgave i kø venter på den der kører."),
    h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, h("th", {}, "Opgave"), h("th", {}, "Status"), h("th", {}, "Fremdrift / seneste kørsel"), h("th", {}))),
      h("tbody", {}, ...jobs.map(j => h("tr", {},
        h("td", { class: "nowrap" }, h("b", {}, j.label)), h("td", {}, jobTag(j.status)), h("td", { style: { width: "100%" } }, progress(j)),
        h("td", { class: "right" }, RUNNABLE.includes(j.name)
          ? h("button", { class: "btn sm", disabled: ["running", "queued"].includes(j.status), onclick: () => run(j) }, "Kør nu") : null)))))),
    d.history.length ? Object.assign(h("details", { class: "more" }, h("summary", {}, `Seneste ${d.history.length} kørsler`),
      h("div", { class: "table-wrap mt" }, h("table", { class: "data" }, h("tbody", {}, ...d.history.map(x => h("tr", {},
        h("td", { class: "nowrap" }, fmtDate(x.finished_at, false), " ", fmtTime(x.finished_at)),
        h("td", {}, x.label), h("td", {}, jobTag(x.status)), h("td", { class: "right num" }, dur(x.duration)),
        h("td", { style: { color: "var(--err)" } }, x.error || ""))))))), { open: !!histOpen }) : null);
}

async function users(body) {
  const q = h("input", { type: "search", placeholder: "Søg obserkode/navn…", style: { maxWidth: "280px" } });
  const out = h("div", { class: "card table-wrap mt" });
  const pendingBox = h("div", {});
  let meta;
  try { meta = await api("/api/admin/modules"); } catch (err) { body.append(errorBox(err)); return; }
  const approvalSwitch = h("input", { type: "checkbox", checked: meta.require_approval, onchange: async (e) => {
    try { await api("/api/admin/settings", { method: "PUT", body: { require_approval: e.target.checked } }); toast(e.target.checked ? "Nye brugere skal godkendes" : "Nye brugere får adgang med det samme"); }
    catch (err) { e.target.checked = !e.target.checked; toast(err.message, "err"); }
  } });
  body.append(
    h("div", { class: "card stack", style: { marginBottom: "12px" } },
      h("label", { class: "check" }, approvalSwitch, h("span", {}, h("b", {}, "Nye brugere skal godkendes"),
        h("span", { class: "muted" }, " – en ny bruger oprettes, når koden er gyldig hos DOFbasen, men får først adgang, når en administrator har godkendt kontoen. Er den slået fra, får nye brugere adgang med det samme."))),
      pendingBox),
    h("p", { class: "muted", style: { marginTop: 0 } }, "Rollen giver et standardsæt af admin-moduler. Under Rettigheder kan den enkelte bruger i stedet få præcis de moduler, der er krydset af."), q, out);
  const isSuper = state.user.role === "superadmin";
  const editable = (u) => u.id !== state.user.id && u.role !== "superadmin" && (isSuper || !["admin", "superadmin"].includes(u.role));
  const approve = async (u) => {
    try { await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { approved: true } }); toast(`${u.navn || u.obserkode} er godkendt`); load(); }
    catch (err) { toast(err.message, "err"); }
  };
  const load = async () => {
    const rows = await api(`/api/admin/users?q=${encodeURIComponent(q.value)}`);
    const waiting = rows.filter(u => !u.approved);
    pendingBox.replaceChildren(waiting.length ? h("div", { class: "notice warn stack", style: { gap: "6px" } },
      h("b", {}, `${waiting.length} ${waiting.length === 1 ? "bruger afventer" : "brugere afventer"} godkendelse`),
      ...waiting.map(u => h("div", { class: "row between" },
        h("span", {}, h("b", {}, u.navn || u.obserkode), h("small", { class: "muted" }, ` ${u.obserkode} · oprettet ${ago(u.created_at)}`)),
        h("span", { class: "row", style: { gap: "6px" } },
          h("button", { class: "btn sm primary", onclick: () => approve(u) }, "Godkend"),
          h("button", { class: "btn sm ghost danger", onclick: async () => { if (confirm(`Afvis og slet ${u.obserkode}? Personen kan søge igen ved at logge ind.`)) { try { await api(`/api/admin/users/${u.id}`, { method: "DELETE" }); toast("Afvist"); load(); } catch (err) { toast(err.message, "err"); } } } }, "Afvis"))))) : h("small", { class: "muted" }, "Ingen venter på godkendelse."));
    out.replaceChildren(h("table", { class: "data stack-sm", style: { "--cols": 3 } },
      h("thead", {}, h("tr", {}, h("th", {}, "Bruger"), h("th", {}, "Rolle"), h("th", {}, "Admin-moduler"), h("th", {}, "Sidst set"), h("th", { class: "right" }, "Enheder/regler"), h("th", {}))),
      h("tbody", {}, ...rows.map(u => h("tr", {},
        h("td", {}, h("b", {}, u.navn || u.obserkode), u.approved ? null : h("span", { class: "tag SUB", style: { marginLeft: "6px" } }, "afventer"),
          h("div", { class: "muted" }, u.obserkode, u.blocked ? " · spærret" : "")),
        h("td", { class: "wide", "data-label": "Rolle" }, roleSelect(u, meta.role_names, load)),
        h("td", { "data-label": "Moduler" },
          h("span", { title: u.modules.map(m => meta.modules[m]).join("\n") || "Ingen" },
            u.role === "superadmin" ? "alle" : u.modules.length ? `${u.modules.length} af ${Object.keys(meta.modules).length}` : "ingen"),
          u.permissions ? h("span", { class: "tag plain", style: { marginLeft: "6px" }, title: "Individuelle rettigheder i stedet for rollens standard" }, "individuel") : null,
          editable(u) ? h("button", { class: "btn sm ghost", style: { marginLeft: "6px" }, onclick: () => permissionsModal(u, meta, load) }, "Rettigheder") : null),
        h("td", { class: "muted", "data-label": "Sidst set" }, u.last_seen ? ago(u.last_seen) : "aldrig"),
        h("td", { class: "right num", "data-label": "Enh./regler" }, `${u.devices} / ${u.rules}`),
        h("td", { class: "right nowrap wide" }, u.role !== "superadmin" ? [
          u.approved ? null : h("button", { class: "btn sm primary", onclick: () => approve(u) }, "Godkend"),
          h("button", { class: "btn sm", onclick: async () => { await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { blocked: !u.blocked } }); load(); } }, u.blocked ? "Ophæv spærring" : "Spær"),
          isSuper ? h("button", { class: "btn sm ghost danger", onclick: async () => { if (confirm(`Slet ${u.obserkode}?`)) { await api(`/api/admin/users/${u.id}`, { method: "DELETE" }); load(); } } }, "Slet") : null] : null))))));
  };
  let t; q.oninput = () => { clearTimeout(t); t = setTimeout(load, 250); };
  await load();
}

/** Rollevalg for en bruger. Superadmin (fra .env) kan ikke ændres og kan ikke tildeles. */
function roleSelect(u, names, onDone) {
  if (u.role === "superadmin") return h("span", { class: "tag plain", title: "Styres fra .env" }, "Superadmin");
  return h("select", { style: { width: "auto", height: "32px" }, disabled: u.id === state.user.id, onchange: async (e) => {
    try { await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { role: e.target.value } }); toast("Rolle ændret"); } catch (err) { toast(err.message, "err"); }
    onDone();
  } }, ...Object.entries(names).map(([k, n]) => h("option", { value: k, selected: k === u.role }, n)),
    names[u.role] ? null : h("option", { value: u.role, selected: true }, u.role));
}

/** Funktioner (kun superadmin): nye moduler kan slås fra, vises som forhåndsvisning eller slås til for alle. */
async function featuresPanel(body) {
  let rows;
  try { rows = await api("/api/admin/features"); } catch (err) { body.replaceChildren(errorBox(err)); return; }
  const STATES = [["off", "Fra"], ["preview", "Forhåndsvisning"], ["on", "Til"]];
  const set = async (key, v) => {
    try { await api("/api/admin/features", { method: "PUT", body: { [key]: v } }); toast("Gemt"); await refreshUser(); } catch (err) { toast(err.message, "err"); }
    featuresPanel(body);
  };
  body.replaceChildren(
    h("p", { class: "muted" }, "Fra: slået fra for alle (også API'et). Forhåndsvisning: kun superadmin og brugere med en rolle i modulet (DOFbasen- eller Netfugl-rolle). Til: alle indloggede. En underfunktion virker kun, når forælderen også er synlig."),
    h("div", { class: "card table-wrap" }, h("table", { class: "data stack-sm", style: { "--cols": 2 } },
      h("thead", {}, h("tr", {}, h("th", {}, "Funktion"), h("th", {}, "Tilstand"), h("th", {}, "Beskrivelse"))),
      h("tbody", {}, ...rows.map(f => h("tr", {},
        h("td", { style: { paddingLeft: f.parent ? "26px" : undefined } }, h("b", {}, f.name),
          h("div", { class: "muted", style: { fontSize: ".78rem" } }, `standard: ${STATES.find(x => x[0] === f.default)[1].toLowerCase()}`)),
        h("td", { "data-label": "Tilstand" }, seg(STATES, f.state, (v) => set(f.key, v))),
        h("td", { class: "muted", "data-label": "Beskrivelse" }, f.description)))))),
    h("p", { class: "mt" }, h("a", { class: "btn", href: "#/dofbasen" }, "Åbn DOFbasen →")));
}

/** Roller (kun superadmin): rollerne vandret, modulerne lodret – og nederst alle med en tildelt rolle. */
async function rolesPanel(body) {
  let d;
  const load = async () => {
    try { d = await api("/api/admin/roles"); } catch (err) { body.replaceChildren(errorBox(err)); return; }
    draw();
  };
  const patch = async (key, payload, msg) => {
    try { await api(`/api/admin/roles/${key}`, { method: "PATCH", body: payload }); toast(msg); } catch (err) { toast(err.message, "err"); }
    load();
  };
  const addRole = async () => {
    const name = prompt("Navn på den nye rolle:");
    if (!name?.trim()) return;
    try { await api("/api/admin/roles", { method: "POST", body: { name: name.trim(), modules: [] } }); toast(`Rollen ${name.trim()} er oprettet`); } catch (err) { toast(err.message, "err"); }
    load();
  };
  const removeRole = async (r) => {
    if (!confirm(`Slet rollen ${r.name}?${r.users ? ` ${r.users} ${r.users === 1 ? "bruger" : "brugere"} med rollen bliver almindelige brugere.` : ""}`)) return;
    try { await api(`/api/admin/roles/${r.key}`, { method: "DELETE" }); toast(`Rollen ${r.name} er slettet`); } catch (err) { toast(err.message, "err"); }
    load();
  };
  const draw = () => {
    const names = Object.fromEntries(d.roles.map(r => [r.key, r.name]));
    const head = h("tr", {}, h("th", { class: "roles-mod" }, "Modul"),
      ...d.roles.map(r => h("th", { class: "roles-col" },
        h("input", { class: "roles-name", value: r.name, maxlength: 60, "aria-label": "Rollens navn",
          onchange: (e) => e.target.value.trim() && e.target.value.trim() !== r.name ? patch(r.key, { name: e.target.value.trim() }, "Navnet er gemt") : (e.target.value = r.name) }),
        h("small", { class: "muted" }, `${fmtN(r.users)} ${r.users === 1 ? "bruger" : "brugere"}`),
        r.fixed ? h("small", { class: "muted", title: r.locked ? "Almindelige brugere har ingen admin-moduler" : "Administratorer har særlige rettigheder i koden og kan ikke slettes" }, "fast rolle")
          : h("button", { class: "btn sm ghost danger", type: "button", onclick: () => removeRole(r) }, "Slet"))),
      h("th", { class: "roles-col" }, h("b", {}, "Superadmin"), h("small", { class: "muted" }, d.superadmin.obserkode ? `${d.superadmin.obserkode} · fra .env` : "fra .env"), h("small", { class: "muted" }, "alle moduler")));
    const rows = Object.entries(d.modules).map(([m, label]) => {
      const [name, rest] = label.split(": ");
      return h("tr", {}, h("th", { class: "roles-mod", scope: "row" }, h("b", {}, name), rest ? h("small", { class: "muted", style: { display: "block" } }, rest) : null),
        ...d.roles.map(r => h("td", { class: "roles-cell" }, h("input", { type: "checkbox", checked: r.modules.includes(m), disabled: r.locked, "aria-label": `${r.name}: ${name}`,
          onchange: (e) => patch(r.key, { modules: e.target.checked ? [...r.modules, m] : r.modules.filter(x => x !== m) }, `${r.name}: ${name} ${e.target.checked ? "givet" : "fjernet"}`) }))),
        h("td", { class: "roles-cell" }, h("input", { type: "checkbox", checked: true, disabled: true, "aria-label": `Superadmin: ${name}` })));
    });
    const people = d.people.map(p => h("tr", {},
      h("td", {}, h("b", {}, p.navn || p.obserkode), h("div", { class: "muted" }, p.obserkode, p.blocked ? " · spærret" : "", p.approved ? "" : " · afventer")),
      h("td", { "data-label": "Rolle" }, roleSelect(p, names, load)),
      h("td", { "data-label": "Moduler" }, h("span", { title: p.modules.map(m => d.modules[m]).join("\n") || "Ingen" },
        p.role === "superadmin" ? "alle" : p.modules.length ? `${p.modules.length} af ${Object.keys(d.modules).length}` : "ingen"),
        p.permissions ? h("span", { class: "tag plain", style: { marginLeft: "6px" }, title: "Individuelle rettigheder i stedet for rollens standard (ændres under Brugere)" }, "individuel") : null),
      h("td", { class: "muted", "data-label": "Sidst set" }, p.last_seen ? ago(p.last_seen) : "aldrig")));
    body.replaceChildren(
      h("div", { class: "row between", style: { marginBottom: "12px", alignItems: "flex-start" } },
        h("p", { class: "muted", style: { margin: 0, flex: "1 1 320px" } }, "Hver rolle giver et sæt admin-moduler. Ændringer gælder med det samme for alle med rollen (undtagen brugere med individuelle rettigheder, som sættes under Brugere). Superadmin har altid alle moduler og styres fra .env."),
        h("button", { class: "btn primary", type: "button", onclick: addRole, html: `${icon("plus")} Ny rolle` })),
      h("div", { class: "card table-wrap" }, h("table", { class: "data roles-table" }, h("thead", {}, head), h("tbody", {}, ...rows))),
      h("h2", { class: "mt" }, "Brugere med roller"),
      h("div", { class: "card table-wrap" }, d.people.length ? h("table", { class: "data stack-sm", style: { "--cols": 2 } },
        h("thead", {}, h("tr", {}, h("th", {}, "Bruger"), h("th", {}, "Rolle"), h("th", {}, "Admin-moduler"), h("th", {}, "Sidst set"))),
        h("tbody", {}, ...people)) : empty("Ingen har roller endnu", "Giv en bruger en rolle under Brugere.")));
  };
  await load();
}

/** Afkrydsningsliste over admin-moduler for én bruger. Moduler man ikke selv har, kan ikke ændres. */
function permissionsModal(u, meta, onSaved) {
  const roleDefault = new Set(meta.roles[u.role] || []);
  const grantable = new Set(meta.grantable);
  let custom = !!u.permissions;
  const boxes = Object.fromEntries(Object.keys(meta.modules).map(m => [m, h("input", { type: "checkbox", checked: u.modules.includes(m) })]));
  const list = h("div", { class: "stack", style: { gap: "6px" } });
  const mode = h("input", { type: "checkbox", checked: custom });
  const draw = () => {
    for (const [m, b] of Object.entries(boxes)) {
      if (!custom) b.checked = roleDefault.has(m);
      b.disabled = !custom || !grantable.has(m);
    }
    list.replaceChildren(...Object.entries(meta.modules).map(([m, label]) => {
      const [name, rest] = label.split(": ");
      return h("label", { class: "check", style: { opacity: grantable.has(m) ? 1 : 0.6 }, title: grantable.has(m) ? "" : "Du har ikke selv adgang til modulet" },
        boxes[m], h("span", {}, h("b", {}, name), rest ? h("span", { class: "muted" }, ` – ${rest}`) : null,
          roleDefault.has(m) ? h("small", { class: "muted" }, ` · ${u.role}`) : null));
    }));
  };
  mode.onchange = () => { custom = mode.checked; draw(); };
  draw();
  const dlg = modal(`Rettigheder: ${u.navn || u.obserkode}`, h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    const permissions = custom ? Object.keys(boxes).filter(m => boxes[m].checked) : null;
    try {
      await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { permissions } });
      toast(custom ? "Rettighederne er gemt" : `Følger nu rollens standard (${u.role})`); dlg.close(); onSaved();
    } catch (err) { toast(err.message, "err"); }
  } },
    h("label", { class: "check" }, mode, h("span", {}, h("b", {}, "Individuelle rettigheder"), h("span", { class: "muted" }, ` – ellers følger brugeren rollens standard (${u.role})`))),
    list,
    h("small", { class: "muted" }, "Rettighederne gælder både faner og funktioner (fx officielle Mobgang-presets og kurser i Læring). Individuelle rettigheder bevares, hvis rollen ændres."),
    h("div", { class: "row" }, h("button", { class: "btn primary" }, "Gem"), h("button", { type: "button", class: "btn ghost", onclick: () => dlg.close() }, "Annullér"))));
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
  body.append(h("form", { class: "card stack", style: { maxWidth: "720px" }, onsubmit: async (e) => { e.preventDefault(); try { await api("/api/admin/news", { method: "POST", body: { title: title.value, body: text.value } }); title.value = text.value = ""; load(); toast("Publiceret"); } catch (err) { toast(err.message, "err"); } } },
    h("h3", {}, "Ny nyhed"), title, text, h("button", { class: "btn primary" }, "Publicér"), news));
  const load = async () => {
    const n = await api("/api/news?limit=20");
    news.replaceChildren(...n.map(x => h("div", { class: "row between" }, h("span", {}, h("b", {}, x.title), h("small", { class: "muted" }, ` ${fmtDate(x.created_at)}`)),
      h("button", { class: "btn sm ghost danger", onclick: async () => { await api(`/api/admin/news/${x.id}`, { method: "DELETE" }); load(); } }, "Slet"))));
  };
  await load();
}

// ---------------------------------------------------------------- moderation af kommentarer (moderator og op)

async function moderation(body) {
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
      location.hash = `#/admin?tab=moderation&vis=beskeder&t=${r.thread}`;
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

async function portalEvents(body) {
  const list = h("div", { class: "stack" });
  body.append(h("div", { class: "card stack" },
    h("div", { class: "row between" }, h("p", { class: "muted", style: { margin: 0 } }, "Ture og begivenheder oprettet i portalen. De vises under Ture sammen med DOF's officielle ture."),
      h("button", { class: "btn primary", html: `${icon("plus")} Ny tur`, onclick: () => eventForm(null, () => load()) })), list));
  const load = async () => {
    const rows = (await api("/api/events?past=true")).filter(e => e.source === "portal").sort((a, b) => b.dato.localeCompare(a.dato));
    list.replaceChildren(...(rows.length ? rows.map(e => h("div", { class: "mod-item" },
      h("div", { style: { minWidth: 0 } }, h("a", { href: `#/ture/${e.id}` }, h("b", {}, e.title)),
        h("div", { class: "muted", style: { fontSize: ".86rem" } }, [fmtDate(e.dato), e.tid_fra, e.lok, e.afdeling, e.created_by_navn && `oprettet af ${e.created_by_navn}`].filter(Boolean).join(" · "))),
      h("div", { class: "row", style: { gap: "4px", flexWrap: "nowrap" } },
        h("button", { class: "btn sm ghost", html: icon("edit"), "aria-label": "Ret", onclick: async () => eventForm(await api(`/api/events/${e.id}`), () => load()) }),
        h("button", { class: "btn sm ghost danger", html: icon("trash"), "aria-label": "Slet", onclick: async () => {
          if (!confirm(`Slet "${e.title}"?`)) return; await api(`/api/events/${e.id}`, { method: "DELETE" }); load();
        } })))) : [empty("Ingen egne ture endnu")]));
  };
  await load();
}

// ---------------------------------------------------------------- listekategorier (moderator og op)

const COUNTABLE = ["A", "B", "C"];
const katTag = (k, title) => h("span", { class: `tag ${COUNTABLE.includes(k) ? "plain" : "SUB"}`, title: title || (COUNTABLE.includes(k) ? "Krydsbar" : "Ikke krydsbar") }, `Kat. ${k}`);
const SOURCE_TXT = { portal: "sat i portalen", dofbasen: "DOFbasen har [klammer] om navnet", standard: "standard" };

async function categoriesPanel(body) {
  const q = h("input", { type: "search", placeholder: "Søg art…", value: params().q || "" });
  const list = h("div", { class: "stack", style: { maxHeight: "70vh", overflow: "auto" } });
  const detail = h("div", { class: "stack" });
  const listBox = h("details", { class: "card more", style: { marginBottom: "12px" } },
    h("summary", {}, "Den Danske Liste (Netfugl) – læg kategorierne ind"));
  listBox.append(h("div", { class: "stack mt" }, h("p", { class: "muted", style: { margin: 0 } }, "Sammenligner portalens arter med Netfugls DK-liste (www.netfugl.dk/artslister/dk: kategori A–C; D, E og F supplerer vi fra Sjældenhedsudvalgets PDF, da Netfugl ikke viser dem). Se først hvad en import vil ændre, og kør den bagefter. Rækker I selv har lavet, røres ikke, og afløste rækker bevares i historikken."),
    h("button", { class: "btn", style: { alignSelf: "flex-start" }, onclick: async (e) => {
      const btn = e.currentTarget; btn.disabled = true;
      try { renderListReport(listBox, await api("/api/categories/danish-list"), () => { loadList(); loadDetail(); }); }
      catch (err) { toast(err.message, "err"); } finally { btn.disabled = false; }
    } }, "Se hvad der ændres")));
  const findsBox = h("details", { class: "card more", style: { marginBottom: "12px" } }, h("summary", {}, "Manuelle fund – fund der ikke står i DOFbasen"));
  findsBox.addEventListener("toggle", () => { if (findsBox.open && !findsBox.dataset.loaded) { findsBox.dataset.loaded = "1"; manualFinds(findsBox); } });
  body.append(
    listBox, findsBox,
    h("p", { class: "muted", style: { marginTop: 0 } }, "Kun kategori A, B og C er krydsbare på årslisterne (D, E og F er ikke). Kategorierne følger Den Danske Liste (A–C fra Netfugl, D–F fra Sjældenhedsudvalgets PDF). En arts kategori kan skifte over tid (gyldig fra en dato), og en serie af observationer på én lokalitet kan overstyres – fx et godkendt A-fund af en D-art. Ændringer slettes aldrig; tidligere værdier ses i historikken."),
    h("div", { class: "cat-layout" },
      h("div", { class: "card stack" }, h("div", { class: "row", style: { flexWrap: "nowrap" } }, q,
        h("button", { class: "btn", html: `${icon("plus")} Opret art`, title: "Opret en art, der ikke findes i listen", onclick: () => newSpeciesForm(q.value, (artnr) => { q.value = ""; setParams({ art: artnr, y: "" }); loadList(); loadDetail(); }) })), list), detail));
  const loadList = async () => {
    setParams({ q: q.value });
    const rows = await api(`/api/categories/species?q=${encodeURIComponent(q.value)}`);
    list.replaceChildren(
      !q.value ? h("small", { class: "muted" }, "Arter der ikke er kategori A, eller som har overstyrede serier. Søg for at finde andre.") : null,
      ...(rows.length ? rows.map(s => h("a", { href: "#", class: "row between", style: { gap: "6px" }, onclick: (e) => { e.preventDefault(); setParams({ art: s.artnr, y: s.kategori === "A" && !s.n_overrides ? new Date().getFullYear() : "" }); loadDetail(); } },
        h("span", {}, s.navn, s.n_overrides ? h("small", { class: "muted" }, ` · ${s.n_overrides} serie${s.n_overrides === 1 ? "" : "r"}`) : null), katTag(s.kategori))) : [empty("Ingen arter")]));
  };
  const loadDetail = async () => {
    const p = params();
    if (!p.art) { detail.replaceChildren(h("div", { class: "card" }, empty("Vælg en art", "Søg til venstre"))); return; }
    detail.replaceChildren(h("div", { class: "skeleton", style: { height: "200px" } }));
    let d;
    try { d = await api(`/api/categories/species/${p.art}${p.y ? `?year=${p.y}` : ""}`); } catch (err) { detail.replaceChildren(errorBox(err)); return; }
    const reload = () => { loadDetail(); loadList(); };
    const katSelect = (value, extra = []) => h("select", { style: { width: "auto" } }, ...extra, ...Object.entries(d.categories).map(([k, t]) => h("option", { value: k, selected: k === value }, `${k} – ${t}`)));
    const who = (r) => [r.created_by && `af ${r.created_by}`, fmtDate(r.created_at)].filter(Boolean).join(" ");
    const removed = (r) => r.removed_at ? h("small", { class: "muted" }, ` · fjernet ${fmtDate(r.removed_at)}${r.removed_by ? ` af ${r.removed_by}` : ""}`) : null;

    // Artens kategori over tid
    const kat = katSelect(d.current.kategori), from = h("input", { type: "date" }), note = h("input", { placeholder: "Note / kilde (valgfri)", maxlength: 1000 });
    const active = d.periods.filter(r => !r.removed_at);
    const periodRow = (r) => h("div", { class: `mod-item ${r.removed_at ? "hidden" : ""}` },
      h("div", {}, katTag(r.kategori), " ", h("b", {}, r.valid_from ? `fra ${fmtDate(r.valid_from)}` : "altid"),
        r.note ? h("div", { style: { fontSize: ".9rem" } }, r.note) : null, h("small", { class: "muted" }, who(r)), removed(r)),
      r.removed_at ? null : h("button", { class: "btn sm ghost danger", onclick: async () => {
        if (!confirm("Fjern kategorien? Den bevares i historikken.")) return;
        await api(`/api/categories/periods/${r.id}`, { method: "DELETE" }); toast("Fjernet"); reload();
      } }, "Fjern"));
    const periodsCard = h("div", { class: "card stack" },
      h("div", { class: "row between" }, h("h2", { style: { margin: 0 } }, d.navn, d.latin ? h("i", { class: "muted", style: { fontSize: "1rem", fontWeight: 400 } }, ` ${d.latin}`) : null),
        h("span", {}, katTag(d.current.kategori), h("small", { class: "muted" }, ` i dag (${SOURCE_TXT[d.current.source]})`))),
      h("h3", { style: { margin: 0 } }, "Artens kategori over tid"),
      ...(active.length ? active.map(periodRow) : [h("p", { class: "muted", style: { margin: 0 } }, d.current.source === "dofbasen" ? "Ingen kategori sat i portalen – arten tæller ikke, fordi DOFbasen har [klammer] om navnet." : "Ingen kategori sat – arten er kategori A.")]),
      h("form", { class: "row", onsubmit: async (e) => {
        e.preventDefault();
        try { await api(`/api/categories/species/${d.artnr}/periods`, { method: "POST", body: { kategori: kat.value, valid_from: from.value || null, note: note.value } }); toast("Gemt"); reload(); }
        catch (err) { toast(err.message, "err"); }
      } }, kat, h("label", { class: "row", style: { gap: "4px" } }, h("small", { class: "muted" }, "gyldig fra"), from), note, h("button", { class: "btn primary" }, "Tilføj")),
      h("small", { class: "muted" }, "Uden dato gælder kategorien altid. Med dato gælder den fra datoen, indtil en senere kategori tager over – sådan registreres en art der skifter kategori."),
      d.periods.some(r => r.removed_at) ? h("details", { class: "more" }, h("summary", {}, "Historik"), h("div", { class: "stack mt" }, ...d.periods.filter(r => r.removed_at).map(periodRow))) : null);

    // Serier: lokalitet × art
    const year = h("select", { style: { width: "auto" }, onchange: () => { setParams({ y: year.value }); loadDetail(); } },
      h("option", { value: "" }, "Alle år"), ...d.years.map(y => h("option", { value: y, selected: String(y) === String(d.year) }, y)));
    const setSeries = async (sr, k) => {
      const n = prompt(`Sæt kategori ${k} for ${d.navn} på ${sr.lok || sr.loknr} ${fmtDate(sr.first)}–${fmtDate(sr.last)}.\nNote / kilde (fx DKU-godkendelse):`, "");
      if (n === null) return false;
      try {
        await api("/api/categories/overrides", { method: "POST", body: { artnr: d.artnr, loknr: sr.loknr, date_from: sr.first, date_to: sr.last, kategori: k, note: n } });
        toast("Gemt"); reload(); return true;
      } catch (err) { toast(err.message, "err"); return false; }
    };
    const seriesCard = h("div", { class: "card stack" },
      h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, "Serier (lokalitet × art)"), year),
      h("small", { class: "muted" }, `Observationer på samme lokalitet hører til samme serie, indtil der går mere end ${d.gap_days} dage uden fund (som ved fund-aggregeringen). Viser højst 400, nyeste først.`),
      d.series.length ? h("div", { class: "table-wrap", style: { maxHeight: "520px" } }, h("table", { class: "data stack-sm", style: { "--cols": 3 } },
        h("thead", {}, h("tr", {}, h("th", {}, "Lokalitet"), h("th", {}, "Periode"), h("th", { class: "right" }, "Dage"), h("th", { class: "right" }, "Obs."), h("th", { class: "right" }, "Maks."), h("th", {}, "Observatører"), h("th", {}, "Kategori"))),
        h("tbody", {}, ...d.series.map(sr => {
          const sel = katSelect(null, [h("option", { value: "", selected: true }, "Sæt…")]);
          sel.style.height = "30px";
          sel.onchange = async () => { if (!await setSeries(sr, sel.value)) sel.value = ""; };
          return h("tr", {},
            h("td", {}, h("a", { href: `#/lok/${sr.loknr}` }, sr.lok || sr.loknr), sr.secret ? h("small", { class: "muted" }, " (hemmelig)") : null),
            h("td", { "data-label": "Periode", class: "nowrap" }, sr.first === sr.last ? fmtDate(sr.first) : `${fmtDate(sr.first)} – ${fmtDate(sr.last)}`),
            h("td", { class: "right num", "data-label": "Dage" }, sr.n_days), h("td", { class: "right num", "data-label": "Obs." }, sr.n_obs), h("td", { class: "right num", "data-label": "Maks." }, fmtN(sr.max_antal)),
            h("td", { class: "muted", "data-label": "Observatører", style: { fontSize: ".85rem" } }, sr.observers.join(", ")),
            h("td", { "data-label": "Kategori" }, h("span", { class: "row", style: { gap: "4px", flexWrap: "nowrap" } }, ...sr.kategorier.map(k => katTag(k)), sel)));
        })))) : empty("Ingen observationer", d.year ? `i ${d.year}` : null));

    // Overstyrede serier
    const ovRow = (v) => h("div", { class: `mod-item ${v.removed_at ? "hidden" : ""}` },
      h("div", {}, katTag(v.kategori), " ", h("a", { href: `#/lok/${v.loknr}` }, v.lok || v.loknr), " ",
        h("span", { class: "nowrap" }, v.date_from === v.date_to ? fmtDate(v.date_from) : `${fmtDate(v.date_from)} – ${fmtDate(v.date_to)}`),
        v.note ? h("div", { style: { fontSize: ".9rem" } }, v.note) : null, h("small", { class: "muted" }, who(v)), removed(v)),
      v.removed_at ? null : h("button", { class: "btn sm ghost danger", onclick: async () => {
        if (!confirm("Fjern overstyringen? Serien får igen artens kategori. Overstyringen bevares i historikken.")) return;
        await api(`/api/categories/overrides/${v.id}`, { method: "DELETE" }); toast("Fjernet"); reload();
      } }, "Fjern"));
    const ovActive = d.overrides.filter(v => !v.removed_at), ovOld = d.overrides.filter(v => v.removed_at);
    const overridesCard = d.overrides.length ? h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "Overstyrede serier"),
      ...(ovActive.length ? ovActive.map(ovRow) : [h("p", { class: "muted", style: { margin: 0 } }, "Ingen aktive overstyringer.")]),
      ovOld.length ? h("details", { class: "more" }, h("summary", {}, `Historik (${ovOld.length})`), h("div", { class: "stack mt" }, ...ovOld.map(ovRow))) : null) : null;

    detail.replaceChildren(periodsCard, overridesCard, seriesCard);
  };
  let t; q.oninput = () => { clearTimeout(t); t = setTimeout(loadList, 250); };
  await Promise.all([loadList(), loadDetail()]);
}

/** Manuelle fund (fx godkendte fund til havs): tæller i Arter pr. år og på lokaliteten, men ikke på personlige lister. */
async function manualFinds(box) {
  const out = h("div", { class: "stack mt" });
  box.append(out);
  let all = [];
  const load = async () => {
    let d;
    try { d = await api("/api/categories/finds"); } catch (err) { out.replaceChildren(errorBox(err)); return; }
    out.replaceChildren(
      h("p", { class: "muted", style: { margin: 0 } }, "Fundet tæller som en almindelig observation i Arter pr. år, på lokaliteten og på artens side – men ikke på nogens personlige liste. Kategorien følger artens kategori som for andre fund. Synkroniseringen fra DOFbasen rører det ikke."),
      d.can_edit ? findForm(load) : h("small", { class: "muted" }, "Kun administratorer kan indsætte og slette fund."),
      d.items.length ? h("div", { class: "table-wrap" }, h("table", { class: "data stack-sm", style: { "--cols": 2 } },
        h("thead", {}, h("tr", {}, h("th", {}, "Dato"), h("th", {}, "Art"), h("th", { class: "right" }, "Antal"), h("th", {}, "Sted"), h("th", {}, "Note"), h("th", {}))),
        h("tbody", {}, ...d.items.map(f => h("tr", {},
          h("td", { class: "nowrap" }, fmtDate(f.dato)), h("td", {}, h("b", {}, f.art)), h("td", { class: "right num", "data-label": "Antal" }, fmtN(f.antal)),
          h("td", { "data-label": "Sted" }, f.lok ? h("a", { href: `#/lok/${f.loknr}` }, f.lok) : null, f.lok && f.sted ? " · " : null, f.sted || null),
          h("td", { class: "muted", "data-label": "Note" }, f.note || "", h("small", { style: { display: "block" } }, `${f.created_by || "Indlæst automatisk"} · ${fmtDate(f.created_at)}`)),
          h("td", { class: "right" }, d.can_edit ? h("button", { class: "btn sm ghost danger", onclick: async () => {
            if (!confirm(`Slet fundet af ${f.art} ${fmtDate(f.dato)}?`)) return;
            try { await api(`/api/categories/finds/${f.id}`, { method: "DELETE" }); toast("Fundet er slettet"); load(); } catch (err) { toast(err.message, "err"); }
          } }, "Slet") : null)))))) : h("small", { class: "muted" }, "Ingen manuelle fund endnu."));
  };
  const findForm = (onSaved) => {
    let artnr = null, loknr = null;
    const art = h("input", { type: "search", placeholder: "Søg art…", autocomplete: "off", required: true });
    const artHits = h("div", { class: "suggest" });
    const ready = api("/api/reports/species").then(l => { all = l; });
    art.addEventListener("input", () => ready.then(() => {
      artnr = null;
      artHits.replaceChildren(...searchSpecies(all, art.value).map(sp => h("button", { type: "button", onclick: () => { artnr = sp.artnr; art.value = sp.navn; artHits.replaceChildren(); } },
        h("span", {}, sp.navn), sp.latin ? h("i", { class: "muted" }, sp.latin) : null)));
    }));
    const lok = h("input", { type: "search", placeholder: "Søg lokalitet (valgfri)…", autocomplete: "off" });
    const lokHits = h("div", { class: "suggest" });
    let t;
    lok.addEventListener("input", () => {
      loknr = null; clearTimeout(t);
      if (lok.value.trim().length < 2) { lokHits.replaceChildren(); return; }
      t = setTimeout(async () => {
        const rows = await api(`/api/locations?q=${encodeURIComponent(lok.value.trim())}`).catch(() => []);
        lokHits.replaceChildren(...rows.slice(0, 8).map(l => h("button", { type: "button", onclick: () => { loknr = l.loknr; lok.value = l.navn; lokHits.replaceChildren(); } },
          h("span", {}, l.navn), l.kommune ? h("i", { class: "muted" }, l.kommune) : null)));
      }, 250);
    });
    const dato = h("input", { type: "date", required: true, max: new Date().toISOString().slice(0, 10) });
    const antal = h("input", { type: "number", min: 1, value: 1, required: true });
    const sted = h("input", { maxlength: 200, placeholder: "Fx '60 km VNV for Hanstholm' (hvis ingen lokalitet)" });
    const note = h("input", { maxlength: 2000, placeholder: "Kilde, fx 'Godkendt af SU 2023' (valgfri)" });
    return h("form", { class: "stack", style: { background: "var(--surface-2)", padding: "12px", borderRadius: "10px" }, onsubmit: async (e) => {
      e.preventDefault();
      if (!artnr) { toast("Vælg arten på listen", "err"); return; }
      if (lok.value.trim() && !loknr) { toast("Vælg lokaliteten på listen – eller ryd feltet og skriv stedet", "err"); return; }
      try {
        await api("/api/categories/finds", { method: "POST", body: { artnr, dato: dato.value, antal: Number(antal.value), loknr, sted: sted.value || null, note: note.value || null } });
        toast("Fundet er lagt ind – Arter pr. år genberegnes i baggrunden"); onSaved();
      } catch (err) { toast(err.message, "err"); }
    } },
      h("div", { class: "form-grid two" },
        h("label", { class: "field" }, h("span", {}, "Art"), art, artHits),
        h("div", { class: "form-grid two" }, h("label", { class: "field" }, h("span", {}, "Dato"), dato), h("label", { class: "field" }, h("span", {}, "Antal"), antal))),
      h("div", { class: "form-grid two" },
        h("label", { class: "field" }, h("span", {}, "Lokalitet"), lok, lokHits),
        h("label", { class: "field" }, h("span", {}, "Sted (fritekst)"), sted)),
      h("label", { class: "field" }, h("span", {}, "Note"), note),
      h("div", {}, h("button", { class: "btn primary" }, "Indsæt fund")));
  };
  await load();
}

/** Rapport fra Den Danske Liste: hvad en import vil ændre, med knap til at gennemføre den. */
function renderListReport(box, r, done) {
  const out = box.querySelector(".stack");
  out.querySelector(".list-report")?.remove();
  const row = (c) => h("li", {}, h("b", {}, c.navn), h("i", { class: "muted" }, ` ${c.latin} `), katTag(c.kategori), c.valid_from ? h("small", { class: "muted" }, ` fra ${fmtDate(c.valid_from)}`) : null);
  const rep = h("div", { class: "list-report stack" },
    h("p", { style: { margin: 0 } }, h("b", {}, `${fmtN(r.matched)} af ${fmtN(r.of)} arter på listen`), ` findes i portalen. ${fmtN(r.n_changes)} ændringer: `,
      Object.entries(r.summary).sort().map(([k, n]) => `${n}× ${k}`).join(", ") || "ingen", `. ${fmtN(r.unchanged)} er allerede som på listen.`),
    r.conflicts.length ? h("div", { class: "notice warn" }, h("b", {}, `${r.conflicts.length} arter røres ikke, fordi en moderator har sat kategorien: `),
      r.conflicts.map(c => `${c.navn} (listen: ${c.liste}, portalen: ${c.portal.map(p => p.kategori).join("/")})`).join(", ")) : null,
    r.unmatched.length ? h("details", {}, h("summary", {}, `${r.n_unmatched} arter på listen findes ikke i portalen`),
      h("small", { class: "muted" }, "Typisk undsluppne arter, som DOFbasen aldrig har haft – vist er A–D og F: "), r.unmatched.map(u => `${u.navn} (${u.kategori})`).join(", ")) : null,
    r.bracket_off_list.length ? h("small", { class: "muted" }, `Står med [klammer] i DOFbasen, men ikke på listen (beholder kategori D): ${r.bracket_off_list.map(x => x.navn).join(", ")}`) : null,
    r.n_changes ? h("details", {}, h("summary", {}, `Vis ændringerne${r.n_changes > r.changes.length ? ` (de første ${r.changes.length})` : ""}`), h("ul", { class: "stack-sm", style: { columns: "2 260px" } }, ...r.changes.map(row))) : null,
    r.n_changes ? h("button", { class: "btn primary", style: { alignSelf: "flex-start" }, onclick: async (e) => {
      if (!confirm(`Læg ${r.n_changes} kategoriændringer ind fra Den Danske Liste?`)) return;
      e.currentTarget.disabled = true;
      try { const w = await api("/api/categories/danish-list", { method: "POST" }); toast(`${w.written} kategorier lagt ind`); rep.remove(); done(); }
      catch (err) { toast(err.message, "err"); e.currentTarget.disabled = false; }
    } }, `Læg ${fmtN(r.n_changes)} kategorier ind`) : h("p", { class: "muted", style: { margin: 0 } }, "Portalen følger allerede listen."));
  out.append(rep);
}

/** Opret en art med kategori (fx en undsluppen art, DOFbasen endnu ikke har på artslisten). */
function newSpeciesForm(navn, onCreated) {
  const f = {
    navn: h("input", { required: true, minlength: 2, maxlength: 200, value: navn || "", placeholder: "Fx Rødhalset Gås eller [Mandarinand]" }),
    latin: h("input", { maxlength: 200, placeholder: "Videnskabeligt navn (valgfrit)" }),
    artnr: h("input", { type: "number", min: 1, max: 899999, placeholder: "DOFbasens artsnummer (valgfrit)" }),
    kategori: h("select", {}, ...Object.entries({ A: "Vild fugl (krydsbar)", B: "Kun vild 1800–1949 (krydsbar)", C: "Indført, selvopretholdende (krydsbar)",
      D: "Mulig vild – tvivl om oprindelse (ikke krydsbar)", E: "Undsluppet/udsat (ikke krydsbar)", F: "Kun registreret før 1800 (ikke krydsbar)" }).map(([k, t]) => h("option", { value: k, selected: k === "E" }, `${k} – ${t}`))),
    from: h("input", { type: "date" }),
    note: h("input", { maxlength: 1000, placeholder: "Note / kilde (valgfri)" }),
  };
  const field = (label, el, help) => h("label", { class: "field" }, h("span", {}, label), el, help ? h("small", { class: "muted" }, help) : null);
  const dlg = modal("Opret art", h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/categories/species", { method: "POST", body: {
        navn: f.navn.value, latin: f.latin.value || null, artnr: f.artnr.value ? Number(f.artnr.value) : null,
        kategori: f.kategori.value, valid_from: f.from.value || null, note: f.note.value || null } });
      toast("Arten er oprettet"); dlg.close(); onCreated(r.artnr);
    } catch (err) { toast(err.message, "err"); }
  } },
    field("Navn", f.navn), field("Latin", f.latin),
    field("Artsnummer i DOFbasen", f.artnr, "Med DOFbasens nummer kobles arten på observationerne, så snart de hentes. Uden nummer får arten et portalnummer og har ingen observationer."),
    h("div", { class: "form-grid two" }, field("Kategori", f.kategori), field("Gyldig fra (valgfri)", f.from)),
    field("Note", f.note),
    h("div", { class: "row" }, h("button", { class: "btn primary" }, "Opret"), h("button", { type: "button", class: "btn ghost", onclick: () => dlg.close() }, "Annullér"))));
  f.navn.focus();
}

// ---------------------------------------------------------------- datapaneler (DOFbasen og vejrarkiv)

const MONTHS = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];
const UNIT_TXT = { min: "minut(ter)", hour: "time(r)", day: "døgn" };
const short = (n) => n >= 1e6 ? `${(n / 1e6).toFixed(1).replace(".", ",")}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(n || "");

function freshness(iso) {
  if (!iso) return null;
  const days = (Date.now() - new Date(iso)) / 864e5;
  return days < 1 ? "f1" : days < 7 ? "f7" : days < 31 ? "f31" : days < 366 ? "f365" : "fold";
}

async function dataPanel(body, source, schedule) {
  const isDof = source === "dof";
  const head = h("div", {});
  const grid = h("div", { class: "card mt table-wrap" });
  const planEl = h("div", { class: "card mt" });
  body.append(head, planEl, grid);
  let planDirty = false;
  const refresh = async () => {
    let d;
    try { d = await api(`/api/admin/data/${source}`); } catch (err) { head.replaceChildren(errorBox(err)); return; }
    head.replaceChildren(isDof ? dofHead(d) : weatherHead(d, refresh));
    grid.replaceChildren(monthGrid(d, source, refresh));
    if (!planDirty) planEl.replaceChildren(planEditor(d, source, () => { planDirty = true; }, async () => { planDirty = false; await refresh(); }));
  };
  schedule(refresh);
}

function dofHead(d) {
  const months = Object.values(d.months);
  const fresh = months.filter(m => freshness(m.updated) === "f1" || freshness(m.updated) === "f7").length;
  return h("div", { class: "stats" },
    st(fmtN(months.reduce((a, m) => a + (m.obs || 0), 0)), "observationer"),
    st(`${fresh}/${months.length}`, "måneder opdateret < 7 d"),
    st(fmtN(d.queue_length), "DOFbasen-kald i kø"),
    st(fmtN(Number(d.calls_today || 0)), "kald i dag (maks. 1/min)"));
}

function weatherHead(d, refresh) {
  const months = Object.values(d.months);
  const done = months.filter(m => m.status === "done").length;
  const live = d.live;
  const liveTxt = !live ? "Ingen hentning lige nu" : live.state === "downloading" ? `Henter ${live.month}: ${live.mb || 0} MB (${live.mbps || "–"} MB/s)`
    : live.state === "parsing" ? `Parser ${live.month}: døgn ${live.day}/${live.days || "?"}` : `Gemmer ${live.month}…`;
  return h("div", {},
    h("div", { class: "stats" }, st(`${done}`, "måneder hentet"), st(fmtN(d.rows_est), "dagsrækker (kvadrat × døgn)"),
      st(fmtN(d.queue.length), "måneder i kø"), st((mb => mb >= 1000 ? `${String((mb / 1000).toFixed(1)).replace(".", ",")} GB` : `${fmtN(mb)} MB`)(months.reduce((a, m) => a + (m.mb || 0), 0)), "hentet i alt")),
    h("div", { class: "card mt row between" }, h("span", {}, live ? h("span", { class: "spinner", style: { width: "14px", height: "14px", marginRight: "8px" } }) : null, liveTxt,
      d.queue.length ? h("small", { class: "muted" }, ` · næste: ${d.queue.slice(0, 4).join(", ")}${d.queue.length > 4 ? " …" : ""}`) : null),
      h("div", { class: "row" },
        h("button", { class: "btn sm", onclick: async () => {
          const y0 = d.plan.start_year, now = new Date(), months = [];
          for (let y = y0; y <= now.getFullYear(); y++) for (let m = 1; m <= 12; m++) if (y < now.getFullYear() || m <= now.getMonth() + 1) months.push([y, m]);
          const r = await api("/api/admin/data/weather/months", { method: "POST", body: { months, only_missing: true } });
          toast(`${r.queued} måneder lagt i kø`); refresh();
        } }, `Hent manglende fra ${d.plan.start_year}`),
        d.queue.length ? h("button", { class: "btn sm ghost danger", onclick: async () => { await api("/api/admin/data/weather/queue", { method: "DELETE" }); refresh(); } }, "Tøm kø") : null)),
    h("p", { class: "muted", style: { fontSize: ".85rem" } }, "DMI's 10 km-grid (Det Danske Kvadratnet) hentes som månedsfiler (≈0,5 GB), én ad gangen. De parses i flere processer direkte fra zip-filen og slettes bagefter. Pr. kvadrat og dansk døgn gemmes temperatur (middel/min/maks), vind, vindstød, nedbør, skydække, solskin og morgenvejret kl. 4–10. Bruges bl.a. under 'Vejret den dag' i trådene."));
}

const MONTHS_LONG = ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december"];

function monthGrid(d, source, refresh) {
  const isDof = source === "dof";
  const now = new Date();
  const keys = Object.keys(d.months);
  const first = isDof ? Math.min(...keys.map(k => +k.slice(0, 4)), now.getFullYear()) : Math.min(d.plan.start_year, ...keys.map(k => +k.slice(0, 4)));
  const years = [];
  for (let y = now.getFullYear(); y >= first; y--) years.push(y);
  const queued = new Set(isDof ? keys.filter(k => d.months[k].queued) : d.queue);
  const liveKey = d.live?.month;
  const cell = (y, m) => {
    const k = `${y}-${String(m).padStart(2, "0")}`;
    const future = y > now.getFullYear() || (y === now.getFullYear() && m > now.getMonth() + 1);
    if (future) return h("td", { class: "mg-cell future" });
    const v = d.months[k];
    let cls = "mg-cell", txt = "", tip = k;
    if (isDof) {
      const f = freshness(v?.updated);
      cls += f ? ` ${f}` : v?.obs ? " partial" : " missing";
      txt = v?.obs ? short(v.obs) : "";
      tip = v ? `${k}: ${fmtN(v.obs)} observationer\n${v.updated ? `Opdateret ${new Date(v.updated).toLocaleString("da-DK")} (${{ "måned": "månedsudtræk", arkiv: "arkivfil", dage: "dagsudtræk" }[v.source] || v.source})` : "Ikke hentet som hel måned"}\nDage hentet enkeltvis: ${v.days}/${v.days_total}`
        : `${k}: ingen data`;
    } else {
      const f = v?.status === "done" ? freshness(v.updated) : null;
      cls += v ? ` ${v.status === "done" ? f : v.status}` : " missing";
      txt = v?.status === "done" ? "✓" : v?.status === "error" ? "!" : v?.status === "unavailable" ? "–" : "";
      tip = v ? `${k}: ${{ done: "hentet", error: "fejl", unavailable: "findes ikke hos DMI", empty: "tom fil" }[v.status] || v.status}\n${new Date(v.updated).toLocaleString("da-DK")}` +
        (v.status === "done" ? `\n${fmtN(v.rows)} rækker · ${v.cells} kvadrater · ${v.mb} MB · ${v.seconds} s` : "") + (v.error ? `\n${v.error}` : "") : `${k}: ikke hentet`;
    }
    if (queued.has(k)) { cls += " queued"; tip += "\nI kø"; }
    if (liveKey === k) { cls += " live"; txt = d.live.state === "downloading" ? "↓" : "⚙"; }
    const hasData = isDof ? !!(v?.obs || v?.updated || v?.days) : !!v;
    return h("td", { class: cls, title: tip, onclick: () => {
      const label = `${MONTHS_LONG[m - 1]} ${y}`;
      const refetch = async () => {
        try {
          if (isDof) await api("/api/admin/data/dof/month", { method: "POST", body: { year: y, month: m } });
          else await api("/api/admin/data/weather/months", { method: "POST", body: { months: [[y, m]] } });
          dlg.close(); toast(`${label} lagt i kø`); refresh();
        } catch (err) { toast(err.message, "err"); }
      };
      const remove = async () => {
        if (!confirm(isDof ? `Slet alle observationer og ture fra ${label} i portalens database? DOFbasen røres ikke – måneden kan hentes igen bagefter.`
          : `Slet vejrdata for ${label}? Måneden kan hentes igen fra DMI bagefter.`)) return;
        try {
          const r = await api(`/api/admin/data/${source}/month/${y}/${m}`, { method: "DELETE" });
          dlg.close(); toast(`${label} slettet (${fmtN(r.deleted)} rækker)`); refresh();
        } catch (err) { toast(err.message, "err"); }
      };
      const dlg = modal(label, h("div", { class: "stack" },
        h("p", { class: "muted", style: { margin: 0, whiteSpace: "pre-line" } }, tip),
        h("div", { class: "row between" },
          hasData ? h("button", { type: "button", class: "btn ghost danger", onclick: remove }, "Slet data") : h("span"),
          h("button", { type: "button", class: "btn primary", onclick: refetch }, isDof ? "Hent igen (ét kald)" : "Hent igen (≈0,5 GB)"))));
    } }, txt);
  };
  const legend = isDof
    ? [["f1", "< 1 døgn"], ["f7", "< 7 døgn"], ["f31", "< 1 md."], ["f365", "< 1 år"], ["fold", "ældre"], ["partial", "data, men ikke hentet som helhed"], ["queued", "i kø"]]
    : [["f1", "< 1 døgn"], ["f7", "< 7 døgn"], ["f31", "< 1 md."], ["f365", "< 1 år"], ["fold", "ældre"], ["error", "fejl"], ["unavailable", "findes ikke"], ["queued", "i kø"]];
  return h("div", {},
    h("h3", { style: { marginTop: 0 } }, isDof ? "Måneder fra DOFbasen" : "Måneder i vejrarkivet"),
    h("p", { class: "muted", style: { fontSize: ".85rem", marginTop: 0 } }, isDof
      ? "Tal = observationer i måneden. Farven viser hvornår måneden sidst blev hentet komplet (månedsudtræk, arkivfil eller alle dage enkeltvis). Klik på en måned for at hente den igen eller slette den."
      : "Klik på en måned for at hente den igen eller slette den."),
    h("table", { class: "month-grid" }, h("thead", {}, h("tr", {}, h("th", {}, "År"), ...MONTHS.map(m => h("th", {}, m)))),
      h("tbody", {}, ...years.map(y => h("tr", {}, h("th", {}, y), ...MONTHS.map((_, i) => cell(y, i + 1)))))),
    h("div", { class: "row mt", style: { gap: "10px", fontSize: ".78rem" } }, ...legend.map(([c, t]) => h("span", { class: "row", style: { gap: "4px" } }, h("i", { class: `mg-cell ${c} mg-key` }), t))));
}

function planEditor(d, source, onDirty, onSaved) {
  const isDof = source === "dof";
  const plan = JSON.parse(JSON.stringify(d.plan));
  const rows = d.tiers.map(([key, label]) => {
    const t = plan.tiers[key];
    const on = h("input", { type: "checkbox", checked: t.on, onchange: () => { t.on = on.checked; onDirty(); } });
    const every = h("input", { type: "number", min: 1, value: t.every, style: { width: "72px" }, oninput: () => { t.every = Number(every.value) || 1; onDirty(); } });
    const unit = h("select", { style: { width: "auto" }, onchange: () => { t.unit = unit.value; onDirty(); } },
      ...Object.entries(UNIT_TXT).filter(([u]) => isDof || u !== "min").map(([u, txt]) => h("option", { value: u, selected: u === t.unit }, txt)));
    const last = d.last_runs[key];
    const perDay = d.per_day[key];
    const what = isDof
      ? ({ today: "live-hentning af dagens liste", days3: "3 dagsudtræk", week: "7 dagsudtræk", month: "månedsudtræk (1–2)", year: "13 månedsudtræk", all: "alle måneder siden 1961 (≈790 kald ≈ 13 t)" })[key]
      : ({ today: "indeværende måneds fil", days3: "måned(er) for de sidste 3 dage", week: "måned(er) for den sidste uge", month: "denne og forrige måned", year: "de sidste 13 måneder", all: `manglende måneder fra ${plan.start_year}` })[key];
    return h("tr", {},
      h("td", {}, h("label", { class: "check" }, on, h("b", {}, label)), h("small", { class: "muted", style: { display: "block" } }, what)),
      h("td", {}, h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, "hver", every, unit)),
      h("td", { class: "muted" }, isDof && key === "today" ? "løbende" : last ? ago(last) : "aldrig"),
      h("td", { class: "right num muted" }, perDay ? (isDof ? `${fmtN(perDay)} kald/døgn` : `${String(perDay).replace(".", ",")} filer/døgn`) : "–"),
      h("td", { class: "right" }, isDof && key === "today" ? h("button", { class: "btn sm", title: "Henter dagens liste med det samme – springer 60 sekunders-grænsen over én gang", onclick: async () => {
        try { await api("/api/admin/data/dof/run/today", { method: "POST" }); toast("Dagen hentes nu – uden ventetid"); } catch (err) { toast(err.message, "err"); }
      } }, "Hent nu") : h("button", { class: "btn sm", onclick: async () => {
        if (key === "all" && !confirm(isDof ? "Hent alle måneder fra DOFbasen igen? Det er ca. 790 kald og tager ca. 13 timer (ét kald i minuttet)." : "Hent alle manglende vejrmåneder? Hver er ca. 0,5 GB.")) return;
        await api(`/api/admin/data/${source}/run/${key}`, { method: "POST" }); toast(`${label}: lagt i kø`);
      } }, "Kør nu")));
  });
  // Kapacitet: ét kald i minuttet = 1.440/døgn. Live skiftes med køen, så køen får altid mindst halvdelen.
  const live = isDof ? Math.min(d.per_day.today || 0, 1440) : 0;
  const queueNeed = isDof ? Object.entries(d.per_day).filter(([k]) => k !== "today").reduce((a, [, v]) => a + v, 0) : 0;
  const queueCap = 1440 - Math.min(live, 720);
  const extras = isDof ? null : h("div", { class: "row mt" },
    h("label", { class: "row", style: { gap: "6px" } }, "Startår", h("input", { type: "number", min: 1990, max: new Date().getFullYear(), value: plan.start_year, style: { width: "90px" }, oninput: (e) => { plan.start_year = Number(e.target.value); onDirty(); } })),
    h("label", { class: "row", style: { gap: "6px" } }, "Parse-processer", h("input", { type: "number", min: 0, max: 32, value: plan.workers || 0, style: { width: "70px" }, oninput: (e) => { plan.workers = Number(e.target.value); onDirty(); } }), h("small", { class: "muted" }, "0 = automatisk")));
  return h("div", {},
    h("h3", { style: { marginTop: 0 } }, "Opdateringsplan"),
    h("p", { class: "muted", style: { fontSize: ".85rem", marginTop: 0 } }, isDof
      ? "Hvor ofte hvert tidsvindue hentes igen fra DOFbasen. Alt går gennem gatewayen: højst ét kald i minuttet, live-hentningen skiftes med køen. Vinduer, der stadig ligger i kø, lægges ikke i kø igen."
      : "Hvor ofte vejrarkivet opdateres. DMI genberegner de nyeste måneder løbende; ældre måneder ændrer sig sjældent."),
    h("div", { class: "table-wrap" }, h("table", { class: "data stack-sm", style: { "--cols": 2 } }, h("thead", {}, h("tr", {}, h("th", {}, "Vindue"), h("th", {}, "Interval"), h("th", {}, "Sidst"), h("th", { class: "right" }, "Belastning"), h("th", {}))), h("tbody", {}, ...rows))),
    isDof ? (queueNeed > queueCap
      ? h("div", { class: "notice warn mt" }, `Planen kræver ca. ${fmtN(Math.round(queueNeed))} kø-kald/døgn, men der er kun plads til ca. ${fmtN(queueCap)} ved siden af live-hentningen (ét kald i minuttet). Køen vil vokse – sæt intervallerne ned.`)
      : h("p", { class: "muted mt", style: { fontSize: ".85rem" } }, `Køen bruger ca. ${fmtN(Math.round(queueNeed))} af ${fmtN(queueCap)} mulige kald/døgn ved siden af live-hentningen (${fmtN(Math.round(live))} kald/døgn, når køen er tom).`)) : null,
    extras,
    h("div", { class: "row mt" }, h("button", { class: "btn primary", onclick: async () => {
      try { await api(`/api/admin/data/${source}/plan`, { method: "PUT", body: { tiers: plan.tiers, start_year: plan.start_year, workers: plan.workers } }); toast("Planen er gemt"); await onSaved(); }
      catch (err) { toast(err.message, "err"); }
    } }, "Gem plan")));
}

// ---------------------------------------------------------------- aktivitet

async function activityPanel(body) {
  let days = 30;
  const out = h("div", {});
  body.append(h("div", { class: "row mb" }, seg([["7", "7 dage"], ["30", "30 dage"], ["90", "90 dage"]], "30", (v) => { days = Number(v); load(); })), out);
  const load = async () => {
    out.replaceChildren(h("div", { class: "skeleton", style: { height: "200px" } }));
    let d;
    try { d = await api(`/api/admin/activity?days=${days}`); } catch (err) { out.replaceChildren(errorBox(err)); return; }
    const a = d.active;
    const idx = (iso) => Math.round((new Date(iso) - new Date(d.by_day[0]?.day || iso)) / 864e5);
    const series = [
      { name: "Aktive brugere", points: d.by_day.map(r => [idx(r.day), r.users]), color: PALETTE[0], width: 2.5 },
      { name: "Handlinger", points: d.by_day.map(r => [idx(r.day), r.actions]), color: PALETTE[2] },
    ];
    const label = (i) => { const dt = new Date(d.by_day[0]?.day); dt.setDate(dt.getDate() + i); return `${dt.getDate()}/${dt.getMonth() + 1}`; };
    const KIND = { login: "Login", report: "Feltmelding", comment: "Kommentar", like: "Like", support: "Supportbesked" };
    out.replaceChildren(
      h("div", { class: "stats" }, st(fmtN(a.d1), "aktive i dag"), st(fmtN(a.d7), "aktive 7 dage"), st(fmtN(a.d30), "aktive 30 dage"), st(fmtN(a.views_d1), "sidevisninger i dag")),
      d.by_day.length > 1 ? h("div", { class: "card mt" }, h("h3", { style: { marginTop: 0 } }, "Aktivitet pr. dag"),
        lineChart({ series, height: 200, xFormat: label, yFormat: (v) => fmtN(Math.round(v)) }), seriesLegend(series)) : null,
      h("div", { class: "grid-2 mt" },
        h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Mest besøgte sider"),
          h("table", { class: "data" }, h("thead", {}, h("tr", {}, h("th", {}, "Side"), h("th", { class: "right" }, "Visninger"), h("th", { class: "right" }, "Brugere"))),
            h("tbody", {}, ...d.paths.map(p => h("tr", {}, h("td", {}, p.path), h("td", { class: "right num" }, fmtN(p.n)), h("td", { class: "right num" }, fmtN(p.users))))))),
        h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Handlinger"),
          h("ul", { class: "mini-list" }, ...d.kinds.map(k => h("li", {}, KIND[k.kind] || k.kind, h("b", { class: "num" }, fmtN(k.n))))),
          h("h3", {}, "Seneste"),
          h("ul", { class: "mini-list", style: { maxHeight: "260px", overflow: "auto" } }, ...d.recent.map(r => h("li", {}, h("span", {}, h("b", {}, r.navn), ` · ${KIND[r.kind] || r.kind}`), h("small", { class: "muted" }, ago(r.created_at))))))),
      h("div", { class: "card mt table-wrap" }, h("h3", { style: { marginTop: 0 } }, "Brugere"),
        h("table", { class: "data stack-sm", style: { "--cols": 3 } }, h("thead", {}, h("tr", {}, h("th", {}, "Bruger"), h("th", {}, "Senest"), h("th", { class: "right" }, "Visninger"), h("th", { class: "right" }, "Handlinger"))),
          h("tbody", {}, ...d.users.map(u => h("tr", { class: "click", onclick: () => userActivity(u) },
            h("td", {}, h("b", {}, u.navn), h("small", { class: "muted" }, ` ${u.obserkode}`)), h("td", { class: "muted", "data-label": "Senest" }, ago(u.last)),
            h("td", { class: "right num", "data-label": "Visninger" }, fmtN(u.views)), h("td", { class: "right num", "data-label": "Handlinger" }, fmtN(u.actions))))))));
  };
  await load();
}

async function userActivity(u) {
  const list = h("div", {}, h("div", { class: "skeleton" }));
  modal(`Aktivitet: ${u.navn}`, list, { wide: true });
  const rows = await api(`/api/admin/activity/user/${u.id}`);
  list.replaceChildren(h("ul", { class: "mini-list", style: { maxHeight: "60vh", overflow: "auto" } }, ...rows.map(r => h("li", {},
    h("span", {}, h("b", {}, r.kind === "view" ? "Side" : r.kind), " ", r.path || (r.detail ? JSON.stringify(r.detail) : "")), h("small", { class: "muted" }, new Date(r.created_at).toLocaleString("da-DK"))))));
}

// ---------------------------------------------------------------- support

async function supportPanel(body) {
  let status = "open";
  const list = h("div", { class: "stack" });
  const detail = h("div", {});
  body.append(h("div", { class: "row mb" }, seg([["open", "Åbne"], ["closed", "Lukkede"], ["all", "Alle"]], status, (v) => { status = v; load(); })),
    h("div", { class: "report-layout" }, list, detail));
  const load = async () => {
    try {
      const rows = await api(`/api/admin/support?status=${status}`);
      list.replaceChildren(...(rows.length ? rows.map(r => h("button", { type: "button", class: "loc-item", onclick: () => open(r.id) },
        h("span", {}, r.unread ? h("span", { class: "badge-inline", style: { marginLeft: 0, marginRight: "6px" } }, "nyt") : null, h("b", {}, r.subject),
          h("small", { class: "muted" }, ` · ${r.navn} · ${KINDS[r.kind] || r.kind} · ${r.n} besked${r.n === 1 ? "" : "er"}`)),
        h("small", { class: "muted" }, ago(r.updated_at)))) : [empty("Ingen samtaler")]));
    } catch (err) { list.replaceChildren(errorBox(err)); }
  };
  const open = async (id) => {
    setParams({ tab: "support", t: id });
    try { detail.replaceChildren(conversation(await api(`/api/support/${id}`), () => { load(); open(id); }, { admin: true })); load(); }
    catch (err) { detail.replaceChildren(errorBox(err)); }
  };
  await load();
  if (params().t) open(params().t);
}
