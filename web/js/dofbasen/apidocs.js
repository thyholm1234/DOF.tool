// DOFbasen: API-dokumentation og brugerens egne API-nøgler (tokens).
import { api, h, fmtN, toast, ago } from "../core.js";
import { head, notice, loadingBox, dbModal } from "./shell.js";
import { layout } from "./common.js";

const ENDPOINTS = [
  ["GET", "/me", "Hvem er jeg (tokenets ejer og grænse)"],
  ["GET", "/observations", "Søg observationer – samme filtre som søgesiden: periode, dato, dato_first, dato_second, dage, aar, maaned, dag, species, omraade, lok, lokal, kommune, iba, stor, artdata, art, fra_art, til_art, antalregl, antal, adf, alder, dragt, sex, metode, obserdata, medobser, koordinat, kommentar, udelad_burfugle, udelad_hybrider_ubestemte, vis_underarter_som_arter, summering, biggestobs, textsearch, sortering, page, limit (højst 500)"],
  ["GET", "/observations/{obsid}", "Én observation"],
  ["GET", "/days/{åååå-mm-dd}?visning=fugle|allfugle|sufugle&afdeling=574", "Observationer på en dag (som observationssiden)"],
  ["GET", "/rare?days=7", "Seneste SU- og SUB-fund"],
  ["GET", "/stats/daily?days=20", "Antal observationer pr. dag"],
  ["GET", "/species", "Artsliste"], ["GET", "/species/{artnr}", "Én art"],
  ["GET", "/locations?q=&afdeling=&kommune=", "Lokaliteter (højst 1000)"], ["GET", "/locations/{loknr}", "Én lokalitet"],
  ["GET", "/areas", "Storlokaliteter og IBA-områder med lokalitetsnumre"],
  ["GET", "/news", "Nyt på DOFbasen"], ["GET", "/lists", "Opslagslister"], ["GET", "/lists/{key}", "Én liste, fx su, sub, adfaerd, alder, dragt, vindstyrke"],
];

export async function page() {
  const base = `${location.origin}/api/dofbasen/v1`;
  const mine = h("div", {}, loadingBox());
  const main = h("div", {},
    head("DOFbasen API"),
    h("p", {}, "Et REST-API til at hente data fra DOFbasen ind i dine egne apps, hjemmesider og analyser. Alle svar er JSON. API'et er kun til læsning, og CORS er åben, så det kan kaldes direkte fra en browser."),
    h("ul", { class: "db-md" },
      h("li", {}, h("b", {}, "Adgang: "), "En administrator skal give dig adgang. Derefter laver du selv dine nøgler herunder."),
      h("li", {}, h("b", {}, "Godkendelse: "), "Send nøglen i headeren ", h("code", {}, "Authorization: Bearer dofb_…"), "."),
      h("li", {}, h("b", {}, "Grænse: "), "Et antal kald pr. minut pr. nøgle (sat af administratoren). Over grænsen svarer API'et 429 med Retry-After."),
      h("li", {}, h("b", {}, "Hemmelige observationer "), "udleveres kun til den observatør, de tilhører."),
      h("li", {}, h("b", {}, "Kildeangivelse: "), "(DOFbasen, www.dofbasen.dk, Dansk Ornitologisk Forening) – se ", h("a", { href: "#/dofbasen/side/rettigheder" }, "Rettigheder"), ".")),
    head("Mine API-nøgler"), mine,
    head("Endpoints"),
    h("p", {}, "Basisadresse: ", h("code", {}, base), " · Maskinlæsbar beskrivelse: ", h("a", { href: "/api/openapi.json", target: "_blank" }, "OpenAPI"), " · ", h("a", { href: "/api/docs#/DOFbasen%20API%20v1", target: "_blank" }, "Interaktiv dokumentation")),
    h("table", { class: "db-table" }, h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Sti"), h("th", {}, "Beskrivelse"))),
      h("tbody", {}, ...ENDPOINTS.map(([m, p, d]) => h("tr", {}, h("td", {}, h("b", {}, m)), h("td", {}, h("code", {}, p)), h("td", {}, d))))),
    head("Eksempler"),
    h("div", { class: "db-code" }, `curl -H "Authorization: Bearer $DOFB_TOKEN" \\\n  "${base}/observations?periode=antaldage&dage=3&species=su"\n\n# Dagens bemærkelsesværdige fra DOF København\ncurl -H "Authorization: Bearer $DOFB_TOKEN" "${base}/days/${new Date().toISOString().slice(0, 10)}?visning=fugle&afdeling=574"`),
    h("div", { class: "db-code", style: { marginTop: "8px" } }, `// JavaScript (browser eller Node)\nconst res = await fetch("${base}/rare?days=7", {\n  headers: { Authorization: "Bearer " + token },\n});\nconst fund = await res.json();`),
    h("div", { class: "db-code", style: { marginTop: "8px" } }, `# Python\nimport requests\nr = requests.get("${base}/observations", params={"periode": "maanedaar", "aar": 2025, "artdata": "art", "art": 1670},\n                 headers={"Authorization": f"Bearer {token}"})\nprint(r.json()["total"])`));
  drawMine(mine);
  return layout(main);
}

