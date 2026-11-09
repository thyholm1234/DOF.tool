import { api, state, h, fmtN, fmtDate, loadRef, requireLogin, toast, seg } from "../core.js";
import { refreshUser, applyTheme } from "../app.js";
import { enablePush } from "./alerts.js";
import { isApp, isPositionDevice, takeOverPosition } from "../native.js";
import { forgetUser } from "../offline.js";
import * as passkey from "../passkey.js";

export async function render(view) {
  if (requireLogin(view)) return;
  await loadRef();
  const u = state.user;
  // Synkronisér alle mine observationer: ét kald i gatewayens kø (ét udtræk pr. minut); status opdateres, mens jobbet venter
  const syncStatus = h("p", { class: "small" });
  const syncBtn = h("button", { class: "btn", onclick: async () => {
    try { await api("/api/me/sync", { method: "POST" }); toast("Synkroniseringen er lagt i kø"); drawSync(); } catch (err) { toast(err.message, "err"); }
  } }, "Synkronisér alle mine observationer");
  let syncTimer = null;
  const drawSync = async () => {
    clearTimeout(syncTimer);
    let st;
    try { st = await api("/api/me/sync"); } catch { return; }
    if (syncTimer !== null && !syncStatus.isConnected) return;  // siden er forladt
    syncBtn.disabled = st.pending || !st.available;
    const last = st.last ? `Sidst synkroniseret ${fmtDate(st.last.at)}: ${fmtN(st.last.rows)} observationer${st.last.deleted ? `, ${fmtN(st.last.deleted)} slettet` : ""}.`
      : st.history_synced_at ? `Historik hentet ${fmtDate(st.history_synced_at)}.` : "Historik er i kø.";
    syncStatus.textContent = st.pending ? `I kø – forventet om ca. ${Math.max(1, Math.round(st.eta / 60))} min. ${last}`
      : `${last}${st.available ? "" : ` Du kan synkronisere igen om ${Math.max(1, Math.round(st.available_in / 60))} min.`}`;
    // Mens jobbet venter, og når knappen bliver ledig igen, opdateres status af sig selv
    if (st.pending) syncTimer = setTimeout(drawSync, 15000);
    else if (!st.available && st.available_in > 0) syncTimer = setTimeout(drawSync, Math.min(st.available_in, 600) * 1000 + 1000);
  };
  drawSync();
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
  const profilePub = h("input", { type: "checkbox", checked: p.profile_public !== false });
  const push = h("input", { type: "checkbox", checked: p.push_enabled });
  // GPS-rute på nye ture: spørg ved oprettelse (standard), start altid, eller aldrig
  const tripGps = seg([["ask", "Spørg"], ["on", "Altid"], ["off", "Aldrig"]], p.trip_gps || "ask", (v) => { p.trip_gps = v; });
  // Udseendet gemmes straks og kun på denne enhed (localStorage)
  const theme = seg([["auto", "Automatisk"], ["light", "Lys"], ["dark", "Mørk"]], localStorage.getItem("theme") || "auto", (v) => { localStorage.setItem("theme", v); applyTheme(); });

  // Baggrundsposition (kun appen): positionen følger én enhed ad gangen; tændes her, overtager denne enhed
  const dev = p.share_location === true ? p.position_device : null;
  const share = h("input", { type: "checkbox", checked: !!dev && isPositionDevice(), disabled: !isApp(), onchange: async () => {
    try {
      if (share.checked) {
        if (dev && !confirm(`Din position følges nu af «${dev.name}». Skift til denne enhed?`)) { share.checked = false; return; }
        const d = await takeOverPosition();
        toast(`Nær mig følger nu ${d.name}`);
      } else {
        await api("/api/me/location", { method: "DELETE" });
        toast("Positionen er slettet fra serveren");
      }
      await refreshUser();
    } catch (err) { share.checked = !share.checked; toast(err.message, "err"); }
  } });
  const devNote = dev && !isPositionDevice() ? `Din position følges lige nu af «${dev.name}». ` : "";
  const positionCard = u.position ? [
    h("h3", { class: "mt" }, "Position og Nær mig"),
    h("label", { class: "check" }, share, "Følg min position med denne enhed (appen)"),
    h("small", { class: "muted", style: { marginTop: "-6px" } }, devNote + (isApp()
      ? "Appen sender din seneste position, når du flytter dig, så feedet og din Nær mig-alarm gælder en radius omkring, hvor du er. Positionen følger én enhed ad gangen – slå den til her for at skifte til denne. Den bruges kun til dine egne filtre og slettes, når du slår delingen fra."
      : "Baggrundsposition kræver appen. I browseren kan du stadig vælge Nær mig i feedet – så bruges din position, mens du vælger."))] : [];

  const phone = h("input", { type: "tel", inputmode: "tel", value: p.phone || "", placeholder: "Fx 20 30 40 50", maxlength: 20, autocomplete: "tel" });
  const save = async () => {
    try {
      await api("/api/me", { method: "PATCH", body: { afdelinger: p.afdelinger, kommuner: p.kommuner, lists_public: pub.checked, ...(state.user.profiler ? { profile_public: profilePub.checked } : {}), push_enabled: push.checked,
        quiet_hours: qs.value && qe.value ? { start: qs.value, end: qe.value } : null, phone: phone.value.trim(), trip_gps: p.trip_gps || "ask" } });
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
        ...positionCard,
        h("h3", { class: "mt" }, "Lister og privatliv"),
        h("label", { class: "check" }, pub, "Vis mig på scoreboards og lad andre se mine lister"),
        state.user.profiler ? h("label", { class: "check" }, profilePub, "Vis min observatørside for andre brugere (tal og fund fra DOFbasen, uden hemmelige observationer)") : null,
        h("h3", { class: "mt" }, "Ture"),
        h("div", {}, h("p", { class: "muted", style: { marginBottom: "6px" } }, "Optag GPS-rute, når du opretter en tur med dags dato. Ruten kan altid startes og stoppes på turen."), tripGps),
        h("h3", { class: "mt" }, "Udseende"), h("p", { class: "muted", style: { marginBottom: "6px" } }, "Gemmes på denne enhed."), theme)),
    h("div", { class: "row mt" }, h("button", { class: "btn primary", onclick: save }, "Gem indstillinger")),
    h("div", { class: "card mt2 row between" }, h("div", {}, h("h3", { style: { margin: 0 } }, "Boligbirding"),
        h("p", { class: "muted", style: { margin: 0 } }, "Matrikler, perioder, flytning og placeringer har deres eget modul.")),
      h("a", { class: "btn", href: "#/matrikel" }, "Gå til Matrikel →")),
    h("div", { class: "card mt2" }, h("h3", {}, "Data"),
      h("p", { class: "muted" }, "Dine observationer kommer fra DOFbasen: historik fra det lokale arkiv, ældre år hentes én gang ved første login, og nye observationer kommer automatisk med live-hentningen. ",
        "«Synkronisér alle mine observationer» henter alle dine observationer fra DOFbasen igen (fra 1900 til i år), retter dem, der er ændret, og fjerner dem, du har slettet i DOFbasen."),
      syncStatus,
      h("div", { class: "row" },
        syncBtn,
        h("a", { class: "btn", href: "/api/me/export", download: "" }, "Hent mine data"),
        h("a", { class: "btn ghost", href: "/privatliv" }, "Privatlivspolitik"),
        h("span", { style: { flex: 1 } }),
        u.role !== "superadmin" ? h("button", { class: "btn ghost danger", onclick: async () => {
          if (!confirm("Slet din konto i portalen? Alt hvad du har lavet her slettes – områder, regler, grupper, feltmeldinger, fotos og kommentarer. Dine data i DOFbasen påvirkes ikke.")) return;
          await api("/api/me", { method: "DELETE" });
          await forgetUser();
          location.hash = "#/"; location.reload();
        } }, "Slet min konto") : null)));
  const pkBox = h("div", { class: "card mt2 stack" });
  view.append(pkBox);
  passkeys(pkBox);
  // API-nøgler til showcase-modulerne: en administrator giver adgang, brugeren laver selv sine nøgler
  for (const [mod, label] of [["dofbasen", "DOFbasen"], ["netfugl", "Netfugl"]]) {
    if (!state.user[mod]?.features?.[`${mod}_api`]) continue;
    const apiBox = h("div", { class: "card mt2 stack" });
    view.append(apiBox);
    apiTokens(apiBox, mod, label);
  }
  if (location.hash.includes("matrikler")) location.hash = "#/matrikel?tab=matrikler";
}

