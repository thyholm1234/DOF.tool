// Netfugl: arter – World Bird List (fuglefamilier), DK-listen, VP-listen og artssider med danske fund fra DOFbasen.
import { api, h, fmtN, toast } from "../core.js";
import { headline, title, link, loadingBox, notice, md, nfModal, MONTHS } from "./shell.js";
import { photoGrid } from "./fotos.js";

export async function page(parts, q) {
  if (parts[0] === "art") return species(+parts[1]);
  if (parts[1] === "dk" || parts[1] === "vp") {
    if (q.art) {
      const d = await api("/api/netfugl/arter/dk");
      const hit = d.rows.find(r => String(r.artnr) === q.art);
      if (hit) return species(hit.id);
    }
    return list(parts[1]);
  }
  return world();
}

async function world() {
  const fams = await api("/api/netfugl/arter/familier");
  const filter = h("input", { type: "search", placeholder: "Søg familie (latin eller engelsk)", style: { width: "300px" } });
  const box = h("div", {});
  const draw = () => {
    const f = filter.value.trim().toLowerCase();
    const groups = [];
    let cur = null;
    for (const r of fams.filter(r => !f || `${r.orden} ${r.familie} ${r.engelsk}`.toLowerCase().includes(f))) {
      if (!cur || cur.orden !== r.orden) { cur = { orden: r.orden, rows: [] }; groups.push(cur); }
      cur.rows.push(r);
    }
    box.replaceChildren(...groups.map(g => h("div", { style: { paddingBottom: "12px" } }, h("h6", { class: "red", style: { margin: "0 0 4px" } }, g.orden),
      ...g.rows.map(r => h("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr 110px", gap: "8px" } }, h("span", {}, r.familie), h("span", {}, r.engelsk), h("span", { class: "muted" }, `${r.antal} species`))))));
  };
  filter.oninput = draw;
  draw();
  return h("div", {}, headline("World Bird List"), h("div", { class: "wrap" },
    h("p", {}, "The World Bird List at Netfugl is based on Gill, F and D Donsker (Eds). IOC World Bird Names. Familieoversigten er kopieret fra netfugl.dk som eksempel; artssiderne i showcasen findes for DK- og VP-listen."),
    h("div", { class: "nf-tools" }, filter, h("span", { class: "spacer" }), link("/arter/dk", "DK listen", { class: "btn" }), link("/arter/vp", "VP listen", { class: "btn" })),
    title("Bird Families of the World"), box));
}

