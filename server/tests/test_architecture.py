"""Modulreglerne i docs/arkitektur.md: lagene må kun afhænge nedad, og SQL hører til i services.

Testen læser kildekoden (ast) og importerer intet, så den er hurtig og kræver ingen database.
"""

import ast
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1] / "portal"

# API-moduler uden SQL: de validerer, tjekker adgang og kalder services
SQL_FREE_API = ["api/geo.py", "api/calendar.py", "api/dku.py", "api/netfugl.py", "api/artikler.py", "api/content.py", "api/admin/artikler.py",
                "api/admin/indhold.py", "api/admin/lokaliteter.py"]
# Kendte afhængigheder på tværs af API-moduler/fra services til API (fra før reglen) – fjern dem herfra, når de flyttes.
# Nye må ikke komme til.
KNOWN_CROSS_IMPORTS = {
    ("api/privacy.py", "portal.api.share"), ("api/reports.py", "portal.api.learn"), ("api/position.py", "portal.api.geo"),
    ("api/trips.py", "portal.api.reports"), ("api/feed.py", "portal.api.social"), ("api/admin/moderation.py", "portal.api.social"),
    ("services/auth.py", "portal.api.social"),
}
ALLOWED_API_IMPORTS = ("portal.api.deps", "portal.api.admin._deps")


def _imports(path: Path) -> list[str]:
    out = []
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.ImportFrom) and n.module:
            out.append(n.module)
        elif isinstance(n, ast.Import):
            out += [a.name for a in n.names]
    return out


def _rel(p: Path) -> str:
    return p.relative_to(PORTAL).as_posix()


def test_domain_is_pure():
    for f in (PORTAL / "domain").glob("*.py"):
        for mod in _imports(f):
            assert not mod.startswith(("sqlalchemy", "fastapi", "redis", "httpx")), f"{_rel(f)} importerer {mod}"
            assert not mod.startswith("portal.") or mod.startswith("portal.domain"), f"{_rel(f)} importerer {mod}"


def test_no_new_cross_imports_into_api():
    found = set()
    for f in PORTAL.rglob("*.py"):
        rel = _rel(f)
        if rel.startswith("api/admin/") and rel != "api/admin/moderation.py":
            continue  # admin-pakken samler sine egne paneler
        for mod in _imports(f):
            if mod.startswith("portal.api.") and not mod.startswith(ALLOWED_API_IMPORTS) and (rel.startswith(("api/", "services/", "dof/", "domain/"))):
                if rel.startswith("api/admin/") and mod.startswith("portal.api.admin"):
                    continue
                found.add((rel, mod))
    new = found - KNOWN_CROSS_IMPORTS
    assert not new, f"nye afhængigheder til API-moduler (flyt den fælles kode til services): {sorted(new)}"
    gone = KNOWN_CROSS_IMPORTS - found
    assert not gone, f"fjern løste afhængigheder fra KNOWN_CROSS_IMPORTS: {sorted(gone)}"


def test_sql_free_api_modules():
    for rel in SQL_FREE_API:
        tree = ast.parse((PORTAL / rel).read_text())
        for n in ast.walk(tree):
            # SQLAlchemys text/select/update/delete som bare navne (ikke dict.update eller session.delete(obj))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                assert n.func.id not in ("text", "select", "update", "delete"), \
                    f"{rel}:{n.lineno} bruger {n.func.id}() – flyt forespørgslen til services"
