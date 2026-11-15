// Snak → Admin (fane i modulet, kun med modulet «snak») og moderationskøen under Moderation (docs/snak-modul-spec.md §2.7–2.8).
import { api, ago, ask, askText, empty, errorBox, fmtN, h, params, seg, setParams, state, toast } from "../core.js";
import { barChart } from "../charts.js";
import { banForm } from "../moderation.js";
import { BASE, loadCss, loadingBox, notice } from "./common.js";

const FLAGS = [["kraever_titel", "Kræver titel"], ["har_loesning", "Har løsning"], ["kun_staff", "Kun redaktion"], ["arkiveret", "Arkiveret"]];
const SETTINGS = [
  ["feed_kommentarer", "Kommentarer pr. opslag i feedet", 0, 20], ["feed_svar", "Svar pr. kommentar i feedet", 0, 10], ["max_billeder_opslag", "Billeder pr. opslag (højst)", 1, 10],
  ["ret_minutter", "Minutter man kan rette sit opslag og sin kommentar", 0, 100000], ["opslag_pr_time", "Opslag pr. bruger pr. time (højst)", 1, 100], ["kommentarer_pr_minut", "Kommentarer pr. bruger pr. minut (højst)", 1, 100],
  ["max_pinned", "Fastgjorte opslag (højst)", 0, 10], ["report_threshold", "Anmeldelser før indhold skjules automatisk (0 = aldrig)", 0, 50],
];

const save = async (fn, ok) => { try { await fn(); if (ok) toast(ok); return true; } catch (e) { toast(e.message, "err"); return false; } };

// ------------------------------------------------------------------ kategorier

