"""Appens ikon – et vibehoved (sort/hvid på rav) – i alle størrelser til web (web/icons) og Android (app/android).
Kør: python3 tools/make_icons.py  (kræver rsvg-convert)."""
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
AMBER, INK, PAPER = "#f0b13a", "#15181c", "#fbf8f1"
# Hovedet vender mod venstre; halsen går helt ned (til 900), så den også når kanten, når fuglen skaleres ned
HEAD = ("M178 236 C185 190 225 160 280 158 C335 156 372 185 380 228 C388 270 372 300 362 330 "
        "C352 362 356 420 372 560 L376 900 L190 900 L196 560 C212 430 214 370 205 332 C190 314 176 292 176 262 Z")
BIRD = f"""
  <path d="M352 196 C400 176 446 140 462 84 C466 70 458 62 450 70 C440 120 404 160 346 180 Z" fill="{INK}"/>
  <path d="M344 182 C384 160 414 124 420 82 C422 70 414 66 408 74 C402 114 378 146 336 168 Z" fill="{INK}"/>
  <path d="{HEAD}" fill="{PAPER}"/>
  <g clip-path="url(#h)" fill="{INK}">
    <path d="M150 250 C210 228 300 222 400 248 L400 120 L150 120 Z"/>
    <path d="M176 262 C215 262 255 274 300 292 C318 299 332 308 342 320 C315 312 286 306 255 298 C225 291 198 284 176 282 Z"/>
    <path d="M150 312 C200 330 260 352 400 372 L400 900 L150 900 Z"/>
  </g>
  <path d="M182 240 L126 262 L180 272 Z" fill="{INK}"/>
  <circle cx="230" cy="254" r="13" fill="{INK}"/>
  <circle cx="234.5" cy="249.5" r="3.6" fill="{PAPER}"/>"""


def svg(bg: str | None = AMBER, shape: str = "rounded", scale: float = 1.0, center: tuple = (256, 280), to: tuple = (256, 280)) -> str:
    """shape: rounded (rx 112), square (fuld flade) eller circle. scale < 1 trækker fuglen ind mod midten (sikkerhedszoner);
    punktet center på fuglen havner i punktet to i billedet."""
    clip = {"rounded": '<rect width="512" height="512" rx="112"/>', "square": '<rect width="512" height="512"/>',
            "circle": '<circle cx="256" cy="256" r="256"/>'}[shape]
    fill = "" if bg is None else f'<rect width="512" height="512" fill="{bg}"/>'
    t = f"translate({to[0] - center[0] * scale:.1f} {to[1] - center[1] * scale:.1f}) scale({scale})"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><defs><clipPath id="o">{clip}</clipPath>'
            f'<clipPath id="h"><path d="{HEAD}"/></clipPath></defs><g clip-path="url(#o)">{fill}'
            f'<g transform="{t}">{BIRD}</g></g></svg>')


def png(src: str, out: pathlib.Path, size: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), "-o", str(out)], input=src.encode(), check=True)


def png_rgb(src: str, out: pathlib.Path, size: int) -> None:
    """Som png(), men kontrollerer, at filen er uden alfakanal (App Store afviser app-ikoner med alfa). rsvg-convert
    skriver RGB uden alfa, når billedet er helt uigennemsigtigt."""
    png(src, out, size)
    assert out.read_bytes()[25] == 2, f"{out} har alfakanal"


web = ROOT / "web/icons"
(web / "icon.svg").write_text(svg() + "\n")
png(svg(), web / "icon-192.png", 192)
png(svg(), web / "icon-512.png", 512)
png(svg(shape="square"), web / "apple-icon-180.png", 180)       # iOS runder selv hjørnerne
png(svg(shape="square", scale=0.78), web / "maskable-512.png", 512)  # indholdet inden for den midterste cirkel (80 %)

res = ROOT / "app/android/app/src/main/res"
if res.is_dir():
    for d, n in {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}.items():
        png(svg(), res / f"mipmap-{d}/ic_launcher.png", n)
        png(svg(shape="circle", scale=0.8, center=(296, 250), to=(256, 256)), res / f"mipmap-{d}/ic_launcher_round.png", n)
        png(svg(bg=None, shape="square", scale=0.62, center=(296, 240), to=(256, 256)), res / f"mipmap-{d}/ic_launcher_foreground.png", n * 108 // 48)  # 66/108 dp sikkerhedszone
    (res / "values/ic_launcher_background.xml").write_text(
        f'<?xml version="1.0" encoding="utf-8"?>\n<resources>\n    <color name="ic_launcher_background">{AMBER.upper()}</color>\n</resources>\n')

ios = ROOT / "app/ios/App/App/Assets.xcassets/AppIcon.appiconset/AppIcon-512@2x.png"
if ios.parent.is_dir():
    png_rgb(svg(shape="square"), ios, 1024)  # iOS runder selv hjørnerne
