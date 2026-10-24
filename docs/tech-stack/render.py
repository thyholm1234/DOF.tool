"""Indsæt diagram (SVG) og forbindelsestabel i doc.html og lav PDF'en med WeasyPrint."""
import re
import sys
from datetime import date

import weasyprint

MONTHS = ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december"]
commit, out = sys.argv[1], sys.argv[2]
d = date.today()
svg = open("_flow.svg").read()
svg = svg[svg.index("<svg"):]
svg = re.sub(r'<svg width="[^"]+" height="[^"]+"', "<svg", svg, count=1)  # skalér efter siden via viewBox
html = (open("doc.html").read().replace("{{SVG}}", svg).replace("{{EDGES}}", open("edges.html").read())
        .replace("{{DATE}}", f"{d.day}. {MONTHS[d.month - 1]} {d.year}").replace("{{COMMIT}}", commit))
weasyprint.HTML(string=html, base_url=".").write_pdf(out, metadata={"title": "DOF.tool – tech-stack"})
print("skrevet", out)