async function kategorier() {
  const box = h("div", { class: "stack" });
  const draw = async () => {
    const { categories } = await api("/api/snak/admin/kategorier");
    const patch = (c, body, ok) => save(async () => { await api(`/api/snak/admin/kategorier/${c.id}`, { method: "PATCH", body }); await draw(); }, ok);
    const rows = categories.map((c, i) => {
      const navn = h("input", { type: "text", value: c.navn, maxlength: 60, "aria-label": `Navn på ${c.navn}` });
      const besk = h("input", { type: "text", value: c.beskrivelse, maxlength: 300, "aria-label": `Beskrivelse af ${c.navn}` });
      const farve = h("input", { type: "color", value: c.farve, "aria-label": `Farve for ${c.navn}` });
      const ikon = h("input", { type: "text", value: c.ikon || "chat", maxlength: 24, size: 10, "aria-label": `Ikon for ${c.navn}` });
      return h("div", { class: `card stack sn-admin-cat ${c.arkiveret ? "arch" : ""}` },
        h("div", { class: "row" }, farve, navn, h("code", { class: "muted small" }, c.slug), h("span", { class: "muted small" }, `${fmtN(c.antal)} opslag`)),
        besk,
        h("div", { class: "row" }, ...FLAGS.map(([k, label]) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: c[k], onchange: (e) => patch(c, { [k]: e.target.checked }) }), h("span", {}, label))),
          h("span", { class: "row" }, "Ikon ", ikon)),
        h("div", { class: "row" },
          h("button", { type: "button", class: "btn sm", disabled: i === 0, "aria-label": `Flyt ${c.navn} op`, onclick: () => swap(categories, i, i - 1) }, "↑"),
          h("button", { type: "button", class: "btn sm", disabled: i === categories.length - 1, "aria-label": `Flyt ${c.navn} ned`, onclick: () => swap(categories, i, i + 1) }, "↓"),
          h("button", { type: "button", class: "btn primary sm", onclick: () => patch(c, { navn: navn.value, beskrivelse: besk.value, farve: farve.value, ikon: ikon.value }, "Gemt") }, "Gem"),
          h("span", { class: "spacer" }),
          h("button", { type: "button", class: "btn sm", onclick: () => flyt(c, categories) }, "Flyt alle opslag …"),
          c.antal || c.arkiveret ? null : h("button", { type: "button", class: "btn sm danger", onclick: async () => { if (await ask(`Slet kategorien «${c.navn}»?`)) save(async () => { await api(`/api/snak/admin/kategorier/${c.id}`, { method: "DELETE" }); await draw(); }); } }, "Slet")));
    });
    const slug = h("input", { type: "text", maxlength: 40, placeholder: "adresse-uden-mellemrum", pattern: "[a-z0-9]+(-[a-z0-9]+)*", required: true, "aria-label": "Adresse" });
    const navn = h("input", { type: "text", maxlength: 60, placeholder: "Navn", required: true, "aria-label": "Navn" });
    box.replaceChildren(...rows,
      h("form", { class: "card row", onsubmit: async (e) => {
        e.preventDefault();
        if (await save(async () => { await api("/api/snak/admin/kategorier", { method: "POST", body: { slug: slug.value, navn: navn.value, farve: "#3b6ea5" } }); await draw(); }, "Kategorien er oprettet")) { slug.value = ""; navn.value = ""; }
      } }, navn, slug, h("button", { class: "btn primary", type: "submit" }, "+ Ny kategori")),
      h("p", { class: "muted small" }, "En kategori med opslag kan ikke slettes, kun arkiveres (så den ikke kan vælges). Rækkefølgen her er rækkefølgen i feedet."));
  };
  const swap = async (cats, a, b) => {
    const [x, y] = [cats[a], cats[b]];
    await save(async () => {
      await api(`/api/snak/admin/kategorier/${x.id}`, { method: "PATCH", body: { sort: b * 10 + 10 } });
      await api(`/api/snak/admin/kategorier/${y.id}`, { method: "PATCH", body: { sort: a * 10 + 10 } });
      await draw();
    });
  };
  const flyt = async (c, cats) => {
    const names = cats.filter(k => k.id !== c.id && !k.arkiveret).map(k => k.navn).join(", ");
    const n = await askText(`Flyt alle opslag fra «${c.navn}» til (${names}):`, "");
    const to = cats.find(k => k.navn.toLowerCase() === (n || "").trim().toLowerCase());
    if (n && !to) return toast("Ukendt kategori", "err");
    if (to) save(async () => { const r = await api(`/api/snak/admin/kategorier/${c.id}/flyt`, { method: "POST", body: { to: to.id } }); toast(`${r.moved} opslag er flyttet`); await draw(); });
  };
  box.append(loadingBox());
  await draw();
  return box;
}

// ------------------------------------------------------------------ indstillinger

async function indstillinger() {
  const { values, defaults } = await api("/api/snak/admin/indstillinger");
  const inputs = Object.fromEntries(SETTINGS.map(([k, , min, max]) => [k, h("input", { type: "number", value: values[k], min, max, style: { width: "110px" }, "aria-label": k })]));
  const preview = h("input", { type: "checkbox", checked: !!values.link_preview });
  return h("form", { class: "card stack", style: { maxWidth: "720px" }, onsubmit: async (e) => {
    e.preventDefault();
    save(() => api("/api/snak/admin/indstillinger", { method: "PUT", body: { values: { ...Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, +el.value])), link_preview: preview.checked ? 1 : 0 } } }), "Gemt");
  } },
  ...SETTINGS.map(([k, label]) => h("label", { class: "field" }, h("span", {}, label), inputs[k], h("small", { class: "muted" }, `Standard: ${defaults[k]}`))),
  h("label", { class: "check" }, preview, h("span", {}, "Forhåndsvis eksterne links (serveren henter titel, beskrivelse og billede)")),
  h("div", { class: "row" }, h("button", { class: "btn primary", type: "submit" }, "Gem")));
}

// ------------------------------------------------------------------ eksempler

