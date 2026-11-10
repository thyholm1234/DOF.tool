// Krydslisterne er samlet med Lister (#/lister/…). Gamle adresser (#/krydslister/…) sendes videre til samme side dér.
export async function render(view, [sub]) {
  const q = location.hash.split("?")[1];
  location.replace(`#/lister${sub ? `/${sub}` : ""}${q ? `?${q}` : ""}`);
}
