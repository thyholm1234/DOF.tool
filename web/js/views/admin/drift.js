// Admin → Drift: overblik, lager, arkivupload, talemodel, backup, job og planlagte opgaver.
import { api, state, h, icon, fmtN, fmtDate, fmtTime, ago, params, toast, errorBox, modal } from "../../core.js";
import { st } from "./common.js";

export async function ops(body) {
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

const fmtBytes = (b) => {
  if (b == null) return "–";
  const [div, unit] = b >= 1e12 ? [1e12, "TB"] : b >= 1e9 ? [1e9, "GB"] : b >= 1e6 ? [1e6, "MB"] : b >= 1e3 ? [1e3, "kB"] : [1, "B"];
  return `${(b / div).toLocaleString("da-DK", { maximumFractionDigits: b >= div * 100 ? 0 : 1 })} ${unit}`;
};

export async function storageCard(box, refresh = false) {
  const btn = h("button", { class: "btn sm ghost", onclick: () => storageCard(box, true) }, "Mål igen");
  box.replaceChildren(h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, "Lagerplads"), btn), h("p", { class: "muted" }, "Måler …"));
  let d;
  try { d = await api(`/api/admin/storage${refresh ? "?refresh=1" : ""}`); } catch (err) { box.append(errorBox(err)); return; }
  // Én serie (bytes) → én farve; tal i tekstfarve; søjlen er relativ til den største post i oversigten
  const rows = [
    ...d.databases.map(x => ({ group: "Databaser", name: x.name, bytes: x.bytes, note: x.error ? `fejl: ${x.error}` : `${x.tables.length ? "største: " + x.tables.slice(0, 3).map(t => `${t.name} ${fmtBytes(t.bytes)}`).join(", ") : ""}`, tables: x.tables })),
    { group: "Objektlager", name: d.objects.name, bytes: d.objects.bytes, note: d.objects.error ? `fejl: ${d.objects.error}` : `${fmtN(d.objects.files ?? 0)} filer (fotos og lyd i observationer)` },
    ...d.files.map(x => ({ group: "Filer på disken", name: x.name, bytes: x.bytes, note: `${fmtN(x.files)} filer` })),
    { group: "Hukommelse", name: d.valkey.name, bytes: d.valkey.bytes, note: `${fmtN(d.valkey.keys)} nøgler` },
  ];
  const max = Math.max(1, ...rows.map(r => r.bytes || 0));
  const total = rows.filter(r => r.group !== "Hukommelse").reduce((t, r) => t + (r.bytes || 0), 0);
  const groups = [...new Set(rows.map(r => r.group))];
  const disk = d.disk;
  box.replaceChildren(
    h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, "Lagerplads"),
      h("span", { class: "row" }, h("small", { class: "muted" }, `målt ${ago(d.measured_at)}`), btn)),
    disk ? h("div", { title: `${fmtBytes(disk.used)} brugt af ${fmtBytes(disk.total)} – ${fmtBytes(disk.free)} fri` },
      h("div", { class: "row between" }, h("span", {}, "Serverens disk (mediemappen)"), h("b", { class: "num" }, `${fmtBytes(disk.used)} af ${fmtBytes(disk.total)} · ${fmtBytes(disk.free)} fri`)),
      h("div", { class: "bar-inline", role: "meter", "aria-valuemin": 0, "aria-valuemax": disk.total, "aria-valuenow": disk.used, "aria-label": "Brugt diskplads", style: { height: "8px" } },
        h("i", { style: { width: `${Math.max(1, disk.used / disk.total * 100)}%` } }))) : null,
    h("p", { class: "muted", style: { margin: 0 } }, `DOF.tool fylder i alt ${fmtBytes(total)} (databaser, objektlager og filer).`),
    ...groups.map(g => h("div", {}, h("h4", { style: { margin: ".6rem 0 .2rem" } }, g),
      h("ul", { class: "mini-list" }, ...rows.filter(r => r.group === g).map(r => h("li", { class: "bar-row", title: `${r.name}: ${fmtBytes(r.bytes)}${r.note ? " – " + r.note : ""}` },
        h("span", {}, r.name, r.note ? h("small", { class: "muted" }, ` · ${r.note}`) : null), h("b", { class: "num" }, fmtBytes(r.bytes)),
        h("div", { class: "bar-inline" }, h("i", { style: { width: `${r.bytes ? Math.max(1, r.bytes / max * 100) : 0}%` } })),
        r.tables?.length ? h("details", { style: { gridColumn: "1 / -1" } }, h("summary", { class: "muted" }, "Største tabeller"),
          h("table", { class: "data" }, h("tbody", {}, ...r.tables.map(t => h("tr", {}, h("td", {}, t.name), h("td", { class: "right num muted" }, `${fmtN(t.rows)} rækker`), h("td", { class: "right num" }, fmtBytes(t.bytes))))))) : null))))));
}

