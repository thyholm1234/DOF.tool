// Tur: /indtast/tur/ny og /indtast/tur/<id>/ret = formular (tur-form.js); /indtast/tur/<id> = turens observationer (tur-view.js).
// <id> er serverens id, eller lokal/<client_id> for en tur, der endnu ikke er sendt (offline).
import { api, h, errorBox, requireLogin } from "../core.js";
import { loadRef, openTrip, tripStore } from "../tur-data.js";
import { view } from "./tur-view.js";
import { form } from "./tur-form.js";

export async function render(page, [which, ret]) {
  if (requireLogin(page)) return;
  page.append(h("div", { class: "skeleton", style: { height: "260px" } }));
  let ref, trip, recent = { locations: [], species: [], adf: [] };
  try {
    ref = await loadRef();
    trip = await openTrip(which, ref);
    recent = await api("/api/reports/recent").catch(() => recent);
  } catch (err) { page.replaceChildren(errorBox(err), h("p", {}, h("a", { href: "#/indtast" }, "← Mine indtastninger"))); return; }
  const store = tripStore(trip.T, trip.files);
  return which === "ny" || ret ? form(page, ref, store, recent, which === "ny") : view(page, ref, store, recent);
}
