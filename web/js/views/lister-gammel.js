// Gamle adresser for Krydslister og Matrikel (#/krydslister/…, #/matrikel?…) – sendes videre til fanerne under Lister.
import { legacy } from "./lister.js";

export function render(view, [path, sub]) {
  location.replace(legacy(path, sub));
}
