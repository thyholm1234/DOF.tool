"""Statisk analyse: hvilke SQL-strenge i portal/ rører tabeller fra begge databasegrupper (kræver omskrivning ved split).

Kør:  python3 tools/sql_groups.py     (afslutter med fejlkode 1, hvis der findes blandede sætninger)
"""
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from portal.dbgroups import OBS_TABLES, USER_TABLES, tables_in  # noqa: E402

SQL = re.compile(r"^\s*\(?\s*(select|with|insert|update|delete|alter|create)\b", re.I)
root = Path(__file__).resolve().parents[1] / "server" / "portal"
bad = 0
for f in sorted(root.rglob("*.py")):
    tree = ast.parse(f.read_text())
    inner = {id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in ast.walk(n) if v is not n}
    for n in ast.walk(tree):
        if id(n) in inner:
            continue
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            sql = n.value
        elif isinstance(n, ast.JoinedStr):
            sql = "".join(p.value if isinstance(p, ast.Constant) else " x " for p in n.values)
        else:
            continue
        if not SQL.match(sql):
            continue
        t = tables_in(sql)
        if t & OBS_TABLES and t & USER_TABLES:
            bad += 1
            print(f"{f.relative_to(root)}:{n.lineno}: obs={sorted(t & OBS_TABLES)} user={sorted(t & USER_TABLES)}")
print("blandede sætninger:", bad)
sys.exit(1 if bad else 0)