async function list(liste) {
  const box = h("div", {}, loadingBox());
  const head = h("div", {});
  const load = async () => {
    const [d, kat] = await Promise.all([api(`/api/netfugl/arter/${liste}`), liste === "dk" ? api("/api/netfugl/sider/kategorier").catch(() => null) : null]);
    const filter = h("input", { type: "search", placeholder: "Søg …", style: { width: "200px" } });
    const tbody = h("tbody", {});
    const draw = () => {
      const f = filter.value.trim().toLowerCase();
      tbody.replaceChildren(...d.rows.filter(r => !f || `${r.navn} ${r.latin} ${r.engelsk || ""}`.toLowerCase().includes(f)).map((r, i) => h("tr", {},
        liste === "vp" ? h("td", {}, i + 1) : null, h("td", { class: "nf-su" }, r.su ? "*" : ""),
        h("td", {}, link(`/art/${r.id}`, r.navn)), h("td", {}, h("i", {}, r.latin)), h("td", {}, liste === "dk" ? r.kategori || "" : r.engelsk || ""),
        d.can_edit ? h("td", {}, h("button", { type: "button", class: "btn sm", onclick: () => editSpecies(liste, r, load) }, "Ret")) : null)));
    };
    filter.oninput = draw;
    draw();
    box.replaceChildren(
      liste === "dk" ? h("p", {}, "Dette er en komplet liste af alle arter, som er truffet i Danmark. Alle kategori A, B og C arter er angivet ud fra retningslinjerne udstukket af SU/BOURC/AERC. Arter markeret med * er SU-arter.")
        : h("p", {}, "Arter markeret med * er betragtet som generelt sjældne i hele Vestpalæarktis. Kryds af en eller flere af disse arter på VP krydslisten skal associeres med land, lokalitet og dato."),
      kat ? h("details", { style: { margin: "8px 0" } }, h("summary", { class: "red" }, "Se kategorier"), md(kat.body)) : null,
      h("div", { class: "nf-tools" }, h("b", {}, `Arter: ${d.rows.length}`), h("span", { class: "spacer" }),
        d.can_edit ? h("button", { type: "button", class: "btn", onclick: () => editSpecies(liste, null, load) }, "+ Tilføj art") : null, h("span", {}, "Søg: "), filter),
      h("div", { class: "nf-scroll" }, h("table", { class: "nf-table" }, h("thead", {}, h("tr", {},
        liste === "vp" ? h("th", {}, "#") : null, h("th", {}, liste === "dk" ? "SU" : "*"), h("th", {}, liste === "dk" ? "Artsnavn" : "Danske navn"), h("th", {}, "Latinske navn"),
        h("th", {}, liste === "dk" ? "Kategori" : "Engelske navn"), d.can_edit ? h("th", {}) : null)), tbody)));
  };
  head.append(headline(liste === "dk" ? "DK listen" : "VP listen"));
  load().catch(err => box.replaceChildren(notice(err.message, "err")));
  return h("div", {}, head, h("div", { class: "wrap" }, box));
}

function editSpecies(liste, r, done) {
  const f = r ? { ...r, engelsk: r.engelsk || "", kategori: r.kategori || "" } : { navn: "", latin: "", engelsk: "", kategori: liste === "dk" ? "A" : "", su: false, sort: 99999 };
  const inp = (k) => h("input", { type: "text", value: f[k] ?? "", style: { width: "100%" }, oninput: (e) => { f[k] = e.target.value; } });
  const m = nfModal(r ? `Ret ${r.navn}` : "Tilføj art", h("div", {},
    h("label", { class: "nf-field" }, h("span", {}, "Dansk navn"), inp("navn")), h("label", { class: "nf-field" }, h("span", {}, "Latinsk navn"), inp("latin")),
    h("label", { class: "nf-field" }, h("span", {}, "Engelsk navn"), inp("engelsk")), liste === "dk" ? h("label", { class: "nf-field" }, h("span", {}, "Kategori"), inp("kategori")) : null,
    h("label", {}, h("input", { type: "checkbox", checked: f.su, onchange: (e) => { f.su = e.target.checked; } }), liste === "dk" ? " SU-art" : " Sjælden i VP (*)"),
    h("label", { class: "nf-field" }, h("span", {}, "Placering (sortering)"), h("input", { type: "number", value: f.sort, oninput: (e) => { f.sort = +e.target.value; } })),
    h("div", { class: "nf-row" },
      r ? h("button", { type: "button", class: "btn danger", onclick: async () => { if (!confirm("Fjern arten fra listen?")) return; await api(`/api/netfugl/art/${r.id}`, { method: "DELETE" }); m.close(); done(); } }, "Fjern") : null,
      h("span", { style: { flex: 1 } }), h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Annullér"),
      h("button", { type: "button", class: "btn primary", onclick: async () => {
        try { await api(`/api/netfugl/art/${r ? r.id : 0}`, { method: "PUT", body: { liste, navn: f.navn, latin: f.latin, engelsk: f.engelsk, kategori: f.kategori, su: f.su, sort: f.sort } }); toast("Gemt"); m.close(); done(); }
        catch (err) { toast(err.message, "err"); }
      } }, "Gem"))));
}

