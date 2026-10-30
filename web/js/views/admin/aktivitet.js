// Admin → Aktivitet: brug af portalen.
import { api, h, fmtN, ago, seg, errorBox, modal } from "../../core.js";
import { lineChart, seriesLegend, PALETTE } from "../../charts.js";
import { st } from "./common.js";

export async function activityPanel(body) {
  let days = 30;
  const out = h("div", {});
  body.append(h("div", { class: "row mb" }, seg([["7", "7 dage"], ["30", "30 dage"], ["90", "90 dage"]], "30", (v) => { days = Number(v); load(); })), out);
  const load = async () => {
    out.replaceChildren(h("div", { class: "skeleton", style: { height: "200px" } }));
    let d;
    try { d = await api(`/api/admin/activity?days=${days}`); } catch (err) { out.replaceChildren(errorBox(err)); return; }
    const a = d.active;
    const idx = (iso) => Math.round((new Date(iso) - new Date(d.by_day[0]?.day || iso)) / 864e5);
    const series = [
      { name: "Aktive brugere", points: d.by_day.map(r => [idx(r.day), r.users]), color: PALETTE[0], width: 2.5 },
      { name: "Handlinger", points: d.by_day.map(r => [idx(r.day), r.actions]), color: PALETTE[2] },
    ];
    const label = (i) => { const dt = new Date(d.by_day[0]?.day); dt.setDate(dt.getDate() + i); return `${dt.getDate()}/${dt.getMonth() + 1}`; };
    const KIND = { login: "Login", report: "Feltmelding", comment: "Kommentar", like: "Like", support: "Supportbesked" };
    out.replaceChildren(
      h("div", { class: "stats" }, st(fmtN(a.d1), "aktive i dag"), st(fmtN(a.d7), "aktive 7 dage"), st(fmtN(a.d30), "aktive 30 dage"), st(fmtN(a.views_d1), "sidevisninger i dag")),
      d.by_day.length > 1 ? h("div", { class: "card mt" }, h("h3", { style: { marginTop: 0 } }, "Aktivitet pr. dag"),
        lineChart({ series, height: 200, xFormat: label, yFormat: (v) => fmtN(Math.round(v)) }), seriesLegend(series)) : null,
      h("div", { class: "grid-2 mt" },
        h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Mest besøgte sider"),
          h("table", { class: "data" }, h("thead", {}, h("tr", {}, h("th", {}, "Side"), h("th", { class: "right" }, "Visninger"), h("th", { class: "right" }, "Brugere"))),
            h("tbody", {}, ...d.paths.map(p => h("tr", {}, h("td", {}, p.path), h("td", { class: "right num" }, fmtN(p.n)), h("td", { class: "right num" }, fmtN(p.users))))))),
        h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Handlinger"),
          h("ul", { class: "mini-list" }, ...d.kinds.map(k => h("li", {}, KIND[k.kind] || k.kind, h("b", { class: "num" }, fmtN(k.n))))),
          h("h3", {}, "Seneste"),
          h("ul", { class: "mini-list", style: { maxHeight: "260px", overflow: "auto" } }, ...d.recent.map(r => h("li", {}, h("span", {}, h("b", {}, r.navn), ` · ${KIND[r.kind] || r.kind}`), h("small", { class: "muted" }, ago(r.created_at))))))),
      h("div", { class: "card mt table-wrap" }, h("h3", { style: { marginTop: 0 } }, "Brugere"),
        h("table", { class: "data stack-sm", style: { "--cols": 3 } }, h("thead", {}, h("tr", {}, h("th", {}, "Bruger"), h("th", {}, "Senest"), h("th", { class: "right" }, "Visninger"), h("th", { class: "right" }, "Handlinger"))),
          h("tbody", {}, ...d.users.map(u => h("tr", { class: "click", onclick: () => userActivity(u) },
            h("td", {}, h("b", {}, u.navn), h("small", { class: "muted" }, ` ${u.obserkode}`)), h("td", { class: "muted", "data-label": "Senest" }, ago(u.last)),
            h("td", { class: "right num", "data-label": "Visninger" }, fmtN(u.views)), h("td", { class: "right num", "data-label": "Handlinger" }, fmtN(u.actions))))))));
  };
  await load();
}

async function userActivity(u) {
  const list = h("div", {}, h("div", { class: "skeleton" }));
  modal(`Aktivitet: ${u.navn}`, list, { wide: true });
  const rows = await api(`/api/admin/activity/user/${u.id}`);
  list.replaceChildren(h("ul", { class: "mini-list", style: { maxHeight: "60vh", overflow: "auto" } }, ...rows.map(r => h("li", {},
    h("span", {}, h("b", {}, r.kind === "view" ? "Side" : r.kind), " ", r.path || (r.detail ? JSON.stringify(r.detail) : "")), h("small", { class: "muted" }, new Date(r.created_at).toLocaleString("da-DK"))))));
}

// ---------------------------------------------------------------- support
