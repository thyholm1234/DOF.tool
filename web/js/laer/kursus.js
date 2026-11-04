// Læring 2.0: ét kursus – lektionsliste, læsetekst, indholdsblokke og aktiviteter (docs/laering-spec.md §4.4).
import { api, h, toast, errorBox } from "../core.js";
import { BASE, backLink, bar, chip, art, banner, mediaEl, pct, loadingBox } from "./common.js";
import { runPlayer } from "./player.js";
import { silEl } from "./common.js";

const METODE = ["Forudsig", "Opdag", "Tolk", "Adskil", "Beslut"];

/** Fotorække: artsnavn/billedtekst øverst på hvert foto, så de kan sammenlignes side om side. */
function labelled(fig, label) {
  if (fig && label) fig.prepend(h("b", { class: "lr-label" }, label));
  return fig;
}

function block(b) {
  switch (b.type) {
    case "tekst": return h("div", { class: "lr-md", html: b.html });
    case "tip": case "advarsel": return h("aside", { class: `lr-box ${b.type}` }, b.titel ? h("b", {}, b.titel) : null, h("div", { class: "lr-md", html: b.html }));
    case "foto": return b.foto ? h("div", {}, mediaEl(b.foto), b.tekst ? h("p", { class: "muted" }, b.tekst) : null) : null;
    case "fotoraekke": return h("div", {}, h("div", { class: "lr-row" }, ...b.fotos.map(f => labelled(mediaEl(f), f.label))), b.tekst ? h("p", { class: "muted" }, b.tekst) : null);
    case "lyd": return b.lyd ? h("div", {}, mediaEl(b.lyd), b.tekst ? h("p", { class: "muted" }, b.tekst) : null) : null;
    case "silhuet": return h("div", {}, mediaEl({ kind: "silhouette", value: b.sil, hue: b.hue }), b.tekst ? h("p", { class: "muted" }, b.tekst) : null);
    case "artskort": {
      if (!b.art) return null;
      const w = b.uger;
      const max = w ? Math.max(...w, 0.0001) : 1;
      return h("div", { class: "lr-artcard" }, h("div", {}, h("a", { href: `#/art/${b.art.artnr}` }, h("b", {}, b.art.navn)), h("div", { class: "muted" }, h("i", {}, b.art.latin || ""), b.art.klass && b.art.klass !== "Alm" ? ` · ${b.art.klass}` : "")),
        w ? h("div", { class: "lr-spark", title: "Forekomst gennem året (rapporteringsrate pr. uge)" }, ...w.map(v => h("i", { style: { height: `${Math.max(2, 100 * v / max)}%` } }))) : null);
    }
    case "kort": return b.sted ? h("p", { class: "lr-place" }, "📍 ", h("a", { href: `#/lok/${b.sted.loknr}` }, b.sted.navn)) : null;
    case "artikel": return b.artikel ? h("p", {}, "📖 ", h("a", { href: `#/artikler/${b.artikel.id}` }, b.artikel.title)) : null;
    case "metode": return h("ol", { class: "lr-method small" }, ...METODE.map((t, i) => h("li", { class: i === b.trin ? "on" : "" }, h("b", {}, `${i + 1}. ${t}`))));
    default: return null;
  }
}

export async function render(view, [slug, unitId], info) {
  const preview = new URLSearchParams(location.hash.split("?")[1] || "").get("preview") === "1";
  const d = await api(`/api/learn/courses/${slug}/units${preview ? "?preview=true" : ""}`);
  const c = d.course;
  let stop = null;
  const body = h("div", { class: "lr-unit" });
  const list = h("ol", { class: "lr-units" });
  const uid = unitId ? +unitId : (d.units.find(u => !u.done && !u.locked) || d.units[0])?.id;
  const done = d.units.filter(u => u.done).length;
  if (!d.units.length) { view.append(backLink(`${BASE}/kurser`, "Kurser"), h("div", { class: "notice" }, "Kurset har ingen lektioner endnu.")); return; }

  d.units.forEach((u, i) => list.append(h("li", { class: `${u.id === uid ? "on" : ""} ${u.done ? "done" : ""} ${u.locked ? "locked" : ""}` },
    u.locked ? h("span", {}, `${i + 1}. ${u.title} 🔒`) : h("a", { href: `${BASE}/kursus/${slug}/${u.id}${preview ? "?preview=1" : ""}` }, `${i + 1}. ${u.title}`, u.done ? " ✓" : ""))));

  async function showUnit(u, idx) {
    stop?.();
    body.replaceChildren(h("h2", {}, u.title),
      u.est_minutes ? h("p", { class: "muted" }, `Ca. ${u.est_minutes} min.`) : null,
      u.html ? h("div", { class: "lr-md", html: u.html }) : null,
      ...u.blocks.map(block));
    if (!u.n_acts) {
      body.append(h("div", { class: "lr-actions" }, nextBtn(idx)));
      return;
    }
    const start = h("button", { class: "btn primary", onclick: async () => {
      start.remove();
      const holder = h("div", { class: "lr-run" }, loadingBox());
      body.append(holder);
      try {
        const s = await api(`/api/learn/units/${u.id}/session${preview ? "?preview=true" : ""}`);
        stop = runPlayer(holder, { title: u.title, eyebrow: c.title, questions: s.questions, preview,
          onDone: async (score, total) => { if (!preview) { try { await api(`/api/learn/units/${u.id}/faerdig`, { method: "POST", body: { score, total } }); } catch (e) { toast(e.message, "err"); } } },
          doneExtra: () => nextBtn(idx), again: () => { holder.remove(); showUnit(u, idx); } });
      } catch (err) { holder.replaceChildren(errorBox(err)); }
    } }, `Start aktiviteterne (${u.n_acts})`);
    body.append(h("div", { class: "lr-actions" }, start));
  }
  function nextBtn(idx) {
    const nx = d.units[idx + 1];
    return nx ? h("a", { class: "btn", href: `${BASE}/kursus/${slug}/${nx.id}${preview ? "?preview=1" : ""}` }, "Næste lektion →") : h("a", { class: "btn", href: `${BASE}/kurser` }, "Til kurserne");
  }

  view.append(backLink(`${BASE}/kurser`, "Kurser"),
    c.cover?.url ? banner(c.cover, c.title) : null,
    h("section", { class: "lr-course-head" }, c.cover?.url ? null : art(c, "big"), h("div", {},
      h("div", { class: "lr-kicker" }, [c.number ? `Kursus ${c.number}` : "Kursus", c.stage ? ` · ${c.stage}` : ""]), h("h2", {}, c.title), h("p", {}, c.intro || c.summary),
      h("div", { class: "lr-meta" }, c.hours ? chip(c.hours) : null, ...(c.skills || []).map(s => chip(s, "soft")), preview ? chip("forhåndsvisning", "warn") : null),
      bar(done, d.units.length), h("small", { class: "muted" }, `${done} af ${d.units.length} lektioner`))),
    c.learn?.length ? h("ul", { class: "lr-learn" }, ...c.learn.map(l => h("li", {}, h("b", {}, l.title), " ", h("span", { class: "muted" }, l.text)))) : null,
    h("div", { class: "lr-split" }, h("nav", { "aria-label": "Lektioner" }, list), body));
  const idx = d.units.findIndex(u => u.id === uid);
  const u = d.units[idx];
  if (u && !u.locked) await showUnit(u, idx);
  else body.append(h("div", { class: "notice" }, "Klar den forrige lektion først."));
  return () => stop?.();
}
