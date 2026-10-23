// Netfugl: forsiden – artikler, sjældne fugle fra DOFbasen, fotos, kort nyt, kommentarer, RSS, seneste kryds og links.
import { api, h } from "../core.js";
import { banner, title, link, loadingBox, notice, tabs, dkDate, on } from "./shell.js";
import { photoGrid } from "./fotos.js";

export async function page() {
  const out = h("div", { class: "wrap" }, loadingBox());
  let d;
  try { d = await api("/api/netfugl/forside"); } catch (err) { out.replaceChildren(notice(err.message, "err")); return h("div", {}, banner(), out); }
  const left = h("div", {}, ...(d.artikler || []).map(a => h("div", { class: "nf-article" },
    h("div", { class: "nf-thumb" }, a.image_url ? h("img", { src: a.image_url, alt: "" }) : "📰"),
    h("h3", {}, link(`/artikler/${a.id}`, a.title)),
    h("div", { class: "meta" }, dkDate(a.created_at && a.dato === a.created_at.slice(0, 10) ? a.created_at : a.dato, true)),
    a.writers ? h("div", {}, ...a.writers.split(",").map((w, i) => [i ? ", " : "", link(`/artikler?q=${encodeURIComponent(w.trim())}`, w.trim())])) : null)),
    on("netfugl_artikler") ? h("div", { style: { textAlign: "right" } }, link("/artikler", "Se alle nyheder & artikler")) : null);
  const rare = h("div", {});
  const mid = h("div", {}, title("Seneste sjældne og fåtallige fugle i DOFbasen"), tabs([["rare", "Sjældne"], ["uncommon", "Fåtallige"]], "rare", (k) => drawRare(rare, k)), rare);
  drawRare(rare, "rare");
  const right = h("div", {}, title("Trækvejr i dag"), h("div", { id: "nf-wx-mini" }, loadingBox()), h("p", { class: "small" }, link("/vejret", "Se trækvejret for alle træksteder →")));
  miniWeather(right.querySelector("#nf-wx-mini"));

  const sections = [];
  if (d.fotos) {
    sections.push(title("Seneste fotos"), d.fotos.length ? photoGrid(d.fotos) : notice("Ingen fotos endnu – vær den første til at uploade et.", "info"),
      h("div", { style: { textAlign: "right", margin: "8px 0" } }, link("/fotos", "Se flere fotos i galleriet")),
      collapse("Fotos med flest likes seneste måned", d.fotos_likes, "/fotos?sort=likes"),
      collapse("Senest kommenterede fotos", d.fotos_kommenteret, "/fotos?sort=kommenteret"),
      collapse("Seneste fotos af SU arter", d.fotos_su, "/fotos?su=1"));
  }
  if (d.kort) {
    sections.push(title("Kort nyt fra ind- og udland"), table(d.kort.map(k => [dkDate(k.dato), link(`/artikler/${k.id}`, k.title), h("span", {}, `${k.kommentarer} 💬`)]), [1]));
    sections.push(title("Seneste kommentarer"), d.kommentarer.length
      ? table(d.kommentarer.map(c => [dkDate(c.created_at, true), link(`/artikler/${c.id}#kommentarer`, c.title), c.navn, h("span", {}, `${c.antal} 💬`)]), [1])
      : h("p", { class: "muted" }, "Ingen kommentarer endnu."));
  }
  if (d.rss_dk) {
    const rss = (rows) => rows.length ? table(rows.map(r => [dkDate(r.published_at, true), h("div", {}, h("div", { class: "small muted", style: { textAlign: "right" } }, r.kilde),
      r.url ? h("a", { href: r.url, target: "_blank", rel: "noopener" }, r.title) : r.title)]), [1], "small") : h("p", { class: "muted small" }, "Ingen indslag endnu – workeren henter kilderne hver time.");
    sections.push(h("div", { class: "nf-cols two" }, h("div", {}, title("Netfugl live (RSS fra Danmark)"), rss(d.rss_dk)), h("div", {}, title("Netfugl live (RSS fra udlandet)"), rss(d.rss_ud))));
  }
  if (d.kryds) {
    const k = d.kryds;
    const kt = (rows, liste) => rows.length ? table(rows.map(r => [link(`/bruger/${r.kode}`, r.person), h("span", { class: "nf-x" }, r.x),
      link(`/kryds/${liste}/art/${encodeURIComponent(r.key)}`, r.navn), r.lok || "", r.dato]), [2], "small",
      ["Navn", "#X", "Art", "Lokation", "Dato"]) : h("p", { class: "muted small" }, "Ingen kryds endnu.");
    const dkRows = k.dk350.length ? k.dk350 : k.dk, vpRows = k.vp700.length ? k.vp700 : k.vp;
    sections.push(h("div", { class: "nf-cols two" },
      h("div", {}, title(k.dk350.length ? "Seneste DK kryds 350+" : "Seneste DK kryds"), kt(dkRows, "dk")),
      h("div", {}, title(k.vp700.length ? "Seneste VP kryds 700+" : "Seneste VP kryds"), kt(vpRows, "vp"))));
  }
  const groups = {};
  for (const l of d.links) (groups[l.gruppe] ||= []).push(l);
  sections.push(title("Links"), h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "16px" } },
    ...Object.entries(groups).map(([g, ls]) => h("div", {}, h("b", {}, g), ...ls.map(l => h("div", { class: "small" }, h("a", { href: l.url, target: "_blank", rel: "noopener" }, l.titel)))))));

  out.replaceChildren(
    h("p", { class: "nf-support" }, "Udvikling og vedligeholdelse af det nye Netfugl finansieres af DOF. ", link("/side/stoet", "Klik her"), " for at være med til at støtte med et valgfrit beløb."),
    h("div", { class: "nf-cols" }, left, mid, right), ...sections);
  return h("div", {}, banner(), out);
}

