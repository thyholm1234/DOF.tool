// Profil og indstillinger – opdelt som på andre sider: en menu med faner til venstre (vandret øverst på mobilen) og ét
// emne pr. fane. Hver fane gemmer sine egne felter (PATCH /api/me tager delvise ændringer). Fanen står i adressen
// (#/profil?fane=sikkerhed), så der kan linkes direkte til den.
import { api, state, h, icon, fmtN, fmtDate, loadRef, requireLogin, toast, seg, params, setParams } from "../core.js";
import { refreshUser, applyTheme } from "../app.js";
import { enablePush } from "./alerts.js";
import { isApp, isPositionDevice, takeOverPosition } from "../native.js";
import { forgetUser } from "../offline.js";
import * as passkey from "../passkey.js";

const ROLE = { user: "Bruger", moderator: "Moderator", editor: "Redaktør", admin: "Administrator", superadmin: "Superadmin" };

export async function render(view) {
  if (requireLogin(view)) return;
  if (location.hash.includes("matrikler")) { location.hash = "#/matrikel?tab=matrikler"; return; }
  await loadRef();
  const u = state.user;
  const viewAs = !!u.view_as;
  const apiMods = [["dofbasen", "DOFbasen"], ["netfugl", "Netfugl"]].filter(([m]) => u[m]?.features?.[`${m}_api`]);
  const TABS = [
    ["oversigt", "Oversigt", "user"],
    ["personligt", "Personlige oplysninger", "edit"],
    ...(viewAs ? [] : [["sikkerhed", "Konto og sikkerhed", "lock"]]),
    ["notifikationer", "Notifikationer", "bell"],
    ["omraader", "Områder og lister", "pin"],
    ["privatliv", "Privatliv og position", "shield"],
    ["praeferencer", "Præferencer", "sun"],
    ...(apiMods.length ? [["api", "API-nøgler", "db"]] : []),
    ["data", "Dine data", "download"],
  ];
  let tab = TABS.some(t => t[0] === params().fane) ? params().fane : "oversigt";
  const panel = h("section", { class: "settings-panel" });
  const nav = h("nav", { class: "settings-nav", "aria-label": "Profil" });
  const show = (key) => {
    tab = key;
    setParams({ fane: key === "oversigt" ? "" : key });
    nav.querySelectorAll("button").forEach(b => { const on = b.dataset.tab === key; b.classList.toggle("on", on); if (on) { b.setAttribute("aria-current", "page"); b.scrollIntoView({ block: "nearest", inline: "nearest" }); } else b.removeAttribute("aria-current"); });
    panel.replaceChildren();
    const T = TABS.find(t => t[0] === key);
    panel.append(h("h2", { class: "settings-title" }, T[1]));
    SECTIONS[key](panel, { u, apiMods, go: show });
  };
  nav.append(...TABS.map(([key, label, ic]) => h("button", { type: "button", "data-tab": key, onclick: () => show(key), html: `${icon(ic)}<span>${label}</span>` })));

  const initials = (u.navn || u.obserkode).split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join("").toUpperCase();
  view.append(
    h("div", { class: "profile-head" },
      h("span", { class: "profile-avatar", "aria-hidden": "true" }, initials),
      h("div", { style: { minWidth: 0 } }, h("h1", {}, u.navn || u.obserkode),
        h("p", { class: "lead" }, [u.obserkode, u.email, u.role !== "user" ? ROLE[u.role] || u.role : null].filter(Boolean).join(" · ")))),
    h("div", { class: "settings" }, nav, panel));
  show(tab);
}

// ---------------------------------------------------------------- fælles

const st = (v, l) => h("div", { class: "stat" }, h("b", {}, v), h("span", {}, l));
const card = (title, desc, ...body) => h("div", { class: "card stack settings-card" },
  title ? h("h3", { style: { margin: 0 } }, title) : null, desc ? h("p", { class: "muted", style: { margin: 0 } }, desc) : null, ...body);