/** Passkeys: log ind med Face ID, fingeraftryk eller skærmlås i stedet for DOFbasen-adgangskoden. */
async function passkeys(box) {
  let d;
  try { d = await api("/api/me/passkeys"); } catch (err) { box.replaceChildren(h("h3", {}, "Passkeys"), h("p", { class: "muted" }, err.message)); return; }
  if (!d.enabled) { box.remove(); return; }
  const add = h("button", { class: "btn primary", type: "button", disabled: !passkey.supported(), onclick: async () => {
    add.disabled = true;
    try { await passkey.register(passkey.deviceName()); toast("Passkey oprettet"); passkeys(box); }
    catch (err) { toast(err.message, "err"); add.disabled = false; }
  } }, "Opret passkey på denne enhed");
  box.replaceChildren(h("h3", { style: { margin: 0 } }, "Passkeys"),
    h("p", { class: "muted", style: { margin: 0 } }, "Med en passkey logger du ind med Face ID, fingeraftryk eller skærmlås – uden DOFbasen-adgangskoden. ",
      "Nøglen bliver på din enhed; portalen gemmer kun den offentlige del. En passkey kan kun oprettes inden for 15 minutter efter et login, ",
      "og hvert halve år skal du logge ind med DOFbasen-adgangskoden én gang, så vi ved, at obserkoden stadig er gyldig."),
    passkey.supported() ? null : h("p", { class: "notice" }, "Denne browser eller app understøtter ikke passkeys."),
    d.passkeys.length ? h("ul", { class: "stack", style: { margin: 0, paddingLeft: "1.1em" } }, ...d.passkeys.map(p => h("li", { class: "row between" },
      h("span", {}, p.name),
      h("button", { class: "btn sm ghost danger", type: "button", onclick: async () => {
        if (!confirm(`Slet passkeyen "${p.name}"? Du kan stadig logge ind med DOFbasen-adgangskoden.`)) return;
        try { await api(`/api/me/passkeys/${p.id}`, { method: "DELETE" }); toast("Passkeyen er slettet"); passkeys(box); }
        catch (err) { toast(err.message, "err"); }
      } }, "Slet")))) : h("p", { style: { margin: 0 } }, "Du har ingen passkeys endnu."),
    h("div", { class: "row" }, add));
}

/** API-nøgler til DOFbasen-API'et: en administrator giver adgang, brugeren laver og sletter selv sine nøgler. */
async function apiTokens(box, mod, label) {
  let d;
  try { d = await api(`/api/${mod}/me/api`); } catch { box.remove(); return; }
  const title = h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, `API-nøgler (${label})`), h("a", { href: `#/${mod}/api` }, "Dokumentation →"));
  if (!d.access) {
    box.replaceChildren(title, h("p", { class: "muted", style: { margin: 0 } }, `Med ${label}-API'et kan du hente data ind i dine egne apps. En administrator skal give dig adgang, før du kan lave nøgler.`));
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
        const t = await api(`/api/${mod}/me/api/tokens`, { method: "POST", body: { name: name.value.trim() || "API-nøgle" } });
        await apiTokens(box, mod, label);
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
          await api(`/api/${mod}/me/api/tokens/${t.id}`, { method: "DELETE" }); toast("Nøglen er slettet"); apiTokens(box, mod, label);
        } }, "Slet"))))))) : null);
  fresh.className = "api-fresh";
}

const st = (v, l) => h("div", { class: "stat" }, h("b", {}, v), h("span", {}, l));
