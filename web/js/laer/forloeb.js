// Læring 2.0: forløb – oversigt og forløbsside med punkter, oplåsning, feltopgaver og bevis (docs/laering-spec.md §9).
import { api, h, toast, errorBox, empty } from "../core.js";
import { BASE, backLink, bar, chip, loadingBox } from "./common.js";

const KIND = { kursus: "Kursus", ovelse: "Øvelse", traening: "Træning", felt: "Feltopgave", tekst: "Info" };

export async function render(view, [slug], info) {
  if (!slug) return list(view, info);
  const qs = new URLSearchParams(location.hash.split("?")[1] || "");
  const hold = qs.get("hold") ? +qs.get("hold") : null;
  const d = await api(`/api/learn/forloeb/${slug}${hold ? `?hold=${hold}` : ""}`);
  const actions = h("div", { class: "lr-actions" });
  const reload = () => { view.replaceChildren(); return render(view, [slug], info); };
  if (!d.tilmeldt && d.published) actions.append(h("button", { class: "btn primary", onclick: async () => { await api(`/api/learn/forloeb/${slug}/tilmeld`, { method: "POST" }); reload(); } }, "Tilmeld mig forløbet"));
  if (d.can_edit) actions.append(h("a", { class: "btn", href: `${BASE}/byg/forloeb/${slug}` }, "Redigér forløbet"));
  if (d.bevis) actions.append(h("a", { class: "btn primary", href: `${BASE}/bevis/${d.bevis}` }, "Se dit bevis"));
  const sections = d.sections.map(s => h("section", { class: "lr-psec" }, h("h3", {}, s.navn || "Punkter"), h("div", { class: "lr-plist" }, ...s.punkter.map(p => item(p)))));

  function item(p) {
    const cls = `lr-pitem${p.done ? " done" : ""}${p.locked ? " locked" : ""}`;
    const body = h("div", { class: "lr-pbody" }, h("div", { class: "lr-kicker" }, `${KIND[p.kind] || p.kind}${p.required ? "" : " · valgfri"}`), h("b", {}, p.title || p.field_task?.titel || "Punkt"));
    if (p.kind === "felt" && p.field_task) body.append(h("p", { class: "muted" }, p.field_task.tekst_md || ""), felt(p));
    if (p.kind === "tekst" && p.text_md) body.append(h("p", { class: "lr-md" }, p.text_md));
    if (p.locked) body.append(h("small", { class: "muted" }, `🔒 ${p.why}`));
    if (p.pct) body.append(h("small", { class: "muted" }, ` Bedste resultat: ${p.pct} %`));
    const go = !p.locked && link(p);
    return h("div", { class: cls }, h("span", { class: "lr-tick" }, p.done ? "✓" : p.venter ? "…" : ""), body, go ? h("span", { class: "lr-meta" }, go) : null);
  }
  function link(p) {
    if (p.kind === "kursus" && p.course) return h("a", { class: "btn sm", href: `${BASE}/kursus/${p.course.slug}` }, p.done ? "Åbn igen" : "Start");
    if (p.kind === "ovelse" && p.course) return h("a", { class: "btn sm", href: `${BASE}/ovelse/${p.course.slug}` }, "Øv");
    if (p.kind === "traening" && p.training) return h("a", { class: "btn sm", href: `${BASE}/traen`, onclick: () => sessionStorage.setItem("lr:traening", JSON.stringify(p.training)) }, "Åbn træningen");
    if ((p.kind === "traening" || p.kind === "tekst") && !p.done) return h("button", { class: "btn sm", onclick: async () => { await api(`/api/learn/forloeb/${slug}/punkter/${p.id}/marker`, { method: "POST", body: { hold: d.hold?.id || null } }); reload(); } }, "Markér som gennemført");
    return null;
  }
  function felt(p) {
    const out = h("div", { class: "lr-felt" });
    const auto = p.field_task.godkendelse !== "selv";
    const btn = auto ? h("button", { class: "btn sm primary", disabled: p.done || p.venter || p.locked, onclick: async () => {
      try {
        const r = await api(`/api/learn/forloeb/${slug}/punkter/${p.id}/tjek`, { method: "POST", body: { hold: d.hold?.id || null } });
        const msg = { godkendt: "Afkrydset ud fra dine observationer!", venter: "Sendt til din underviser til godkendelse.", mangler: `Ikke endnu – fundet ${r.fundet} af ${r.kraeves} arter siden opgaven åbnede.`, ingen_profil: "Knyt din DOFbasen-profil under Profil, så vi kan se dine observationer." }[r.status] || r.status;
        toast(msg, r.status === "godkendt" || r.status === "venter" ? "" : "err"); if (r.status === "godkendt" || r.status === "venter") reload();
      } catch (e) { toast(e.message, "err"); }
    } }, p.done ? "Klaret" : p.venter ? "Venter på underviser" : "Tjek mine observationer") : null;
    out.append(btn);
    if (!auto && !p.done) out.append(h("button", { class: "btn sm", onclick: async () => { await api(`/api/learn/forloeb/${slug}/punkter/${p.id}/marker`, { method: "POST", body: { hold: d.hold?.id || null } }); reload(); } }, "Markér som klaret"));
    return out;
  }

  view.append(backLink(`${BASE}/forloeb`, "Forløb"), h("section", { class: "lr-course-head" }, h("div", {}, h("div", { class: "lr-kicker" }, "Forløb"), h("h2", {}, d.title), h("p", {}, d.summary), d.intro_md ? h("p", { class: "lr-md" }, d.intro_md) : null,
    h("div", { class: "lr-meta" }, d.certificate ? chip("giver bevis", "soft") : null, d.hold ? chip(`Hold: ${d.hold.title}`) : null, d.faerdig ? chip("gennemført", "soft") : null),
    d.tilmeldt ? [bar(d.fremgang.done, d.fremgang.total), h("small", { class: "muted" }, `${d.fremgang.done} af ${d.fremgang.total} krævede punkter`)] : null, actions)),
    d.mine_hold.length > 1 ? h("p", {}, "Hold: ", ...d.mine_hold.map(c => h("a", { href: `${BASE}/forloeb/${slug}?hold=${c.id}`, style: { marginRight: "10px" } }, c.title))) : null, ...sections);
}

