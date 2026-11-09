// Forside for ikke-indloggede: kort præsentation + login med DOFbasen-obserkode.
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

export async function render(view) {
  if (state.user) { location.hash = params().next || "#/"; return; }
  const kode = h("input", { autocomplete: "username webauthn", placeholder: "Fx 8000ABC", required: true, value: passkey.lastObserkode(), style: { textTransform: "uppercase" } });
  const pw = h("input", { type: "password", autocomplete: "current-password", required: true });
  const btn = h("button", { class: "btn primary", style: { width: "100%", height: "44px" } }, "Log ind");
  const msg = h("div", {});
  const done = (r) => {
    toast(r.new ? `Velkommen, ${r.user.navn}! Vi henter dine ældre observationer i baggrunden.` : `Velkommen tilbage, ${r.user.navn}`);
    location.hash = params().next || (r.new ? "#/profil" : "#/");
  };
  // Login med passkey: kun obserkoden – Face ID, fingeraftryk eller skærmlås i stedet for DOFbasen-adgangskoden
  const pkBtn = h("button", { type: "button", class: "btn", style: { width: "100%", height: "44px", display: "none" }, onclick: async () => {
    if (!kode.value.trim()) { msg.replaceChildren(h("div", { class: "notice err" }, "Skriv din obserkode først.")); kode.focus(); return; }
    pkBtn.disabled = true;
    try {
      const r = await passkey.login(kode.value.trim().toUpperCase());
      await refreshUser();
      live.restart();
      done(r);
    } catch (err) {
      msg.replaceChildren(h("div", { class: "notice err" }, err.message));
    } finally { pkBtn.disabled = false; }
  } }, "Log ind med passkey");
  if (passkey.supported()) api("/api/settings/public").then(s => { if (s?.passkeys) pkBtn.style.display = ""; }).catch(() => {});
  view.append(h("div", { class: "landing" },
    h("section", { class: "landing-hero" },
      h("div", {},
        h("p", { class: "landing-kicker" }, "Portal for feltornitologer"),
        h("h1", {}, "Fuglene, fundene og folkene – samlet ét sted"),
        h("p", { class: "lead" }, "DOF.tool samler live-observationer fra DOFbasen, egne indtastninger, kalendere for trækposter, DOF's ture, lister, fænologi og læring. Log ind med din DOFbasen-obserkode."),
        h("ul", { class: "landing-features" }, ...FEATURES.map(([ic, t, d]) => h("li", {},
          h("span", { class: "lf-icon", html: icon(ic) }), h("div", {}, h("b", {}, t), h("p", {}, d)))))),
      h("form", { class: "card stack landing-login", onsubmit: async (e) => {
        e.preventDefault();
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Logger ind…';
        const slow = setTimeout(() => msg.replaceChildren(h("div", { class: "notice" }, "Adgangskoden tjekkes hos DOFbasen – det tager normalt kun et øjeblik.")), 2500);
        try {
          const r = await api("/api/auth/login", { method: "POST", body: { obserkode: kode.value.trim(), password: pw.value } });
          passkey.rememberObserkode(r.user.obserkode);
          await refreshUser();
          live.restart();
          // Første login forbinder DOFbasen-brugeren; derefter kan brugeren logge ind med passkey alene
          if (r.passkeys && passkey.supported() && confirm("Vil du oprette en passkey på denne enhed? Så kan du næste gang logge ind med Face ID, fingeraftryk eller skærmlås – uden DOFbasen-adgangskoden.")) {
            try { await passkey.register(passkey.deviceName()); toast("Passkey oprettet"); }
            catch (err) { toast(`${err.message} Du kan prøve igen under Profil.`, "err"); }
          }
          done(r);
        } catch (err) {
          // Gyldig kode, men kontoen venter på en administrator: det er ikke en fejl
          msg.replaceChildren(err.status === 403 && /afventer/.test(err.message)
            ? h("div", { class: "notice" }, h("b", {}, "Tak – din konto afventer godkendelse. "), err.message.replace(/^[^.]*\.\s*/, ""))
            : h("div", { class: "notice err" }, err.message));
        } finally {
          clearTimeout(slow);
          btn.disabled = false; btn.textContent = "Log ind";
        }
      } },
        h("h2", { style: { margin: 0 } }, "Log ind"),
        h("label", { class: "field" }, h("span", {}, "Obserkode"), kode),
        h("label", { class: "field" }, h("span", {}, "Adgangskode (som på DOFbasen)"), pw),
        btn, pkBtn, msg,
        h("p", { class: "muted", style: { fontSize: ".82rem", margin: 0 } }, "Adgangskoden kontrolleres hos DOFbasen og gemmes ikke i DOF.tool. Med en passkey logger du ind uden adgangskoden. Portalen er kun for indloggede brugere. ",
          h("a", { href: "/privatliv" }, "Privatlivspolitik"))))));
  kode.focus();
}
