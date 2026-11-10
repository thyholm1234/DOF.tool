// Mine lister (#/lister): alle ens lister som kort – DK-årsliste og livsliste, ædelarter, områder og krydslister med placering.
// Et kort fører til listen (årslister fra DOFbasen) eller til «mine kryds» (krydslister, hvor man også kan indtaste manuelt).
import { api, state, h, icon, fmtN, requireLogin, errorBox, store } from "../core.js";

const YEAR = new Date().getFullYear();
const klOn = (f = "krydslister") => !!state.user?.krydslister?.features?.[f];
const place = (p, n) => p ? `nr. ${fmtN(p)} af ${fmtN(n)}` : null;

/** Et kort: titel, stort tal, evt. under-tal og et mærke (placering, «manuel»). */
function card({ href, title, count, unit = "arter", sub, badge, kind = "", icon: ic }) {
  return h("a", { class: `lister-card ${kind}`, href },
    h("div", { class: "lister-card-top" }, ic ? h("span", { class: "lister-card-ic", html: icon(ic) }) : null, h("span", { class: "lister-card-title" }, title),
      badge ? h("span", { class: "lister-badge" }, badge) : null),
    h("div", { class: "lister-card-count" }, count === null || count === undefined ? h("span", { class: "skeleton-text" }, "…") : fmtN(count), h("small", {}, ` ${unit}`)),
    sub ? h("div", { class: "lister-card-sub" }, sub) : null);
}

const section = (title, lead, ...cards) => h("section", { class: "lister-section" },
  h("div", { class: "lister-section-head" }, h("h2", {}, title), lead ? h("p", { class: "muted" }, lead) : null),
  h("div", { class: "lister-grid" }, ...cards.flat().filter(Boolean)));

