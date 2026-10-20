// Delt moderation: udelukkelse af en bruger fra at kommentere. Bruges fra Moderation → Kommentarer og direkte fra
// kommentarerne i en tråd. x er en kommentar ({ user_id, navn, obserkode, id, body }) eller en bruger uden id/body.
import { api, h, seg, toast, modal } from "./core.js";

export function banForm(x, done) {
  let days = 7;
  const reason = h("textarea", { maxlength: 1000, required: true, placeholder: "Fx 'Gentagne nedsættende kommentarer om andre observatører'", style: { minHeight: "80px" } });
  const hide = h("input", { type: "checkbox", checked: true });
  const notify = h("input", { type: "checkbox", checked: true });
  const m = modal(`Udeluk ${x.navn || x.obserkode} fra at kommentere`, h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    try {
      await api(`/api/admin/bans/${x.user_id}`, { method: "PUT", body: { reason: reason.value, days: days || null, hide_comments: hide.checked, notify: notify.checked, comment_id: x.body ? x.id : null } });
      m.close(); toast(`${x.navn || x.obserkode} er udelukket`); done?.();
    } catch (err) { toast(err.message, "err"); }
  } },
    x.body ? h("blockquote", { class: "quote" }, x.body) : null,
    h("label", { class: "field" }, h("span", {}, "Årsag (påkrævet – vises for brugeren)"), reason),
    h("div", { class: "field" }, h("span", { class: "label" }, "Varighed"), seg([[1, "1 døgn"], [7, "7 dage"], [30, "30 dage"], [0, "Indtil videre"]], days, (v) => { days = Number(v); })),
    h("label", { class: "check" }, hide, h("span", {}, "Skjul brugerens kommentarer for andre, så længe udelukkelsen gælder")),
    h("label", { class: "check" }, notify, h("span", {}, "Giv brugeren besked (notifikation med årsagen)")),
    h("div", { class: "row" }, h("button", { class: "btn danger" }, "Udeluk"))));
}
