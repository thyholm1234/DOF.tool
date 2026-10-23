// Netfugl: RSS (Netfugl live), vejret (DMI-trækvejr fra portalen), brugere og profiler, tekstsider og søgning på Netfugl.
import { api, h, fmtN, setParams } from "../core.js";
import { headline, title, link, loadingBox, notice, md, editPage, pager, dkDate } from "./shell.js";
import { photoGrid } from "./fotos.js";
import { dirName, scoreChip } from "./forside.js";

// ---------------------------------------------------------------- RSS

export async function rss(parts, q) {
  const st = { q: q.q || "", kilde: q.kilde || "", land: q.land || "", page: +q.side || 1 };
  const out = h("div", {});
  const input = h("input", { type: "search", value: st.q, placeholder: "Tekst", style: { width: "220px" } });
  const kilde = h("select", {});
  const land = h("select", {});
  let first = true;
  const load = async () => {
    setParams({ q: st.q, kilde: st.kilde, land: st.land, side: st.page > 1 ? st.page : "" });
    out.replaceChildren(loadingBox());
    const d = await api(`/api/netfugl/rss?${new URLSearchParams({ q: st.q, land: st.land, page: st.page, ...(st.kilde ? { kilde: st.kilde } : {}) })}`);
    if (first) {
      kilde.replaceChildren(h("option", { value: "" }, "Alle sites"), ...d.kilder.map(k => h("option", { value: k.id, selected: String(k.id) === st.kilde }, k.navn)));
      land.replaceChildren(h("option", { value: "" }, "Alle lande"), ...d.lande.map(l => h("option", { value: l, selected: l === st.land }, l)));
      first = false;
    }
    out.replaceChildren(title("Netfugl live (RSS)"),
      d.rows.length ? h("table", { class: "nf-table" }, h("tbody", {}, ...d.rows.map(r => h("tr", {},
        h("td", { style: { width: "150px", whiteSpace: "nowrap" } }, dkDate(r.published_at, true)),
        h("td", {}, r.url ? h("a", { href: r.url, target: "_blank", rel: "noopener" }, r.title) : r.title, r.summary ? h("div", { class: "small muted" }, r.summary.slice(0, 220)) : null),
        h("td", { class: "small", style: { textAlign: "right" } }, r.kilde, h("br"), h("span", { class: "muted" }, r.land)))))) : notice("Ingen indslag endnu. Workeren henter kilderne hver time.", "info"),
      pager(st.page, Math.ceil(d.total / 30), (p) => { st.page = p; load(); }));
  };
  load();
  return h("div", {}, headline("RSS Nyheder"), h("div", { class: "wrap" }, title("Søg i RSS nyheder"),
    h("form", { class: "nf-tools", onsubmit: (e) => { e.preventDefault(); Object.assign(st, { q: input.value.trim(), kilde: kilde.value, land: land.value, page: 1 }); load(); } },
      input, kilde, land, h("button", { class: "btn primary", type: "submit" }, "Søg")), out));
}

// ---------------------------------------------------------------- vejret

