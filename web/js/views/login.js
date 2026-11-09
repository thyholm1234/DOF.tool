// Forside for ikke-indloggede: kort præsentation + login, oprettelse af DOF.tool-konto og ny adgangskode.
//
// Login: e-mail eller obserkode + DOF.tool-adgangskode (eller passkey). Med totrinslogin følger en kode fra
// authenticator-appen (server/portal/services/totp.py) – også efter «Glemt adgangskode».
// Opret konto / glemt adgangskode (server/portal/services/konto.py):
//   1. Bekræft obserkoden med ét DOFbasen-login (adgangskoden sendes kun videre til DOFbasen)
//   2. Vælg e-mail og egen adgangskode – kontoen bindes til obserkoden
//   3. Bekræft e-mailen med koden, der sendes til den (eller linket i mailen: #/login?bekraeft=…&kode=…)
//   4. Klar (tilbud om passkey på enheden)
// Uden DOFbasen: navn, e-mail og adgangskode → koden på mail → kontoen med en lokal kode (kan bindes senere under Profil).
// Glemt adgangskode: en kode til den bekræftede e-mail (#/login?nulstil=…&kode=…) – eller DOFbasen-vejen ovenfor.
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
  const link = params();
  if (state.user && !link.bekraeft && !link.nulstil) { location.hash = link.next || "#/"; return; }
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
    mail: (r, code) => showMail(card, go, flow, r, code),
    done: (r) => showDone(card, r),
    totp: (r) => showTotp(card, go, r),
    forgot: (prefill) => showForgot(card, go, flow, prefill),
    local: () => showCreateLocal(card, go),
    reset: (r, code) => showReset(card, go, r, code),
  };
  const mode = link.konto;
  if (link.bekraeft) go.mail({ pending: link.bekraeft, email: "", link: true }, link.kode);
  else if (link.nulstil) go.reset({ token: link.nulstil, link: true }, link.kode);
  else if (mode === "opret") go.verify("opret");
  else if (mode === "glemt") go.forgot();
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
  const steps = ["Obserkode", "Konto", "E-mail", "Klar"];
  return h("ol", { class: "auth-steps", "aria-label": `Trin ${active} af ${steps.length}` }, ...steps.map((s, i) =>
    h("li", { class: i + 1 < active ? "done" : i + 1 === active ? "on" : "", "aria-current": i + 1 === active ? "step" : null },
      h("span", { class: "auth-dot", html: i + 1 < active ? icon("check") : String(i + 1) }), h("span", {}, s))));
}

const back = (go, text = "← Tilbage til log ind") => h("button", { type: "button", class: "linkbtn", onclick: () => go.login() }, text);

async function finish(r, go) {  // r.flow: kom fra oprettelsen (trin 3 vises med trinviseren)
  await refreshUser();
  live.restart();
  if (r.passkeys && passkey.supported()) { go.done(r); return; }
  toast(r.new && r.user.bound ? `Velkommen, ${r.user.navn}! Vi henter dine ældre observationer i baggrunden.` : r.new ? `Velkommen, ${r.user.navn}!` : `Velkommen, ${r.user.navn}`);
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
      if (r.totp) { go.totp(r); return; }
      await finish(r, go);
    } catch (err) { handle(err); pw.select(); } finally { done(); }
  } },
    field("E-mail eller obserkode", ident),
    field("Adgangskode", pw, h("button", { type: "button", class: "linkbtn", onclick: () => go.forgot(identValue()) }, "Glemt adgangskode?")),
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
    reset ? null : h("div", { class: "auth-new stack" },
      h("div", {}, h("b", {}, "Ingen DOFbasen-konto?"),
        h("p", { class: "muted", style: { margin: "2px 0 0" } }, "Opret en konto med e-mail og adgangskode alene. Du kan binde den til en obserkode senere under Profil.")),
      big("", "Opret konto uden DOFbasen", { type: "button", onclick: () => go.local() })),
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
        flow.bind = null;
        if (r.pending) { go.mail({ ...r, reset }); return; }
        if (r.totp) { go.totp({ ...r, reset, flow: true }); return; }
        passkey.rememberLogin(r.user.email || r.user.obserkode);
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
      reset ? null : h("small", { class: "muted", style: { marginTop: "-6px" } }, "Du kan logge ind med e-mailen eller obserkoden. Vi sender en kode til adressen, som du skal bekræfte den med."),
      reset ? field("Ny e-mail (valgfri)", email) : null,
      reset ? h("small", { class: "muted", style: { marginTop: "-6px" } }, "Udfyldes kun, hvis du vil skifte adresse – så bekræfter du den med en kode, før den nye adgangskode gemmes.") : null,
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

