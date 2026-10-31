// Admin → Lokaliteter: importér zip'en fra tools/dofbasen_lokaliteter (grænser, typer, beskrivelser, kommuner, IBA),
// se ændringsrapporten og vælg, hvad der skal anvendes (services/location_import.py). Portalens egne grænser
// overskrives aldrig uden at admin vælger det.
import { api, h, fmtN, fmtDate, fmtTime, toast, errorBox, empty } from "../../core.js";

const BASE = "/api/admin/locations/imports";
const STATUS = { uploader: ["plain", "uploader"], validerer: ["bemaerk", "validerer"], klar: ["SUB", "klar til at anvende"],
  anvender: ["bemaerk", "anvender"], anvendt: ["local", "anvendt"], fejl: ["SU", "fejl"] };
const GROUPS = [
  ["nye_lokaliteter", "Nye lokaliteter", "Lokaliteter hos DOFbasen, som portalen ikke kender endnu", true],
  ["detaljer", "Detaljer", "Typer, beskrivelser, kommuner, lokalafdelinger, region og IBA", true],
  ["nye_graenser", "Nye grænser", "Lokaliteter, der ikke har en grænse i portalen", true],
  ["aendrede_graenser", "Ændrede grænser", "DOFbasen har rettet grænsen siden sidste import", true],
  ["fjernede_graenser", "Fjernede grænser", "Grænsen findes ikke længere hos DOFbasen (lukkes, slettes ikke)", false],
];
const busy = (s) => s === "validerer" || s === "anvender";
const mb = (b) => `${(b / 1e6).toLocaleString("da-DK", { maximumFractionDigits: 1 })} MB`;
const when = (iso) => iso ? `${fmtDate(iso, false)} ${fmtTime(iso)}` : "–";

