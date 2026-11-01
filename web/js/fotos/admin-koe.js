// Fotos → Admin → Kø: nye (afventer), afviste og afpublicerede fotos. Tastatur: j/k næste/forrige, g godkend, a afvis.
import { api, h, toast } from "../core.js";
import { BASE, dkDate, loadingBox, nowrap, pager, notice, cleanups } from "./common.js";
import { reasonDialog, setStatus } from "./moderering.js";

const TABS = [["afventer", "Afventer"], ["afvist", "Afvist"], ["afpubliceret", "Afpubliceret"]];

export async function page(parts, query = {}) {
  let status = TABS.some(t => t[0] === query.status) ? query.status : "afventer", side = 1, cur = 0;
  const list = h("div", { class: "stack" }), tabs = h("div", { class: "seg" });
  let rows = [];
  async function load() {
    list.replaceChildren(loadingBox());
    const d = await api(`/api/admin/fotos/koe?status=${status}&side=${side}`);
    rows = d.rows; cur = Math.min(cur, Math.max(0, rows.length - 1));
    tabs.replaceChildren(...TABS.map(([k, t]) => h("button", { type: "button", class: k === status ? "on" : "", onclick: () => { status = k; side = 1; cur = 0; load(); } }, `${t} (${nowrap(d.antal[k])})`)));
    list.replaceChildren(rows.length ? h("div", { class: "stack" }, ...rows.map(card)) : h("p", { class: "muted", style: { padding: "20px 0" } }, "Køen er tom."),
      pager(side, Math.ceil(d.total / 30), (p) => { side = p; cur = 0; load(); window.scrollTo({ top: 0 }); }));
    mark();
  }
  const mark = () => list.querySelectorAll(".fot-q").forEach((el, i) => el.classList.toggle("cur", i === cur));
  function card(r) {
    const done = async (fn, msg) => { try { await fn(); toast(msg); await load(); } catch (e) { toast(e.message, "err"); } };
    const art = r.arter?.[0];
    return h("div", { class: "fot-q" },
      h("a", { href: r.medium, target: "_blank", rel: "noopener", class: "fot-q-img", title: "Vis stort" }, h("img", { src: r.thumb, alt: art?.navn || "Foto", loading: "lazy" })),
      h("div", { class: "fot-q-body stack" },
        h("div", {}, h("a", { href: `${BASE}/${r.id}`, class: "plain" }, h("b", {}, (r.arter || []).map(a => a.navn).join(", ") || "Foto")), r.titel ? ` – ${r.titel}` : ""),
        h("div", { class: "muted small" }, [dkDate(r.dato), r.lokalitet, r.land].filter(Boolean).join(" · ")),
        h("div", { class: "small" }, "Fotograf: ", h("a", { href: `${BASE}/fotograf/${r.fotograf.id}` }, r.fotograf.navn || "–"), r.fotograf.obserkode ? ` (${r.fotograf.obserkode})` : "", " · uploadet ", dkDate(r.uploadet, { time: true })),
        h("div", { class: `small ${r.over_graense ? "fot-red" : ""}` }, `${nowrap(r.n_art)} publicerede fotos af arten (grænse ${r.graense})`, r.sjaelden ? " · sjælden art – grænsen gælder ikke" : ""),
        r.afvist_grund ? h("div", { class: "notice small" }, r.afvist_grund) : null,
        h("div", { class: "row" },
          r.status !== "publiceret" ? h("button", { class: "btn sm primary", type: "button", onclick: () => done(() => setStatus(r.id, "publiceret"), "Godkendt") }, "Godkend (g)") : null,
          r.status === "afventer" ? h("button", { class: "btn sm", type: "button", onclick: () => reject(r) }, "Afvis… (a)") : null,
          h("a", { class: "btn sm", href: `${BASE}/${r.id}/ret` }, "Ret"), r.laast ? h("span", { class: "muted small" }, "🔒 låst") : null)));
  }
  const reject = (r) => reasonDialog("Afvis foto", async (g) => { await setStatus(r.id, "afvist", g); toast("Afvist"); await load(); });
  const keys = (e) => {
    if (e.target.closest("input,textarea,select,[contenteditable]") || e.ctrlKey || e.metaKey || !rows.length) return;
    const r = rows[cur];
    if (e.key === "j") { cur = Math.min(rows.length - 1, cur + 1); mark(); list.querySelectorAll(".fot-q")[cur]?.scrollIntoView({ block: "center", behavior: "smooth" }); }
    else if (e.key === "k") { cur = Math.max(0, cur - 1); mark(); list.querySelectorAll(".fot-q")[cur]?.scrollIntoView({ block: "center", behavior: "smooth" }); }
    else if (e.key === "g" && r.status !== "publiceret") setStatus(r.id, "publiceret").then(() => { toast("Godkendt"); load(); }).catch(x => toast(x.message, "err"));
    else if (e.key === "a" && r.status === "afventer") reject(r);
  };
  addEventListener("keydown", keys);
  cleanups.push(() => removeEventListener("keydown", keys));
  await load();
  return h("div", { class: "stack" }, tabs, h("p", { class: "muted small" }, "Tastatur: j/k næste/forrige · g godkend · a afvis"), list);
}
