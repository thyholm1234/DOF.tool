// Enkle fuglesilhuetter tegnet som SVG (ingen fotos). Bruges som billeder i øvelser og som pladsholdere.
// Alle tegninger bruger viewBox 0 0 200 140 og farven "currentColor".

const S = {
  spurvefugl: {
    navn: "Spurvefugl (fx solsort)",
    g: `<ellipse cx="96" cy="78" rx="38" ry="24"/><circle cx="132" cy="58" r="15"/>
        <polygon points="144,54 162,58 144,62"/><polygon points="62,74 22,92 26,100 66,86"/>
        <rect x="92" y="98" width="3" height="20"/><rect x="104" y="98" width="3" height="20"/>
        <rect x="60" y="118" width="80" height="4" rx="2"/>`
  },
  mejse: {
    navn: "Mejse",
    g: `<ellipse cx="100" cy="80" rx="30" ry="26"/><circle cx="124" cy="60" r="17"/>
        <polygon points="139,58 150,61 139,64"/><polygon points="74,84 46,100 50,106 78,92"/>
        <rect x="96" y="102" width="3" height="14"/><rect x="106" y="102" width="3" height="14"/>
        <rect x="50" y="116" width="100" height="5" rx="2"/>`
  },
  and: {
    navn: "Svømmeand (fx gråand)",
    g: `<ellipse cx="96" cy="92" rx="58" ry="22"/><rect x="134" y="52" width="14" height="40" rx="7"/>
        <ellipse cx="148" cy="52" rx="17" ry="13"/><polygon points="160,50 184,56 160,60"/>
        <polygon points="40,86 22,76 30,96"/><rect x="10" y="108" width="180" height="3" rx="1.5" opacity=".5"/>`
  },
  svane: {
    navn: "Svane",
    g: `<ellipse cx="88" cy="96" rx="62" ry="22"/><path d="M128 92 C150 80 150 50 140 34 C134 24 146 16 154 24 L150 30 C146 28 144 30 148 40 C158 60 156 86 140 100 Z"/>
        <polygon points="154,22 172,30 152,30"/><rect x="10" y="114" width="180" height="3" rx="1.5" opacity=".5"/>`
  },
  maage: {
    navn: "Måge",
    g: `<ellipse cx="100" cy="74" rx="44" ry="18"/><circle cx="142" cy="58" r="12"/>
        <polygon points="152,56 172,60 152,64"/><polygon points="60,66 26,74 30,80 70,80"/>
        <rect x="96" y="90" width="3" height="26"/><rect x="108" y="90" width="3" height="26"/>`
  },
  rovfugl: {
    navn: "Siddende rovfugl (fx musvåge)",
    g: `<ellipse cx="100" cy="70" rx="27" ry="40"/><circle cx="104" cy="30" r="15"/>
        <path d="M117 28 L126 32 L120 40 Z"/><polygon points="88,104 112,104 108,128 92,128"/>
        <rect x="40" y="106" width="120" height="6" rx="3"/>`
  },
  rovfugl_flugt: {
    navn: "Svævende rovfugl",
    g: `<path d="M100 60 C80 50 50 40 10 52 C30 60 50 66 70 70 L90 74 L84 96 L116 96 L110 74 L130 70 C150 66 170 60 190 52 C150 40 120 50 100 60 Z"/>
        <circle cx="100" cy="58" r="9"/>`
  },
  hejre: {
    navn: "Hejre",
    g: `<ellipse cx="96" cy="66" rx="30" ry="16" transform="rotate(-20 96 66)"/>
        <path d="M118 56 C132 44 120 30 130 20 C134 16 142 18 140 24 C134 32 142 44 128 60 Z"/>
        <polygon points="138,18 170,22 138,26"/><polygon points="70,72 50,86 72,80"/>
        <rect x="92" y="76" width="3" height="54"/><rect x="102" y="76" width="3" height="54"/>`
  },
  spaette: {
    navn: "Spætte",
    g: `<rect x="128" y="0" width="22" height="140" rx="4" opacity=".55"/>
        <ellipse cx="112" cy="74" rx="16" ry="32"/><circle cx="108" cy="40" r="13"/>
        <polygon points="96,38 76,40 96,44"/><polygon points="114,100 124,128 108,104"/>`
  },
  svale: {
    navn: "Svale i flugt",
    g: `<path d="M100 70 C88 60 60 40 20 34 C50 50 70 62 86 74 L70 112 L92 84 L100 86 L108 84 L130 112 L114 74 C130 62 150 50 180 34 C140 40 112 60 100 70 Z"/>
        <circle cx="100" cy="66" r="8"/>`
  },
  vadefugl: {
    navn: "Vadefugl (fx strandskade)",
    g: `<ellipse cx="94" cy="70" rx="40" ry="18"/><circle cx="134" cy="54" r="12"/>
        <polygon points="144,52 182,62 144,60"/><polygon points="58,66 30,72 58,76"/>
        <rect x="92" y="84" width="3" height="40"/><rect x="104" y="84" width="3" height="40"/>`
  },
  skarv: {
    navn: "Skarv",
    g: `<ellipse cx="96" cy="80" rx="22" ry="36"/><path d="M100 48 C102 30 110 24 116 22 L120 28 C112 32 110 40 110 52 Z"/>
        <circle cx="116" cy="24" r="9"/><polygon points="122,22 146,26 124,30"/>
        <polygon points="86,112 106,112 100,132 92,132"/><rect x="40" y="116" width="120" height="6" rx="3"/>`
  },
  due: {
    navn: "Due",
    g: `<ellipse cx="96" cy="80" rx="40" ry="24"/><circle cx="130" cy="56" r="12"/>
        <polygon points="140,56 150,58 140,61"/><polygon points="60,76 24,84 28,94 64,90"/>
        <rect x="96" y="100" width="3" height="14"/><rect x="106" y="100" width="3" height="14"/>
        <rect x="40" y="114" width="120" height="4" rx="2"/>`
  },
  gaas: {
    navn: "Gås",
    g: `<ellipse cx="92" cy="80" rx="54" ry="24"/><rect x="128" y="34" width="12" height="50" rx="6"/>
        <ellipse cx="140" cy="34" rx="14" ry="10"/><polygon points="150,32 170,36 150,40"/>
        <rect x="88" y="100" width="4" height="22"/><rect x="102" y="100" width="4" height="22"/>`
  }
};

