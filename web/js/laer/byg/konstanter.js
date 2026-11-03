export const SILS = ["spurvefugl", "mejse", "and", "svane", "maage", "rovfugl", "rovfugl_flugt", "hejre", "spaette", "svale", "vadefugl", "skarv", "due", "gaas"];
export const HUES = ["skov", "himmel", "sand", "rav", "sø", "hav", "eng", "skumring"];
export const TYPER = {
  Statiske: [["valg", "Multiple choice"], ["tekst", "Tekstsvar"], ["silhuet", "Silhuet (multiple choice)"], ["raekkefoelge", "Rækkefølge"], ["billedpunkt", "Klik i billedet"], ["refleksion", "Refleksion (uden facit)"]],
  "Foto og lyd": [["foto_art", "Foto: gæt arten"], ["foto_sammenlign", "Foto: hvilket er arten?"], ["lyd_art", "Lyd: gæt arten"]],
  DOFbasen: [["obs_odds", "Odds: hvilken er mest sandsynlig?"], ["obs_habitat", "Levested: hvad hører til her?"], ["obs_saeson", "Sæson: hvornår trækker de?"], ["obs_sjaelden", "Sjældenhed"], ["obs_status", "SU / SUB / almindelig"]],
};
export const TYPE_NAME = Object.fromEntries(Object.values(TYPER).flat());
export const GENERATOR = new Set(["foto_art", "foto_sammenlign", "lyd_art", "obs_odds", "obs_habitat", "obs_saeson", "obs_sjaelden", "obs_status"]);