async function drawMine(box) {
  let d;
  try { d = await api("/api/dofbasen/me/api"); } catch (err) { box.replaceChildren(notice(err.message, true)); return; }
  if (!d.access) {
    box.replaceChildren(notice("Du har ikke adgang til API'et endnu. Kontakt en administrator (DOFbasen → Admin → API), som kan give dig adgang."));
    return;
  }
  const active = d.tokens.filter(t => !t.revoked_at);
  const name = h("input", { type: "text", placeholder: "Navn, fx 'Min hjemmeside'", maxlength: 80, style: { width: "220px" } });
  box.replaceChildren(
    h("p", {}, `Du har adgang med højst ${fmtN(d.rate_per_min)} kald i minuttet pr. nøgle og højst ${d.max_tokens} aktive nøgler.`),
    h("form", { class: "db-form-row", onsubmit: async (e) => {
      e.preventDefault();
      try {
        const t = await api("/api/dofbasen/me/api/tokens", { method: "POST", body: { name: name.value.trim() || "API-nøgle" } });
        showToken(t);
        drawMine(box);
      } catch (err) { toast(err.message, "err"); }
    } }, name, h("button", { type: "submit", class: "db-primary", disabled: active.length >= d.max_tokens }, "Lav ny nøgle")),
    d.tokens.length ? h("table", { class: "db-table" }, h("thead", {}, h("tr", {}, ...["Navn", "Nøgle", "Oprettet", "Senest brugt", "Kald", ""].map(t => h("th", {}, t)))),
      h("tbody", {}, ...d.tokens.map(t => h("tr", { style: t.revoked_at ? { opacity: .5 } : {} },
        h("td", {}, t.name), h("td", {}, h("code", {}, `${t.prefix}…`)), h("td", {}, ago(t.created_at)), h("td", {}, t.last_used_at ? ago(t.last_used_at) : "aldrig"),
        h("td", { class: "num" }, fmtN(t.uses)),
        h("td", {}, t.revoked_at ? "spærret" : h("button", { type: "button", class: "db-danger", onclick: async () => {
          if (!confirm(`Slet nøglen "${t.name}"? Apps, der bruger den, holder op med at virke.`)) return;
          await api(`/api/dofbasen/me/api/tokens/${t.id}`, { method: "DELETE" }); toast("Nøglen er slettet"); drawMine(box);
        } }, "Slet")))))) : h("p", { class: "muted" }, "Du har ingen nøgler endnu."));
}

function showToken(t) {
  dbModal("Din nye API-nøgle", h("div", {},
    h("p", {}, h("b", {}, "Kopiér nøglen nu – den vises kun denne ene gang."), " Portalen gemmer kun et fingeraftryk af den."),
    h("div", { class: "db-token" }, t.token),
    h("div", { class: "db-form-row" }, h("button", { type: "button", class: "db-primary", onclick: async () => {
      try { await navigator.clipboard.writeText(t.token); toast("Kopieret"); } catch { toast("Markér og kopiér nøglen manuelt", "err"); }
    } }, "Kopiér"))));
}