async function eksempler() {
  const box = h("div", { class: "card stack", style: { maxWidth: "720px" } });
  const draw = async () => {
    const { rows } = await api("/api/snak/admin/eksempler");
    box.replaceChildren(
      h("p", { class: "muted" }, "Eksempelopslagene er opdigtede og mærket «Eksempel». De kan slettes samlet og lægges ind igen."),
      rows.length ? h("ul", {}, ...rows.map(r => h("li", {}, h("a", { href: `${BASE}/${r.id}` }, r.titel || `Opslag ${r.id}`), h("span", { class: "muted small" }, ` – ${r.navn} · ${r.n_comments} kommentarer`)))) : empty("Ingen eksempelopslag"),
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn danger", disabled: !rows.length, onclick: async () => { if (await ask("Slet alle eksempelopslag?")) save(async () => { const r = await api("/api/snak/admin/eksempler", { method: "DELETE" }); toast(`${r.deleted} opslag er slettet`); await draw(); }); } }, "Slet alle eksempler"),
        h("button", { type: "button", class: "btn", onclick: () => save(async () => { const r = await api("/api/snak/admin/eksempler", { method: "POST" }); toast(r.created ? `${r.created} eksempler er lagt ind` : "Eksemplerne findes allerede"); await draw(); }) }, "Gendan eksempler")));
  };
  await draw();
  return box;
}

// ------------------------------------------------------------------ statistik

async function statistik() {
  const s = await api("/api/snak/admin/statistik");
  const x = s.uger.map((u, i) => [i, new Date(u.uge).toLocaleDateString("da-DK", { day: "numeric", month: "numeric" })]).filter((_, i, a) => i % Math.ceil(a.length / 6) === 0);
  const top = Math.max(1, ...s.uger.map(u => Math.max(u.opslag, u.kommentarer)));
  const tile = (n, label) => h("div", { class: "card sn-stat" }, h("b", {}, fmtN(n)), h("span", { class: "muted small" }, label));
  const max = Math.max(1, ...s.kategorier.map(c => c.antal));
  return h("div", { class: "stack" },
    h("div", { class: "sn-stats" }, tile(s.total.opslag, "opslag"), tile(s.total.kommentarer, "kommentarer"), tile(s.total.reaktioner, "reaktioner"), tile(s.aktive_30d, "aktive brugere (30 dage)"), tile(s.total.anmeldelser, "åbne anmeldelser")),
    h("div", { class: "card stack" }, h("h3", {}, "Opslag pr. uge"), barChart({ bars: s.uger.map(u => ({ v: u.opslag, tip: `Uge fra ${u.uge}: ${u.opslag} opslag` })), xTicks: x, yMax: top, height: 180 })),
    h("div", { class: "card stack" }, h("h3", {}, "Kommentarer pr. uge"), barChart({ bars: s.uger.map(u => ({ v: u.kommentarer, tip: `Uge fra ${u.uge}: ${u.kommentarer} kommentarer`, hl: true })), xTicks: x, yMax: top, height: 180 })),
    h("div", { class: "card stack" }, h("h3", {}, "Opslag pr. kategori"),
      ...s.kategorier.map(c => h("div", { class: "sn-bar-row" }, h("span", {}, c.navn), h("span", { class: "sn-bar-track" }, h("span", { class: "sn-bar-fill", style: { width: `${c.antal / max * 100}%`, background: c.farve } })), h("b", {}, fmtN(c.antal))))));
}

const TABS = { kategorier, indstillinger, eksempler, statistik };
const LABELS = [["kategorier", "Kategorier"], ["indstillinger", "Indstillinger"], ["eksempler", "Eksempler"], ["statistik", "Statistik"]];

/** #/snak/admin */
export async function page() {
  loadCss("snak");
  if (!state.user?.snak?.can_admin) return notice("Du har ikke adgang til administration af Snak.", "err");
  let vis = TABS[params().vis] ? params().vis : "kategorier";
  const inner = h("div", {});
  const draw = async () => {
    inner.replaceChildren(loadingBox());
    try { inner.replaceChildren(await TABS[vis]()); } catch (e) { inner.replaceChildren(errorBox(e)); }
  };
  const root = h("div", { class: "stack" }, h("h1", {}, "Admin"), seg(LABELS, vis, (v) => { vis = v; setParams({ vis: v === "kategorier" ? "" : v }); draw(); }), inner);
  draw();
  return root;
}

