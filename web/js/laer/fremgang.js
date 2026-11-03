// Læring 2.0: min fremgang – færdigheder, artsmestring, kalibrering, aktivitet, mål og beviser (docs/laering-spec.md §11).
import { api, h, toast, errorBox, empty, ago } from "../core.js";
import { BASE, bar, chip, loadingBox, pct } from "./common.js";

function radar(items) {
  const n = items.length, R = 92, cx = 130, cy = 120;
  const pt = (i, r) => [cx + r * Math.sin((2 * Math.PI * i) / n), cy - r * Math.cos((2 * Math.PI * i) / n)];
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 260 250"); svg.setAttribute("class", "lr-radar"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Færdigheder som stjernediagram");
  const el = (tag, attrs, text) => { const e = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text) e.textContent = text; svg.append(e); return e; };
  for (const f of [0.25, 0.5, 0.75, 1]) el("polygon", { points: items.map((_, i) => pt(i, R * f).join(",")).join(" "), fill: "none", stroke: "currentColor", "stroke-opacity": ".15" });
  items.forEach((it, i) => { const [x, y] = pt(i, R); el("line", { x1: cx, y1: cy, x2: x, y2: y, stroke: "currentColor", "stroke-opacity": ".15" }); const [lx, ly] = pt(i, R + 14); el("text", { x: lx, y: ly + 3, "text-anchor": "middle", "font-size": "9", fill: "currentColor" }, it.skill); });
  el("polygon", { points: items.map((it, i) => pt(i, R * ((it.pct ?? 0) / 100)).join(",")).join(" "), fill: "var(--brand-2)", "fill-opacity": ".35", stroke: "var(--brand)", "stroke-width": "2" });
  return svg;
}