export const SILHOUETTES = Object.fromEntries(Object.entries(S).map(([k, v]) => [k, v.navn]));

export function silhouette(key, { cls = "", color = "currentColor", label = "" } = {}) {
  const s = S[key] || S.spurvefugl;
  const aria = label ? `role="img" aria-label="${label.replace(/"/g, "&quot;")}"` : `aria-hidden="true"`;
  return `<svg class="sil ${cls}" viewBox="0 0 200 140" ${aria} fill="${color}">${s.g}</svg>`;
}

// Farvede baggrunde der står i stedet for fotos.
export const HUES = {
  skov: "linear-gradient(160deg,#3d5a3f 0%,#20382a 60%,#142219 100%)",
  himmel: "linear-gradient(180deg,#9fb9cf 0%,#c9d8e2 55%,#e3e7df 100%)",
  sand: "linear-gradient(180deg,#a9c3d6 0%,#d9c9a0 55%,#c9a86a 100%)",
  rav: "radial-gradient(120% 90% at 30% 20%,#ffe6a8 0%,#f7c35a 45%,#e39b2f 100%)",
  sø: "linear-gradient(180deg,#3f6f8f 0%,#24506e 60%,#173a52 100%)",
  hav: "linear-gradient(180deg,#6f9aa3 0%,#4f7f8a 60%,#3b6670 100%)",
  eng: "linear-gradient(180deg,#c9d6a8 0%,#9db27a 60%,#6f8a52 100%)",
  skumring: "linear-gradient(180deg,#5d6b78 0%,#8f8a86 55%,#c2a98a 100%)"
};

export function hueOf(name) { return HUES[name] || name || HUES.eng; }