// ---------------------------------------------------------------- upload af DOFbasen-udtræk (gentegnes ikke af 15 s-opdateringen)

const CSV_RE = /\.csv$/i;

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

/**
 * Upload i små bidder (en proxy foran portalen afviser ellers store forespørgsler med 413): hver bid sendes for sig, og
 * bliver proxyen ved med at afvise, halveres bidden ned til 256 kB. Afbrydes forbindelsen, fortsætter uploaden, hvor
 * serveren nåede til. Til sidst bedes serveren kontrollere og tage filen i brug.
 */
async function uploadChunked(file, base, onProgress) {
  const id = Array.from(crypto.getRandomValues(new Uint8Array(12)), b => (b % 36).toString(36)).join("");
  const send = (url, init) => fetch(url, { method: "POST", headers: { "X-Requested-With": "fetch" }, ...init });
  let offset = 0, chunk = 16 * 1024 * 1024, failures = 0;
  while (offset < file.size) {
    const end = Math.min(file.size, offset + chunk);
    let res;
    try { res = await send(`${base}/${id}?offset=${offset}`, { body: file.slice(offset, end) }); }
    catch { if (++failures > 5) throw new Error("Forbindelsen blev afbrudt"); await new Promise(r => setTimeout(r, 1000 * failures)); continue; }
    if (res.status === 413 && chunk > 256 * 1024) { chunk = Math.max(256 * 1024, chunk >> 1); continue; }
    if (res.status === 409) {  // serveren er et andet sted: fortsæt derfra (eller forfra)
      const m = /(\d+) bytes/.exec((await res.json().catch(() => ({}))).detail || "");
      offset = m ? Number(m[1]) : 0;
      if (++failures > 5) throw new Error("Uploaden kom ud af takt med serveren – prøv igen");
      continue;
    }
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Fejl ${res.status} ved upload`);
    failures = 0;
    offset = end;
    onProgress(offset / file.size);
  }
  const done = await send(`${base}/${id}/finish`, {});
  const data = await done.json().catch(() => ({}));
  if (!done.ok) throw new Error(data.detail || `Fejl ${done.status}`);
  return data;
}

export async function archiveUpload(box) {
  const input = h("input", { type: "file", accept: ".csv", multiple: true, class: "hide" });
  const drop = h("label", { class: "dropzone", onclick: () => input.click() },
    h("b", {}, "Vælg eller træk DOFbasen-udtræk hertil"), h("span", { class: "muted" }, "CSV-filer fra DOFbasens søgning – flere ad gangen"));
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
    const bad = [...all].filter(f => !CSV_RE.test(f.name));
    const good = [...all].filter(f => CSV_RE.test(f.name)).sort((a, b) => a.name.localeCompare(b.name));
    if (!good.length) { toast("Ingen CSV-filer valgt", "err"); return; }
    busy = true; drop.classList.add("busy");
    const total = good.reduce((n, f) => n + f.size, 0);
    const bar = h("progress", { max: 1, value: 0, style: { width: "100%" } });
    const line = h("div", { class: "muted" });
    const errors = h("div", { class: "stack" }, ...bad.map(f => h("div", { class: "notice err" }, `${f.name}: springes over – ikke en CSV-fil`)));
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

  box.append(h("h3", { style: { margin: 0 } }, "Upload af DOFbasen-udtræk"),
    h("p", { class: "muted", style: { margin: 0 } }, "CSV-udtræk fra DOFbasens søgning importeres direkte uden kald til dofbasen.dk – månedsfiler (dofbasen_ÅÅÅÅ_MM.csv) såvel som andre udtræk, fx én lokalafdeling over en årrække. Dage der er dækket af en månedsfil, hentes ikke for at fylde huller; andre udtræk dækker ikke nødvendigvis alt og markerer ingen dage. Står en observation i flere lokalafdelingers udtræk, lægges afdelingerne sammen. Uploadede filer slettes fra serveren, så snart de er importeret; filer der allerede er importeret uændret, slettes med det samme."),
    drop, input, status, list);
  await loadList();
}

// ---------------------------------------------------------------- worker-opgaver (status opdateres hvert 3. sekund)

const RUNNABLE = ["compute", "archive_import", "aggregates", "climate", "events", "forecast", "regions", "gaps", "location_years", "loc_finds", "speech_model"];

const JOB_STATUS = { idle: ["plain", "ikke kørt"], queued: ["bemaerk", "i kø"], running: ["local", "kører"], done: ["plain", "ok"], error: ["SU", "fejl"], interrupted: ["SUB", "afbrudt"] };

const jobTag = (status) => { const [k, l] = JOB_STATUS[status] || ["plain", status]; return h("span", { class: `tag ${k}` }, l); };

const dur = (s) => s == null ? "–" : s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} t`;

/** Talemodellen til tale-indtastning i appen: versionen serveren leverer, og byggeagenten. */
export async function speechCard(box) {
  let d;
  try { d = await api("/api/admin/speech-model"); } catch (err) { box.replaceChildren(errorBox(err)); return; }
  const m = d.manifest;
  const build = (force) => async () => {
    if (force && !confirm("Bygge modellen forfra? Det henter 1,3 GB fra Hugging Face og tager ca. 5 minutter.")) return;
    try { await api("/api/admin/worker/speech_model", { method: "POST", body: { force } }); toast("Talemodellen lægges i kø"); speechCard(box); } catch (err) { toast(err.message, "err"); }
  };
  const pick = h("input", { type: "file", accept: ".tar", class: "hide" });
  const upBtn = h("button", { class: "btn sm ghost", onclick: () => pick.click() }, "Upload model …");
  pick.onchange = async () => {
    const f = pick.files[0];
    pick.value = "";
    if (!f) return;
    upBtn.disabled = true;
    try {
      const r = await uploadChunked(f, "/api/admin/speech-model/upload", (p) => { upBtn.textContent = p < 1 ? `Uploader … ${Math.round(p * 100)} %` : "Kontrollerer …"; });
      toast(`Talemodellen er lagt på serveren (${r.version})`);
    } catch (err) { toast(err.message, "err"); }
    speechCard(box);
  };
  box.replaceChildren(
    h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, "Talemodel til tale-indtastning"),
      h("span", { class: "row" }, pick, upBtn, h("button", { class: "btn sm", onclick: build(false) }, m ? "Tjek / opdatér" : "Byg modellen"), m ? h("button", { class: "btn sm ghost", onclick: build(true) }, "Byg forfra") : null)),
    m ? h("ul", { class: "mini-list" },
      h("li", {}, h("span", {}, "Version"), h("b", { class: "num" }, m.version)),
      h("li", {}, h("span", {}, "Størrelse (hentes til telefonen)"), h("b", { class: "num" }, fmtBytes(m.size))),
      h("li", {}, h("span", {}, "Model"), h("b", {}, m.model)),
      h("li", {}, h("span", {}, "Bygget"), h("b", {}, m.built_at ? ago(m.built_at) : "–")))
      : h("p", { class: "muted", style: { margin: 0 } }, "Modellen er ikke bygget endnu – appen kan ikke tilbyde tale-indtastning, før den er det."),
    h("p", { class: "muted", style: { margin: 0, fontSize: ".85rem" } },
      d.agent_alive ? `Byggeagenten kører${d.pending ? ` · ${d.pending} i kø` : ""}.` : "Byggeagenten (compose-servicen speech-model) kører ikke – start den med «docker compose up -d speech-model», eller upload en model bygget et andet sted (speech-model.tar fra tools/speech-model/pack.sh).",
      " Fremdrift og seneste kørsler står under Worker-opgaver."));
}

