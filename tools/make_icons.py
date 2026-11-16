"""Appens ikon – den oprindelige fuglesilhuet i rav på grafit (som mærket i sidehovedet med Console-temaet) – i alle
størrelser til web (web/icons), Android og iOS (app/). Kør: python3 tools/make_icons.py  (kræver rsvg-convert)."""
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
AMBER, INK = "#f7b83d", "#14181b"  # --brand og --surface i det mørke tema
# Fuglen fra web/index.html (32×32), skaleret til 512
BIRD = (f'<g transform="scale(16)"><path d="M7 19c3.5-1 6-3.6 7.6-7.2.4-.9 1.6-1 2.1-.1l1 1.8 4.6-1.3c.6-.2 1 .5.6 1l-3.2 '
        f'3.4c-1.4 4.3-5 6.9-9.6 6.9H7.6c-.9 0-1.2-1.2-.6-1.5z" fill="{AMBER}"/><circle cx="17.6" cy="13.2" r=".9" fill="{INK}"/></g>')


def svg(bg: str | None = INK, shape: str = "rounded", scale: float = 1.0, center: tuple = (242, 274), to: tuple = (256, 256)) -> str:
    """shape: rounded (rx 112), square (fuld flade) eller circle. scale < 1 trækker fuglen ind mod midten (sikkerhedszoner);
    punktet center på fuglen havner i punktet to i billedet."""
    clip = {"rounded": '<rect width="512" height="512" rx="112"/>', "square": '<rect width="512" height="512"/>',
            "circle": '<circle cx="256" cy="256" r="256"/>'}[shape]
    fill = "" if bg is None else f'<rect width="512" height="512" fill="{bg}"/>'
    t = f"translate({to[0] - center[0] * scale:.1f} {to[1] - center[1] * scale:.1f}) scale({scale})"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><defs><clipPath id="o">{clip}</clipPath>'
            f'</defs><g clip-path="url(#o)">{fill}'
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
png(svg(shape="square"), web / "maskable-512.png", 512)  # indholdet inden for den midterste cirkel (80 %)

res = ROOT / "app/android/app/src/main/res"
if res.is_dir():
    for d, n in {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}.items():
        png(svg(), res / f"mipmap-{d}/ic_launcher.png", n)
        png(svg(shape="circle"), res / f"mipmap-{d}/ic_launcher_round.png", n)
        png(svg(bg=None, shape="square", scale=0.9), res / f"mipmap-{d}/ic_launcher_foreground.png", n * 108 // 48)  # 66/108 dp sikkerhedszone
    (res / "values/ic_launcher_background.xml").write_text(
        f'<?xml version="1.0" encoding="utf-8"?>\n<resources>\n    <color name="ic_launcher_background">{INK.upper()}</color>\n</resources>\n')

ios = ROOT / "app/ios/App/App/Assets.xcassets/AppIcon.appiconset/AppIcon-512@2x.png"
if ios.parent.is_dir():
    png_rgb(svg(shape="square"), ios, 1024)  # iOS runder selv hjørnerne