export async function page(body) {
  if (requireLogin(body)) return;
  let year = +(store("lister-aar") || YEAR);
  const yearSel = h("select", { "aria-label": "År", class: "lister-year" });
  const out = h("div", { class: "lister-sections" }, h("div", { class: "skeleton", style: { height: "260px" } }));
  body.append(h("div", { class: "lister-hub-head" }, h("h2", {}, "Mine lister"), h("span", { class: "spacer" }), h("label", { class: "lister-year-label" }, h("span", { class: "muted" }, "År"), yearSel)), out);

  const load = async () => {
    out.replaceChildren(h("div", { class: "skeleton", style: { height: "260px" } }));
    try {
      const [ov, kryds] = await Promise.all([api(`/api/lists/overview?year=${year}`), klOn() ? api(`/api/krydslister/mine-lister?aar=${year}`).catch(() => null) : null]);
      const years = [...new Set([YEAR, year, ...ov.years])].sort((a, b) => b - a);
      yearSel.replaceChildren(...years.map(y => h("option", { value: y, selected: y === year }, y)));
      draw(ov, kryds);
    } catch (err) { out.replaceChildren(errorBox(err)); }
  };
  yearSel.onchange = () => { year = +yearSel.value; store("lister-aar", year === YEAR ? null : year); load(); };

  function draw(ov, kryds) {
    const dk = ov.scopes.find(s => s.scope === "dk") || { year: 0, life: 0 };
    const others = ov.scopes.filter(s => s.scope !== "dk");
    const kl = kryds?.lister || [];
    const byBase = (b) => kl.find(l => l.base === b);
    const aedelYear = byBase("aedelaar"), aedelLife = byBase("aedel");
    const yearRank = h("span", {}), lifeRank = h("span", {});
    const main = section("Danmark", null,
      card({ href: `#/lister/liste?year=${year}`, title: `Årsliste ${year}`, count: dk.year, kind: "hero", icon: "calendar", badge: yearRank,
        sub: aedelYear ? h("span", {}, h("span", { class: "lister-star on", html: icon("star") }), ` ${fmtN(aedelYear.antal)} ædelarter`) : null }),
      card({ href: "#/lister/liste?year=all", title: "Livsliste", count: dk.life, kind: "hero", icon: "list", badge: lifeRank,
        sub: aedelLife ? h("span", {}, h("span", { class: "lister-star on", html: icon("star") }), ` ${fmtN(aedelLife.antal)} på ædel-livslisten`) : null }));
    // Placering på ranglisterne hentes bagefter (de kan tage et øjeblik første gang)
    for (const [y, el] of [[year, yearRank], ["all", lifeRank]]) {
      api(`/api/lists/scoreboard?scope=dk&year=${y}`).then(d => {
        const me = d.rows.find(r => r.obserkode === state.user.obserkode);
        if (me && me.antal) el.textContent = place(me.placering, d.rows.filter(r => r.antal).length);
      }).catch(() => {});
    }
    const aedel = klOn() && (aedelYear || aedelLife) ? section("Ædelarter", "Arter du har set under særligt gode forhold. Markér dem med ★ på din årsliste eller livsliste – eller indtast dem manuelt.",
      aedelYear ? card({ href: `#/lister/liste?year=${year}`, title: `Ædelårsliste ${year}`, count: aedelYear.antal, kind: "aedel", icon: "star", badge: place(aedelYear.placering, aedelYear.deltagere) }) : null,
      aedelLife ? card({ href: "#/lister/liste?year=all", title: "Ædel-livsliste", count: aedelLife.antal, kind: "aedel", icon: "star", badge: place(aedelLife.placering, aedelLife.deltagere) }) : null,
      kryds?.kan_krydse ? h("a", { class: "lister-card add", href: "#/lister/mine?liste=aedel" }, h("span", { html: icon("edit") }), h("span", {}, "Indtast ædelarter manuelt")) : null) : null;
    const areas = section("Mine områder", "Lokalafdelinger, kommuner og egne områder.",
      ...others.map(s => card({ href: `#/lister/liste?scope=${encodeURIComponent(s.scope)}&year=${year}`, title: s.label, count: s.year, icon: s.own ? "pin" : "map",
        sub: `${fmtN(s.life)} arter i alt` })),
      h("a", { class: "lister-card add", href: "#/profil?fane=omraader" }, h("span", { html: icon("plus") }), h("span", {}, "Vælg afdelinger og kommuner")),
      h("a", { class: "lister-card add", href: "#/alarmer?tab=omraader" }, h("span", { html: icon("plus") }), h("span", {}, "Tegn et eget område")));
    const skip = new Set(["aedel", "aedelaar"]);
    const krydsCards = kl.filter(l => !skip.has(l.base)).map(l => card({ href: `#/lister/mine?liste=${encodeURIComponent(l.base)}`, title: l.name, count: l.antal,
      kind: l.manuel && !l.auto ? "manual" : "", icon: l.manuel && !l.auto ? "edit" : "check", badge: place(l.placering, l.deltagere),
      sub: l.manuel && !l.auto ? "Manuel indtastning" : l.manuel ? "Automatisk fra DOFbasen + manuelle kryds" : "Automatisk fra DOFbasen" }));
    const krydsSec = kl.length ? section("Krydslister", "Krydslisterne beregnes fra dine observationer; manuelle lister (VP, ringmærkning …) indtaster du selv.",
      ...krydsCards, h("a", { class: "lister-card add", href: "#/lister/alle" }, h("span", { html: icon("list") }), h("span", {}, "Se alle krydslister"))) : null;
    const notPart = kl.length && kl.some(l => !l.participant) ? h("div", { class: "notice info" }, "Du er ikke med på ranglisterne, fordi du har valgt ikke at vise dine lister. Slå det til under ",
      h("a", { href: "#/profil" }, "Profil → Lister og privatliv"), ".") : null;
    out.replaceChildren(notPart, main, aedel, areas, krydsSec,
      h("p", { class: "muted small" }, "Døde fugle tæller ikke på personlige lister, men gerne på DK-listen. Listerne opdateres få minutter efter, at observationerne er kommet ind."));
  }
  await load();
}
