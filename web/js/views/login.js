// Forside for ikke-indloggede: kort præsentation + login med DOFbasen-obserkode.
import { api, state, h, icon, params, toast } from "../core.js";
import { refreshUser } from "../app.js";
import { live } from "../live.js";

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
  const kode = h("input", { autocomplete: "username", placeholder: "Fx 8000ABC", required: true, style: { textTransform: "uppercase" } });
  const pw = h("input", { type: "password", autocomplete: "current-password", required: true });
  const btn = h("button", { class: "btn primary", style: { width: "100%", height: "44px" } }, "Log ind");
  const msg = h("div", {});
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
        const slow = setTimeout(() => msg.replaceChildren(h("div", { class: "notice" }, "Første login tjekkes hos DOFbasen. Portalen kalder højst DOFbasen én gang i minuttet for ikke at belaste serveren – det kan tage op til et minut.")), 2500);
        try {
          const r = await api("/api/auth/login", { method: "POST", body: { obserkode: kode.value.trim(), password: pw.value } });
          await refreshUser();
          live.restart();
          toast(r.new ? `Velkommen, ${r.user.navn}! Vi henter dine ældre observationer i baggrunden.` : `Velkommen tilbage, ${r.user.navn}`);
          location.hash = params().next || (r.new ? "#/profil" : "#/");
        } catch (err) {
          msg.replaceChildren(h("div", { class: "notice err" }, err.message));
        } finally {
          clearTimeout(slow);
          btn.disabled = false; btn.textContent = "Log ind";
        }
      } },
        h("h2", { style: { margin: 0 } }, "Log ind"),
        h("label", { class: "field" }, h("span", {}, "Obserkode"), kode),
        h("label", { class: "field" }, h("span", {}, "Adgangskode (som på DOFbasen)"), pw),
        btn, msg,
        h("p", { class: "muted", style: { fontSize: ".82rem", margin: 0 } }, "Adgangskoden sendes kun til DOFbasen ved første login og gemmes derefter som en krypteret hash – aldrig i klartekst. Portalen er kun for indloggede brugere.")))));
  kode.focus();
}