// ---------------------------------------------------------------- konto uden DOFbasen

function showCreateLocal(card, go) {
  const navn = h("input", { autocomplete: "name", maxlength: 160, required: true });
  const email = h("input", { type: "email", autocomplete: "email", maxlength: 254, required: true, placeholder: "navn@eksempel.dk" });
  const pw = h("input", { type: "password", autocomplete: "new-password", required: true, minlength: PW_MIN, maxlength: 200 });
  const pw2 = h("input", { type: "password", autocomplete: "new-password", required: true, maxlength: 200 });
  const btn = big("primary", "Opret konto");
  const msg = h("div", {});
  card.replaceChildren(
    h("h2", { style: { margin: 0 } }, "Opret konto uden DOFbasen"),
    h("p", { class: "muted", style: { margin: 0 } }, "Kontoen får en lokal kode i stedet for en obserkode. Live-fund, ture, kalendere og læring virker som for alle andre; dine egne observationer fra DOFbasen kommer først med, hvis du binder kontoen til en obserkode under Profil."),
    h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      if (pw.value.length < PW_MIN) { msg.replaceChildren(notice(`Adgangskoden skal være mindst ${PW_MIN} tegn.`)); return; }
      if (pw.value !== pw2.value) { msg.replaceChildren(notice("De to adgangskoder er ikke ens.")); return; }
      const done = busy(btn, "Opretter…");
      try {
        const r = await api("/api/auth/konto/opret-lokal", { method: "POST", body: { navn: navn.value.trim(), email: email.value.trim(), password: pw.value } });
        go.mail({ ...r, local: true });
      } catch (err) { msg.replaceChildren(notice(err.message)); } finally { done(); }
    } },
      field("Navn", navn),
      field("E-mail", email),
      h("small", { class: "muted", style: { marginTop: "-6px" } }, "Du logger ind med e-mailen. Vi sender en kode til adressen, som du skal bekræfte den med."),
      field("Adgangskode", pw),
      field("Gentag adgangskoden", pw2),
      h("small", { class: "muted" }, `Mindst ${PW_MIN} tegn.`),
      btn),
    msg,
    back(go, "Annullér"));
  navn.focus();
}

// ---------------------------------------------------------------- trin 3: bekræft e-mailen