/** Send filen i bidder, i rækkefølge. Ved afbrud svarer serveren 409 med, hvor langt den er – så fortsættes derfra. */
async function upload(file, onProgress) {
  const imp = await api(BASE, { method: "POST", body: { filename: file.name, size: file.size } });
  const post = (url, body) => fetch(url, { method: "POST", headers: { "X-Requested-With": "fetch" }, credentials: "same-origin", body });
  let offset = 0, chunk = 8 * 1024 * 1024, failures = 0;
  while (offset < file.size) {
    const end = Math.min(file.size, offset + chunk);
    let res;
    try { res = await post(`${BASE}/${imp.id}/upload?offset=${offset}`, file.slice(offset, end)); }
    catch { if (++failures > 5) throw new Error("Forbindelsen blev afbrudt"); await new Promise(r => setTimeout(r, 1000 * failures)); continue; }
    if (res.status === 413 && chunk > 256 * 1024) { chunk = Math.max(256 * 1024, chunk >> 1); continue; }
    if (res.status === 409) {
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
  return api(`${BASE}/${imp.id}/finish`, { method: "POST" });
}

function uploadCard(onDone) {
  const input = h("input", { type: "file", accept: ".zip,application/zip" });
  const bar = h("progress", { max: 1, value: 0, class: "hide", style: { width: "100%" } });
  const msg = h("small", { class: "muted" });
  const btn = h("button", { class: "btn primary", type: "button", onclick: async () => {
    const file = input.files?.[0];
    if (!file) { toast("Vælg zip-filen fra hent.py pak"); return; }
    btn.disabled = true; bar.classList.remove("hide"); msg.textContent = `Uploader ${mb(file.size)} …`;
    try {
      await upload(file, (f) => { bar.value = f; msg.textContent = `Uploader … ${Math.round(f * 100)} %`; });
      msg.textContent = "Uploadet – workeren validerer og sammenligner nu.";
      input.value = ""; onDone();
    } catch (err) { msg.textContent = ""; toast(err.message, "err"); }
    finally { btn.disabled = false; bar.classList.add("hide"); }
  } }, "Upload og sammenlign");
  return h("div", { class: "card stack" },
    h("h3", {}, "Importér lokaliteter fra DOFbasen"),
    h("p", { class: "muted", style: { margin: 0 } },
      "Zip-filen laves på en computer med ", h("code", {}, "tools/dofbasen_lokaliteter/hent.py alt"), " og ",
      h("code", {}, "hent.py pak"), ". Intet ændres, før du har set ændringsrapporten og trykket Anvend."),
    h("div", { class: "row wrap", style: { gap: "8px" } }, input, btn), bar, msg);
}

function samples(rows, cols) {
  if (!rows?.length) return null;
  return h("table", { class: "data", style: { fontSize: ".85rem" } },
    h("tbody", {}, ...rows.map(r => h("tr", {}, ...cols.map(c => h("td", {}, c === "loknr" ? h("a", { href: `#/lok/${r.loknr}` }, r.loknr) : r[c] ?? "–"))))));
}

function reportView(imp, reload) {
  const r = imp.report || {};
  const g = r.graenser || {};
  const ex = r.eksempler || {};
  const stat = (n, label) => h("div", { class: "stat" }, h("b", {}, fmtN(n ?? 0)), h("span", {}, label));
  const checks = Object.fromEntries(GROUPS.map(([k, , , on]) => [k, h("input", { type: "checkbox", checked: on })]));
  const conflict = h("select", { style: { width: "auto" } },
    h("option", { value: "behold" }, "Behold portalens grænse (DOFbasens gemmes som historik)"),
    h("option", { value: "dofbasen" }, "Brug DOFbasens grænse"));
  const apply = h("button", { class: "btn primary", type: "button", onclick: async () => {
    const groups = GROUPS.map(([k]) => k).filter(k => checks[k].checked);
    if (!groups.length) { toast("Vælg mindst én gruppe"); return; }
    if (!confirm(`Anvend ${groups.length} gruppe(r) fra ${imp.filename}?`)) return;
    apply.disabled = true;
    try { await api(`${BASE}/${imp.id}/apply`, { method: "POST", body: { groups, conflicts: conflict.value } }); toast("Workeren anvender importen"); reload(); }
    catch (err) { toast(err.message, "err"); apply.disabled = false; }
  } }, "Anvend");
  const count = { nye_lokaliteter: r.nye_lokaliteter, detaljer: (r.nye_detaljer || 0) + (r.aendrede_detaljer || 0),
    nye_graenser: g.nye, aendrede_graenser: g.aendrede, fjernede_graenser: g.fjernede };
  return h("div", { class: "stack" },
    h("div", { class: "stats", style: { gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))" } },
      stat(r.lokaliteter, "lokaliteter i zip'en"), stat(r.med_graense, "med grænse"), stat(r.med_side, "med detaljer"),
      stat(r.nye_lokaliteter, "nye for portalen"), stat(g.nye, "nye grænser"), stat(g.aendrede, "ændrede grænser"),
      stat(g.konflikter, "konflikter"), stat(g.fjernede, "fjernede grænser")),
    r.afviste ? h("div", { class: "notice" }, `${fmtN(r.afviste)} lokaliteter blev afvist ved valideringen`,
      h("details", {}, h("summary", {}, "Se eksempler"), samples(r.afviste_eksempler?.map(([f, why]) => ({ f, why })), ["f", "why"]))) : null,
    r.repareret ? h("small", { class: "muted" }, `${fmtN(r.repareret)} ugyldige polygoner (fx selvskæring) er repareret automatisk.`) : null,
    g.portalrettelser_bevares ? h("small", { class: "muted" }, `${fmtN(g.portalrettelser_bevares)} grænser, portalen har rettet, er uændrede hos DOFbasen og bevares.`) : null,
    r.andet_navn ? h("small", { class: "muted" }, `${fmtN(r.andet_navn)} lokaliteter har et andet navn hos DOFbasen end i portalen (navne følger referencedata og ændres ikke her).`) : null,
    h("details", {}, h("summary", {}, "Eksempler"), h("div", { class: "stack mt" },
      ...[["nye_lokaliteter", "Nye lokaliteter", ["loknr", "navn"]], ["ny", "Nye grænser", ["loknr", "navn", "punkter"]],
        ["aendret", "Ændrede grænser", ["loknr", "navn", "punkter"]], ["konflikt", "Konflikter", ["loknr", "navn", "punkter"]],
        ["fjernede_graenser", "Fjernede grænser", ["loknr", "navn"]], ["andet_navn", "Andet navn hos DOFbasen", ["loknr", "portal", "dofbasen"]]]
        .filter(([k]) => ex[k]?.length).map(([k, t, cols]) => h("div", {}, h("b", {}, t), samples(ex[k], cols))))),
    h("div", { class: "card stack", style: { background: "var(--bg-2)" } },
      h("b", {}, "Anvend"),
      ...GROUPS.map(([k, label, help]) => h("label", { class: "row", style: { gap: "8px", alignItems: "flex-start" } }, checks[k],
        h("span", {}, h("b", {}, `${label} (${fmtN(count[k] ?? 0)})`), h("small", { class: "muted", style: { display: "block" } }, help)))),
      g.konflikter ? h("label", { class: "field" }, h("span", {}, `Konflikter (${fmtN(g.konflikter)}): portalen og DOFbasen har begge ændret grænsen`), conflict) : null,
      h("div", {}, apply)));
}

function importCard(imp, reload) {
  const [k, label] = STATUS[imp.status] || ["plain", imp.status];
  const del = imp.status !== "anvendt" && !busy(imp.status) ? h("button", { class: "btn sm ghost", type: "button", onclick: async () => {
    if (!confirm("Slet importen? Intet i portalen ændres.")) return;
    try { await api(`${BASE}/${imp.id}`, { method: "DELETE" }); reload(); } catch (err) { toast(err.message, "err"); }
  } }, "Slet") : null;
  const applied = imp.applied ? h("div", { class: "muted" }, "Anvendt: ", Object.entries(imp.applied).map(([g, n]) => `${g.replace(/_/g, " ")} ${fmtN(n)}`).join(" · ")) : null;
  return h("div", { class: "card stack" },
    h("div", { class: "row between wrap" },
      h("div", {}, h("b", {}, imp.filename), " ", h("span", { class: `tag ${k}` }, label),
        h("small", { class: "muted", style: { display: "block" } }, `${mb(imp.size)} · uploadet ${when(imp.created_at)}`
          + (imp.applied_at ? ` · anvendt ${when(imp.applied_at)}` : "") + (imp.manifest?.bygget ? ` · høstet ${when(imp.manifest.bygget)}` : ""))),
      del),
    imp.error ? errorBox(new Error(imp.error)) : null,
    busy(imp.status) ? h("p", { class: "muted", style: { margin: 0 } }, "Workeren arbejder … (se også Admin → Drift → Job)") : null,
    applied,
    imp.status === "klar" && imp.report ? reportView(imp, reload) : null);
}

export async function renderLocations(body, every) {
  const list = h("div", { class: "stack mt" });
  let last = "";
  const load = async () => {
    let rows;
    try { rows = await api(BASE); } catch (err) { list.replaceChildren(errorBox(err)); return; }
    const sig = JSON.stringify(rows.map(r => [r.id, r.status, r.error]));
    if (sig === last) return;  // tegn kun igen, når noget er ændret (ellers mister man sine afkrydsninger)
    last = sig;
    list.replaceChildren(...(rows.length ? rows.map(r => importCard(r, () => { last = ""; load(); }))
      : [empty("Ingen importer endnu", "Upload zip-filen fra hent.py pak.")]));
  };
  body.append(uploadCard(() => { last = ""; load(); }), list);
  every(load, 4000);
}
