// Er portalen synlig for brugeren? I appen melder Android selv, når appen lægges i baggrunden eller kommer frem igen
// (MainActivity → window.dofLifecycle), fordi WebView'ens visibilitychange ikke kommer pålideligt på alle telefoner.
// I browseren bruges visibilitychange alene. Bruges til at spare strøm: livestrøm og timere hviler, mens siden er skjult.

let hiddenSince = document.visibilityState === "hidden" ? Date.now() : null;
const subs = new Set();

function set(hidden) {
  if (hidden === (hiddenSince !== null)) return;
  const away = hiddenSince === null ? 0 : Date.now() - hiddenSince;
  hiddenSince = hidden ? Date.now() : null;
  for (const fn of [...subs]) { try { fn(!hidden, away); } catch { /* én lytter må ikke vælte de andre */ } }
}

document.addEventListener("visibilitychange", () => set(document.visibilityState === "hidden"));
window.dofLifecycle = (s) => set(s === "hidden");

/** Er siden synlig lige nu? */
export const isVisible = () => hiddenSince === null;

/** fn(visible, awayMs) ved hvert skift; awayMs = hvor længe siden var skjult (0, når den skjules). Returnerer afmeld. */
export function onVisibility(fn) { subs.add(fn); return () => subs.delete(fn); }

/** setInterval, der kun kører, mens siden er synlig. */
export function visibleInterval(fn, ms) {
  const id = setInterval(() => { if (isVisible()) fn(); }, ms);
  return () => clearInterval(id);
}
