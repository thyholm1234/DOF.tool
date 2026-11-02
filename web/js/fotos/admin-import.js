// Import af fotoarkivet: vælg en mappe/zip på serveren, eller upload en zip i dele (genoptagelig); følg importen og læs rapporten.
import { api, h, toast } from "../core.js";
import { dkDate, nowrap, bytes } from "./common.js";

const PARALLEL = 3, RETRIES = 4;
const OPTIONS = [
  ["import_comments", "Importér kommentarerne fra netfugl.dk (vises som arkiv, kun til læsning)", true],
  ["overwrite_local", "Overskriv også fotos, der er rettet her", false],
  ["force", "Importér alt igen, også fotos der ikke er ændret siden sidst", false],
  ["dry_run", "Prøvekørsel: gennemgå kilden og rapportér, uden at gemme noget", false],
  ["keep_files", "Behold zip-filen på serveren efter importen", false],
];
const LABELS = { fotos_i_kilden: "Fotos i kilden", oprettet: "Oprettet", opdateret: "Opdateret", uaendret: "Uændrede", sprunget_lokalt: "Sprunget over (rettet her)", fejl: "Fejl",
  billeder_genbrugt: "Billeder genbrugt", billeder_erstattet: "Billeder erstattet", fugle_uden_artnr: "Fugle uden art i DOFbasen", ukendte_koder: "Nye koder", kommentarer: "Kommentarer", afvist: "Afviste filer (usikre)", fotografer: "Nye fotografer", grupper: "Grupper" };

const statGrid = (stats) => h("div", { class: "fot-statgrid" }, ...Object.entries(stats || {}).filter(([k, v]) => v || ["oprettet", "opdateret", "fejl"].includes(k)).map(([k, v]) => h("div", {}, h("b", {}, nowrap(v)), h("span", {}, LABELS[k] || k))));
const logBox = (log) => log?.length ? h("div", { class: "fot-log" }, ...log.map(l => h("div", { class: l.niveau }, l.netfugl_id ? `#${l.netfugl_id}: ` : "", l.besked))) : null;

async function putPart(url, blob) {
  for (let attempt = 1; ; attempt++) {
    try {
      const res = await fetch(url, { method: "PUT", body: blob, credentials: "same-origin", headers: { "X-Requested-With": "fetch", "Content-Type": "application/octet-stream" } });
      if (res.ok) return;
      if (res.status < 500 && res.status !== 408 && res.status !== 429) throw new Error((await res.json().catch(() => ({}))).detail || `Fejl ${res.status}`);
      throw new Error(`Serverfejl ${res.status}`);
    } catch (e) {
      if (attempt >= RETRIES || /^(Delen|Uploaden|Importen)/.test(e.message)) throw e;
      await new Promise(r => setTimeout(r, 800 * attempt));
    }
  }
}

