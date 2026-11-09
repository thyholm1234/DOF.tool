// Forside for ikke-indloggede: kort præsentation + login, oprettelse af DOF.tool-konto og ny adgangskode.
//
// Login: e-mail eller obserkode + DOF.tool-adgangskode (eller passkey).
// Opret konto / glemt adgangskode (server/portal/services/konto.py):
//   1. Bekræft obserkoden med ét DOFbasen-login (adgangskoden sendes kun videre til DOFbasen)
//   2. Vælg e-mail og egen adgangskode – kontoen bindes til obserkoden
//   3. Klar (tilbud om passkey på enheden)
import { api, state, h, icon, params, toast } from "../core.js";
import { refreshUser } from "../app.js";
import { live } from "../live.js";
import * as passkey from "../passkey.js";

const FEATURES = [
  ["now", "Live fra DOFbasen", "Dagens fund samlet i tråde – SU, SUB, bemærkelsesværdige og lokale hits, med alarmer og push."],
  ["plus", "Indtast og meld status", "Indtast med DOFbasens felter, og meld om fuglen stadig er der. Andre kan bekræfte på stedet."],
  ["calendar", "Kalender for trækposter", "Planlæg indsatsen: se hvem der kommer hvornår, trækvejret og hvad der plejer at trække."],
  ["flag", "DOF-ture", "Alle kommende ture og aktiviteter fra DOF, kodet med lokalafdeling og lokalitet."],
  ["list", "Lister og boligbirding", "Årslister, matrikler, kommuner og scoreboards for dig og dine grupper."],
  ["trend", "Træk og fænologi", "Kommer fuglene tidligere eller senere? DMI-vejr og klima koblet til 30 mio. observationer."],
];
const PW_MIN = 10;
const PENDING = /afventer/;

export async function render(view) {
  if (state.user) { location.hash = params().next || "#/"; return; }
  const card = h("div", { class: "card stack landing-login auth-card" });
  view.append(h("div", { class: "landing" },
    h("section", { class: "landing-hero" },
      h("div", {},
        h("p", { class: "landing-kicker" }, "Portal for feltornitologer"),
        h("h1", {}, "Fuglene, fundene og folkene – samlet ét sted"),
        h("p", { class: "lead" }, "DOF.tool samler live-observationer fra DOFbasen, egne indtastninger, kalendere for trækposter, DOF's ture, lister, fænologi og læring. Opret en konto, og bind den til din DOFbasen-obserkode."),
        h("ul", { class: "landing-features" }, ...FEATURES.map(([ic, t, d]) => h("li", {},
          h("span", { class: "lf-icon", html: icon(ic) }), h("div", {}, h("b", {}, t), h("p", {}, d)))))),
      card)));
  // Fælles tilstand mellem trinene
  const flow = { kode: "", bind: null };
  const go = {
    login: (prefill) => showLogin(card, go, flow, prefill),
    verify: (purpose, kode) => showVerify(card, go, flow, purpose, kode),
    create: () => showCreate(card, go, flow),
    done: (r) => showDone(card, r),
  };
  const mode = params().konto;
  if (mode === "opret") go.verify("opret");
  else if (mode === "glemt") go.verify("glemt");
  else go.login();
}

// ---------------------------------------------------------------- små byggeklodser

const field = (label, input, extra) => h("label", { class: "field" }, extra ? h("span", { class: "auth-label" }, h("span", {}, label), extra) : h("span", {}, label), input);
const notice = (text, kind = "err") => h("div", { class: `notice ${kind}` }, text);
const big = (cls, text, attrs = {}) => h("button", { class: `btn ${cls}`, style: { width: "100%", height: "44px" }, ...attrs }, text);

