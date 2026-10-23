// Netfugl: API-dokumentation og brugerens egne API-nøgler.
import { api, h, fmtN, toast, ago } from "../core.js";
import { headline, title, notice, loadingBox, nfModal } from "./shell.js";

const ENDPOINTS = [
  ["/me", "Hvem er jeg (nøglens ejer og grænse)"],
  ["/articles?q=&kind=artikel|kort&page=", "Nyheder og artikler"], ["/articles/{id}", "Én artikel med tekst"],
  ["/photos?art=&sort=nyeste|likes&page=", "Fotos (metadata og adresse; ophavsret hos fotografen)"],
  ["/rss?land=", "Netfugl live – de seneste RSS-indslag"],
  ["/lists", "Krydslisterne"], ["/lists/{key}/ranking", "Rangliste, fx dk, aar:2026, md:5, afd:574, kom:0101, vp"],
  ["/lists/{key}/latest?n=100", "Seneste kryds"], ["/lists/{key}/blockers", "Blockers og blockers-statistik"],
  ["/species/dk", "DK-listen"], ["/species/vp", "VP-listen"], ["/users/{obserkode}", "En deltagers krydslister"],
];

export async function page() {
  const base = `${location.origin}/api/netfugl/v1`;
  const mine = h("div", {}, loadingBox());
  drawMine(mine);
  return h("div", {}, headline("Netfugl API"), h("div", { class: "wrap" },
    h("p", {}, "Hent Netfugls artikler, krydslister, fotos og RSS ind i dine egne apps. JSON, kun læsning og åben CORS. Krydslisterne bygger på DOFbasen-data i portalen."),
    h("ul", {}, h("li", {}, "En administrator giver dig adgang; derefter laver du selv dine nøgler herunder."),
      h("li", {}, "Send nøglen som ", h("code", {}, "Authorization: Bearer nf_…"), "."),
      h("li", {}, "Grænse pr. nøgle pr. minut – ellers svarer API'et 429 med Retry-After.")),
    title("Mine API-nøgler"), mine,
    title("Endpoints"), h("p", {}, "Basisadresse: ", h("code", {}, base), " · ", h("a", { href: "/api/docs#/Netfugl%20API%20v1", target: "_blank" }, "Interaktiv dokumentation")),
    h("table", { class: "nf-table" }, h("tbody", {}, ...ENDPOINTS.map(([p, d]) => h("tr", {}, h("td", {}, h("b", {}, "GET")), h("td", {}, h("code", {}, p)), h("td", {}, d))))),
    title("Eksempel"), h("div", { class: "nf-code" }, `curl -H "Authorization: Bearer $NF_TOKEN" "${base}/lists/dk/ranking"\n\nconst r = await fetch("${base}/lists/aar:${new Date().getFullYear()}/latest?n=20", { headers: { Authorization: "Bearer " + token } });`)));
}

async function drawMine(box) {
  let d;
  try { d = await api("/api/netfugl/me/api"); } catch (err) { box.replaceChildren(notice(err.message, "err")); return; }
  if (!d.access) { box.replaceChildren(notice("Du har ikke adgang til API'et endnu. En administrator kan give dig adgang under Netfugl → Administration → API-adgang.", "info")); return; }
  const name = h("input", { type: "text", placeholder: "Navn på nøglen", maxlength: 80 });
  const active = d.tokens.filter(t => !t.revoked_at).length;
  box.replaceChildren(h("p", {}, `Højst ${fmtN(d.rate_per_min)} kald i minuttet pr. nøgle og ${d.max_tokens} aktive nøgler.`),
    h("form", { class: "nf-tools", onsubmit: async (e) => {
      e.preventDefault();
      try {
        const t = await api("/api/netfugl/me/api/tokens", { method: "POST", body: { name: name.value.trim() || "API-nøgle" } });
        nfModal("Din nye API-nøgle", h("div", {}, h("p", {}, h("b", {}, "Kopiér nøglen nu – den vises kun denne ene gang.")), h("div", { class: "nf-token" }, t.token)));
        drawMine(box);
      } catch (err) { toast(err.message, "err"); }
    } }, name, h("button", { class: "btn primary", type: "submit", disabled: active >= d.max_tokens }, "Lav ny nøgle")),
    d.tokens.length ? h("table", { class: "nf-table small" }, h("thead", {}, h("tr", {}, ...["Navn", "Nøgle", "Oprettet", "Senest brugt", "Kald", ""].map(t => h("th", {}, t)))),
      h("tbody", {}, ...d.tokens.map(t => h("tr", { style: t.revoked_at ? { opacity: .5 } : {} }, h("td", {}, t.name), h("td", {}, h("code", {}, `${t.prefix}…`)), h("td", {}, ago(t.created_at)),
        h("td", {}, t.last_used_at ? ago(t.last_used_at) : "aldrig"), h("td", { class: "num" }, fmtN(t.uses)),
        h("td", {}, t.revoked_at ? "slettet" : h("button", { type: "button", class: "btn sm danger", onclick: async () => { if (!confirm("Slet nøglen?")) return; await api(`/api/netfugl/me/api/tokens/${t.id}`, { method: "DELETE" }); drawMine(box); } }, "Slet")))))) : h("p", { class: "muted" }, "Ingen nøgler endnu."));
}
