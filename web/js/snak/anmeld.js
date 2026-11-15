// Anmeld et opslag eller en kommentar (docs/snak-modul-spec.md §2.3, §8.2): grund + valgfri forklaring i en native <dialog>.
import { api, h, modal, seg, toast } from "../core.js";

const REASONS = [["chikane", "Chikane eller nedsættende"], ["spam", "Spam eller reklame"], ["forkert", "Forkert eller vildledende"], ["andet", "Andet"]];

/** reportDialog("post" | "comment", id, onDone) */
export function reportDialog(kind, id, onDone) {
  let reason = "chikane";
  const note = h("input", { type: "text", maxlength: 500, placeholder: "Evt. en forklaring (valgfri)", "aria-label": "Forklaring" });
  const path = kind === "post" ? `/api/snak/${id}/anmeld` : `/api/snak/kommentarer/${id}/anmeld`;
  const m = modal(kind === "post" ? "Anmeld opslag" : "Anmeld kommentar", h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    try {
      const r = await api(path, { method: "POST", body: { reason, note: note.value } });
      m.close();
      toast(r.hidden ? "Tak – indholdet er skjult, til en moderator har set på det" : "Tak – en moderator ser på det");
      onDone?.(r);
    } catch (err) { toast(err.message, "err"); }
  } },
  h("p", { class: "muted" }, "Anmeldelser går til moderatorerne. Indholdet skjules automatisk, hvis flere anmelder det."),
  seg(REASONS, reason, (v) => { reason = v; }), note,
  h("div", { class: "row" }, h("button", { class: "btn primary", type: "submit" }, "Send anmeldelse"))));
}