/** Backup (kun superadmin): pg_dump til SFTP efter den faste plan (Planlagte opgaver → Backup til SFTP) og som download. */
export async function backupCard(box) {
  let d;
  try { d = await api("/api/admin/backup"); } catch (err) { box.replaceChildren(errorBox(err)); return; }
  const inp = (v, attrs = {}) => h("input", { value: v ?? "", ...attrs });
  const host = inp(d.host, { placeholder: "backup.example.dk" }), port = inp(d.port, { type: "number", min: 1, max: 65535, style: { width: "90px" } });
  const user = inp(d.user), path = inp(d.path), keep = inp(d.keep, { type: "number", min: 1, max: 1000, style: { width: "90px" } });
  const pw = inp("", { type: "password", autocomplete: "new-password", placeholder: d.has_password ? "(gemt – lad stå tomt for at beholde)" : "" });
  const key = h("textarea", { rows: 3, placeholder: d.has_key ? "(gemt – lad stå tomt for at beholde)" : "-----BEGIN OPENSSH PRIVATE KEY----- …", style: { fontFamily: "monospace", fontSize: ".8rem" } });
  const field = (label, el) => h("label", { class: "field" }, h("span", {}, label), el);
  const call = async (fn, ok) => { try { const r = await fn(); if (ok) toast(ok); return r; } catch (err) { toast(err.message, "err"); } };
  const save = () => call(async () => {
    const body = { host: host.value.trim(), port: Number(port.value) || 22, user: user.value.trim(), path: path.value.trim(), keep: Number(keep.value) || 14 };
    if (pw.value) body.password = pw.value;
    if (key.value.trim()) body.private_key = key.value;
    await api("/api/admin/backup", { method: "PUT", body });
    backupCard(box);
  }, "Gemt");
  const clear = (k, label) => h("button", { class: "btn sm ghost danger", type: "button", onclick: () => confirm(`Fjern ${label}?`) && call(async () => {
    await api("/api/admin/backup", { method: "PUT", body: { host: d.host, port: d.port, user: d.user, path: d.path, keep: d.keep, [k]: "" } }); backupCard(box);
  }, "Fjernet") }, `Fjern ${label}`);
  const trust = () => call(async () => {
    const k = await api("/api/admin/backup/host-key");
    if (!confirm(`Serverens værtsnøgle har fingeraftrykket\n\n${k.fingerprint}\n\nPasser det med serverens (ssh-keygen -lf /etc/ssh/ssh_host_*_key.pub)? Så godkendes den.`)) return;
    await api("/api/admin/backup/host-key", { method: "POST", body: { key: k.key } });
    backupCard(box);
  }, null);
  const test = () => call(async () => {
    const r = await api("/api/admin/backup/test", { method: "POST" });
    toast(`Forbindelsen virker – ${r.files.length} backupfiler i mappen`);
  });
  const run = () => call(() => api("/api/admin/backup/run", { method: "POST" }), "Backup lagt i kø – følg den under Worker-opgaver");
  const l = d.last;
  box.replaceChildren(
    h("h3", { style: { margin: 0 } }, "Backup (pg_dump)"),
    h("p", { class: "muted", style: { margin: 0, fontSize: ".85rem" } },
      "Workeren sender en dump af hver database til SFTP-serveren efter planen (Planlagte opgaver → Backup til SFTP). Filerne er i pg_dumps eget format og gendannes med pg_restore. Kun de nyeste beholdes pr. database."),
    h("div", { class: "row" }, h("b", {}, "Download nu:"), ...d.targets.map(t => h("a", { class: "btn sm", href: `/api/admin/backup/download/${t}`, download: "" },
      { portal: "Hele databasen", users: "Brugerdata", observations: "Observationer" }[t] || t))),
    l ? h("p", { style: { margin: 0 } }, l.ok ? "✅ " : "⚠️ ", `Seneste SFTP-backup ${ago(l.at)}: `,
      l.files.map(f => `${f.name} (${fmtBytes(f.size)})`).join(", ") || "ingen filer", l.error ? h("span", { style: { color: "var(--err)" } }, ` – ${l.error}`) : null) : null,
    h("div", { class: "grid-2" },
      field("SFTP-server", h("div", { class: "row", style: { flexWrap: "nowrap" } }, host, port)),
      field("Bruger", user),
      field("Kodeord", h("div", { class: "row", style: { flexWrap: "nowrap" } }, pw, d.has_password ? clear("password", "kodeord") : null)),
      field("Mappe på serveren", path),
      field("Privat nøgle (i stedet for kodeord, uden adgangskode)", h("div", { class: "stack" }, key, d.has_key ? clear("private_key", "nøgle") : null)),
      field("Behold antal pr. database", keep)),
    h("p", { style: { margin: 0 } }, "Værtsnøgle: ", d.host_key ? h("code", {}, d.host_key) : h("span", { class: "muted" }, "ikke godkendt – workeren forbinder ikke, før den er")),
    h("div", { class: "row" },
      h("button", { class: "btn primary", onclick: save }, "Gem"),
      d.host ? h("button", { class: "btn", onclick: trust }, d.host_key ? "Hent værtsnøgle igen" : "Hent og godkend værtsnøgle") : null,
      d.configured ? h("button", { class: "btn", onclick: test }, "Test forbindelse") : null,
      d.configured ? h("button", { class: "btn", onclick: run }, "Kør backup nu") : null));
}