function showMail(card, go, flow, r, prefill) {
  const code = h("input", { inputmode: "numeric", autocomplete: "one-time-code", pattern: "[0-9 ]*", maxlength: 7, required: true,
    placeholder: "123456", value: prefill || "", style: { fontSize: "1.4rem", letterSpacing: ".3em", textAlign: "center" } });
  const btn = big("primary", "Bekræft");
  const msg = h("div", {});
  const restart = (text) => h("div", { class: "notice err stack", style: { gap: "8px" } }, h("span", {}, text),
    h("button", { type: "button", class: "btn sm", onclick: () => r.local ? go.local() : go.verify(r.reset ? "glemt" : "opret") }, "Start forfra"));
  const submit = async () => {
    const done = busy(btn, "Bekræfter…");
    try {
      const res = await api("/api/auth/konto/email", { method: "POST", body: { pending: r.pending, code: code.value } });
      history.replaceState(null, "", location.pathname + "#/login");  // linket virker kun én gang
      if (res.totp) { go.totp({ ...res, reset: r.reset, flow: true }); return; }
      passkey.rememberLogin(res.user.email || res.user.obserkode);
      await finish({ ...res, reset: r.reset, flow: true }, go);
    } catch (err) {
      if (err.status === 403 && PENDING.test(err.message)) { showDone(card, null, err.message); return; }
      if (err.status === 410) { msg.replaceChildren(notice(err.message, "")); history.replaceState(null, "", location.pathname + "#/login"); setTimeout(() => go.login(), 2500); return; }
      msg.replaceChildren(/udløbet|for mange/i.test(err.message) ? restart(err.message) : notice(err.message));
      code.select();
    } finally { done(); }
  };
  const resend = h("button", { type: "button", class: "linkbtn", onclick: async () => {
    resend.disabled = true;
    try { await api("/api/auth/konto/email/igen", { method: "POST", body: { pending: r.pending } }); msg.replaceChildren(notice("Vi har sendt en ny kode. Den gamle virker ikke længere.", "")); code.value = ""; code.focus(); }
    catch (err) { msg.replaceChildren(/udløbet|for mange/i.test(err.message) ? restart(err.message) : notice(err.message)); }
    finally { setTimeout(() => { resend.disabled = false; }, 30000); }
  } }, "Send en ny kode");

  card.replaceChildren(
    stepper(3),
    h("h2", { style: { margin: 0 } }, "Bekræft din e-mail"),
    h("p", { style: { margin: 0 } }, r.email
      ? ["Vi har sendt en kode med seks cifre til ", h("b", {}, r.email), ". Skriv den her, eller tryk på linket i mailen."]
      : "Skriv koden fra mailen."),
    h("form", { class: "stack", onsubmit: (e) => { e.preventDefault(); submit(); } }, field("Kode", code), btn),
    msg,
    r.link ? null : h("p", { class: "muted", style: { fontSize: ".82rem", margin: 0 } },
      `Koden gælder i ${Math.round((r.expires_in || 1800) / 60)} minutter. Ingen mail? Kig i spam-mappen, eller `, resend, "."),
    r.reset ? h("p", { class: "muted", style: { fontSize: ".82rem", margin: 0 } }, "Den nye adgangskode gemmes først, når e-mailen er bekræftet.") : null,
    back(go, "Annullér"));
  if (prefill) submit(); else code.focus();
}

// ---------------------------------------------------------------- glemt adgangskode: kode til den bekræftede e-mail

function showForgot(card, go, flow, prefill) {
  const ident = h("input", { autocomplete: "username", placeholder: "navn@eksempel.dk eller 8000ABC", required: true, maxlength: 254,
    value: prefill || "", autocapitalize: "none", spellcheck: "false" });
  const btn = big("primary", "Send kode");
  const msg = h("div", {});
  const value = () => { const v = ident.value.trim(); return v.includes("@") ? v : v.toUpperCase(); };
  card.replaceChildren(
    h("h2", { style: { margin: 0 } }, "Glemt adgangskode"),
    h("p", { class: "muted", style: { margin: 0 } }, "Vi sender en kode til den e-mail, du har bekræftet på din konto. Med koden vælger du en ny adgangskode."),
    h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      const done = busy(btn, "Sender…");
      try {
        const r = await api("/api/auth/konto/glemt", { method: "POST", body: { login: value() } });
        go.reset(r);
      } catch (err) { msg.replaceChildren(notice(err.message)); } finally { done(); }
    } }, field("E-mail eller obserkode", ident), btn),
    msg,
    h("div", { class: "auth-new stack" },
      h("div", {}, h("b", {}, "Ingen adgang til e-mailen – eller ikke bekræftet?"),
        h("p", { class: "muted", style: { margin: "2px 0 0" } }, "Bekræft i stedet, at obserkoden er din, med din DOFbasen-adgangskode.")),
      big("", "Brug DOFbasen-login", { type: "button", onclick: () => go.verify("glemt", value().includes("@") ? "" : value()) })),
    back(go));
  ident.focus();
}

