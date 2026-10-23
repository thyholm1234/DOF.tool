import { api, state, h, fmtN, fmtDate, loadRef, requireLogin, toast, seg } from "../core.js";
import { refreshUser, applyTheme } from "../app.js";
import { enablePush } from "./alerts.js";

export async function render(view) {
  if (requireLogin(view)) return;
  await loadRef();
  const u = state.user;
  const p = { lists_public: true, push_enabled: true, afdelinger: [], kommuner: [], ...u.prefs };
  const stats = await api("/api/me/stats").catch(() => null);

  const afd = h("div", { class: "chips", style: { flexWrap: "wrap" } });
  const drawAfd = () => afd.replaceChildren(...state.afdelinger.map(a => h("button", { type: "button", class: `chip ${p.afdelinger.includes(String(a.id)) ? "on" : ""}`, onclick: () => {
    const id = String(a.id);
    p.afdelinger = p.afdelinger.includes(id) ? p.afdelinger.filter(x => x !== id) : [...p.afdelinger, id].slice(-3);
    drawAfd();
  } }, a.navn.replace("DOF ", ""))));
  drawAfd();
  const komSel = h("select", {}, h("option", { value: "" }, "Tilføj kommune…"), ...state.kommuner.map(k => h("option", { value: k.kode }, k.navn)));
  const komChips = h("div", { class: "row" });
  const drawKom = () => komChips.replaceChildren(...p.kommuner.map(k => h("span", { class: "chip on" }, state.kommuner.find(x => x.kode === k)?.navn || k,
    h("span", { style: { cursor: "pointer", marginLeft: "4px" }, onclick: () => { p.kommuner = p.kommuner.filter(x => x !== k); drawKom(); } }, "×"))));
  komSel.onchange = () => { if (komSel.value && !p.kommuner.includes(komSel.value)) p.kommuner = [...p.kommuner, komSel.value].slice(-3); komSel.value = ""; drawKom(); };
  drawKom();

  const qs = h("input", { type: "time", value: p.quiet_hours?.start || "" });
  const qe = h("input", { type: "time", value: p.quiet_hours?.end || "" });
  const pub = h("input", { type: "checkbox", checked: p.lists_public });
  const push = h("input", { type: "checkbox", checked: p.push_enabled });
  const theme = seg([["auto", "Automatisk"], ["light", "Lys"], ["dark", "Mørk"]], p.theme || "auto", (v) => { p.theme = v; localStorage.setItem("theme", v); state.user.prefs.theme = v; applyTheme(); });

  const phone = h("input", { type: "tel", inputmode: "tel", value: p.phone || "", placeholder: "Fx 20 30 40 50", maxlength: 20, autocomplete: "tel" });
  const save = async () => {
    try {
      await api("/api/me", { method: "PATCH", body: { afdelinger: p.afdelinger, kommuner: p.kommuner, lists_public: pub.checked, push_enabled: push.checked,
        quiet_hours: qs.value && qe.value ? { start: qs.value, end: qe.value } : null, theme: p.theme || "auto", phone: phone.value.trim() } });
      await refreshUser();
      toast("Indstillinger gemt");
    } catch (err) { toast(err.message, "err"); }
  };

  view.append(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, u.navn || u.obserkode), h("p", { class: "lead" }, `${u.obserkode} · ${u.role}`))),
    stats ? h("div", { class: "stats" },
      st(fmtN(stats.n_obs), "observationer"), st(fmtN(stats.n_trips), "ture"), st(fmtN(stats.n_taxa), "taxa"), st(stats.first ? new Date(stats.first).getFullYear() : "–", "første år")) : null,
    h("div", { class: "grid-2 mt" },
      h("div", { class: "card stack" }, h("h3", {}, "Mine områder"),
        h("div", {}, h("p", { class: "muted", style: { marginBottom: "6px" } }, "Lokalafdelinger (op til 3) – bruges i feed, scoreboards og som standard i quizzer."), afd),
        h("div", {}, h("p", { class: "muted", style: { marginBottom: "6px" } }, "Kommuner (op til 3) – giver kommunelister og -scoreboards."), komSel, komChips),
        h("p", { class: "muted", style: { margin: 0 } }, "Egne polygoner og radier oprettes under ", h("a", { href: "#/alarmer?tab=omraader" }, "Alarmer → Områder"), ".")),
      h("div", { class: "card stack" }, h("h3", {}, "Kontakt"),
        h("label", { class: "field" }, h("span", {}, "Telefonnummer"), phone),
        h("small", { class: "muted", style: { marginTop: "-6px" } }, "Vises kun på de kalenderbesøg, hvor du selv vælger det – så andre på trækposten kan ringe, fx når der er noget at se."),
        h("h3", { class: "mt" }, "Notifikationer"),
        h("label", { class: "check" }, push, "Send push til mine enheder"),
        h("div", {}, h("p", { class: "muted", style: { marginBottom: "6px" } }, "Stille timer (ingen push – notifikationer ligger stadig i indbakken)"), h("div", { class: "row" }, qs, "–", qe)),
        h("button", { type: "button", class: "btn", onclick: async () => { try { await enablePush(); toast("Push slået til på denne enhed"); } catch (err) { toast(err.message, "err"); } } }, "Slå push til på denne enhed"),
        h("h3", { class: "mt" }, "Lister og privatliv"),
        h("label", { class: "check" }, pub, "Vis mig på scoreboards og lad andre se mine lister"),
        h("h3", { class: "mt" }, "Udseende"), theme)),
    h("div", { class: "row mt" }, h("button", { class: "btn primary", onclick: save }, "Gem indstillinger")),
    h("div", { class: "card mt2 row between" }, h("div", {}, h("h3", { style: { margin: 0 } }, "Boligbirding"),
        h("p", { class: "muted", style: { margin: 0 } }, "Matrikler, perioder, flytning og placeringer har deres eget modul.")),
      h("a", { class: "btn", href: "#/matrikel" }, "Gå til Matrikel →")),
    h("div", { class: "card mt2" }, h("h3", {}, "Data"),
      h("p", { class: "muted" }, "Dine observationer kommer fra DOFbasen: historik fra det lokale arkiv, ældre år hentes én gang ved første login, og nye observationer kommer automatisk med live-hentningen.",
        u.history_synced_at ? ` Historik hentet ${fmtDate(u.history_synced_at)}.` : " Historik er i kø."),
      h("div", { class: "row" },
        h("button", { class: "btn", onclick: async () => { try { const r = await api("/api/me/sync", { method: "POST" }); toast(`Lagt i kø (${r.queue} job foran – ét kald pr. minut)`); } catch (err) { toast(err.message, "err"); } } }, "Hent min historik igen"),
        h("span", { style: { flex: 1 } }),
        u.role !== "superadmin" ? h("button", { class: "btn ghost danger", onclick: async () => {
          if (!confirm("Slet din konto i portalen? Dine områder, regler og grupper slettes. Dine data i DOFbasen påvirkes ikke.")) return;
          await api("/api/me", { method: "DELETE" }); location.hash = "#/"; location.reload();
        } }, "Slet min konto") : null)));
  if (state.user.dofbasen?.features?.dofbasen_api) {
    const apiBox = h("div", { class: "card mt2 stack" });
    view.append(apiBox);
    apiTokens(apiBox);
  }
  if (location.hash.includes("matrikler")) location.hash = "#/matrikel?tab=matrikler";
}