export async function list(view, info) {
  const body = h("div", {}, loadingBox());
  view.append(h("div", { class: "lr-intro" }, h("h2", {}, "Forløb og hold"), h("p", { class: "muted" }, "Et forløb samler kurser, øvelser og feltopgaver i en rækkefølge – alene eller sammen med et hold og en underviser.")), body);
  try {
    const d = await api("/api/learn/forloeb");
    const code = h("input", { placeholder: "Holdkode (6 tegn)", maxlength: 12, "aria-label": "Holdkode" });
    body.replaceChildren(
      h("div", { class: "lr-setup" }, code, h("button", { class: "btn", onclick: async () => { try { const r = await api("/api/learn/hold/tilmeld", { method: "POST", body: { code: code.value } }); location.hash = `${BASE}/hold/${r.id}`; } catch (e) { toast(e.message, "err"); } } }, "Tilmeld hold"),
        h("a", { class: "btn ghost", href: `${BASE}/hold` }, "Mine hold")),
      d.forloeb.length ? h("div", { class: "lr-grid" }, ...d.forloeb.map(p => h("a", { class: "lr-tile", href: `${BASE}/forloeb/${p.slug}` }, h("div", { class: "lr-kicker" }, p.published ? "Forløb" : "Kladde"), h("h3", {}, p.title), h("p", { class: "muted" }, p.summary)))) : empty("Ingen forløb endnu"));
  } catch (e) { body.replaceChildren(errorBox(e)); }
}
