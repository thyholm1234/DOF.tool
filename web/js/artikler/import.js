// Import af artikelarkivet: vælg zip, upload i dele (genoptagelig, 3 ad gangen), vælg indstillinger, følg importen og læs rapporten.
import { api, h, toast } from "../core.js";
import { dkDate, nowrap, loadingBox } from "./common.js";

const PARALLEL = 3, RETRIES = 4;
const bytes = (n) => n >= 1e9 ? `${(n / 1e9).toFixed(2).replace(".", ",")} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1).replace(".", ",")} MB` : `${Math.round(n / 1e3)} kB`;
const OPTIONS = [
  ["import_comments", "Importér kommentarerne fra netfugl.dk (vises som arkiv, kun til læsning)", true],
  ["fetch_images", "Hent billeder, der kun findes som adresser på netfugl.dk, og gem dem her (ellers vises de som links)", true],
  ["publish", "Publicér nye artikler med det samme (ellers bliver de kladder)", true],
  ["overwrite_local", "Overskriv også artikler, der er rettet i portalen", false],
  ["force", "Importér alt igen, også artikler der ikke er ændret siden sidst", false],
  ["dry_run", "Prøvekørsel: gennemgå zip'en og rapportér, uden at gemme noget", false],
  ["keep_zip", "Behold zip-filen på serveren efter importen", false],
];
const LABELS = { artikler_i_zip: "Artikler i zip", oprettet: "Oprettet", opdateret: "Opdateret", uaendret: "Uændrede", sprunget_lokalt: "Sprunget over (rettet i portalen)", fejl: "Fejl",
  billeder: "Billeder", hentede_billeder: "Hentede billeder", billeder_findes_ikke: "Billeder der ikke findes mere", pdf: "PDF'er", filer: "Andre filer", kommentarer: "Kommentarer", afvist: "Afviste filer (usikre)" };

