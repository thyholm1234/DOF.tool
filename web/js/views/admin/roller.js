// Admin → Roller (superadmin): roller og deres moduler.
import { api, h, icon, fmtN, ago, toast, errorBox, empty } from "../../core.js";
import { roleSelect } from "./common.js";

/** Roller (kun superadmin): rollerne vandret, modulerne lodret – og nederst alle med en tildelt rolle. */
export async function rolesPanel(body) {
  let d;
  const load = async () => {
    try { d = await api("/api/admin/roles"); } catch (err) { body.replaceChildren(errorBox(err)); return; }
    draw();
  };
  const patch = async (key, payload, msg) => {
    try { await api(`/api/admin/roles/${key}`, { method: "PATCH", body: payload }); toast(msg); } catch (err) { toast(err.message, "err"); }
    load();
  };
  const addRole = async () => {
    const name = prompt("Navn på den nye rolle:");
    if (!name?.trim()) return;
    try { await api("/api/admin/roles", { method: "POST", body: { name: name.trim(), modules: [] } }); toast(`Rollen ${name.trim()} er oprettet`); } catch (err) { toast(err.message, "err"); }
    load();
  };
  const removeRole = async (r) => {
    if (!confirm(`Slet rollen ${r.name}?${r.users ? ` ${r.users} ${r.users === 1 ? "bruger" : "brugere"} med rollen bliver almindelige brugere.` : ""}`)) return;
    try { await api(`/api/admin/roles/${r.key}`, { method: "DELETE" }); toast(`Rollen ${r.name} er slettet`); } catch (err) { toast(err.message, "err"); }
    load();
  };
  const draw = () => {
    const names = Object.fromEntries(d.roles.map(r => [r.key, r.name]));
    const head = h("tr", {}, h("th", { class: "roles-mod" }, "Modul"),
      ...d.roles.map(r => h("th", { class: "roles-col" },
        h("input", { class: "roles-name", value: r.name, maxlength: 60, "aria-label": "Rollens navn",
          onchange: (e) => e.target.value.trim() && e.target.value.trim() !== r.name ? patch(r.key, { name: e.target.value.trim() }, "Navnet er gemt") : (e.target.value = r.name) }),
        h("small", { class: "muted" }, `${fmtN(r.users)} ${r.users === 1 ? "bruger" : "brugere"}`),
        r.fixed ? h("small", { class: "muted", title: r.locked ? "Almindelige brugere har ingen admin-moduler" : "Administratorer har særlige rettigheder i koden og kan ikke slettes" }, "fast rolle")
          : h("button", { class: "btn sm ghost danger", type: "button", onclick: () => removeRole(r) }, "Slet"))),
      h("th", { class: "roles-col" }, h("b", {}, "Superadmin"), h("small", { class: "muted" }, d.superadmin.obserkode ? `${d.superadmin.obserkode} · fra .env` : "fra .env"), h("small", { class: "muted" }, "alle moduler")));
    const rows = Object.entries(d.modules).map(([m, label]) => {
      const [name, rest] = label.split(": ");
      return h("tr", {}, h("th", { class: "roles-mod", scope: "row" }, h("b", {}, name), rest ? h("small", { class: "muted", style: { display: "block" } }, rest) : null),
        ...d.roles.map(r => h("td", { class: "roles-cell" }, h("input", { type: "checkbox", checked: r.modules.includes(m), disabled: r.locked, "aria-label": `${r.name}: ${name}`,
          onchange: (e) => patch(r.key, { modules: e.target.checked ? [...r.modules, m] : r.modules.filter(x => x !== m) }, `${r.name}: ${name} ${e.target.checked ? "givet" : "fjernet"}`) }))),
        h("td", { class: "roles-cell" }, h("input", { type: "checkbox", checked: true, disabled: true, "aria-label": `Superadmin: ${name}` })));
    });
    const people = d.people.map(p => h("tr", {},
      h("td", {}, h("b", {}, p.navn || p.obserkode), h("div", { class: "muted" }, p.obserkode, p.blocked ? " · spærret" : "", p.approved ? "" : " · afventer")),
      h("td", { "data-label": "Rolle" }, roleSelect(p, names, load)),
      h("td", { "data-label": "Moduler" }, h("span", { title: p.modules.map(m => d.modules[m]).join("\n") || "Ingen" },
        p.role === "superadmin" ? "alle" : p.modules.length ? `${p.modules.length} af ${Object.keys(d.modules).length}` : "ingen"),
        p.permissions ? h("span", { class: "tag plain", style: { marginLeft: "6px" }, title: "Individuelle rettigheder i stedet for rollens standard (ændres under Brugere)" }, "individuel") : null),
      h("td", { class: "muted", "data-label": "Sidst set" }, p.last_seen ? ago(p.last_seen) : "aldrig")));
    body.replaceChildren(
      h("div", { class: "row between", style: { marginBottom: "12px", alignItems: "flex-start" } },
        h("p", { class: "muted", style: { margin: 0, flex: "1 1 320px" } }, "Hver rolle giver et sæt admin-moduler. Ændringer gælder med det samme for alle med rollen (undtagen brugere med individuelle rettigheder, som sættes under Brugere). Superadmin har altid alle moduler og styres fra .env."),
        h("button", { class: "btn primary", type: "button", onclick: addRole, html: `${icon("plus")} Ny rolle` })),
      h("div", { class: "card table-wrap" }, h("table", { class: "data roles-table" }, h("thead", {}, head), h("tbody", {}, ...rows))),
      h("h2", { class: "mt" }, "Brugere med roller"),
      h("div", { class: "card table-wrap" }, d.people.length ? h("table", { class: "data stack-sm", style: { "--cols": 2 } },
        h("thead", {}, h("tr", {}, h("th", {}, "Bruger"), h("th", {}, "Rolle"), h("th", {}, "Admin-moduler"), h("th", {}, "Sidst set"))),
        h("tbody", {}, ...people)) : empty("Ingen har roller endnu", "Giv en bruger en rolle under Brugere.")));
  };
  await load();
}