/** API-nøgler til DOFbasen-API'et: en administrator giver adgang, brugeren laver og sletter selv sine nøgler. */
async function apiTokens(box) {
  let d;
  try { d = await api("/api/dofbasen/me/api"); } catch { box.remove(); return; }
  const title = h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, "API-nøgler (DOFbasen)"), h("a", { href: "#/dofbasen/api" }, "Dokumentation →"));
  if (!d.access) {
    box.replaceChildren(title, h("p", { class: "muted", style: { margin: 0 } }, "Med DOFbasen-API'et kan du hente observationer, arter og lokaliteter ind i dine egne apps. En administrator skal give dig adgang, før du kan lave nøgler."));
    return;
  }
  const active = d.tokens.filter(t => !t.revoked_at);
  const name = h("input", { type: "text", placeholder: "Navn på nøglen, fx 'Min hjemmeside'", maxlength: 80 });
  const fresh = h("div", {});
  box.replaceChildren(title,
    h("p", { class: "muted", style: { margin: 0 } }, `Højst ${fmtN(d.rate_per_min)} kald i minuttet pr. nøgle og ${d.max_tokens} aktive nøgler. Send nøglen som "Authorization: Bearer …".`),
    fresh,
    h("form", { class: "row", onsubmit: async (e) => {
      e.preventDefault();
      try {
        const t = await api("/api/dofbasen/me/api/tokens", { method: "POST", body: { name: name.value.trim() || "API-nøgle" } });
        await apiTokens(box);
        box.querySelector(".api-fresh")?.replaceChildren(h("div", { class: "notice" }, h("b", {}, "Kopiér nøglen nu – den vises kun én gang: "),
          h("code", { style: { wordBreak: "break-all", userSelect: "all" } }, t.token)));
      } catch (err) { toast(err.message, "err"); }
    } }, name, h("button", { class: "btn primary", type: "submit", disabled: active.length >= d.max_tokens }, "Lav ny nøgle")),
    d.tokens.length ? h("div", { class: "table-wrap" }, h("table", { class: "data stack-sm", style: { "--cols": 2 } },
      h("thead", {}, h("tr", {}, h("th", {}, "Navn"), h("th", {}, "Nøgle"), h("th", {}, "Oprettet"), h("th", {}, "Kald"), h("th", {}))),
      h("tbody", {}, ...d.tokens.map(t => h("tr", { style: t.revoked_at ? { opacity: .5 } : {} },
        h("td", {}, t.name), h("td", { "data-label": "Nøgle" }, h("code", {}, `${t.prefix}…`)), h("td", { "data-label": "Oprettet" }, fmtDate(t.created_at)),
        h("td", { "data-label": "Kald" }, fmtN(t.uses)),
        h("td", {}, t.revoked_at ? "slettet" : h("button", { class: "btn sm ghost danger", type: "button", onclick: async () => {
          if (!confirm(`Slet nøglen "${t.name}"? Apps, der bruger den, holder op med at virke.`)) return;
          await api(`/api/dofbasen/me/api/tokens/${t.id}`, { method: "DELETE" }); toast("Nøglen er slettet"); apiTokens(box);
        } }, "Slet"))))))) : null);
  fresh.className = "api-fresh";
}

const st = (v, l) => h("div", { class: "stat" }, h("b", {}, v), h("span", {}, l));