export async function page() {
  const root = h("div", { class: "stack" });
  const opts = Object.fromEntries(OPTIONS.map(([k, , d]) => [k, h("input", { type: "checkbox", checked: d })]));
  const limit = h("input", { type: "number", min: 1, placeholder: "alle", style: { width: "100px" } });
  const options = () => ({ ...Object.fromEntries(Object.entries(opts).map(([k, el]) => [k, el.checked])), ...(limit.value ? { limit: +limit.value } : {}) });
  const live = h("div", { class: "stack" }), history = h("div", { class: "stack" }), folders = h("div", { class: "stack" });
  const bar = h("div", { class: "fot-progress", hidden: true }, h("div", { style: { width: "0%" } })), info = h("div", { class: "muted small" });
  const file = h("input", { type: "file", accept: ".zip,application/zip" });
  const go = h("button", { class: "btn", type: "button" }, "Upload og importér");
  let timer = null;

  async function follow(id) {
    clearInterval(timer);
    const tick = async () => {
      const s = await api(`/api/admin/fotos/import/${id}`).catch(() => null);
      if (!s) return;
      live.replaceChildren(h("div", { class: "card stack" }, h("b", {}, `Import #${s.id}: ${s.filename || s.sti}`),
        h("div", {}, s.status === "klar" ? "Venter på workeren …" : s.status === "koerer" ? "Importerer … (kan tage lang tid; se Admin → Drift for fremdrift)" : s.status === "faerdig" ? "✓ Færdig" : s.status === "fejl" ? "✗ Fejlede" : s.status),
        statGrid(s.stats), logBox(s.log)));
      if (["faerdig", "fejl"].includes(s.status)) { clearInterval(timer); timer = null; refresh(); }
    };
    await tick();
    timer = setInterval(tick, 2500);
    window.addEventListener("hashchange", () => clearInterval(timer), { once: true });
  }
  async function startFolder(sti) {
    if (!confirm(`Start importen af «${sti}»?`)) return;
    try { const s = await api("/api/admin/fotos/import/mappe", { method: "POST", body: { sti, ...options() } }); toast("Importen er sat i gang"); follow(s.id); } catch (e) { toast(e.message, "err"); }
  }
  async function loadFolders() {
    const list = await api("/api/admin/fotos/import/mapper").catch(() => []);
    folders.replaceChildren(...(list.length ? list.map(f => h("div", { class: "row" }, h("b", {}, f.sti), h("span", { class: "muted small" }, f.slags === "zip" ? "zip-fil" : `${f.fotos ? nowrap(f.fotos) + " fotos" : "zip-filer: " + f.zip}`),
      h("button", { class: "btn primary sm", type: "button", onclick: () => startFolder(f.sti) }, "Importér"))) : [h("p", { class: "muted" }, "Ingen mapper fundet. Læg arkivet i importmappen på serveren (se docs/drift.md).")]));
  }
  const resumeKey = (f) => `fot-import:${f.name}:${f.size}:${f.lastModified}`;
  async function upload() {
    const f = file.files[0];
    if (!f) { toast("Vælg en zip-fil først", "err"); return; }
    go.disabled = true; bar.hidden = false;
    try {
      let rec = null;
      try { rec = JSON.parse(localStorage.getItem(resumeKey(f)) || "null"); } catch { /* ingen */ }
      let created, done = new Set();
      if (rec) {
        const st = await api(`/api/admin/fotos/import/${rec.id}`).catch(() => null);
        if (st?.status === "uploader") { created = { id: rec.id, chunk_size: rec.chunk, parts: rec.parts }; done = new Set((await api(`/api/admin/fotos/import/${rec.id}/dele`)).dele); }
      }
      if (!created) {
        created = await api("/api/admin/fotos/import", { method: "POST", body: { filename: f.name, size: f.size } });
        localStorage.setItem(resumeKey(f), JSON.stringify({ id: created.id, chunk: created.chunk_size, parts: created.parts }));
      }
      const { id, chunk_size: cs, parts } = created;
      const todo = [...Array(parts).keys()].filter(n => !done.has(n));
      let sent = done.size * cs, next = 0;
      const show = () => { const pct = Math.min(100, Math.round(100 * sent / f.size)); bar.firstChild.style.width = pct + "%"; info.textContent = `${bytes(Math.min(sent, f.size))} af ${bytes(f.size)} (${pct} %)`; };
      show();
      await Promise.all(Array.from({ length: PARALLEL }, async () => {
        while (next < todo.length) { const n = todo[next++]; await putPart(`/api/admin/fotos/import/${id}/del/${n}`, f.slice(n * cs, Math.min(f.size, (n + 1) * cs))); sent += Math.min(cs, f.size - n * cs); show(); }
      }));
      info.textContent = "Kontrollerer filen på serveren …";
      await api(`/api/admin/fotos/import/${id}/start`, { method: "POST", body: options() });
      localStorage.removeItem(resumeKey(f));
      toast("Importen er sat i gang"); info.textContent = ""; bar.hidden = true;
      follow(id);
    } catch (e) { toast(e.message, "err"); info.textContent = e.message + " – vælg samme fil igen for at genoptage uploaden."; }
    go.disabled = false; refresh();
  }
  go.onclick = upload;
  async function refresh() {
    const list = await api("/api/admin/fotos/import").catch(() => []);
    history.replaceChildren(h("h3", {}, "Tidligere importer"),
      h("button", { class: "btn sm danger", type: "button", onclick: async () => {
        if (!confirm("Slet midlertidige zip-uploads, som ingen import bruger lige nu? Fotos og importlog beholdes.")) return;
        try { const r = await api("/api/admin/fotos/import/uploads", { method: "DELETE" }); toast(`Slettede ${r.filer} filer (${(r.bytes / 1048576).toFixed(0)} MB)`); } catch (e) { toast(e.message, "err"); } } }, "Slet midlertidige zip-uploads"), ...(list.length ? list.map(i => h("details", { class: "card" },
      h("summary", {}, `#${i.id} · ${i.filename || i.sti} · ${i.status} · ${dkDate(i.created_at, { time: true })}`), statGrid(i.stats), logBox(i.log),
      ["klar", "uploader", "faerdig", "fejl"].includes(i.status) ? h("button", { class: "btn sm danger", type: "button", onclick: async () => {
        if (!confirm("Fjern importen og dens log? Fotos beholdes.")) return;
        try { await api(`/api/admin/fotos/import/${i.id}`, { method: "DELETE" }); refresh(); } catch (e) { toast(e.message, "err"); } } }, "Fjern") : null)) : [h("p", { class: "muted" }, "Ingen importer endnu.")]));
    const running = list.find(i => ["klar", "koerer"].includes(i.status));
    if (running && !timer) follow(running.id);
  }
  root.append(
    h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "Indlæs fotoarkivet"),
      h("p", { class: "muted" }, "Arkivet kommer fra tools/netfugl_fotos (mappen Fotos/ eller Fotos-*.zip). Importen kan køres igen uden dubletter: fotos genkendes på deres netfugl-nummer, uændrede springes over, og fotos du har rettet her overskrives ikke. Importen sender ingen notifikationer. Til sidst matches fotograferne automatisk på navn."),
      h("div", { class: "stack" }, ...OPTIONS.map(([k, t]) => h("label", { class: "check" }, opts[k], h("span", {}, t))),
        h("label", { class: "check" }, limit, h("span", {}, "Importér kun de første N fotos (til test)")))),
    h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "Mappe eller zip på serveren (anbefalet til 40–80 GB)"), folders),
    h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "Eller upload en zip (højst 20 GB)"), h("label", { class: "field" }, h("span", {}, "Zip-fil"), file), h("div", { class: "row" }, go), bar, info),
    live, history);
  loadFolders(); refresh();
  return root;
}