export function statGrid(stats) {
  const always = ["oprettet", "opdateret", "fejl"];
  return h("div", { class: "art-statgrid" }, ...Object.entries(stats || {}).filter(([k, v]) => v || always.includes(k)).map(([k, v]) => h("div", {}, h("b", {}, nowrap(v)), h("span", {}, LABELS[k] || k))));
}
export function logBox(log) {
  return log?.length ? h("div", { class: "art-log" }, ...log.map(l => h("div", { class: l.niveau }, l.netfugl_id ? `#${l.netfugl_id}: ` : "", l.besked))) : null;
}

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
  const root = h("div", { class: "art-adm stack" });
  const file = h("input", { type: "file", accept: ".zip,application/zip" });
  const opts = Object.fromEntries(OPTIONS.map(([k, , d]) => [k, h("input", { type: "checkbox", checked: d })]));
  const bar = h("div", { class: "art-progress", hidden: true }, h("div", { style: { width: "0%" } }));
  const info = h("div", { class: "muted small" });
  const live = h("div", { class: "stack" });
  const history = h("div", { class: "stack" });
  const go = h("button", { class: "btn primary", type: "button" }, "Upload og importér");
  let timer = null;

  const resumeKey = (f) => `art-import:${f.name}:${f.size}:${f.lastModified}`;
  async function upload() {
    const f = file.files[0];
    if (!f) { toast("Vælg en zip-fil først", "err"); return; }
    go.disabled = true; bar.hidden = false;
    try {
      let rec = null;
      try { rec = JSON.parse(localStorage.getItem(resumeKey(f)) || "null"); } catch { /* ingen */ }
      let created, done = new Set();
      if (rec) {
        const st = await api(`/api/admin/artikler/import/${rec.id}`).catch(() => null);
        if (st?.status === "uploader") { created = { id: rec.id, chunk_size: rec.chunk, parts: rec.parts }; done = new Set((await api(`/api/admin/artikler/import/${rec.id}/dele`)).dele); }
      }
      if (!created) {
        created = await api("/api/admin/artikler/import", { method: "POST", body: { filename: f.name, size: f.size } });
        created.chunk = created.chunk_size;
        localStorage.setItem(resumeKey(f), JSON.stringify({ id: created.id, chunk: created.chunk_size, parts: created.parts }));
      }
      const { id, chunk_size: cs, parts } = created;
      const todo = [...Array(parts).keys()].filter(n => !done.has(n));
      let sent = done.size * cs, t0 = Date.now(), next = 0;
      const show = () => {
        const pct = Math.min(100, Math.round(100 * sent / f.size)), sec = (Date.now() - t0) / 1000;
        bar.firstChild.style.width = pct + "%";
        info.textContent = `${bytes(Math.min(sent, f.size))} af ${bytes(f.size)} (${pct} %)${sec > 2 ? ` · ${bytes((sent - done.size * cs) / sec)}/s` : ""}${done.size ? ` · genoptager efter ${done.size} dele` : ""}`;
      };
      show();
      const worker = async () => {
        while (next < todo.length) {
          const n = todo[next++];
          await putPart(`/api/admin/artikler/import/${id}/del/${n}`, f.slice(n * cs, Math.min(f.size, (n + 1) * cs)));
          sent += Math.min(cs, f.size - n * cs); show();
        }
      };
      await Promise.all(Array.from({ length: PARALLEL }, worker));
      info.textContent = "Kontrollerer filen på serveren …";
      await api(`/api/admin/artikler/import/${id}/start`, { method: "POST", body: Object.fromEntries(Object.entries(opts).map(([k, el]) => [k, el.checked])) });
      localStorage.removeItem(resumeKey(f));
      toast("Importen er sat i gang");
      info.textContent = "";
      bar.hidden = true;
      follow(id);
    } catch (e) {
      toast(e.message, "err");
      info.textContent = e.message + " – vælg samme fil igen for at genoptage uploaden.";
    }
    go.disabled = false;
    refresh();
  }
  go.onclick = upload;

  async function follow(id) {
    clearInterval(timer);
    const tick = async () => {
      const s = await api(`/api/admin/artikler/import/${id}`).catch(() => null);
      if (!s) return;
      live.replaceChildren(h("div", { class: "card stack" }, h("b", {}, `Import #${s.id}: ${s.filename}`),
        h("div", {}, s.status === "klar" ? "Venter på workeren …" : s.status === "koerer" ? "Importerer … (kan tage flere minutter)" : s.status === "faerdig" ? "✓ Færdig" : s.status === "fejl" ? "✗ Fejlede" : s.status),
        statGrid(s.stats), logBox(s.log)));
      if (["faerdig", "fejl"].includes(s.status)) { clearInterval(timer); refresh(); }
    };
    await tick();
    timer = setInterval(tick, 2500);
    window.addEventListener("hashchange", () => clearInterval(timer), { once: true });
  }
  async function refresh() {
    const list = await api("/api/admin/artikler/import").catch(() => []);
    history.replaceChildren(h("h3", {}, "Tidligere importer"), ...(list.length ? list.map(i => h("details", { class: "card" },
      h("summary", {}, `#${i.id} · ${i.filename} · ${bytes(i.size)} · ${i.status} · ${dkDate(i.created_at, { time: true })}`),
      statGrid(i.stats), logBox(i.log), i.status === "klar" || i.status === "uploader" || i.status === "faerdig" || i.status === "fejl" ? h("button", { class: "btn sm danger", type: "button", onclick: async () => {
        if (!confirm("Fjern importen og dens log? Artiklerne beholdes.")) return;
        try { await api(`/api/admin/artikler/import/${i.id}`, { method: "DELETE" }); refresh(); } catch (e) { toast(e.message, "err"); } } }, "Fjern") : null)) : [h("p", { class: "muted" }, "Ingen importer endnu.")]));
    const running = list.find(i => ["klar", "koerer"].includes(i.status));
    if (running && !timer) follow(running.id);
  }

  root.append(h("div", { class: "card stack" },
    h("h3", { style: { margin: 0 } }, "Indlæs artikelarkivet fra en zip"),
    h("p", { class: "muted" }, "Zip'en indeholder én mappe pr. artikel (0012, 0155 …) med meta.json, artikel.md, kommentarer.json, billeder/ og filer/ – se MANIFEST.md i arkivet. ",
      "Importen kan køres igen, når arkivet er blevet større: artikler genkendes på deres netfugl.dk-nummer, uændrede springes over, og artikler du har rettet her overskrives ikke. ",
      "Turrapporter i zip'en ignoreres indtil videre. Importen sender ingen notifikationer."),
    h("label", { class: "field" }, h("span", {}, "Zip-fil"), file),
    h("div", { class: "stack" }, ...OPTIONS.map(([k, t]) => h("label", { class: "check" }, opts[k], h("span", {}, t)))),
    h("div", { class: "row" }, go), bar, info),
    live, history);
  root.append(loadingBox("")); root.lastChild.remove();
  refresh();
  return root;
}