function table(rows, wide = [], cls = "", head = null) {
  return h("div", { class: "nf-scroll" }, h("table", { class: `nf-table ${cls}` }, head ? h("thead", {}, h("tr", {}, ...head.map(t => h("th", {}, t)))) : null,
    h("tbody", {}, ...rows.map(r => h("tr", {}, ...r.map((c, i) => h("td", { style: wide.includes(i) ? { width: "60%" } : {} }, c)))))));
}

function collapse(label, photos, href) {
  if (!photos) return null;
  const body = h("div", { hidden: true }, photos.length ? photoGrid(photos) : h("p", { class: "muted" }, "Ingen fotos endnu."), h("p", { style: { textAlign: "right" } }, link(href, "Se flere")));
  return h("div", { class: "nf-collapse" }, h("button", { type: "button", onclick: () => { body.hidden = !body.hidden; } }, label), body);
}

async function drawRare(box, kind) {
  box.replaceChildren(loadingBox());
  let d;
  try { d = await api(`/api/netfugl/sjaeldne?kind=${kind}`); } catch (err) { box.replaceChildren(notice(err.message, "err")); return; }
  let page = 1;
  const draw = () => {
    const rows = d.rows.slice((page - 1) * 10, page * 10);
    const pages = Math.min(5, Math.ceil(d.rows.length / 10));
    // Integration: fundet åbnes i DOFbasen-modulet (popup med hele turen); ellers på dofbasen.dk
    const href = (r) => d.dofbasen ? `#/dofbasen/obs/${r.obsid}` : `https://dofbasen.dk/popobs.php?obsid=${r.obsid}&summering=tur&obs=obs`;
    box.replaceChildren(rows.length ? table(rows.map(r => [h("a", { href: href(r), ...(d.dofbasen ? {} : { target: "_blank", rel: "noopener" }) }, `${r.art} - ${r.lokalitet || r.loknr}`), r.dato]), [0])
      : h("p", { class: "muted" }, "Ingen i perioden."),
      pages > 1 ? h("div", { class: "nf-pager", style: { justifyContent: "center" } }, ...Array.from({ length: pages }, (_, i) => i + 1).map(p => p === page ? h("span", { class: "on" }, p)
        : h("a", { href: "javascript:void 0", onclick: () => { page = p; draw(); } }, p))) : null);
  };
  draw();
}

async function miniWeather(box) {
  try {
    const { spots } = await api("/api/weather/forecast");
    const now = Date.now();
    const rows = spots.slice(0, 8).map(s => {
      const hNow = s.hours.find(x => new Date(x.t).getTime() >= now - 3600e3) || s.hours[0];
      return [s.navn, hNow ? `${Math.round(hNow.speed)} m/s ${dirName(hNow.dir)}` : "–", hNow ? scoreChip(Math.max(hNow.land, hNow.sea)) : ""];
    });
    box.replaceChildren(table(rows, [0], "small"));
  } catch { box.replaceChildren(h("p", { class: "muted small" }, "Vejrprognosen er ikke hentet endnu.")); }
}

export const dirName = (deg) => ["N", "NØ", "Ø", "SØ", "S", "SV", "V", "NV"][Math.round(((deg % 360) + 360) % 360 / 45) % 8];
export function scoreChip(v) {
  const c = v >= 4 ? "#2e7d4f" : v >= 2.5 ? "#7da33c" : v >= 1.5 ? "#c98a12" : "#999";
  return h("span", { class: "nf-score", style: { background: c }, title: "Trækvejr (0–5)" }, (Math.round(v * 10) / 10).toLocaleString("da-DK"));
}

