// Tekstsiderne «Om krydslister» og «Krydsregler» (redigeres under Admin → Indstillinger).
import { api, h } from "../core.js";

const page = (slug, titel) => async () => {
  const d = await api(`/api/krydslister/sider/${slug}`);
  return h("div", { class: "stack" }, h("h2", {}, titel), d.html ? h("div", { class: "art-body", html: d.html }) : h("p", { class: "muted" }, "Teksten er ikke skrevet endnu."));
};
export const om = page("om", "Om krydslister");
export const regler = page("regler", "Krydsregler");