function showReset(card, go, r, prefill) {
  const code = h("input", { inputmode: "numeric", autocomplete: "one-time-code", pattern: "[0-9 ]*", maxlength: 7, required: true,
    placeholder: "123456", value: prefill || "", style: { fontSize: "1.4rem", letterSpacing: ".3em", textAlign: "center" } });
  const pw = h("input", { type: "password", autocomplete: "new-password", required: true, minlength: PW_MIN, maxlength: 200 });
  const pw2 = h("input", { type: "password", autocomplete: "new-password", required: true, maxlength: 200 });
  const btn = big("primary", "Gem ny adgangskode");
  const msg = h("div", {});
  const again = (text) => h("div", { class: "notice err stack", style: { gap: "8px" } }, h("span", {}, text),
    h("button", { type: "button", class: "btn sm", onclick: () => go.forgot() }, "Bed om en ny kode"));
  card.replaceChildren(
    h("h2", { style: { margin: 0 } }, "Vælg ny adgangskode"),
    r.link ? h("p", { class: "muted", style: { margin: 0 } }, "Koden fra mailen er udfyldt. Vælg en ny adgangskode.")
      : notice([r.message || "Vi har sendt en kode, hvis kontoen har en bekræftet e-mail.", " Kig også i spam-mappen."], ""),
    h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      if (pw.value.length < PW_MIN) { msg.replaceChildren(notice(`Adgangskoden skal være mindst ${PW_MIN} tegn.`)); return; }
      if (pw.value !== pw2.value) { msg.replaceChildren(notice("De to adgangskoder er ikke ens.")); return; }
      const done = busy(btn, "Gemmer…");
      try {
        const res = await api("/api/auth/konto/nulstil", { method: "POST", body: { token: r.token, code: code.value, password: pw.value } });
        history.replaceState(null, "", location.pathname + "#/login");  // linket virker kun én gang
        if (res.totp) { go.totp({ ...res, reset: true }); return; }
        passkey.rememberLogin(res.user.email || res.user.obserkode);
        await finish({ ...res, reset: true }, go);
      } catch (err) {
        if (err.status === 403 && PENDING.test(err.message)) { showDone(card, null, err.message); return; }
        msg.replaceChildren(/udløbet|for mange/i.test(err.message) ? again(err.message) : notice(err.message));
      } finally { done(); }
    } },
      field("Kode fra mailen", code),
      field("Ny adgangskode", pw),
      field("Gentag adgangskoden", pw2),
      h("small", { class: "muted" }, `Mindst ${PW_MIN} tegn. Når du gemmer, logges alle dine enheder ud.`),
      btn),
    msg,
    back(go, "Annullér"));
  (code.value ? pw : code).focus();
}

// ---------------------------------------------------------------- totrinslogin: kode fra authenticator-appen