export async function vejret() {
  const box = h("div", {}, loadingBox("Henter DMI's prognose …"));
  api("/api/weather/forecast").then(({ spots, explain }) => {
    const days = {};
    box.replaceChildren(
      h("p", {}, "Netfugl viser et vejrkort fra Windy. I showcasen bruges i stedet portalens egen trækvejrsprognose fra DMI (HARMONIE) for de klassiske træksteder – med en vurdering af trækvejret for landfugle og havfugle time for time. ",
        h("a", { href: "#/traek?tab=kort" }, "Se vindkortet og trækvejret i DOF.tool →")),
      h("div", { class: "nf-wx" }, ...spots.map(s => {
        const morning = s.hours.filter(x => { const hr = new Date(x.t).getHours(); return hr >= 5 && hr <= 11; });
        const byDay = {};
        for (const x of morning) (byDay[x.t.slice(0, 10)] ||= []).push(x);
        Object.keys(byDay).forEach(d => { days[d] = 1; });
        return h("div", { class: "nf-card spot" }, h("div", { class: "body" }, h("h4", {}, s.navn, s.sea ? h("span", { class: "nf-chip", style: { marginLeft: "6px" } }, "kyst") : null),
          h("table", {}, h("tbody", {}, ...Object.entries(byDay).slice(0, 3).map(([d, hs]) => {
            const avg = (k) => hs.reduce((a, x) => a + x[k], 0) / hs.length;
            const dir = Math.atan2(hs.reduce((a, x) => a + Math.sin(x.dir * Math.PI / 180), 0), hs.reduce((a, x) => a + Math.cos(x.dir * Math.PI / 180), 0)) * 180 / Math.PI;
            return h("tr", {}, h("td", {}, dkDate(d)), h("td", {}, `${Math.round(avg("speed"))} m/s ${dirName(dir)}`), h("td", {}, `${Math.round(avg("temp"))}°`),
              h("td", {}, scoreChip(avg("land"))), s.sea ? h("td", {}, scoreChip(avg("sea"))) : null);
          }))),
          h("div", { class: "small muted" }, "Morgen (kl. 5–11): vind, temperatur, trækvejr land", s.sea ? " / hav" : "")));
      })),
      h("p", { class: "small muted", style: { marginTop: "12px" } }, explain));
  }).catch(() => box.replaceChildren(notice("Prognosen er ikke hentet endnu.", "info")));
  return h("div", {}, headline("Vejret"), h("div", { class: "wrap" }, box));
}

// ---------------------------------------------------------------- brugere

export async function brugere(parts, q) {
  if (parts[0] === "bruger") return profil(parts[1]);
  const rows = await api(`/api/netfugl/brugere`);
  const filter = h("input", { type: "search", value: q.q || "", placeholder: "Søg bruger", style: { width: "220px" } });
  const tbody = h("tbody", {});
  const draw = () => {
    const f = filter.value.trim().toLowerCase();
    tbody.replaceChildren(...rows.filter(r => !f || `${r.navn} ${r.kode}`.toLowerCase().includes(f)).map(r => h("tr", {},
      h("td", {}, link(`/bruger/${r.kode}`, r.navn)), h("td", { class: "num" }, fmtN(r.dk)), h("td", { class: "num" }, fmtN(r.vp)), h("td", { class: "num" }, fmtN(r.fotos)))));
  };
  filter.oninput = draw;
  draw();
  return h("div", {}, headline("Alle brugere"), h("div", { class: "wrap" },
    h("p", {}, "Deltagerne er portalens brugere, der viser deres lister (kan slås fra under Profil). DK-krydsene beregnes fra DOFbasen."),
    h("div", { class: "nf-tools" }, h("b", {}, `${rows.length} brugere`), h("span", { class: "spacer" }), filter),
    h("table", { class: "nf-table" }, h("thead", {}, h("tr", {}, h("th", {}, "Navn"), h("th", {}, "DK kryds"), h("th", {}, "VP kryds"), h("th", {}, "Fotos"))), tbody)));
}

