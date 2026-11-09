// Indtast: vælg, hvad du vil – fortsæt en indtastning, der ikke er afsluttet, normal indtastning (ny tur), eller en
// spontantælling / FiNN-tælling (fem minutter, komplet artsliste – views/taelling.js).
import { h, icon, fmtDate, fmtN, requireLogin } from "../core.js";
import { openEntries, tripHash, isCount, countLeft, SPONTAN, FINN } from "../tur-data.js";

const KIND = { [SPONTAN]: "Spontantælling", [FINN]: "FiNN-tælling" };

const choice = (href, ic, title, text, cls = "") => h("a", { class: `card indtast-choice ${cls}`, href },
  h("span", { class: "indtast-ic", html: icon(ic) }),
  h("span", { class: "indtast-txt" }, h("b", {}, title), h("small", { class: "muted" }, text)),
  h("span", { class: "indtast-go", html: icon("next") }));

export async function render(page) {
  if (requireLogin(page)) return;
  const current = h("div", { class: "stack", style: { gap: "10px" } });
  page.append(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Indtast"),
      h("p", { class: "lead", style: { margin: 0 } }, h("a", { href: "#/indtast" }, "Mine indtastninger →")))),
    h("div", { class: "indtast-start stack" },
      current,
      choice("#/indtast/tur/ny", "edit", "Normal indtastning", "Opret en tur med lokalitet, tider og vejr – og indtast dine observationer undervejs."),
      h("h3", { class: "indtast-sub" }, "Tællinger"),
      choice("#/indtast/spontan", "now", "Spontantælling",
        "Stå stille i fem minutter, og indtast alt, hvad du ser og hører. Kan laves næsten hvor og hvornår som helst."),
      choice("#/indtast/finn", "pin", "FiNN-tælling",
        "Spontantælling i et FiNN-område (Fugle i Ny Natur) – gerne ved et FiNN-punkt.")));

  let rows = [];
  try { rows = await openEntries(); } catch { /* vis bare valgene */ }
  if (!rows.length) return;
  current.append(h("h3", { class: "indtast-sub", style: { marginTop: 0 } }, "Fortsæt aktuel indtastning"),
    ...rows.slice(0, 3).map(t => {
      const left = countLeft(t);
      const what = isCount(t) ? KIND[t.metode_id] : "Tur";
      const when = [fmtDate(t.dato), t.turtidfra ? `fra ${t.turtidfra}` : null].filter(Boolean).join(" ");
      const status = left != null && left > 0 ? `${Math.ceil(left / 60)} min. tilbage` : "ikke afsluttet";
      return choice(tripHash(t), isCount(t) ? "now" : "list", `${what}: ${t.lok || "Lokalitet ikke valgt"}`,
        `${when} · ${fmtN(t.n_obs || 0)} obs. · ${status}${t.server_id ? "" : " · ikke sendt endnu"}`, "current");
    }));
}
