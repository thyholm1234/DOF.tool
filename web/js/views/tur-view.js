// Tur: kun denne turs observationer. Her tilføjes og rettes observationer (+1 / +10 på hver), og ynglepar.
// Selve turens oplysninger redigeres på sin egen side (tur-form.js).
import { h, icon, fmtN, fmtDate, tag, toast, store, state, KAT, todayISO } from "../core.js";
import { tracker, nowHM, finishTrack } from "../tur-data.js";
import { quickSpecies } from "../tur-quick.js";
import { isApp } from "../native.js";
import { newId } from "../offline.js";
import { openObsSheet, openPairSheet } from "../obs-sheet.js";
import { voiceSupported, voiceContext, listen, modelStatus, installModel, prepareModel } from "../voice.js";
import { tabs } from "../tur-ui.js";

const STATE = { ok: ["Gemt", "ok"], gemmer: ["Gemmer …", "busy"], offline: ["Ikke sendt – sendes, når der er net", "off"], fejl: ["Kunne ikke gemmes", "err"] };

export function view(page, ref, store_, recent) {
  const { T, files } = store_;
  const sp = (artnr) => ref.species.find(s => s.artnr === artnr);
  const adfText = (code) => ref.codes.adf.find(a => a.code === code)?.text;
  const reuse = { on: !!store("turreuse") };

  // --- status for lagring
  const chip = h("span", { class: "sync-chip" });
  const drawChip = () => { const [t, k] = STATE[store_.status] || STATE.ok; chip.className = `sync-chip ${k}`; chip.textContent = t; chip.title = store_.message || ""; };
  drawChip();
  const unsub = store_.subscribe(() => { drawChip(); drawRows(); drawSpeech(); });

  // --- observationer
  const obsList = h("div", { class: "obs-list" });
  const countEl = h("small", { class: "muted" });
  const lastAdf = () => T.observations.at(-1)?.adfkode || "";
  const rowState = (o) => !o.id || store_.status !== "ok" ? h("span", { class: "obs-state off", title: "Ikke gemt på serveren endnu", html: icon("refresh") })
    : h("span", { class: "obs-state ok", title: "Gemt", html: icon("check") });
  const row = (o) => {
    const num = h("b", { class: "num" }, fmtN(o.antal));
    const bump = (d) => () => { o.antal = (Number(o.antal) || 0) + d; num.textContent = fmtN(o.antal); store_.save(); };
    return h("div", { class: `obs-row ${o.klass === "SU" ? "SU" : o.klass === "SUB" ? "SUB" : ""}` },
      h("button", { type: "button", class: "obs-main", onclick: () => edit(o) },
        h("span", { class: "obs-title" }, h("b", {}, o.art || sp(o.artnr)?.navn), o.klass && o.klass !== "Alm" ? tag(o.klass, KAT[o.klass] || o.klass) : null, o.hemmelig ? h("span", { class: "tag plain", html: icon("lock") }) : null),
        h("small", { class: "muted" }, [adfText(o.adfkode), o.obstidfra, (o._media || files[o.client_id]?.length) ? "📷" : null].filter(Boolean).join(" · "))),
      num,
      h("button", { type: "button", class: "btn sm accent", onclick: bump(1) }, "+1"),
      h("button", { type: "button", class: "btn sm", onclick: bump(10) }, "+10"),
      rowState(o));
  };
  const drawRows = () => {
    obsList.replaceChildren(...(T.observations.length ? [...T.observations].reverse().map(row) : [h("p", { class: "muted", style: { margin: 0 } }, "Ingen observationer endnu – tryk på + Observation.")]));
    countEl.textContent = `${T.observations.length} observation${T.observations.length === 1 ? "" : "er"} · ${new Set(T.observations.map(o => o.artnr)).size} arter`;
  };

  // isNew: o er udfyldt på forhånd (tale-indtastning), men endnu ikke lagt på turen
  const edit = (o = null, isNew = false) => openObsSheet({
    ref, recent, T, obs: o, files: o && !isNew ? files[o.client_id] || [] : [], adfDefault: reuse.on ? lastAdf() : "", guideDefault: T.observations.some(x => x.retningslinjer),
    onSave: (O, newFiles) => {
      const i = o ? T.observations.findIndex(x => x.client_id === o.client_id) : -1;
      if (i >= 0) T.observations[i] = O; else T.observations.push(O);
      if (newFiles.length) files[O.client_id] = newFiles; else delete files[O.client_id];
      for (const e of T.speech) if (e.pending && e.pend_cid === O.client_id) { e.pending = false; e.obs_cid = O.client_id; delete e.pend_cid; }
      store_.save(); drawRows(); drawSpeech();
    },
    onDelete: () => { T.observations = T.observations.filter(x => x.client_id !== o.client_id); delete files[o.client_id]; store_.save(); drawRows(); },
  });

  // --- ynglepar
  const pairList = h("div", { class: "obs-list" });
  const pairCard = h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "Ynglepar"), pairList);
  const addPair = (p = null) => openPairSheet({
    ref, recent, T, pair: p,
    onSave: (P) => { const i = p ? T.breeding_pairs.findIndex(x => x.client_id === p.client_id) : -1; if (i >= 0) T.breeding_pairs[i] = P; else T.breeding_pairs.push(P); store_.save(); drawPairs(); },
    onDelete: () => { T.breeding_pairs = T.breeding_pairs.filter(x => x.client_id !== p.client_id); store_.save(); drawPairs(); },
  });
  const drawPairs = () => {
    pairCard.classList.toggle("hide", !T.breeding_pairs.length);
    pairList.replaceChildren(...T.breeding_pairs.map(p => h("button", { type: "button", class: "obs-row pair", onclick: () => addPair(p) },
      h("span", { class: "obs-main" }, h("span", { class: "obs-title" }, h("b", {}, p.art || sp(p.artnr)?.navn), h("small", { class: "muted" }, `${p.antal} par`)), h("small", { class: "muted" }, adfText(p.adfkode) || "")))));
  };

  // --- forstyrrelser (redigeres i turens data)
  const dist = T.disturbances.length ? h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "Forstyrrelser"),
    ...T.disturbances.map(d => h("div", {}, h("b", {}, ref.forstyrrelser.find(x => x.id === d.type_id)?.navn || "Forstyrrelse"),
      h("small", { class: "muted" }, [d.antal != null ? ` · ${d.antal} stk.` : "", d.noter ? ` · ${d.noter}` : ""].join(""))))) : null;

  // Afslut tur: sluttid (nu, hvis den mangler), ruten stoppes og beskæres til turens tidsrum, turen gemmes – og så tilbydes
  // de almindelige arter på lokaliteten, som endnu ikke står på turen
  const finishBtn = h("button", { class: "btn danger solid tur-finish", type: "button", onclick: async () => {
    if (!T.turtidtil && T.dato === todayISO()) T.turtidtil = nowHM();
    finishTrack(T);
    finishBtn.disabled = true;
    const status = await store_.saveNow();
    finishBtn.disabled = false;
    if (status === "fejl") return;
    if (status === "offline") toast("Ingen forbindelse – turen er gemt på enheden og sendes automatisk", "ok");
    await quickSpecies(T, ref, (added) => { T.observations.push(...added); store_.save(); drawRows(); });
  } }, "Afslut tur");
  // GPS-ruten styres under turens data (tur-form.js); mens den optages, gemmes turen her mindst hvert minut
  if (tracker.cid === T.client_id) T.gps_track = tracker.points;
  let lastSave = 0;
  const onPoint = () => { if (tracker.cid === T.client_id && Date.now() - lastSave > 60000) { lastSave = Date.now(); store_.save(); } };
  tracker.listeners.add(onPoint);

  // --- tale-indtastning (kun i appen): hold knappen inde på fanen Tale, sig fx «2 bomlærker syd», slip.
  // Hver indtalt post står for sig under Tale (kan slettes eller lægges antal til), mens den almindelige liste viser summen:
  // en post lægges til den observation på turen, der har samme art og adfærd (ellers oprettes den).
  T.speech ||= [];
  const voiceStatus = h("div", { class: "voice-status hide", "aria-live": "polite" });
  // Fast felt nederst på fanen Tale: hold nede og tal. Det flytter sig ikke (position: fixed), og siden scroller ikke under fingeren.
  const padLabel = h("span", {}, "Hold nede og tal");
  const voiceBtn = h("button", { type: "button", class: "voice-pad disabled", "aria-label": "Hold nede og tal", title: "Hold nede og sig fx «2 bomlærker syd»" },
    h("span", { class: "voice-pad-icon", html: icon("mic") }), padLabel);
  const speechList = h("div", { class: "obs-list speech-feed" });
  // Feedet ruller i sin egen boks, der slutter lige over mikrofonfeltet – så glider intet ind under feltet
  const speechScroll = h("div", { class: "speech-scroll" });
  let vCtx = null, stopVoice = null, vBusy = false, voiceReady = false, curTab = "obs";
  const say = (...parts) => { voiceStatus.replaceChildren(...parts); voiceStatus.classList.remove("hide"); toBottom(); };
  const describe = (e) => `${fmtN(e.antal)} ${e.art}${e.adfkode ? ` · ${adfText(e.adfkode) || e.adfkode}` : ""}`;
  const syncVoiceBtn = () => {
    voiceBtn.classList.toggle("disabled", !voiceReady);
    if (!voiceBtn.classList.contains("on")) padLabel.textContent = voiceReady ? "Hold nede og tal" : "Talemodellen mangler – se ovenfor";
  };
  const fitFeed = () => {
    if (curTab !== "tale") { speechScroll.style.height = ""; return; }
    window.scrollTo(0, 0);
    // Feedet fylder op til fortolkningsboksen (hvis den vises), som igen står lige over mikrofonfeltet – begge er faste.
    // Posterne står nederst i feedet (CSS), så den nyeste altid er lige over boksen
    const stop = (voiceStatus.classList.contains("hide") ? voiceBtn : voiceStatus).getBoundingClientRect().top;
    speechScroll.style.height = `${Math.max(120, Math.floor(stop - speechScroll.getBoundingClientRect().top - 8))}px`;
  };
  const toBottom = () => requestAnimationFrame(() => { fitFeed(); speechScroll.scrollTop = speechScroll.scrollHeight; });
  window.addEventListener("resize", fitFeed);

  const findObs = (artnr, adf) => T.observations.findLast(o => o.artnr === artnr && (o.adfkode || "") === (adf || ""));
  const obsOf = (e) => e.obs_cid && T.observations.find(o => o.client_id === e.obs_cid);
  const obsFor = (e, antal, cid = newId()) => ({ client_id: cid, artnr: e.artnr, art: e.art, klass: e.klass, antal, antalmetode: 1, maerker: [],
    grundlag: ["set"], sikkerhed: "sikker", status: "ukendt", obstidfra: e.t, adfkode: e.adfkode, retningslinjer: false, hemmelig: false, skjul_position: false });
  // SU/SUB kræver sikkerhed, grundlag og retningslinjer, og uden adfærd mangler et påkrævet felt: så udfyldes observationen i formularen
  const needsForm = (e) => !e.adfkode || ["SU", "SUB"].includes(e.klass);

  /** En ny indtalt post. Lægges til en eksisterende observation med samme art og adfærd, ellers oprettes en ny (eller venter på formularen). */
  const addEntry = (it, adfkode) => {
    const e = { id: newId(), artnr: it.art.artnr, art: it.art.navn, klass: it.art.klass, antal: it.antal, adfkode, t: nowHM(), ts: Date.now(), heard: it.heard, obs_cid: null, pending: false };
    const O = findObs(e.artnr, e.adfkode);
    if (O) { O.antal = (Number(O.antal) || 0) + e.antal; e.obs_cid = O.client_id; }
    else if (needsForm(e)) e.pending = true;
    else { const N = obsFor(e, e.antal); T.observations.push(N); e.obs_cid = N.client_id; }
    T.speech.push(e);
    return e;
  };
  const bumpEntry = (e, d) => {
    e.antal += d;
    const O = obsOf(e);
    if (O) O.antal = (Number(O.antal) || 0) + d;
    store_.save(); drawRows(); drawSpeech();
  };
  const removeEntry = (e) => {
    const O = obsOf(e);
    if (O) {
      O.antal = (Number(O.antal) || 0) - e.antal;
      if (O.antal <= 0) { T.observations = T.observations.filter(x => x !== O); delete files[O.client_id]; }
    }
    T.speech = T.speech.filter(x => x !== e);
    store_.save(); drawRows(); drawSpeech();
  };
  /** Poster der mangler oplysninger (SU/SUB eller ingen adfærd): åbn formularen med summen af dem med samme art og adfærd. */
  const completeGroup = (e) => {
    const group = T.speech.filter(x => x.pending && x.artnr === e.artnr && x.adfkode === e.adfkode);
    const sum = group.reduce((t, x) => t + x.antal, 0);
    const O = findObs(e.artnr, e.adfkode);
    if (O) {  // der er kommet en observation med samme art og adfærd siden
      O.antal = (Number(O.antal) || 0) + sum;
      group.forEach(x => { x.pending = false; x.obs_cid = O.client_id; delete x.pend_cid; });
      store_.save(); drawRows(); drawSpeech();
      return;
    }
    const cid = group[0].pend_cid || newId();
    group.forEach(x => { x.pend_cid = cid; });
    edit(obsFor(group[0], sum, cid), true);
  };

  const OLD_MS = 2 * 60 * 1000;
  const isOld = (e) => !e.ts || Date.now() - e.ts > OLD_MS;
  const speechRow = (e) => {
    const O = obsOf(e);
    return h("div", { class: `obs-row ${e.klass === "SU" ? "SU" : e.klass === "SUB" ? "SUB" : ""} ${isOld(e) ? "speech-old" : ""}`, "data-ts": e.ts || 0 },
      h("span", { class: "obs-main" },
        h("span", { class: "obs-title" }, h("b", {}, e.art), e.klass && e.klass !== "Alm" ? tag(e.klass, KAT[e.klass] || e.klass) : null,
          e.pending ? h("span", { class: "tag bemaerk" }, "mangler oplysninger") : null),
        h("small", { class: "muted" }, [adfText(e.adfkode) || (e.adfkode ? e.adfkode : "ingen adfærd"), e.t, !e.pending && !O ? "observationen er slettet" : null].filter(Boolean).join(" · "))),
      h("b", { class: "num" }, fmtN(e.antal)),
      e.pending ? h("button", { type: "button", class: "btn sm accent", onclick: () => completeGroup(e) }, "Udfyld")
        : [h("button", { type: "button", class: "btn sm accent", onclick: () => bumpEntry(e, 1) }, "+1"), h("button", { type: "button", class: "btn sm", onclick: () => bumpEntry(e, 10) }, "+10")],
      h("button", { type: "button", class: "btn sm ghost danger", "aria-label": "Slet", html: icon("trash"), onclick: () => removeEntry(e) }));
  };
  const drawSpeech = () => {
    speechList.replaceChildren(...(T.speech.length ? T.speech.map(speechRow)
      : [h("p", { class: "muted", style: { margin: 0 } }, "Ingen indtalte poster endnu – hold knappen nede og sig fx «2 bomlærker syd».")]));
  };
  const fadeTimer = setInterval(() => {
    for (const r of speechList.children) if (r.dataset.ts != null) r.classList.toggle("speech-old", Date.now() - Number(r.dataset.ts) > OLD_MS);
  }, 10000);

  const pressStart = async (e) => {
    e.preventDefault();
    if (!voiceReady || stopVoice || vBusy) return;
    try { voiceBtn.setPointerCapture(e.pointerId); } catch { /* ingen aktiv pointer (fx tastatur) */ }  // fingeren må glide uden at stoppe optagelsen
    padLabel.textContent = "Lytter … slip for at tilføje";
    vBusy = true;
    voiceBtn.classList.add("on");
    navigator.vibrate?.(25);
    vCtx ||= voiceContext(ref.species, ref.codes.adf);
    try { stopVoice = await listen(vCtx, { level: (v) => voiceBtn.style.setProperty("--lvl", v.toFixed(2)) }); }
    catch (err) { voiceBtn.classList.remove("on"); say(h("span", { class: "err" }, err.message || "Mikrofonen kunne ikke startes")); }
    finally { vBusy = false; }
    if (stopVoice && !voiceBtn.classList.contains("on")) pressEnd();  // sluppet mens optagelsen startede
  };
  const pressEnd = async () => {
    voiceBtn.classList.remove("on");
    syncVoiceBtn();
    const stop = stopVoice;
    if (!stop) return;
    stopVoice = null;
    say(h("span", { class: "muted" }, "Tolker …"));
    voiceBtn.style.removeProperty("--lvl");
    let res;
    try { res = await stop(); } catch (err) { say(h("span", { class: "err" }, err.message || "Talen kunne ikke genkendes")); return; }
    const { items, heard } = res;
    if (!heard && !items.length) { say(h("span", { class: "muted" }, "Hørte ingenting – hold knappen inde, mens du taler.")); return; }
    const dflt = reuse.on ? lastAdf() : "";  // «genanvend adfærd» gælder fugle uden talt adfærd
    const added = [], missed = [];
    for (const it of items) {
      if (!it.art) { missed.push(it.heard); continue; }
      added.push(addEntry(it, it.adfkode || dflt));
    }
    if (added.length) { store_.save(); drawRows(); drawSpeech(); navigator.vibrate?.([20, 60, 20]); }
    toBottom();
    const undo = added.length ? h("button", { type: "button", class: "btn sm ghost", onclick: () => {
      [...added].reverse().forEach(removeEntry);
      say(h("span", { class: "muted" }, "Fortrudt."));
    } }, "Fortryd") : null;
    say(h("div", { class: "stack", style: { gap: "4px" } },
      ...added.map(e => h("div", {}, h("b", {}, e.pending ? "Udfyld resten: " : "Tilføjet: "), describe(e))),
      ...missed.map(t => h("div", { class: "err" }, `Forstod ikke: «${t}»`)),
      !items.length ? h("div", { class: "err" }, `Forstod ikke: «${heard}»`) : null,
      h("small", { class: "muted" }, `Hørt: «${heard}»`)), undo);
    const pending = added.find(e => e.pending);
    if (pending) completeGroup(pending);
  };
  voiceBtn.addEventListener("pointerdown", pressStart);
  for (const ev of ["pointerup", "pointercancel"]) voiceBtn.addEventListener(ev, () => { if (voiceBtn.classList.contains("on")) pressEnd(); });
  voiceBtn.addEventListener("contextmenu", (e) => e.preventDefault());

  // Talemodellen ligger på telefonen og hentes én gang fra serveren (ca. 355 MB – helst over wi-fi)
  const offerModel = (st) => {
    const mb = Math.round((st.manifest?.size || 0) / 1e6);
    const btn = h("button", { type: "button", class: "btn sm", onclick: async () => {
      btn.disabled = true;
      try {
        await installModel(st.manifest, (done, total) => { btn.textContent = `Henter talemodel … ${Math.floor((done / total) * 100)} %`; });
        voiceStatus.classList.add("hide"); toBottom(); voiceReady = true; syncVoiceBtn(); prepareModel();
      } catch (err) { btn.disabled = false; btn.textContent = "Prøv igen"; toast(err.message, "err"); }
    } }, st.installed ? `Opdatér talemodel (${mb} MB)` : `Hent talemodel (${mb} MB)`);
    say(h("div", { class: "row", style: { gap: "8px", alignItems: "center" } }, btn,
      h("small", { class: "muted" }, "Tale-indtastning genkender talen på telefonen. Modellen hentes én gang – brug gerne wi-fi.")));
  };
  const showTale = isApp() && !!state.user?.voice;
  if (showTale) {
    if (!voiceSupported()) say(h("span", { class: "muted" }, "Tale-indtastning kræver den nyeste version af appen – installér den opdaterede app."));
    else modelStatus().then((st) => {
      if (st.installed) { voiceReady = true; syncVoiceBtn(); prepareModel(); }
      if (st.manifest && (!st.installed || !st.current)) offerModel(st);
      else if (!st.manifest && !st.installed) say(h("span", { class: "muted" }, "Tale-indtastning: talemodellen ligger ikke på serveren endnu (upload eller byg den under Admin → Drift)."));
    }).catch((err) => say(h("span", { class: "err" }, `Talemodellen kunne ikke undersøges: ${err.message || err}`)));
  }

  const reuseBox = h("input", { type: "checkbox", checked: reuse.on, onchange: (e) => { reuse.on = e.target.checked; store("turreuse", reuse.on); } });
  const time = [T.turtidfra, T.turtidtil].filter(Boolean).join(" – ");
  const obsCard = h("div", { class: "card stack" }, h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, "Observationer"), countEl),
    h("label", { class: "check" }, reuseBox, h("span", {}, "Genanvend adfærd")), obsList);
  // Tale: kun feedet med de indtalte poster (nyeste nederst, ved mikrofonen) – ingen ynglepar eller knapper
  const taleCard = h("div", { class: "stack" }, speechScroll);
  speechScroll.append(speechList);
  const extras = h("div", { class: "tur-extra stack" }, pairCard, dist);
  const tabsEl = showTale ? tabs([["obs", "Observationer", obsCard], ["tale", "Tale", taleCard]], {
    onChange: (k) => { curTab = k; page.classList.toggle("tale-mode", k === "tale"); syncVoiceBtn(); if (k === "tale") toBottom(); } }) : null;
  tabsEl?.panels.classList.add("tur-panels");
  page.replaceChildren(
    h("div", { class: "page-head" }, h("div", {}, h("p", { style: { margin: 0 } }, h("a", { href: "#/indtast" }, "← Mine indtastninger")),
      h("h1", { style: { marginTop: "4px" } }, T.lok || "Tur"),
      h("p", { class: "lead tur-date", html: `${icon("calendar")} ` }, [fmtDate(T.dato), time].filter(Boolean).join(" · "))),
      h("span", { class: "spacer" }), chip,
      h("div", { class: "row tur-actions" },
        h("a", { class: "btn tur-edit", href: `#/indtast/tur/${T.server_id || `lokal/${T.client_id}`}/ret`, html: `${icon("edit")} Redigér tur` }),
        finishBtn)),
    T.on_behalf_of ? h("div", { class: "notice warn mb" }, `Indtastet på vegne af ${T.on_behalf_of}.`) : null,
    showTale ? tabsEl.bar : null, showTale ? tabsEl.panels : obsCard,
    extras,
    // Ynglepar (1/3) og Observation (2/3) som store felter i samme stil som mikrofonfeltet på fanen Tale
    h("div", { class: "tur-bar" },
      h("button", { class: "tur-pad", type: "button", onclick: () => addPair() }, h("span", { class: "tur-pad-icon", html: icon("home") }), "Ynglepar"),
      h("button", { class: "tur-pad primary", type: "button", onclick: () => edit() }, h("span", { class: "tur-pad-icon", html: icon("plus") }), "Observation")),
    showTale ? [voiceStatus, voiceBtn] : null);
  drawRows(); drawSpeech(); drawPairs();
  return () => { unsub(); tracker.listeners.delete(onPoint); clearInterval(fadeTimer); window.removeEventListener("resize", fitFeed); page.classList.remove("tale-mode"); stopVoice?.(); };
}