function busy(btn, text) {
  const label = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> ${text}`;
  return () => { btn.disabled = false; btn.textContent = label; };
}

function stepper(active) {
  const steps = ["Bekræft obserkode", "Konto", "Klar"];
  return h("ol", { class: "auth-steps", "aria-label": `Trin ${active} af 3` }, ...steps.map((s, i) =>
    h("li", { class: i + 1 < active ? "done" : i + 1 === active ? "on" : "", "aria-current": i + 1 === active ? "step" : null },
      h("span", { class: "auth-dot", html: i + 1 < active ? icon("check") : String(i + 1) }), h("span", {}, s))));
}

const back = (go, text = "← Tilbage til log ind") => h("button", { type: "button", class: "linkbtn", onclick: () => go.login() }, text);

async function finish(r, go) {  // r.flow: kom fra oprettelsen (trin 3 vises med trinviseren)
  await refreshUser();
  live.restart();
  if (r.passkeys && passkey.supported()) { go.done(r); return; }
  toast(r.new ? `Velkommen, ${r.user.navn}! Vi henter dine ældre observationer i baggrunden.` : `Velkommen, ${r.user.navn}`);
  location.hash = params().next || (r.new ? "#/profil?fane=omraader" : "#/");
}

// ---------------------------------------------------------------- log ind

function showLogin(card, go, flow, prefill) {
  const ident = h("input", { autocomplete: "username webauthn", placeholder: "navn@eksempel.dk eller 8000ABC", required: true,
    value: prefill ?? passkey.lastLogin(), maxlength: 254, autocapitalize: "none", spellcheck: "false" });
  const pw = h("input", { type: "password", autocomplete: "current-password", required: true, maxlength: 200 });
  const btn = big("primary", "Log ind");
  const msg = h("div", {});
  const identValue = () => { const v = ident.value.trim(); return v.includes("@") ? v : v.toUpperCase(); };
  const handle = (err) => {
    if (err.status === 409) {  // obserkoden findes, men har ingen konto endnu
      const kode = identValue().includes("@") ? "" : identValue();
      msg.replaceChildren(h("div", { class: "notice warn stack", style: { gap: "8px" } },
        h("span", {}, err.message),
        h("button", { type: "button", class: "btn primary sm", onclick: () => go.verify("opret", kode) }, kode ? `Opret konto for ${kode}` : "Opret konto")));
    } else if (err.status === 403 && PENDING.test(err.message)) {
      msg.replaceChildren(notice([h("b", {}, "Din konto afventer godkendelse. "), err.message.replace(/^[^.]*\.\s*/, "")], ""));
    } else msg.replaceChildren(notice(err.message));
  };
  const pkBtn = big("", "Log ind med passkey", { type: "button", style: { width: "100%", height: "44px", display: "none" }, onclick: async () => {
    if (!ident.value.trim()) { msg.replaceChildren(notice("Skriv din e-mail eller obserkode først.")); ident.focus(); return; }
    pkBtn.disabled = true;
    try { await finish(await passkey.login(identValue()), go); } catch (err) { handle(err); } finally { pkBtn.disabled = false; }
  } });
  const or = h("div", { class: "auth-or", style: { display: "none" } }, h("span", {}, "eller"));
  if (passkey.supported()) api("/api/settings/public").then(s => { if (s?.passkeys) { pkBtn.style.display = ""; or.style.display = ""; } }).catch(() => {});

  const form = h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    const done = busy(btn, "Logger ind…");
    try {
      const r = await api("/api/auth/login", { method: "POST", body: { login: identValue(), password: pw.value } });
      passkey.rememberLogin(identValue());
      await finish(r, go);
    } catch (err) { handle(err); pw.select(); } finally { done(); }
  } },
    field("E-mail eller obserkode", ident),
    field("Adgangskode", pw, h("button", { type: "button", class: "linkbtn", onclick: () => go.verify("glemt", identValue().includes("@") ? "" : identValue()) }, "Glemt adgangskode?")),
    btn);

  card.replaceChildren(
    h("h2", { style: { margin: 0 } }, "Log ind"),
    form, or, pkBtn, msg,
    h("div", { class: "auth-new stack" },
      h("div", {}, h("b", {}, "Ny i DOF.tool – eller har du hidtil logget ind med obserkoden?"),
        h("p", { class: "muted", style: { margin: "2px 0 0" } }, "Opret en konto med e-mail og egen adgangskode. Den bindes til din obserkode, og alt, du har lavet i DOF.tool, følger med.")),
      big("", "Opret konto", { type: "button", onclick: () => go.verify("opret", identValue().includes("@") ? "" : identValue()) })),
    h("p", { class: "muted", style: { fontSize: ".82rem", margin: 0 } }, "Portalen er kun for indloggede brugere. ", h("a", { href: "/privatliv" }, "Privatlivspolitik")));
  (ident.value ? pw : ident).focus();
}

// ---------------------------------------------------------------- trin 1: bekræft obserkoden hos DOFbasen

function showVerify(card, go, flow, purpose, kode) {
  const reset = purpose === "glemt";
  const obs = h("input", { autocomplete: "off", placeholder: "Fx 8000ABC", required: true, maxlength: 16, value: kode || flow.kode || "",
    style: { textTransform: "uppercase" }, autocapitalize: "characters", spellcheck: "false" });
  const pw = h("input", { type: "password", autocomplete: "off", required: true, maxlength: 200 });
  const btn = big("primary", "Bekræft hos DOFbasen");
  const msg = h("div", {});
  card.replaceChildren(
    stepper(1),
    h("h2", { style: { margin: 0 } }, reset ? "Glemt adgangskode" : "Opret konto"),
    h("p", { class: "muted", style: { margin: 0 } }, reset
      ? "Bekræft, at obserkoden er din, med din DOFbasen-adgangskode – så kan du vælge en ny adgangskode til DOF.tool."
      : "Først bekræfter vi én gang hos DOFbasen, at obserkoden er din. Derefter vælger du e-mail og adgangskode til DOF.tool."),
    h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      const done = busy(btn, "Spørger DOFbasen…");
      const slow = setTimeout(() => msg.replaceChildren(notice("Adgangskoden tjekkes hos DOFbasen – det tager normalt kun et øjeblik.", "")), 2500);
      try {
        flow.kode = obs.value.trim().toUpperCase();
        flow.bind = await api("/api/auth/konto/bekraeft", { method: "POST", body: { obserkode: flow.kode, password: pw.value } });
        flow.bind.at = Date.now();
        pw.value = "";
        go.create();
      } catch (err) { msg.replaceChildren(notice(err.message)); pw.select(); } finally { clearTimeout(slow); done(); }
    } },
      field("Obserkode", obs),
      field("DOFbasen-adgangskode", pw),
      btn),
    msg,
    h("div", { class: "auth-why" }, h("span", { html: icon("shield") }),
      h("p", {}, "DOFbasen-adgangskoden sendes kun videre til DOFbasen og gemmes ikke i DOF.tool – heller ikke krypteret. Du skal bruge den her og aldrig igen.")),
    back(go));
  (obs.value ? pw : obs).focus();
}

// ---------------------------------------------------------------- trin 2: e-mail og adgangskode

function showCreate(card, go, flow) {
  const b = flow.bind;
  const reset = b.has_account;
  const navn = h("input", { autocomplete: "name", maxlength: 160, value: b.navn || "", required: true });
  const email = h("input", { type: "email", autocomplete: "email", maxlength: 254, required: !reset, placeholder: reset ? b.email || "" : "navn@eksempel.dk" });
  const pw = h("input", { type: "password", autocomplete: "new-password", required: true, minlength: PW_MIN, maxlength: 200 });
  const pw2 = h("input", { type: "password", autocomplete: "new-password", required: true, maxlength: 200 });
  const user = h("input", { type: "hidden", autocomplete: "username", value: b.obserkode });  // til browserens adgangskodehusker
  const btn = big("primary", reset ? "Gem ny adgangskode" : "Opret konto");
  const msg = h("div", {});
  const rules = [
    [`Mindst ${PW_MIN} tegn`, () => pw.value.length >= PW_MIN],
    ["Ikke din obserkode eller e-mail", () => pw.value && ![b.obserkode.toLowerCase(), email.value.trim().toLowerCase(), email.value.trim().toLowerCase().split("@")[0]].includes(pw.value.toLowerCase())],
    ["De to adgangskoder er ens", () => pw.value && pw.value === pw2.value],
  ];
  const list = h("ul", { class: "auth-rules" });
  const check = () => list.replaceChildren(...rules.map(([t, f]) => { const ok = !!f(); return h("li", { class: ok ? "ok" : "" }, h("span", { class: "auth-tick", html: ok ? icon("check") : "" }), t); }));
  for (const i of [pw, pw2, email]) i.addEventListener("input", check);
  check();
  const left = () => Math.max(0, Math.round((b.expires_in * 1000 - (Date.now() - b.at)) / 60000));

  card.replaceChildren(
    stepper(2),
    h("h2", { style: { margin: 0 } }, reset ? "Vælg ny adgangskode" : "Din DOF.tool-konto"),
    h("div", { class: "auth-bound" }, h("span", { class: "auth-dot done", html: icon("check") }),
      h("div", {}, h("b", {}, b.obserkode), b.navn ? ` · ${b.navn}` : "",
        h("div", { class: "muted", style: { fontSize: ".82rem" } }, "Bekræftet hos DOFbasen – kontoen bindes til denne obserkode"))),
    reset ? h("p", { class: "muted", style: { margin: 0 } }, "Obserkoden har allerede en konto", b.email ? ` (${b.email})` : "",
      ". Når du gemmer, logges alle dine enheder ud.") : null,
    h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      const bad = rules.find(([, f]) => !f());
      if (bad) { msg.replaceChildren(notice(bad[0] === rules[2][0] ? "De to adgangskoder er ikke ens." : `Adgangskoden opfylder ikke: ${bad[0].toLowerCase()}.`)); return; }
      const done = busy(btn, reset ? "Gemmer…" : "Opretter…");
      try {
        const body = { token: b.token, password: pw.value };
        if (!reset) { body.email = email.value.trim(); body.navn = navn.value.trim(); }
        else if (email.value.trim()) body.email = email.value.trim();
        const r = await api("/api/auth/konto/opret", { method: "POST", body });
        passkey.rememberLogin(r.user.email || r.user.obserkode);
        flow.bind = null;
        await finish({ ...r, reset, flow: true }, go);
      } catch (err) {
        if (err.status === 403 && PENDING.test(err.message)) { showDone(card, null, err.message); return; }
        if (/udløbet/.test(err.message)) {
          msg.replaceChildren(h("div", { class: "notice err stack", style: { gap: "8px" } }, h("span", {}, err.message),
            h("button", { type: "button", class: "btn sm", onclick: () => go.verify(reset ? "glemt" : "opret", b.obserkode) }, "Bekræft igen")));
        } else msg.replaceChildren(notice(err.message));
      } finally { done(); }
    } },
      user,
      reset ? null : field("Navn", navn),
      reset ? null : field("E-mail", email),
      reset ? null : h("small", { class: "muted", style: { marginTop: "-6px" } }, "Du kan logge ind med e-mailen eller obserkoden. DOF.tool sender ikke e-mails."),
      field(reset ? "Ny adgangskode" : "Adgangskode til DOF.tool", pw),
      field("Gentag adgangskoden", pw2),
      list,
      h("small", { class: "muted" }, "Brug gerne en anden adgangskode end på DOFbasen."),
      btn),
    msg,
    h("p", { class: "muted", style: { fontSize: ".8rem", margin: 0 } }, `Bekræftelsen gælder i ${left()} minutter mere.`),
    back(go, "Annullér"));
  (reset ? pw : email).focus();
}

// ---------------------------------------------------------------- trin 3: klar

function showDone(card, r, pendingMsg) {
  const next = () => { location.hash = params().next || (r?.new ? "#/profil?fane=omraader" : "#/"); };
  if (pendingMsg) {
    card.replaceChildren(stepper(3),
      h("div", { class: "auth-hello" }, h("span", { class: "auth-dot done big", html: icon("check") }),
        h("h2", { style: { margin: 0 } }, "Kontoen er oprettet")),
      notice([h("b", {}, "Den afventer godkendelse. "), pendingMsg.replace(/^[^.]*\.\s*/, "")], ""),
      h("p", { class: "muted", style: { margin: 0 } }, "Når en administrator har godkendt den, logger du ind med din e-mail eller obserkode og den adgangskode, du lige har valgt."),
      big("", "Til log ind", { type: "button", onclick: () => { location.hash = "#/login"; location.reload(); } }));
    return;
  }
  const pkBtn = big("primary", "Opret passkey", { type: "button", onclick: async () => {
    const done = busy(pkBtn, "Venter på enheden…");
    try { await passkey.register(passkey.deviceName()); toast("Passkey oprettet"); next(); }
    catch (err) { done(); toast(`${err.message} Du kan prøve igen under Profil.`, "err"); }
  } });
  const title = r.reset ? "Ny adgangskode gemt" : r.flow ? `Velkommen, ${r.user.navn}!` : `Velkommen tilbage, ${r.user.navn}`;
  const text = r.reset ? "Alle dine andre enheder er logget ud." : r.flow ? `Din konto er bundet til obserkoden ${r.user.obserkode}.` : "";
  card.replaceChildren(r.flow ? stepper(3) : null,
    h("div", { class: "auth-hello" }, h("span", { class: "auth-dot done big", html: icon("check") }),
      h("h2", { style: { margin: 0 } }, title)),
    text || r.new ? h("p", { style: { margin: 0 } }, text, r.new ? " Vi henter dine ældre observationer i baggrunden." : "") : null,
    h("div", { class: "auth-why" }, h("span", { html: icon("user") }),
      h("p", {}, h("b", {}, "Log ind hurtigere næste gang. "), "Med en passkey bruger du Face ID, fingeraftryk eller skærmlås i stedet for adgangskoden. Nøglen bliver på denne enhed.")),
    pkBtn, big("ghost", "Ikke nu", { type: "button", onclick: next }));
}