export async function jobsCard(box) {
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

const SCHED_UNITS = { min: "minut(ter)", hour: "time(r)", day: "døgn", week: "uge(r)" };

const when = (iso) => iso ? `${fmtDate(iso, false)} ${fmtTime(iso)}` : "–";

export async function scheduleCard(box) {
  let d;
  try { d = await api("/api/admin/schedule"); } catch (err) { box.replaceChildren(errorBox(err)); return; }
  const changed = {};
  const save = h("button", { class: "btn primary", disabled: true, onclick: async () => {
    try { await api("/api/admin/schedule", { method: "PUT", body: changed }); toast("Planen er gemt"); scheduleCard(box); }
    catch (err) { toast(err.message, "err"); }
  } }, "Gem plan");
  const rows = d.tasks.map(t => {
    const set = (k, v) => { changed[t.key] = { ...(changed[t.key] || {}), [k]: v }; save.disabled = false; };
    const on = h("input", { type: "checkbox", checked: t.on, onchange: () => set("on", on.checked) });
    const at = h("input", { type: "time", value: t.at, style: { width: "auto" }, onchange: () => set("at", at.value) });
    let interval;
    if (t.unit === "month") {
      interval = h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, "månedligt kl.", at,
        h("button", { class: "btn sm", type: "button", onclick: () => locfindsModal(() => scheduleCard(box)) }, "Fordeling…"));
    } else {
      const every = h("input", { type: "number", min: 1, max: 1000, value: t.every, style: { width: "64px" }, oninput: () => set("every", Number(every.value) || 1) });
      const unit = h("select", { style: { width: "auto" }, onchange: () => { set("unit", unit.value); sync(); } },
        ...Object.entries(SCHED_UNITS).map(([u, txt]) => h("option", { value: u, selected: u === t.unit }, txt)));
      const atWrap = h("span", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, unit.value === "hour" ? "fra kl." : "kl.", at);
      const sync = () => { atWrap.classList.toggle("hide", unit.value === "min"); atWrap.firstChild.textContent = unit.value === "hour" ? "fra kl." : unit.value === "week" ? "mandag kl." : "kl."; };
      sync();
      interval = h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, "hver", every, unit, atWrap);
    }
    return h("tr", {},
      h("td", {}, h("label", { class: "check" }, on, h("b", {}, t.label)), h("small", { class: "muted", style: { display: "block" } }, t.desc)),
      h("td", {}, interval),
      h("td", { class: "muted nowrap" }, t.last ? ago(t.last) : "–"),
      h("td", { class: "muted nowrap" }, t.unit === "month" ? "se fordeling" : t.on ? when(t.next) : "slået fra"));
  });
  box.replaceChildren(h("h3", {}, "Planlagte opgaver"),
    h("p", { class: "muted", style: { fontSize: ".85rem", marginTop: 0 } },
      "Hvor ofte workeren selv starter opgaverne. «Hver 6. time fra kl. 01:20» kører 01:20, 07:20, 13:20 og 19:20; ugentlige opgaver kører mandag. Ændringer gælder fra workerens næste tjek (inden for 30 sekunder). Hentning fra DOFbasen og vejrarkivet styres under Data og Vejr."),
    h("div", { class: "table-wrap" }, h("table", { class: "data stack-sm", style: { "--cols": 2 } },
      h("thead", {}, h("tr", {}, h("th", {}, "Opgave"), h("th", {}, "Interval"), h("th", {}, "Sidst"), h("th", {}, "Næste"))),
      h("tbody", {}, ...rows))),
    h("div", { class: "row" }, save));
}

