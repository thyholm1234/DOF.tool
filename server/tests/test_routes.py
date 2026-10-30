"""API'ets rutetabel skal være uændret, når modulerne omstruktureres: samme stier, metoder, parametre og funktionsnavne.

Tilføjes eller ændres en rute med vilje, opdateres snapshottet med:
    UPDATE_ROUTES=1 python -m pytest tests/test_routes.py
"""

import json
import os
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "api_routes.json"


def _routes() -> dict:
    from portal.main import app  # conftest.py peger medie- og modelmapperne på en midlertidig mappe

    spec = app.openapi()
    return {p: {m: {"op": v.get("operationId"),
                    "params": sorted([x["name"], x["in"], x.get("required", False)] for x in v.get("parameters", [])),
                    "body": bool(v.get("requestBody"))} for m, v in ms.items()}
            for p, ms in spec["paths"].items()}


def test_route_table_unchanged():
    current = _routes()
    if os.environ.get("UPDATE_ROUTES"):
        FIXTURE.write_text(json.dumps(current, ensure_ascii=False, indent=0, sort_keys=True))
    expected = json.loads(FIXTURE.read_text())
    assert sorted(current) == sorted(expected), "stier tilføjet/fjernet"
    for path, methods in expected.items():
        assert current[path] == methods, path
