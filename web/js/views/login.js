import { api, state, h, params, toast } from "../core.js";
import { refreshUser } from "../app.js";
import { live } from "../live.js";

export async function render(view) {
  if (state.user) { location.hash = params().next || "#/"; return; }
  const kode = h("input", { autocomplete: "username", placeholder: "Fx 8000ABC", required: true, style: { textTransform: "uppercase" } });
  const pw = h("input", { type: "password", autocomplete: "current-password", required: true });
  const btn = h("button", { class: "btn primary", style: { width: "100%" } }, "Log ind");
  const msg = h("div", {});
  view.append(h("div", { class: "login-wrap" },
    h("div", { class: "hero" },
      h("svg", { class: "bg", viewBox: "0 0 32 32", html: '<path d="M7 19c3.5-1 6-3.6 7.6-7.2.4-.9 1.6-1 2.1-.1l1 1.8 4.6-1.3c.6-.2 1 .5.6 1l-3.2 3.4c-1.4 4.3-5 6.9-9.6 6.9H7.6c-.9 0-1.2-1.2-.6-1.5z" fill="#fff"/>' }),
      h("h1", {}, "Velkommen"), h("p", {}, "Log ind med din DOFbasen-obserkode og adgangskode. Ét login til notifikationer, lister, fænologi og læring.")),
    h("form", { class: "card stack", onsubmit: async (e) => {
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
      h("label", { class: "field" }, h("span", {}, "Obserkode"), kode),
      h("label", { class: "field" }, h("span", {}, "Adgangskode (som på DOFbasen)"), pw),
      btn, msg,
      h("p", { class: "muted", style: { fontSize: ".82rem", margin: 0 } }, "Din adgangskode sendes kun til DOFbasen ved første login og gemmes derefter som en krypteret hash – aldrig i klartekst."))));
  kode.focus();
}
