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
    fcmCard(),
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
