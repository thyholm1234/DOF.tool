// Admin → Brugere: brugerliste, godkendelse, testbrugere, roller og individuelle rettigheder.
import { api, state, h, ago, toast, errorBox, modal } from "../../core.js";
import { roleSelect } from "./common.js";

export async function users(body) {
  const q = h("input", { type: "search", placeholder: "Søg obserkode/navn…", style: { maxWidth: "280px" } });
  const out = h("div", { class: "card table-wrap mt" });
  const pendingBox = h("div", {});
  let meta;
  try { meta = await api("/api/admin/modules"); } catch (err) { body.append(errorBox(err)); return; }
  const approvalSwitch = h("input", { type: "checkbox", checked: meta.require_approval, onchange: async (e) => {
    try { await api("/api/admin/settings", { method: "PUT", body: { require_approval: e.target.checked } }); toast(e.target.checked ? "Nye brugere skal godkendes" : "Nye brugere får adgang med det samme"); }
    catch (err) { e.target.checked = !e.target.checked; toast(err.message, "err"); }
  } });
  body.append(
    h("div", { class: "card stack", style: { marginBottom: "12px" } },
      h("label", { class: "check" }, approvalSwitch, h("span", {}, h("b", {}, "Nye brugere skal godkendes"),
        h("span", { class: "muted" }, " – en ny bruger oprettes, når koden er gyldig hos DOFbasen, men får først adgang, når en administrator har godkendt kontoen. Er den slået fra, får nye brugere adgang med det samme."))),
      pendingBox),
    h("div", { class: "card row between", style: { marginBottom: "12px" } },
      h("span", {}, h("b", {}, "Testbrugere"), h("span", { class: "muted" }, " – konti med lokal adgangskode, til at afprøve appen uden en DOFbasen-konto. De vises ikke på scoreboards.")),
      h("button", { class: "btn sm primary", onclick: () => testUserModal(null, load) }, "Opret testbruger")),
    h("p", { class: "muted", style: { marginTop: 0 } }, "Rollen giver et standardsæt af admin-moduler. Under Rettigheder kan den enkelte bruger i stedet få præcis de moduler, der er krydset af."), q, out);
  const isSuper = state.user.role === "superadmin";
  const editable = (u) => u.id !== state.user.id && u.role !== "superadmin" && (isSuper || !["admin", "superadmin"].includes(u.role));
  const approve = async (u) => {
    try { await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { approved: true } }); toast(`${u.navn || u.obserkode} er godkendt`); load(); }
    catch (err) { toast(err.message, "err"); }
  };
  const load = async () => {
    const rows = await api(`/api/admin/users?q=${encodeURIComponent(q.value)}`);
    const waiting = rows.filter(u => !u.approved);
    pendingBox.replaceChildren(waiting.length ? h("div", { class: "notice warn stack", style: { gap: "6px" } },
      h("b", {}, `${waiting.length} ${waiting.length === 1 ? "bruger afventer" : "brugere afventer"} godkendelse`),
      ...waiting.map(u => h("div", { class: "row between" },
        h("span", {}, h("b", {}, u.navn || u.obserkode), h("small", { class: "muted" }, ` ${u.obserkode} · oprettet ${ago(u.created_at)}`)),
        h("span", { class: "row", style: { gap: "6px" } },
          h("button", { class: "btn sm primary", onclick: () => approve(u) }, "Godkend"),
          h("button", { class: "btn sm ghost danger", onclick: async () => { if (confirm(`Afvis og slet ${u.obserkode}? Personen kan søge igen ved at logge ind.`)) { try { await api(`/api/admin/users/${u.id}`, { method: "DELETE" }); toast("Afvist"); load(); } catch (err) { toast(err.message, "err"); } } } }, "Afvis"))))) : h("small", { class: "muted" }, "Ingen venter på godkendelse."));
    out.replaceChildren(h("table", { class: "data stack-sm", style: { "--cols": 3 } },
      h("thead", {}, h("tr", {}, h("th", {}, "Bruger"), h("th", {}, "Rolle"), h("th", {}, "Admin-moduler"), h("th", {}, "Sidst set"), h("th", { class: "right" }, "Enheder/regler"), h("th", {}))),
      h("tbody", {}, ...rows.map(u => h("tr", {},
        h("td", {}, h("b", {}, u.navn || u.obserkode), u.approved ? null : h("span", { class: "tag SUB", style: { marginLeft: "6px" } }, "afventer"),
          u.is_test ? h("span", { class: "tag plain", style: { marginLeft: "6px" }, title: "Testbruger med lokal adgangskode" }, "test") : null,
          h("div", { class: "muted" }, u.obserkode, u.blocked ? " · spærret" : "")),
        h("td", { class: "wide", "data-label": "Rolle" }, roleSelect(u, meta.role_names, load)),
        h("td", { "data-label": "Moduler" },
          h("span", { title: u.modules.map(m => meta.modules[m]).join("\n") || "Ingen" },
            u.role === "superadmin" ? "alle" : u.modules.length ? `${u.modules.length} af ${Object.keys(meta.modules).length}` : "ingen"),
          u.permissions ? h("span", { class: "tag plain", style: { marginLeft: "6px" }, title: "Individuelle rettigheder i stedet for rollens standard" }, "individuel") : null,
          editable(u) ? h("button", { class: "btn sm ghost", style: { marginLeft: "6px" }, onclick: () => permissionsModal(u, meta, load) }, "Rettigheder") : null),
        h("td", { class: "muted", "data-label": "Sidst set" }, u.last_seen ? ago(u.last_seen) : "aldrig"),
        h("td", { class: "right num", "data-label": "Enh./regler" }, `${u.devices} / ${u.rules}`),
        h("td", { class: "right nowrap wide" }, u.role !== "superadmin" ? [
          u.approved ? null : h("button", { class: "btn sm primary", onclick: () => approve(u) }, "Godkend"),
          u.is_test ? h("button", { class: "btn sm", onclick: () => testUserModal(u, load) }, "Ny kode") : null,
          h("button", { class: "btn sm", onclick: async () => { await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { blocked: !u.blocked } }); load(); } }, u.blocked ? "Ophæv spærring" : "Spær"),
          isSuper || u.is_test ? h("button", { class: "btn sm ghost danger", onclick: async () => { if (confirm(`Slet ${u.obserkode}?`)) { await api(`/api/admin/users/${u.id}`, { method: "DELETE" }); load(); } } }, "Slet") : null] : null))))));
  };
  let t; q.oninput = () => { clearTimeout(t); t = setTimeout(load, 250); };
  await load();
}

