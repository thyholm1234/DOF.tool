// DOFbasen: tekstsider (Hvad er det?, Rettigheder, Hjælp, Kontakt …), nyheder, Min side og fotogalleri.
import { api, h, toast, state } from "../core.js";
import { BASE, head, link, md, dmy, notice, loadingBox, editPage, dbModal, markupHelp, on } from "./shell.js";
import { layout, popupLinks } from "./common.js";

// Sider der ikke er en del af showcasen (findes som egne apps hos DOF)
const EXTERNAL = {
  atlas: ["Atlas III", "Atlas III – Danmarks nye fugleatlas – er en selvstændig app hos DOF og er ikke kopieret til showcasen.", "https://dofbasen.dk/atlas"],
  punkt: ["Punkttællinger", "Punkttællingsprojektet har sit eget modul hos DOF og er ikke kopieret til showcasen.", "https://dofbasen.dk/pkt/kort.php"],
};

export async function page(parts) {
  const slug = parts[1] || "start";
  if (EXTERNAL[slug]) {
    const [t, text, url] = EXTERNAL[slug];
    return layout(h("div", {}, head(t), h("p", {}, text), h("p", {}, h("a", { href: url, target: "_blank", rel: "noopener" }, "Åbn originalen på dofbasen.dk ↗"))));
  }
  const box = h("div", {});
  let p;
  try { p = await api(`/api/dofbasen/sider/${encodeURIComponent(slug)}`); } catch (err) {
    box.append(notice(err.status === 404 ? "Siden findes ikke." : err.message, true));
    return layout(box);
  }
  const draw = (pg) => {
    const edit = pg.can_edit ? () => editPage(pg, (np) => draw({ ...np, can_edit: true })) : null;
    if (slug === "hjaelp") {
      // Hjælp: spørgsmålene i venstre side, svarene til højre (som help.php)
      const ids = pg.sections.map((_, i) => `dbq${i}`);
      box.replaceChildren(h("div", { class: "db-help" },
        h("nav", {}, head("Spørgsmål", { edit }), ...pg.sections.slice(1).map((s, i) => h("a", { href: "javascript:void 0", onclick: () => document.getElementById(ids[i + 1])?.scrollIntoView({ behavior: "smooth" }) }, cap(s.title)))),
        h("div", {}, ...pg.sections.flatMap((s, i) => [h("div", { id: ids[i] }, head(s.title)), md(s.body)]))));
    } else {
      box.replaceChildren(...pg.sections.flatMap((s, i) => [head(s.title || pg.title, { edit: i === 0 ? edit : null }), md(s.body)]));
      if (!pg.sections.length) box.replaceChildren(head(pg.title, { edit }), h("p", { class: "muted" }, "Siden er tom."));
    }
    if (slug === "kontakt") coordinators(box);
  };
  draw(p);
  if (slug === "hjaelp") return h("div", { class: "db-page wide" }, h("div", { class: "db-col" }, box));
  return layout(box);
}

// Overskrifterne står med store bogstaver; indholdsfortegnelsen vises med almindelige (egennavne og forkortelser bevares)
const cap = (s) => (s.charAt(0) + s.slice(1).toLowerCase()).replace(/dofbase/g, "DOFbase").replace(/\b(pwa|dku|ios)\b/g, (m) => m === "ios" ? "iOS" : m.toUpperCase())
  .replace(/iphone/g, "iPhone").replace(/android/g, "Android").replace(/hjælp!!/, "HJÆLP!!");

async function coordinators(box) {
  const el = h("div", {});
  box.append(el);
  try {
    const d = await api("/api/dofbasen/lister/koordinatorer");
    const { editList } = await import("./opslag.js");
    el.replaceChildren(head("Lokalkoordinatorer", { edit: d.can_edit ? () => editList(d, () => { el.remove(); coordinators(box); }) : null }),
      h("table", { class: "db-table" }, h("thead", {}, h("tr", {}, h("th", {}, "Lokalafdeling:"), h("th", {}, "Koordinator:"), h("th", {}, "Evt. kommuneopdeling:"))),
        h("tbody", {}, ...d.rows.map(r => h("tr", {}, h("td", {}, r[0] + ":"), h("td", {}, h("b", {}, r[1]), r[2] && r[2] !== "Lokalkoordinator" ? h("div", { class: "muted" }, r[2]) : null), h("td", {}, r[3] || ""))))),
      h("p", { class: "muted small", style: { marginTop: "6px" } }, d.intro));
  } catch { /* listen findes ikke */ }
}

// ---------------------------------------------------------------- nyheder

export async function nyheder(parts) {
  const id = parts[1] ? +parts[1] : null;
  const box = h("div", {}, loadingBox());
  const load = async () => {
    try {
      if (id) {
        const n = await api(`/api/dofbasen/nyheder/${id}`);
        box.replaceChildren(head("Nyheder", { edit: n.can_edit ? () => editNews(n, load) : null }), h("p", {}, link("/nyheder", "Se de seneste nyheder i DOFbasen"), "."),
          h("table", { class: "db-table plain" }, h("tbody", {}, h("tr", {}, h("td", { style: { width: "96px" } }, h("b", { class: "db-su" }, dmy(n.dato).replaceAll("-", "/")), ":"),
            h("td", {}, h("b", {}, n.title), n.published ? "" : h("span", { class: "muted" }, " (ikke offentliggjort)"), md(n.body))))));
      } else {
        const d = await api("/api/dofbasen/nyheder?alle=true");
        box.replaceChildren(head("Nyheder", { edit: d.can_edit ? () => editNews(null, load) : null }),
          h("table", { class: "db-table" }, h("tbody", {}, ...d.rows.map(n => h("tr", {},
            h("td", { style: { width: "96px" } }, h("b", {}, dmy(n.dato).replaceAll("-", "/")), ":"),
            h("td", {}, link(`/nyheder/${n.id}`, n.title), n.pinned ? h("span", { class: "db-chip" }, "fastgjort") : null, n.published ? null : h("span", { class: "db-chip" }, "kladde")))))));
      }
    } catch (err) { box.replaceChildren(notice(err.message, true)); }
  };
  load();
  return layout(box);
}

