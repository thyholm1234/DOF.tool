// Læring 2.0 – bygger: oversigt (kurser, øvelser, forløb, hold, import/eksport) – spec §8.1.
import { api, h, toast, errorBox, modal, empty, ago } from "../../core.js";
import { BASE, loadingBox, chip } from "../common.js";

export async function render(view, parts, info) {
  const [a, b] = parts;
  if (a === "kursus" && b) return (await import("./kursus.js")).render(view, [b], info);
  if (a === "forloeb" && b && info.forloeb) return (await import("./forloeb.js")).render(view, [b], info);
  const tab = a || "kurser";
  const tabs = h("div", { class: "lr-subtabs" }, ...[["kurser", "Kurser"], ["ovelser", "Øvelser og quizzer"], info.forloeb ? ["forloeb", "Forløb"] : null, info.forloeb ? ["hold", "Hold"] : null, ["import", "Import og eksport"]].filter(Boolean)
    .map(([k, t]) => h("a", { href: `${BASE}/byg/${k}`, class: tab === k ? "on" : "" }, t)));
  const body = h("div", {}, loadingBox());
  view.append(tabs, body);
  try {
    if (tab === "forloeb") return (await import("./forloeb.js")).list(body, info);
    if (tab === "hold") return (await import("../hold.js")).list(body, info);
    if (tab === "import") return importPage(body);
    const rows = await api("/api/learn/byg/oversigt");
    const want = tab === "ovelser" ? (r) => r.kind !== "kursus" : (r) => r.kind === "kursus";
    const list = rows.filter(want);
    const create = () => {
      const title = h("input", { required: true, placeholder: tab === "ovelser" ? "Fx 'Måger i 1. vinter'" : "Fx 'Fuglekig om efteråret'" });
      const kind = h("select", {}, ...(tab === "ovelser" ? [["ovelse", "Øvelse"], ["quiz", "Quiz"], ["spil", "Spil"]] : [["kursus", "Kursus"]]).map(([v, t]) => h("option", { value: v }, t)));
      const m = modal(tab === "ovelser" ? "Ny øvelse" : "Nyt kursus", h("form", { class: "stack", onsubmit: async (e) => {
        e.preventDefault();
        try { const r = await api("/api/learn/byg/kurser", { method: "POST", body: { title: title.value, kind: kind.value } }); m.close(); location.hash = `${BASE}/byg/kursus/${r.slug}`; } catch (er) { toast(er.message, "err"); }
      } }, h("label", { class: "lr-f" }, h("span", {}, "Titel"), title), tab === "ovelser" ? h("label", { class: "lr-f" }, h("span", {}, "Type"), kind) : null, h("button", { class: "btn primary" }, "Opret og redigér")));
    };
    body.replaceChildren(h("div", { class: "lr-actions" }, h("button", { class: "btn primary", onclick: create }, tab === "ovelser" ? "+ Ny øvelse" : "+ Nyt kursus")),
      list.length ? h("table", { class: "lr-table" }, h("thead", {}, h("tr", {}, ...["Titel", "Status", "Aktiviteter", "Advarsler", "Ejer", "Rettet"].map(t => h("th", {}, t)))),
        h("tbody", {}, ...list.map(r => h("tr", {}, h("td", {}, h("a", { href: `${BASE}/byg/kursus/${r.slug}` }, r.title), r.kind !== "kursus" ? h("small", { class: "muted" }, ` ${r.kind}`) : null),
          h("td", {}, r.published ? chip(r.changed ? "ændret" : `v${r.version}`, r.changed ? "warn" : "soft") : chip("kladde")), h("td", {}, r.n_units ? `${r.n_units} lektioner · ${r.n_acts}` : r.n_acts),
          h("td", {}, r.fejl ? chip(`${r.fejl} fejl`, "warn") : null, r.advarsler ? chip(`${r.advarsler} adv.`) : null), h("td", {}, r.mine ? "mig" : r.owner || ""), h("td", {}, r.updated_at ? ago(r.updated_at) : ""))))) : empty("Intet her endnu"));
  } catch (e) { body.replaceChildren(errorBox(e)); }
}

function importPage(body) {
  const file = h("input", { type: "file", accept: "application/json,.json" });
  const mode = h("select", {}, h("option", { value: "kopi" }, "Opret som nye kladder (kopi)"), h("option", { value: "overskriv" }, "Overskriv kladder med samme navn"));
  const out = h("div", {});
  let parsed = null;
  file.addEventListener("change", async () => {
    try { parsed = JSON.parse(await file.files[0].text()); } catch { toast("Filen er ikke gyldig JSON", "err"); return; }
    try {
      const r = await api("/api/learn/byg/import", { method: "POST", body: { data: parsed, mode: mode.value, dry_run: true } });
      out.replaceChildren(h("p", {}, "Forhåndsvisning:"), h("ul", {}, ...r.rapport.map(x => h("li", {}, `${x.title} (${x.slug}) – ${x.status} · ${x.n_units} lektioner · ${x.n_acts} aktiviteter`))),
        h("button", { class: "btn primary", onclick: async () => { try { await api("/api/learn/byg/import", { method: "POST", body: { data: parsed, mode: mode.value } }); toast("Importeret som kladder"); location.hash = `${BASE}/byg/kurser`; } catch (e) { toast(e.message, "err"); } } }, "Importér"));
    } catch (e) { out.replaceChildren(errorBox(e)); }
  });
  body.replaceChildren(h("h3", {}, "Importér"), h("p", { class: "muted" }, "Understøtter Trækrutes eksport, DOF.tool-formatet (learning.json) og Lærings egen eksport. Alt importeres som kladder."), h("div", { class: "lr-actions" }, file, mode), out,
    h("h3", {}, "Eksportér"), h("p", { class: "muted" }, "Hent alle dine kurser og øvelser som én fil (medier som adresser, ikke filer)."),
    h("button", { class: "btn", onclick: async () => {
      const d = await api("/api/learn/byg/eksport");
      const a = h("a", { href: URL.createObjectURL(new Blob([JSON.stringify(d, null, 1)], { type: "application/json" })), download: `laering-${new Date().toISOString().slice(0, 10)}.json` });
      document.body.append(a); a.click(); a.remove();
    } }, "Hent alt (.json)"));
}