export async function render(view, _p, info) {
  const body = h("div", {}, loadingBox());
  view.append(h("div", { class: "lr-intro" }, h("h2", {}, "Min fremgang"), h("p", { class: "muted" }, "Dine tal er private. De bygger på dine svar de seneste 90 dage.")), body);
  try {
    const d = await api("/api/learn/fremgang");
    const sk = d.faerdigheder;
    const m = d.mestring;
    const goal = d.maal;
    const goalBox = h("div", { class: "lr-card-body" });
    const drawGoal = () => goalBox.replaceChildren(h("h3", {}, "Mit mål"), goal
      ? [h("p", {}, `Lær ${goal.n_species} arter på ${goal.modality === "lyd" ? "lyd" : "foto"}${goal.deadline ? ` før ${new Date(goal.deadline).toLocaleDateString("da-DK", { day: "numeric", month: "long" })}` : ""}`), bar(goal.mestret, goal.n_species), h("small", { class: "muted" }, `${goal.mestret} af ${goal.n_species}`),
        h("div", { class: "lr-actions" }, h("button", { class: "btn sm ghost", onclick: async () => { await api("/api/learn/maal", { method: "PUT", body: { n_species: null } }); location.reload(); } }, "Fjern målet"))]
      : [h("p", { class: "muted" }, "Sæt et mål, fx «Lær 40 arter på lyd før 1. april»."), h("div", { class: "lr-actions" },
        (() => { const n = h("input", { type: "number", min: 1, placeholder: "Antal arter", style: { width: "110px" } }), mo = h("select", {}, h("option", { value: "lyd" }, "på lyd"), h("option", { value: "foto" }, "på foto")), dl = h("input", { type: "date" });
          return [n, mo, dl, h("button", { class: "btn sm primary", onclick: async () => { try { await api("/api/learn/maal", { method: "PUT", body: { n_species: +n.value, modality: mo.value, deadline: dl.value || null } }); location.reload(); } catch (e) { toast(e.message, "err"); } } }, "Gem mål")]; })())]);
    drawGoal();
    const strip = d.streak;
    const list = h("div", { class: "lr-list" });
    const grp = h("select", { "aria-label": "Artsgruppe", onchange: () => loadSpecies(grp.value) }, h("option", { value: "" }, "Alle arter"));
    api("/api/learn/traen/muligheder").then(o => grp.append(...o.grupper.map(g => h("option", { value: g.key }, g.navn)))).catch(() => {});
    const box = (b) => b ? h("span", { class: `lr-box5 b${b.box}${b.forfalden ? " due" : ""}`, title: `Kasse ${b.box} · ${b.ok} rigtige, ${b.forkert} forkerte` }, "●".repeat(Math.min(5, b.box + 1))) : h("span", { class: "muted" }, "–");
    async function loadSpecies(g) {
      const rows = await api(`/api/learn/fremgang/arter${g ? `?gruppe=${g}` : ""}`);
      list.replaceChildren(...(rows.length ? rows.map(r => h("div", { class: "lr-listrow" }, h("a", { href: `#/art/${r.artnr}` }, r.navn), h("span", { class: "lr-meta" }, h("small", { class: "muted" }, "foto "), box(r.foto), h("small", { class: "muted" }, " lyd "), box(r.lyd)))) : [h("p", { class: "muted" }, "Ingen arter trænet endnu.")]));
    }
    loadSpecies("");
    body.replaceChildren(
      h("div", { class: "lr-stats" }, h("div", { class: "lr-stat" }, h("b", {}, strip.dage_i_traek), h("span", {}, "dage i træk")), h("div", { class: "lr-stat" }, h("b", {}, strip.dage_30), h("span", {}, "dage med træning (30 d)")),
        h("div", { class: "lr-stat" }, h("b", {}, m.foto.mestret), h("span", {}, `arter mestret på foto (af ${m.foto.set})`)), h("div", { class: "lr-stat" }, h("b", {}, m.lyd.mestret), h("span", {}, `arter mestret på lyd (af ${m.lyd.set})`))),
      !strip.traenet_i_dag ? h("a", { class: "lr-due", href: `${BASE}/repetition` }, "Du har ikke trænet i dag – tag dagens repetition →") : null,
      h("div", { class: "lr-two" }, h("div", { class: "lr-card-body" }, h("h3", {}, "Færdigheder"), radar(sk), h("small", { class: "muted" }, "Andel rigtige pr. færdighed – mangler et ben, har du ikke øvet den.")),
        h("div", { class: "lr-card-body" }, h("h3", {}, "I tal"), h("table", { class: "lr-table" }, h("tbody", {}, ...sk.map(s => h("tr", {}, h("td", {}, s.skill), h("td", {}, s.pct == null ? h("span", { class: "muted" }, "–") : `${s.pct} %`), h("td", { class: "muted" }, s.n ? `${s.n} svar` : ""))))))),
      h("h3", {}, "Hvor godt kender du din egen sikkerhed?"),
      h("div", { class: "lr-grid four" }, ...d.kalibrering.map(c => h("div", { class: "lr-tile" }, h("div", { class: "lr-kicker" }, `${c.niveau} sikkerhed`), h("h3", {}, c.pct == null ? "–" : `${c.pct} % rigtige`), h("p", { class: "muted" }, `${c.n} svar · ret: ${c.maal[0]}–${c.maal[1]} %`),
        c.godt === true ? chip("godt kalibreret", "soft") : c.godt === false ? chip("overvej: din sikkerhed passer ikke", "warn") : chip("for få svar endnu")))),
      h("p", { class: "muted small" }, "Er du «høj sikker», bør du have ret næsten hver gang. Er du «lav sikker», er et gæt fint – og det er okay at have det."),
      h("div", { class: "lr-card", style: { marginTop: "16px" } }, goalBox),
      h("h3", {}, "Mine arter"), grp, list,
      d.beviser.length ? [h("h3", {}, "Beviser"), h("div", { class: "lr-list" }, ...d.beviser.map(b => h("a", { class: "lr-listrow", href: `${BASE}/bevis/${b.certificate_no}` }, h("b", {}, b.title), h("span", { class: "muted" }, b.certificate_no))))] : null,
      h("h3", {}, "Seneste aktivitet"), d.aktivitet.length ? h("div", { class: "lr-list" }, ...d.aktivitet.slice(0, 15).map(a => h("div", { class: "lr-listrow" }, h("span", {}, h("small", { class: "muted" }, `${a.kind} · `), a.title), h("span", { class: "muted" }, a.total ? `${a.score}/${a.total} · ` : "", ago(a.t))))) : empty("Ingen aktivitet endnu"));
  } catch (e) { body.replaceChildren(errorBox(e)); }
}