// ------------------------------------------------------------------ moderationskø (under Moderation)

const REASON = { chikane: "Chikane", spam: "Spam", forkert: "Forkert", andet: "Andet" };

/** Køen med anmeldte, skjulte og seneste opslag og kommentarer. Bruges af Moderation → Snak. */
export async function commentQueue() {
  let tab = "anmeldte";
  const out = h("div", { class: "stack" });
  const draw = async () => {
    out.replaceChildren(loadingBox());
    let rows;
    try { ({ rows } = await api(`/api/snak/admin/koe?tab=${tab}`)); } catch (e) { return out.replaceChildren(errorBox(e)); }
    const act = async (r, action) => {
      let reason = "";
      if (action === "skjul") { reason = await askText("Årsag til at skjule (vises for forfatteren):", ""); if (reason === null) return; }
      const path = r.kind === "post" ? `/api/snak/${r.id}/moderer` : `/api/snak/kommentarer/${r.id}/moderer`;
      if (await save(() => api(path, { method: "POST", body: { action, reason } }))) draw();
    };
    out.replaceChildren(...(rows.length ? rows.map(r => h("div", { class: "card stack sn-q" },
      h("div", { class: "row" }, h("span", { class: "sn-chip" }, r.kind === "post" ? "Opslag" : "Kommentar"), h("b", {}, r.navn || "?"),
        h("a", { href: `${BASE}/${r.post_id}${r.kind === "comment" ? `?k=${r.id}` : ""}` }, r.titel || `Opslag ${r.post_id}`), h("span", { class: "muted small" }, `${r.kategori} · ${ago(r.created_at)}`),
        h("span", { class: `sn-chip ${r.status === "skjult" ? "warn" : ""}` }, r.status), r.auto ? h("span", { class: "sn-chip warn" }, "automatisk skjult") : null, r.locked ? h("span", { class: "sn-chip" }, "🔒") : null,
        r.n_reports ? h("span", { class: "sn-chip warn" }, `🚩 ${r.n_reports}`) : null),
      h("div", { class: "sn-q-body" }, r.body),
      r.hidden_reason ? h("div", { class: "muted small" }, `Årsag: ${r.hidden_reason}`) : null,
      r.reports?.length ? h("ul", { class: "small" }, ...r.reports.map(x => h("li", {}, `${x.af}: ${REASON[x.reason] || x.reason}${x.note ? " – " + x.note : ""}`))) : null,
      h("div", { class: "row" },
        r.status === "synlig" ? h("button", { class: "btn sm danger", onclick: () => act(r, "skjul") }, "Skjul") : null,
        r.status === "skjult" ? h("button", { class: "btn sm", onclick: () => act(r, "vis") }, "Vis igen") : null,
        r.n_reports ? h("button", { class: "btn sm", onclick: () => act(r, "afvis_anmeldelser") }, "Afvis anmeldelser") : null,
        r.kind === "post" ? h("button", { class: "btn sm", onclick: () => act(r, r.locked ? "aabn" : "laas") }, r.locked ? "Åbn tråd" : "Lås tråd") : null,
        r.user_id && r.user_id !== state.user.id ? h("button", { class: "btn sm", onclick: async () => (await import("../views/admin/moderation.js")).messageForm({ user_id: r.user_id, navn: r.navn, obserkode: r.obserkode }) }, "Skriv") : null,
        r.user_id && r.user_id !== state.user.id ? h("button", { class: "btn sm danger", onclick: () => banForm({ user_id: r.user_id, navn: r.navn, obserkode: r.obserkode }, draw) }, "Udeluk") : null))) : [empty("Intet her", "Køen er tom.")]));
  };
  draw();
  return h("div", {}, seg([["anmeldte", "Anmeldte"], ["skjulte", "Skjulte"], ["seneste", "Alle nye (7 dage)"]], tab, (v) => { tab = v; draw(); }), h("div", { style: { marginTop: "12px" } }, out));
}