async function profil(kode) {
  const d = await api(`/api/netfugl/brugere/${encodeURIComponent(kode)}`);
  const box = h("div", { class: "wrap" });
  box.append(title("Generel information"),
    h("table", { class: "nf-table" }, h("tbody", {},
      h("tr", {}, h("td", { style: { width: "220px" } }, "Navn"), h("td", {}, d.navn)),
      h("tr", {}, h("td", {}, "Obserkode"), h("td", {}, d.kode, d.dofbasen ? [" · ", h("a", { href: `#/dofbasen/obser/${d.kode}` }, "Observatør i DOFbasen")] : null)))),
    d.lister.length ? [title("Artslister"), ...d.lister.map(l => h("div", { class: "nf-card" }, h("div", { class: "body" },
      h("b", {}, link(`/kryds/${l.key}/ranking`, l.name)), h("div", {}, `Kryds: ${l.antal}`),
      l.blockers.length ? h("div", { class: "small" }, `Blockers (${l.blockers.length}): `, l.blockers.map(b => `${b.navn} (${b.antal})`).join(", ")) : null)))]
      : d.deltager ? null : notice("Brugeren deltager ikke i krydslisterne.", "info"),
    d.fotos ? [title("Fotogalleri"), h("table", { class: "nf-table" }, h("tbody", {},
      h("tr", {}, h("td", { style: { width: "220px" } }, "Antal fotos"), h("td", {}, d.fotos.antal ? link(`/fotos?fotograf=${d.fotos.user_id}`, fmtN(d.fotos.antal)) : "0")),
      h("tr", {}, h("td", {}, "Seneste"), h("td", {}, d.fotos.seneste ? d.fotos.seneste.slice(0, 10) : "–")),
      h("tr", {}, h("td", {}, "Visninger i alt"), h("td", {}, fmtN(d.fotos.visninger))),
      h("tr", {}, h("td", {}, "Højeste"), h("td", {}, fmtN(d.fotos.hoejeste))))),
      d.seneste_fotos?.length ? photoGrid(d.seneste_fotos) : null] : null);
  return h("div", {}, headline(d.navn), box);
}

// ---------------------------------------------------------------- tekstsider og søgning

export async function side(parts) {
  const slug = parts[1] || "om";
  const box = h("div", {});
  const head = h("div", {});
  const load = async () => {
    try {
      const p = await api(`/api/netfugl/sider/${encodeURIComponent(slug)}`);
      head.replaceChildren(headline(p.title));
      box.replaceChildren(p.can_edit ? h("div", { class: "nf-tools" }, h("button", { class: "btn btn-edit", type: "button", onclick: () => editPage(p, load) }, "Redigér")) : null,
        md(p.body), h("p", { class: "small muted", style: { marginTop: "24px" } }, "Teksten er kopieret fra netfugl.dk som eksempel."));
    } catch (err) { head.replaceChildren(headline("Netfugl")); box.replaceChildren(notice(err.status === 404 ? "Siden findes ikke." : err.message, "err")); }
  };
  await load();
  return h("div", {}, head, h("div", { class: "wrap" }, box));
}

export async function soeg(parts, q) {
  const input = h("input", { type: "search", value: q.q || "", placeholder: "Søg i nyheder, arter, fotos, brugere og RSS", style: { width: "380px" } });
  const out = h("div", {});
  const run = async () => {
    const term = input.value.trim();
    setParams({ q: term });
    if (term.length < 2) { out.replaceChildren(); return; }
    out.replaceChildren(loadingBox());
    const d = await api(`/api/netfugl/soeg?q=${encodeURIComponent(term)}`);
    const sec = (t, items) => items?.length ? [title(`${t} (${items.length})`), ...items] : [];
    out.replaceChildren(
      ...sec("Arter", d.arter.map(a => h("div", {}, link(`/art/${a.id}`, a.navn), " ", h("i", { class: "muted" }, a.latin), h("span", { class: "nf-chip" }, a.liste.toUpperCase())))),
      ...sec("Nyheder & artikler", d.artikler?.map(a => h("div", {}, dkDate(a.dato), " – ", link(`/artikler/${a.id}`, a.title)))),
      ...(d.fotos?.length ? [title(`Fotos (${d.fotos.length})`), photoGrid(d.fotos)] : []),
      ...sec("Brugere", d.brugere.map(b => h("div", {}, link(`/bruger/${b.kode}`, b.navn)))),
      ...sec("RSS", d.rss?.map(r => h("div", {}, dkDate(r.published_at), " – ", r.url ? h("a", { href: r.url, target: "_blank", rel: "noopener" }, r.title) : r.title, h("span", { class: "muted small" }, ` (${r.kilde})`)))),
      Object.values(d).every(v => !v?.length) ? notice("Ingen resultater.", "info") : null);
  };
  if (q.q) run();
  return h("div", {}, headline("Søg på Netfugl"), h("div", { class: "wrap" },
    h("form", { class: "nf-tools", onsubmit: (e) => { e.preventDefault(); run(); } }, input, h("button", { class: "btn primary", type: "submit" }, "Søg")), out));
}