/** Opret en testbruger (u = null) eller giv en ny adgangskode. Adgangskoden vises kun her, så den kan gives videre. */
function testUserModal(u, done) {
  const navn = h("input", { type: "text", placeholder: "Fx Testbruger Anna", maxlength: 160, value: u?.navn || "" });
  const pw = h("input", { type: "text", placeholder: "Tom = tilfældig adgangskode", minlength: 6, maxlength: 100, autocomplete: "off" });
  const out = h("div", {});
  const go = h("button", { class: "btn primary", type: "button" }, u ? "Giv ny adgangskode" : "Opret");
  const body = h("div", { class: "stack" }, u ? h("p", { class: "muted", style: { margin: 0 } }, `${u.navn || u.obserkode} (${u.obserkode}) bliver logget ud overalt.`)
    : h("label", { class: "field" }, h("span", {}, "Navn"), navn),
    h("label", { class: "field" }, h("span", {}, "Adgangskode"), pw), go, out);
  const m = modal(u ? "Ny adgangskode" : "Opret testbruger", body);
  go.onclick = async () => {
    go.disabled = true;
    try {
      const r = u ? await api(`/api/admin/test-users/${u.id}/password`, { method: "POST", body: { password: pw.value } })
        : await api("/api/admin/test-users", { method: "POST", body: { navn: navn.value, password: pw.value || null } });
      const text = `Obserkode: ${r.obserkode}\nAdgangskode: ${r.password}`;
      out.replaceChildren(h("div", { class: "notice stack", style: { gap: "6px" } }, h("b", {}, "Log ind med"), h("pre", { style: { margin: 0, userSelect: "all" } }, text),
        h("small", { class: "muted" }, "Adgangskoden vises kun nu."),
        h("button", { class: "btn sm", type: "button", onclick: () => navigator.clipboard?.writeText(text).then(() => toast("Kopieret")) }, "Kopiér")));
      go.remove(); done();
    } catch (err) { go.disabled = false; toast(err.message, "err"); }
  };
  return m;
}

/** Afkrydsningsliste over admin-moduler for én bruger. Moduler man ikke selv har, kan ikke ændres. */
function permissionsModal(u, meta, onSaved) {
  const roleDefault = new Set(meta.roles[u.role] || []);
  const grantable = new Set(meta.grantable);
  let custom = !!u.permissions;
  const boxes = Object.fromEntries(Object.keys(meta.modules).map(m => [m, h("input", { type: "checkbox", checked: u.modules.includes(m) })]));
  const list = h("div", { class: "stack", style: { gap: "6px" } });
  const mode = h("input", { type: "checkbox", checked: custom });
  const draw = () => {
    for (const [m, b] of Object.entries(boxes)) {
      if (!custom) b.checked = roleDefault.has(m);
      b.disabled = !custom || !grantable.has(m);
    }
    list.replaceChildren(...Object.entries(meta.modules).map(([m, label]) => {
      const [name, rest] = label.split(": ");
      return h("label", { class: "check", style: { opacity: grantable.has(m) ? 1 : 0.6 }, title: grantable.has(m) ? "" : "Du har ikke selv adgang til modulet" },
        boxes[m], h("span", {}, h("b", {}, name), rest ? h("span", { class: "muted" }, ` – ${rest}`) : null,
          roleDefault.has(m) ? h("small", { class: "muted" }, ` · ${u.role}`) : null));
    }));
  };
  mode.onchange = () => { custom = mode.checked; draw(); };
  draw();
  const dlg = modal(`Rettigheder: ${u.navn || u.obserkode}`, h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    const permissions = custom ? Object.keys(boxes).filter(m => boxes[m].checked) : null;
    try {
      await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { permissions } });
      toast(custom ? "Rettighederne er gemt" : `Følger nu rollens standard (${u.role})`); dlg.close(); onSaved();
    } catch (err) { toast(err.message, "err"); }
  } },
    h("label", { class: "check" }, mode, h("span", {}, h("b", {}, "Individuelle rettigheder"), h("span", { class: "muted" }, ` – ellers følger brugeren rollens standard (${u.role})`))),
    list,
    h("small", { class: "muted" }, "Rettighederne gælder både faner og funktioner (fx officielle Mobgang-presets og kurser i Læring). Individuelle rettigheder bevares, hvis rollen ændres."),
    h("div", { class: "row" }, h("button", { class: "btn primary" }, "Gem"), h("button", { type: "button", class: "btn ghost", onclick: () => dlg.close() }, "Annullér"))));
}