export function editNews(n, done) {
  const dato = h("input", { type: "date", value: n?.dato || new Date().toISOString().slice(0, 10) });
  const title = h("input", { type: "text", class: "full", value: n?.title || "", maxlength: 200 });
  const body = h("textarea", { rows: 14 }, n?.body || "");
  const pub = h("input", { type: "checkbox", checked: n ? n.published : true });
  const pin = h("input", { type: "checkbox", checked: !!n?.pinned });
  const m = dbModal(n ? "Redigér nyhed" : "Ny nyhed", h("div", { class: "db-admin" },
    h("div", { class: "db-form-row" }, h("label", {}, "Dato", dato), h("label", {}, h("span", {}, pub, " Offentliggjort")), h("label", {}, h("span", {}, pin, " Fastgjort under listen (fx Kendte fejl)"))),
    h("div", { class: "field" }, h("b", {}, "Overskrift"), title), markupHelp(), h("div", { class: "field" }, h("b", {}, "Tekst"), body),
    h("div", { class: "db-form-row" },
      n ? h("button", { type: "button", class: "db-danger", onclick: async () => { if (!confirm("Slet nyheden?")) return; await api(`/api/dofbasen/nyheder/${n.id}`, { method: "DELETE" }); m.close(); location.hash = `${BASE}/nyheder`; } }, "Slet") : null,
      h("span", { style: { flex: 1 } }), h("button", { type: "button", onclick: () => m.close() }, "Annullér"),
      h("button", { type: "button", class: "db-primary", onclick: async () => {
        try {
          await api(n ? `/api/dofbasen/nyheder/${n.id}` : "/api/dofbasen/nyheder", { method: n ? "PUT" : "POST",
            body: { dato: dato.value, title: title.value.trim(), body: body.value, published: pub.checked, pinned: pin.checked } });
          toast("Nyheden er gemt"); m.close(); done?.();
        } catch (err) { toast(err.message, "err"); }
      } }, "Gem"))), { wide: true });
}

// ---------------------------------------------------------------- Min side og fotogalleri

export async function bruger() {
  const u = state.user;
  const main = h("div", {}, head("Min side"),
    h("table", { class: "db-table" }, h("tbody", {},
      h("tr", {}, h("td", { style: { width: "160px" } }, "Navn:"), h("td", {}, u.navn || "")),
      h("tr", {}, h("td", {}, "Obserkode:"), h("td", {}, u.obserkode)),
      h("tr", {}, h("td", {}, "DOFbasen-roller:"), h("td", {}, (await rolesText()) || "Ingen")))),
    head("Mine observationer"),
    h("ul", { class: "db-md" },
      on("dofbasen_search") ? h("li", {}, link(`/soeg?periode=antaldage&dage=30&obserdata=${u.obserkode}&soeg=1`, "Mine observationer de seneste 30 dage")) : null,
      on("dofbasen_search") ? h("li", {}, link(`/soeg?periode=none&obserdata=${u.obserkode}&biggestobs=50&soeg=1`, "Mine 50 største observationer")) : null,
      h("li", {}, link(`/obser/${u.obserkode}`, "Min observatørprofil")),
      h("li", {}, h("a", { href: "#/indtast" }, "Indtastning (DOF.tool)"))),
    on("dofbasen_api") ? [head("API"), h("p", {}, "Brug DOFbasen-API'et i dine egne apps. ", link("/api", "Se dokumentation og dine API-nøgler"), ".")] : null,
    head("Brugeroplysninger"),
    h("p", {}, "Login sker med din DOFbasen-obserkode og adgangskode, som tjekkes hos DOFbasen. Portalen gemmer ikke din adgangskode. Ret dine personoplysninger på ",
      h("a", { href: "https://dofbasen.dk/mine/person.php", target: "_blank", rel: "noopener" }, "dofbasen.dk ↗"), "."));
  popupLinks(main);
  return layout(main);
}

async function rolesText() {
  try {
    const { ctx } = await import("./shell.js");
    return (ctx.data.roles || []).join(", ") + (ctx.data.perms?.length && !ctx.data.roles?.length ? "Alle rettigheder (portaladministrator)" : "");
  } catch { return ""; }
}

export async function fotogalleri() {
  const rows = await api("/api/dofbasen/forside/fotos").catch(() => []);
  return layout(h("div", {}, head("Fotogalleri"),
    rows.length ? h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: "10px" } }, ...rows.map(p =>
      h("figure", { style: { margin: 0, border: "4px solid #eee" } }, h("img", { src: p.url, alt: p.art, loading: "lazy", style: { width: "100%", height: "140px", objectFit: "cover", display: "block" } }),
        h("figcaption", { style: { padding: "4px" } }, h("b", {}, p.art), h("br"), `${p.lokalitet || ""} · ${dmy(p.dato)}`))))
      : h("p", { class: "muted" }, "Der er endnu ingen fotos. DOFbasens egne fotos (copyright pr. fotograf) hentes ikke i showcasen; fotos fra indtastninger i DOF.tool vises her."),
    h("p", { class: "muted small" }, "Der er copyright på alle billeder, tilhørende den enkelte fotograf.")));
}