async function species(id) {
  const d = await api(`/api/netfugl/art/${id}`);
  const box = h("div", { class: "wrap" });
  const s = d.dofb;
  const mx = s ? Math.max(1, ...s.months.map(m => m.n)) : 1;
  box.append(h("h2", { style: { margin: "6px 0 2px" } }, d.su ? h("span", { class: "nf-su" }, "* ") : null, d.navn, " ", h("i", { class: "muted", style: { fontSize: "1.1rem" } }, d.latin)),
    h("p", { class: "muted" }, [d.engelsk, d.kategori ? `Kategori ${d.kategori}` : null, d.liste === "dk" ? "DK listen" : "VP listen"].filter(Boolean).join(" · ")),
    h("div", { class: "nf-row" }, link(`/arter/${d.liste}`, `← ${d.liste === "dk" ? "DK listen" : "VP listen"}`),
      d.kryds ? link(`/kryds/${d.kryds.liste}/art/${encodeURIComponent(d.kryds.key)}`, `Krydset af ${d.kryds.antal} af ${d.kryds.deltagere} deltagere`) : null,
      d.artnr && d.dofbasen ? h("a", { href: `#/dofbasen/faeno/art/${d.artnr}` }, "Fænologi i DOFbasen →") : null,
      d.artnr ? link(`/fotos?art=${d.artnr}`, "Fotos af arten") : null));
  if (s) {
    box.append(title("Danske fund i DOFbasen"),
      h("p", {}, `${fmtN(s.fund)} fund (dato og lokalitet) med ${fmtN(s.obs)} observationer`, s.first ? `, fra ${s.first} til ${s.last}` : "", ". Opgjort direkte fra DOFbasen-data i portalen."),
      h("div", { class: "nf-cols two" },
        h("div", {}, h("b", {}, "Fund pr. måned"), h("div", { style: { display: "flex", alignItems: "flex-end", gap: "3px", height: "120px", borderBottom: "1px solid #999", marginTop: "8px" } },
          ...Array.from({ length: 12 }, (_, i) => { const n = s.months.find(m => m.m === i + 1)?.n || 0; return h("div", { title: `${MONTHS[i]}: ${n}`, style: { flex: 1, background: "#a70e00", opacity: .8, height: `${(n / mx) * 100}%` } }); })),
          h("div", { style: { display: "flex", gap: "3px" } }, ...MONTHS.map(m => h("span", { class: "small", style: { flex: 1, textAlign: "center" } }, m[0].toUpperCase())))),
        h("div", {}, h("b", {}, "Fund pr. lokalafdeling"), h("table", { class: "nf-table small" }, h("tbody", {}, ...s.afdelinger.map(a => h("tr", {}, h("td", {}, a.navn), h("td", { class: "num" }, fmtN(a.n)))))))));
    if (s.fundliste.length) {
      const obs = (r) => d.dofbasen ? h("a", { href: `#/dofbasen/obs/${r.obsid}` }, r.lokalitet || r.loknr) : h("a", { href: `https://dofbasen.dk/popobs.php?obsid=${r.obsid}&summering=tur&obs=obs`, target: "_blank", rel: "noopener" }, r.lokalitet || r.loknr);
      box.append(title(`Samtlige danske fund (${s.fundliste.length})`), h("div", { class: "nf-scroll", style: { maxHeight: "480px", overflow: "auto" } },
        h("table", { class: "nf-table small" }, h("thead", {}, h("tr", {}, h("th", {}, "Dato"), h("th", {}, "Lokalitet"), h("th", {}, "Antal"), h("th", {}, "Observatør"))),
          h("tbody", {}, ...s.fundliste.map(r => h("tr", {}, h("td", {}, r.dato), h("td", {}, obs(r)), h("td", { class: "num" }, fmtN(r.antal)), h("td", {}, r.observator || "")))))));
    }
  } else if (d.liste === "dk") box.append(notice("Arten er ikke koblet til en art i DOFbasen.", "info"));
  if (d.fotos?.length) box.append(title("Fotos"), photoGrid(d.fotos));
  return h("div", {}, headline(d.navn), box);
}