const saveRow = (fn, label = "Gem") => {
  const b = h("button", { class: "btn primary", type: "button", onclick: async () => {
    b.disabled = true;
    try { await fn(); await refreshUser(); toast("Gemt"); } catch (err) { toast(err.message, "err"); } finally { b.disabled = false; }
  } }, label);
  return h("div", { class: "row" }, b);
};
const patch = (body) => api("/api/me", { method: "PATCH", body });
const prefs = () => ({ lists_public: true, push_enabled: true, afdelinger: [], kommuner: [], ...state.user.prefs });
const pwField = (label, ac) => { const i = h("input", { type: "password", autocomplete: ac, maxlength: 200 }); return [i, h("label", { class: "field" }, h("span", {}, label), i)]; };
const row = (label, value, action) => h("div", { class: "settings-row" }, h("div", { class: "settings-row-label" }, label), h("div", { class: "settings-row-value" }, value), action || h("span", {}));

const SECTIONS = {
  // ---------------------------------------------------------------- oversigt
  async oversigt(panel, { u, go }) {
    const stats = h("div", { class: "stats" });
    const links = [
      ["personligt", "edit", "Personlige oplysninger", "Navn, e-mail og telefon"],
      ...(u.view_as ? [] : [["sikkerhed", "lock", "Konto og sikkerhed", "Adgangskode, passkeys og enheder"]]),
      ["notifikationer", "bell", "Notifikationer", "Push og stille timer"],
      ["omraader", "pin", "Områder og lister", "Lokalafdelinger og kommuner"],
      ["data", "download", "Dine data", "Synkronisering, udtræk og sletning"],
    ];
    panel.append(stats,
      h("div", { class: "settings-tiles" }, ...links.map(([k, ic, t, d]) => h("button", { type: "button", class: "settings-tile", onclick: () => go(k) },
        h("span", { class: "lf-icon", html: icon(ic) }), h("span", {}, h("b", {}, t), h("small", {}, d))))),
      card("Boligbirding", "Matrikler, perioder, flytning og placeringer har deres eget modul.", h("div", { class: "row" }, h("a", { class: "btn", href: "#/matrikel" }, "Gå til Matrikel →"))));
    const s = await api("/api/me/stats").catch(() => null);
    if (s) stats.replaceChildren(st(fmtN(s.n_obs), "observationer"), st(fmtN(s.n_trips), "ture"), st(fmtN(s.n_taxa), "taxa"), st(s.first ? new Date(s.first).getFullYear() : "–", "første år"));
    else stats.remove();
  },

  // ---------------------------------------------------------------- personlige oplysninger
  personligt(panel, { u, go }) {
    const p = prefs();
    const navn = h("input", { autocomplete: "name", maxlength: 160, value: u.navn || "" });
    const phone = h("input", { type: "tel", inputmode: "tel", value: p.phone || "", placeholder: "Fx 20 30 40 50", maxlength: 20, autocomplete: "tel" });
    panel.append(
      card("Profil", "Navnet vises på lister, scoreboards, kommentarer og kalenderbesøg.",
        h("label", { class: "field" }, h("span", {}, "Navn"), navn),
        saveRow(() => patch({ navn: navn.value.trim() }))),
      card("Kontaktoplysninger", null,
        u.view_as ? null : row("E-mail", h("span", {}, h("b", {}, u.email || "–"), u.email && !u.email_verified ? h("span", { class: "tag bemaerk", style: { marginLeft: "6px" } }, "ikke bekræftet") : ""), h("button", { type: "button", class: "btn sm ghost", onclick: () => go("sikkerhed") }, "Skift")),
        h("label", { class: "field" }, h("span", {}, "Telefonnummer"), phone),
        h("small", { class: "muted", style: { marginTop: "-6px" } }, "Vises kun på de kalenderbesøg, hvor du selv vælger det – så andre på trækposten kan ringe, fx når der er noget at se."),
        saveRow(() => patch({ phone: phone.value.trim() }))),
      card("DOFbasen", null,
        row("Obserkode", h("span", {}, h("b", {}, u.obserkode), h("span", { class: "tag plain", style: { marginLeft: "6px" }, title: "Bekræftet hos DOFbasen, da kontoen blev oprettet" }, "bundet"))),
        h("p", { class: "muted", style: { margin: 0 } }, "Kontoen er bundet til obserkoden. Dine observationer kommer fra DOFbasen og rettes dér.")));
  },

  // ---------------------------------------------------------------- konto og sikkerhed
  sikkerhed(panel, { u }) {
    const msg = h("div", {});
    const show = (text, ok) => msg.replaceChildren(h("div", { class: `notice${ok ? "" : " err"}` }, text));
    const [cur, curL] = pwField("Nuværende adgangskode", "current-password");
    const [nw, nwL] = pwField("Ny adgangskode (mindst 10 tegn)", "new-password");
    const [nw2, nw2L] = pwField("Gentag ny adgangskode", "new-password");
    const pwForm = h("form", { class: "stack hide", onsubmit: async (e) => {
      e.preventDefault();
      if (nw.value !== nw2.value) return show("De to nye adgangskoder er ikke ens.");
      try {
        await api("/api/me/password", { method: "POST", body: { current: cur.value, new: nw.value } });
        pwForm.reset(); pwForm.classList.add("hide");
        show("Adgangskoden er skiftet. Dine andre enheder er logget ud.", true);
      } catch (err) { show(err.message); }
    } }, h("input", { type: "hidden", autocomplete: "username", value: u.email || u.obserkode }), curL, nwL, nw2L,
      h("div", { class: "row" }, h("button", { class: "btn primary" }, "Skift adgangskode"),
        h("button", { type: "button", class: "btn ghost", onclick: () => pwForm.classList.add("hide") }, "Annullér")));

    // E-mail: ny adresse + adgangskode → kode sendes til adressen → koden tastes (eller linket i mailen åbnes:
    // #/profil?fane=sikkerhed&mailkode=…). Adressen gemmes først, når koden er godkendt.
    const mail = h("input", { type: "email", autocomplete: "email", maxlength: 254, value: u.email || "" });
    const [mcur, mcurL] = pwField("Din adgangskode", "current-password");
    const unverified = () => h("span", { class: "tag bemaerk", style: { marginLeft: "6px" }, title: "Bekræft adressen med en kode, vi sender til den" }, "ikke bekræftet");
    const mailVal = h("span", {});
    const showMail = () => mailVal.replaceChildren(h("b", {}, state.user.email || "–"), state.user.email && !state.user.email_verified ? unverified() : "");
    showMail();
    const code = h("input", { inputmode: "numeric", autocomplete: "one-time-code", maxlength: 7, placeholder: "123456",
      value: params().mailkode || "", style: { letterSpacing: ".25em", maxWidth: "12em" } });
    const codeText = h("p", { style: { margin: 0 } });
    const codeForm = h("form", { class: "stack hide", onsubmit: async (e) => {
      e.preventDefault();
      try {
        const r = await api("/api/me/email/bekraeft", { method: "POST", body: { code: code.value } });
        Object.assign(state.user, { email: r.user.email, email_verified: r.user.email_verified });
        showMail(); code.value = ""; codeForm.classList.add("hide");
        if (params().mailkode) setParams({ fane: "sikkerhed" });
        show("E-mailadressen er bekræftet.", true);
      } catch (err) { show(err.message); code.select(); }
    } }, codeText, h("label", { class: "field" }, h("span", {}, "Kode fra mailen"), code),
      h("div", { class: "row" }, h("button", { class: "btn primary" }, "Bekræft"),
        h("button", { type: "button", class: "btn ghost", onclick: () => codeForm.classList.add("hide") }, "Annullér")));
    const mailForm = h("form", { class: "stack hide", onsubmit: async (e) => {
      e.preventDefault();
      try {
        const r = await api("/api/me/email", { method: "POST", body: { current: mcur.value, email: mail.value.trim() } });
        mcur.value = ""; mailForm.classList.add("hide"); msg.replaceChildren();
        codeText.replaceChildren("Vi har sendt en kode med seks cifre til ", h("b", {}, r.email),
          `. Skriv den her, eller tryk på linket i mailen. Koden gælder i ${Math.round(r.expires_in / 60)} minutter – indtil da er din e-mail uændret.`);
        codeForm.classList.remove("hide"); code.value = ""; code.focus();
      } catch (err) { show(err.message); }
    } }, h("label", { class: "field" }, h("span", {}, "E-mailadresse"), mail),
      h("small", { class: "muted", style: { marginTop: "-6px" } }, "Vi sender en kode til adressen. Den gemmes først, når du har bekræftet den."),
      mcurL,
      h("div", { class: "row" }, h("button", { class: "btn primary" }, "Send kode"),
        h("button", { type: "button", class: "btn ghost", onclick: () => mailForm.classList.add("hide") }, "Annullér")));
    if (params().mailkode) { codeText.textContent = "Bekræfter koden fra mailen …"; codeForm.classList.remove("hide"); setTimeout(() => codeForm.requestSubmit(), 0); }

    const pkBox = h("div", { class: "card stack settings-card" });
    panel.append(
      card("Log ind", "Du logger ind med e-mail eller obserkode og din DOF.tool-adgangskode. Har du glemt den, kan du vælge en ny fra login-siden ved at bekræfte obserkoden hos DOFbasen igen.",
        row("E-mail", mailVal, h("button", { type: "button", class: "btn sm ghost", onclick: () => { pwForm.classList.add("hide"); codeForm.classList.add("hide"); mailForm.classList.toggle("hide"); mail.focus(); } }, state.user.email && !state.user.email_verified ? "Bekræft / skift" : "Skift")),
        mailForm, codeForm,
        row("Adgangskode", h("span", { class: "muted" }, "••••••••••"), h("button", { type: "button", class: "btn sm ghost", onclick: () => { mailForm.classList.add("hide"); pwForm.classList.toggle("hide"); cur.focus(); } }, "Skift")),
        pwForm, msg),
      pkBox,
      card("Enheder", "Er du logget ind på en telefon eller computer, du ikke længere har, kan du logge ud alle andre steder. Telefonens positionsdeling stopper også.",
        h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: async () => {
          if (!confirm("Log ud på alle andre enheder? Du forbliver logget ind her.")) return;
          try { await api("/api/me/logout-all", { method: "POST" }); toast("Du er logget ud på alle andre enheder"); } catch (err) { toast(err.message, "err"); }
        } }, "Log ud på alle andre enheder"))));
    passkeys(pkBox);
  },

  // ---------------------------------------------------------------- notifikationer
  notifikationer(panel) {
    const p = prefs();
    const push = h("input", { type: "checkbox", checked: p.push_enabled });
    const qs = h("input", { type: "time", value: p.quiet_hours?.start || "" });
    const qe = h("input", { type: "time", value: p.quiet_hours?.end || "" });
    panel.append(
      card("Push", "Hvad du får besked om, styres af dine alarmer.",
        h("label", { class: "check" }, push, "Send push til mine enheder"),
        h("div", { class: "row" },
          h("button", { type: "button", class: "btn", onclick: async () => { try { await enablePush(); toast("Push slået til på denne enhed"); } catch (err) { toast(err.message, "err"); } } }, "Slå push til på denne enhed"),
          h("a", { class: "btn ghost", href: "#/alarmer" }, "Indstil alarmer →")),
        saveRow(() => patch({ push_enabled: push.checked }))),
      card("Stille timer", "Ingen push i tidsrummet – notifikationerne ligger stadig i indbakken.",
        h("div", { class: "row" }, qs, "–", qe),
        saveRow(() => patch({ quiet_hours: qs.value && qe.value ? { start: qs.value, end: qe.value } : null }))));
  },

  // ---------------------------------------------------------------- områder og lister
  omraader(panel) {
    const p = prefs();
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
    panel.append(
      card("Lokalafdelinger", "Op til 3 – bruges i feed, scoreboards og som standard i quizzer.", afd),
      card("Kommuner", "Op til 3 – giver kommunelister og -scoreboards.", komSel, komChips),
      saveRow(() => patch({ afdelinger: p.afdelinger, kommuner: p.kommuner }), "Gem områder"),
      card("Egne områder og matrikler", null,
        h("p", { class: "muted", style: { margin: 0 } }, "Egne polygoner og radier oprettes under ", h("a", { href: "#/alarmer?tab=omraader" }, "Alarmer → Områder"),
          ". Matrikler, perioder og placeringer findes under ", h("a", { href: "#/matrikel" }, "Matrikel"), ".")));
  },

  // ---------------------------------------------------------------- privatliv og position
  privatliv(panel, { u }) {
    const p = prefs();
    const pub = h("input", { type: "checkbox", checked: p.lists_public });
    const profilePub = h("input", { type: "checkbox", checked: p.profile_public !== false });
    panel.append(card("Synlighed", "Hvad andre brugere kan se om dig.",
      h("label", { class: "check" }, pub, "Vis mig på scoreboards og lad andre se mine lister"),
      u.profiler ? h("label", { class: "check" }, profilePub, "Vis min observatørside for andre brugere (tal og fund fra DOFbasen, uden hemmelige observationer)") : null,
      saveRow(() => patch({ lists_public: pub.checked, ...(u.profiler ? { profile_public: profilePub.checked } : {}) }))));
    if (u.position) {
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
      panel.append(card("Position og Nær mig", null,
        h("label", { class: "check" }, share, "Følg min position med denne enhed (appen)"),
        h("small", { class: "muted" }, devNote + (isApp()
          ? "Appen sender din seneste position, når du flytter dig, så feedet og din Nær mig-alarm gælder en radius omkring, hvor du er. Positionen følger én enhed ad gangen – slå den til her for at skifte til denne. Den bruges kun til dine egne filtre og slettes, når du slår delingen fra."
          : "Baggrundsposition kræver appen. I browseren kan du stadig vælge Nær mig i feedet – så bruges din position, mens du vælger."))));
    }
    panel.append(card(null, null, h("p", { class: "muted", style: { margin: 0 } }, "Sådan behandler vi dine oplysninger: ", h("a", { href: "/privatliv" }, "Privatlivspolitik"), ".")));
  },

  // ---------------------------------------------------------------- præferencer
  praeferencer(panel) {
    const p = prefs();
    let gps = p.trip_gps || "ask";
    const theme = seg([["auto", "Automatisk"], ["light", "Lys"], ["dark", "Mørk"]], localStorage.getItem("theme") || "auto", (v) => { localStorage.setItem("theme", v); applyTheme(); });
    panel.append(
      card("Udseende", "Gemmes straks og kun på denne enhed.", theme),
      card("Ture", "Optag GPS-rute, når du opretter en tur med dags dato. Ruten kan altid startes og stoppes på turen.",
        seg([["ask", "Spørg"], ["on", "Altid"], ["off", "Aldrig"]], gps, (v) => { gps = v; }),
        saveRow(() => patch({ trip_gps: gps }))));
  },

  // ---------------------------------------------------------------- API-nøgler
  api(panel, { apiMods }) {
    for (const [mod, label] of apiMods) {
      const box = h("div", { class: "card stack settings-card" });
      panel.append(box);
      apiTokens(box, mod, label);
    }
  },

  // ---------------------------------------------------------------- dine data
  data(panel, { u }) {
    // Synkronisér alle mine observationer: ét kald i gatewayens kø; status opdateres, mens jobbet venter
    const syncStatus = h("p", { class: "small", style: { margin: 0 } });
    const syncBtn = h("button", { class: "btn", onclick: async () => {
      try { await api("/api/me/sync", { method: "POST" }); toast("Synkroniseringen er lagt i kø"); drawSync(); } catch (err) { toast(err.message, "err"); }
    } }, "Synkronisér alle mine observationer");
    let syncTimer = null;
    const drawSync = async () => {
      clearTimeout(syncTimer);
      let s;
      try { s = await api("/api/me/sync"); } catch { return; }
      if (!syncStatus.isConnected && syncTimer !== null) return;  // fanen eller siden er forladt
      syncBtn.disabled = s.pending || !s.available;
      const last = s.last ? `Sidst synkroniseret ${fmtDate(s.last.at)}: ${fmtN(s.last.rows)} observationer${s.last.deleted ? `, ${fmtN(s.last.deleted)} slettet` : ""}.`
        : s.history_synced_at ? `Historik hentet ${fmtDate(s.history_synced_at)}.` : "Historik er i kø.";
      syncStatus.textContent = s.pending ? `I kø – forventet om ca. ${Math.max(1, Math.round(s.eta / 60))} min. ${last}`
        : `${last}${s.available ? "" : ` Du kan synkronisere igen om ${Math.max(1, Math.round(s.available_in / 60))} min.`}`;
      if (s.pending) syncTimer = setTimeout(drawSync, 15000);
      else if (!s.available && s.available_in > 0) syncTimer = setTimeout(drawSync, Math.min(s.available_in, 600) * 1000 + 1000);
    };
    drawSync();
    panel.append(
      card("Observationer fra DOFbasen", "Historik kommer fra det lokale arkiv, ældre år hentes én gang ved første login, og nye observationer kommer automatisk med live-hentningen. Synkroniseringen henter alle dine observationer fra DOFbasen igen (fra 1900 til i år), retter dem, der er ændret, og fjerner dem, du har slettet i DOFbasen.",
        syncStatus, h("div", { class: "row" }, syncBtn)),
      card("Hent dine data", "Alt, hvad du har lavet i DOF.tool, som én JSON-fil.",
        h("div", { class: "row" }, h("a", { class: "btn", href: "/api/me/export", download: "" }, "Hent mine data"), h("a", { class: "btn ghost", href: "/privatliv" }, "Privatlivspolitik"))),
      u.role !== "superadmin" && !u.view_as ? h("div", { class: "card stack settings-card danger-zone" },
        h("h3", { style: { margin: 0 } }, "Slet konto"),
        h("p", { class: "muted", style: { margin: 0 } }, "Alt, hvad du har lavet i DOF.tool, slettes – områder, regler, grupper, feltmeldinger, fotos og kommentarer. Dine data i DOFbasen påvirkes ikke."),
        h("div", { class: "row" }, h("button", { class: "btn danger solid", onclick: async () => {
          if (!confirm("Slet din konto i DOF.tool? Det kan ikke fortrydes.")) return;
          await api("/api/me", { method: "DELETE" });
          await forgetUser();
          location.hash = "#/"; location.reload();
        } }, "Slet min konto"))) : null);
  },
};

/** Passkeys: log ind med Face ID, fingeraftryk eller skærmlås i stedet for adgangskoden. */
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
    h("p", { class: "muted", style: { margin: 0 } }, "Med en passkey logger du ind med Face ID, fingeraftryk eller skærmlås – uden adgangskode. ",
      "Nøglen bliver på din enhed; portalen gemmer kun den offentlige del. En passkey kan kun oprettes inden for 15 minutter efter et login."),
    passkey.supported() ? null : h("p", { class: "notice" }, "Denne browser eller app understøtter ikke passkeys."),
    d.passkeys.length ? h("ul", { class: "stack", style: { margin: 0, paddingLeft: "1.1em" } }, ...d.passkeys.map(p => h("li", { class: "row between" },
      h("span", {}, p.name),
      h("button", { class: "btn sm ghost danger", type: "button", onclick: async () => {
        if (!confirm(`Slet passkeyen "${p.name}"? Du kan stadig logge ind med din adgangskode.`)) return;
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

