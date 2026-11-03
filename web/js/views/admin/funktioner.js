// Admin → Funktioner (superadmin): funktionsflag og FCM-konto.
import { api, h, seg, toast, errorBox } from "../../core.js";
import { refreshUser } from "../../app.js";

/** Funktioner (kun superadmin): nye moduler kan slås fra, vises som forhåndsvisning eller slås til for alle. */
export async function featuresPanel(body) {
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
    fcmCard(), lydquizCard(),
    h("p", { class: "mt" }, h("a", { class: "btn", href: "#/dofbasen" }, "Åbn DOFbasen →")));
}

/** Firebase-servicekonto til push i Android-appen. Kan også sættes i .env (FCM_SERVICE_ACCOUNT_B64), som så vinder. */
function fcmCard() {
  const box = h("div", { class: "card stack mt" });
  const draw = (d) => {
    const file = h("input", { type: "file", accept: "application/json,.json" });
    file.onchange = async () => {
      const f = file.files[0]; if (!f) return;
      try { draw(await api("/api/admin/fcm", { method: "PUT", body: { account: await f.text() } })); toast("Firebase-nøglen er gemt"); }
      catch (err) { toast(err.message, "err"); }
    };
    box.replaceChildren(h("h3", { style: { margin: 0 } }, "Push til Android-appen (Firebase)"),
      d.source ? h("p", { style: { margin: 0 } }, `✅ Sat op ${d.source === "env" ? "i .env" : "her"} – projekt ${d.project_id} (${d.client_email}). ${d.devices} app-enheder registreret.`)
        : h("div", { class: "notice warn" }, `Ingen servicekonto – appen får ikke push. ${d.devices} app-enheder venter.`),
      d.last_error ? h("p", { class: "muted", style: { margin: 0, wordBreak: "break-word" } }, "Seneste fejl fra Firebase: " + d.last_error) : null,
      d.source === "env" ? null : h("label", { class: "field" }, h("span", {}, "Upload servicekontoens JSON-fil (Firebase → Projektindstillinger → Tjenestekonti → Generér ny privat nøgle)"), file),
      d.source === "admin" ? h("button", { class: "btn sm ghost danger", style: { alignSelf: "flex-start" }, onclick: async () => {
        if (confirm("Fjern Firebase-nøglen? Appen får så ikke push.")) draw(await api("/api/admin/fcm", { method: "DELETE" }));
      } }, "Fjern nøgle") : null);
  };
  api("/api/admin/fcm").then(draw).catch((err) => box.replaceChildren(errorBox(err)));
  return box;
}

/** Lydquiz: xeno-canto-nøgle (gratis, fra xeno-canto-kontoen) og filtre for, hvilke optagelser der findes automatisk pr. art. Nøglen vises aldrig igen efter at være gemt. */
function lydquizCard() {
  const box = h("div", { class: "card stack mt" });
  const sel = (label, key, d, opts) => h("label", { class: "field" }, h("span", {}, label), h("select", { "data-k": key }, ...opts.map(([v, t]) => h("option", { value: v, selected: String(d[key]) === v }, t))));
  const num = (label, key, d, min, max) => h("label", { class: "field" }, h("span", {}, label), h("input", { type: "number", "data-k": key, min, max, value: d[key] }));
  const draw = (d) => {
    const key = h("input", { type: "password", autocomplete: "off", placeholder: d.has_key ? `Gemt (…${d.key_hint}) – skriv for at skifte` : "xeno-canto API-nøgle", "data-k": "xc_key" });
    const result = h("div", { class: "muted" });
    const form = h("div", { class: "stack" },
      h("label", { class: "field" }, h("span", {}, "xeno-canto API-nøgle (xeno-canto.org → din konto → API-nøgle)"), key),
      sel("Mindste kvalitet", "min_quality", d, [["A", "A (bedst)"], ["B", "B eller bedre"], ["C", "C eller bedre"]]),
      sel("Område", "area", d, [["europe", "Europa"], ["denmark", "Kun Danmark"], ["", "Hele verden"]]),
      num("Optagelser gemt pr. art", "per_species", d, 3, 50), num("Længste optagelse (sekunder)", "max_length", d, 5, 300),
      h("label", { class: "field" }, h("span", {}, "Ekstra søgetekst (xeno-canto-syntaks, valgfri – fx «method:field recording»)"), h("input", { type: "text", "data-k": "extra", value: d.extra || "", maxlength: 200 })));
    const collect = () => Object.fromEntries([...form.querySelectorAll("[data-k]")].map(e => [e.dataset.k, e.type === "number" ? +e.value : e.value]));
    const save = async (extra = {}) => { try { draw(await api("/api/admin/lydquiz", { method: "PUT", body: { ...collect(), ...extra } })); toast("Gemt"); } catch (err) { toast(err.message, "err"); } };
    const st = d.stats || {};
    box.replaceChildren(h("h3", { style: { margin: 0 } }, "Lydquiz (xeno-canto og Macaulay Library)"),
      d.has_key ? null : h("div", { class: "notice warn" }, "Ingen xeno-canto-nøgle – lydquizzen kan kun bruge Macaulay-optagelser, som redaktører lægger ind."),
      h("p", { class: "muted", style: { margin: 0 } }, `Gemt lige nu: ${st.xc?.optagelser || 0} xeno-canto-optagelser (${st.xc?.arter || 0} arter) og ${st.ml?.optagelser || 0} Macaulay-optagelser (${st.ml?.arter || 0} arter). Optagelser hentes automatisk første gang en art indgår i en test.`),
      form,
      h("div", { class: "row" }, h("button", { class: "btn primary", onclick: () => save() }, "Gem"),
        h("button", { class: "btn", onclick: async () => {
          result.textContent = "Prøver …";
          try { await save(); const r = await api("/api/admin/lydquiz/test", { method: "POST" });
            result.textContent = r.ok ? `✅ ${r.antal} optagelser af ${r.art} passer til filtrene (søgning: ${r.soegning})` : `❌ ${r.fejl} (søgning: ${r.soegning})`; }
          catch (err) { result.textContent = err.message; }
        } }, "Gem og afprøv (Rødhals)"),
        d.has_key ? h("button", { class: "btn ghost danger", onclick: () => { if (confirm("Fjern nøglen?")) save({ clear_key: true, xc_key: "" }); } }, "Fjern nøgle") : null,
        h("button", { class: "btn ghost", title: "Glem de hentede xeno-canto-optagelser, så de hentes igen med de aktuelle filtre", onclick: async () => {
          if (!confirm("Slet alle hentede xeno-canto-optagelser? (Macaulay-optagelser beholdes)")) return;
          const r = await api("/api/admin/lydquiz/cache", { method: "DELETE" }); toast(`${r.slettet} optagelser slettet`); draw(await api("/api/admin/lydquiz"));
        } }, "Ryd hentede optagelser")),
      result);
  };
  api("/api/admin/lydquiz").then(draw).catch((err) => box.replaceChildren(errorBox(err)));
  return box;
}