async function locfindsModal(onSaved) {
  let d;
  try { d = await api("/api/admin/locfinds"); } catch (err) { toast(err.message, "err"); return; }
  const days = Object.fromEntries(d.batches.map(b => [b.id, b.day]));
  const on = h("input", { type: "checkbox", checked: d.on });
  const at = h("input", { type: "time", value: d.at, style: { width: "auto" } });
  const first = h("input", { type: "number", min: 1, max: d.max_day, value: 1, style: { width: "64px" } });
  const last = h("input", { type: "number", min: 1, max: d.max_day, value: d.max_day, style: { width: "64px" } });
  const order = h("select", { style: { width: "auto" } }, h("option", { value: "navn" }, "efter navn"), h("option", { value: "size" }, "største først"));
  const inputs = {};
  const runOne = async (ids, label) => {
    try { await api("/api/admin/worker/loc_finds", { method: "POST", body: ids ? { afdelinger: ids } : {} }); toast(`${label}: lagt i kø`); }
    catch (err) { toast(err.message, "err"); }
  };
  const rows = d.batches.map(b => {
    inputs[b.id] = h("input", { type: "number", min: 1, max: d.max_day, value: b.day, style: { width: "64px" }, oninput: (e) => { days[b.id] = Number(e.target.value); } });
    const state = b.started_at && (!b.at || b.started_at > b.at) ? "kører/afbrudt" : b.queued_at && (!b.at || b.queued_at > b.at) ? "i kø" : null;
    return h("tr", {},
      h("td", {}, h("b", {}, b.navn)),
      h("td", { class: "right num" }, fmtN(b.n_loc)),
      h("td", {}, inputs[b.id]),
      h("td", { class: "muted nowrap" }, state && !b.error ? h("span", { class: "tag bemaerk" }, state) : b.at ? `${when(b.at)} · ${dur(b.secs)} · ${fmtN(b.rows)} rækker` : "aldrig",
        b.error ? h("div", { style: { color: "var(--err)", whiteSpace: "normal" } }, b.error) : null),
      h("td", { class: "right" }, h("button", { class: "btn sm", type: "button", onclick: () => runOne([b.id], b.navn) }, "Kør nu")));
  });
  const distribute = () => {
    const ids = d.batches.slice().sort((a, b) => order.value === "size" ? b.n_loc - a.n_loc : 0).map(b => b.id);
    const lo = Math.min(Number(first.value), Number(last.value)), hi = Math.max(Number(first.value), Number(last.value));
    ids.forEach((id, k) => { days[id] = lo + (ids.length > 1 ? Math.round(k * (hi - lo) / (ids.length - 1)) : 0); inputs[id].value = days[id]; });
  };
  const m = modal("Lokale hits: fordeling over måneden", h("div", { class: "stack" },
    h("p", { class: "muted", style: { margin: 0 } },
      "Estimerede fund pr. art og lokalitet gennem alle år genberegnes én gang om måneden, én lokalafdeling ad gangen, så belastningen spredes. Hver afdeling kører på sin dag i måneden fra det valgte klokkeslæt (dag 1–28, så alle måneder har dagen). Er workeren nede på dagen, køres afdelingen, så snart den er oppe igen. Desuden opdateres hver nat (under Genberegning) de lokaliteter og arter, der har fået nye observationer."),
    h("div", { class: "row", style: { gap: "1rem", flexWrap: "wrap" } },
      h("label", { class: "check" }, on, "Slået til"),
      h("label", { class: "row", style: { gap: "6px" } }, "Fra kl.", at)),
    h("div", { class: "row card", style: { gap: "6px", flexWrap: "wrap", padding: ".6rem" } },
      "Fordel jævnt fra dag", first, "til dag", last, order,
      h("button", { class: "btn sm", type: "button", onclick: distribute }, "Fordel")),
    h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, h("th", {}, "Lokalafdeling"), h("th", { class: "right" }, "Lokaliteter"), h("th", {}, "Dag"), h("th", {}, "Seneste beregning"), h("th", {}))),
      h("tbody", {}, ...rows))),
    h("div", { class: "row between" },
      h("button", { class: "btn", type: "button", onclick: () => { if (confirm("Genberegn alle lokalafdelinger nu? Det kan tage lang tid.")) runOne(null, "Alle lokalafdelinger"); } }, "Kør alle nu"),
      h("button", { class: "btn primary", type: "button", onclick: async () => {
        try {
          await api("/api/admin/locfinds", { method: "PUT", body: { on: on.checked, at: at.value, days } });
          toast("Fordelingen er gemt"); m.close(); onSaved?.();
        } catch (err) { toast(err.message, "err"); }
      } }, "Gem"))), { wide: true });
}

/** Fanen Drift: kortene opdateres hver for sig med hver sin frekvens (every rydder op, når fanen forlades). */
export function renderDrift(body, every) {
  const jobsBox = h("div", { class: "card stack" }), uploadBox = h("div", { class: "card stack mt" }), opsBox = h("div", { class: "mt" });
  const storageBox = h("div", { class: "card stack mt" }), schedBox = h("div", { class: "card stack mt" }), speechBox = h("div", { class: "card stack mt" });
  body.append(jobsBox, schedBox, speechBox, storageBox, uploadBox, opsBox);
  if (state.user.role === "superadmin") { const backupBox = h("div", { class: "card stack mt" }); schedBox.after(backupBox); backupCard(backupBox); }
  every(() => speechCard(speechBox), 10000);
  scheduleCard(schedBox);
  storageCard(storageBox);
  archiveUpload(uploadBox);
  every(() => jobsCard(jobsBox), 3000);
  every(() => { opsBox.replaceChildren(); ops(opsBox); }, 15000);
}