function showTotp(card, go, r) {
  const code = h("input", { inputmode: "numeric", autocomplete: "one-time-code", pattern: "[0-9 ]*", maxlength: 7, required: true,
    placeholder: "123456", style: { fontSize: "1.4rem", letterSpacing: ".3em", textAlign: "center" } });
  const btn = big("primary", "Bekræft");
  const msg = h("div", {});
  const again = (text) => h("div", { class: "notice err stack", style: { gap: "8px" } }, h("span", {}, text),
    h("button", { type: "button", class: "btn sm", onclick: () => go.login() }, "Log ind igen"));
  let recovery = false;
  const label = h("span", {}, "Kode");
  const lead = h("p", { style: { margin: 0 } });
  const toggle = h("button", { type: "button", class: "linkbtn" });
  const mode = (rc) => {  // genoprettelseskode («ab12c-3de45») i stedet for koden fra appen
    recovery = rc;
    code.value = "";
    Object.assign(code, rc ? { inputMode: "text", maxLength: 12, placeholder: "ab12c-3de45", autocomplete: "off" }
      : { inputMode: "numeric", maxLength: 7, placeholder: "123456", autocomplete: "one-time-code" });
    code.removeAttribute("pattern");
    if (!rc) code.setAttribute("pattern", "[0-9 ]*");
    code.style.letterSpacing = rc ? ".12em" : ".3em";
    code.autocapitalize = "none";
    label.textContent = rc ? "Genoprettelseskode" : "Kode";
    lead.replaceChildren(...(rc ? ["Skriv en af de genoprettelseskoder, du fik, da du slog totrinslogin til. Hver kode virker kun én gang."]
      : ["Åbn din authenticator-app, og skriv den kode med seks cifre, der står ved ", h("b", {}, "DOF"), "."]));
    toggle.textContent = rc ? "Brug koden fra appen i stedet" : "Mistet telefonen? Brug en genoprettelseskode";
    msg.replaceChildren();
    code.focus();
  };
  toggle.onclick = () => mode(!recovery);
  const submit = async () => {
    if (btn.disabled) return;
    const done = busy(btn, "Bekræfter…");
    try {
      const res = await api("/api/auth/totp", { method: "POST", body: { token: r.totp, code: code.value } });
      passkey.rememberLogin(res.user.email || res.user.obserkode);
      await finish({ ...res, reset: r.reset, flow: r.flow }, go);
      if (res.recovery_used) setTimeout(() => toast("Du har brugt en genoprettelseskode. Lav nye koder under Profil → Konto og sikkerhed – og slå totrinslogin til igen på en ny telefon, hvis du har mistet den gamle.", "err"), 400);
    } catch (err) {
      if (err.status === 403 && PENDING.test(err.message)) { showDone(card, null, err.message); return; }
      msg.replaceChildren(/udløbet|for mange forkerte/i.test(err.message) ? again(err.message) : notice(err.message));
      code.value = ""; code.focus();
    } finally { done(); }
  };
  code.addEventListener("input", () => { if (!recovery && /^\d{6}$/.test(code.value.replace(/\s/g, ""))) submit(); });
  card.replaceChildren(
    h("h2", { style: { margin: 0 } }, "Totrinslogin"),
    r.reset ? notice("Den nye adgangskode er gemt. Skriv koden fra din authenticator-app for at logge ind.", "") : null,
    lead,
    h("form", { class: "stack", onsubmit: (e) => { e.preventDefault(); submit(); } }, h("label", { class: "field" }, label, code), btn),
    msg,
    toggle,
    h("p", { class: "muted", style: { fontSize: ".82rem", margin: 0 } },
      "Ingen genoprettelseskoder? Log ind med en passkey, hvis du har en, og slå totrinslogin fra under Profil – eller kontakt en administrator."),
    back(go, "Annullér"));
  mode(false);
}

// ---------------------------------------------------------------- trin 4: klar

function showDone(card, r, pendingMsg) {
  const next = () => { location.hash = params().next || (r?.new ? "#/profil?fane=omraader" : "#/"); };
  if (pendingMsg) {
    card.replaceChildren(stepper(4),
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
  const text = r.reset ? "Alle dine andre enheder er logget ud." : !r.flow ? ""
    : r.user.bound ? `Din konto er bundet til obserkoden ${r.user.obserkode}.` : "Din konto er klar. Du kan binde den til en obserkode i DOFbasen under Profil.";
  card.replaceChildren(r.flow ? stepper(4) : null,
    h("div", { class: "auth-hello" }, h("span", { class: "auth-dot done big", html: icon("check") }),
      h("h2", { style: { margin: 0 } }, title)),
    text || r.new ? h("p", { style: { margin: 0 } }, text, r.new && r.user.bound ? " Vi henter dine ældre observationer i baggrunden." : "") : null,
    h("div", { class: "auth-why" }, h("span", { html: icon("user") }),
      h("p", {}, h("b", {}, "Log ind hurtigere næste gang. "), "Med en passkey bruger du Face ID, fingeraftryk eller skærmlås i stedet for adgangskoden. Nøglen bliver på denne enhed.")),
    pkBtn, big("ghost", "Ikke nu", { type: "button", onclick: next }));
}
