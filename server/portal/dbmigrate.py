"""Flyt en eksisterende samlet database over på de opdelte databaser fra db-admin.

    docker compose run --rm api python -m portal.dbmigrate --from postgresql+asyncpg://portal:KODE@postgres:5432/portal

Kør med DBADMIN_URL sat, efter at primaries for både 'users' og 'observations' er oprettet i db-admin og kører.
Eksisterende data i målet erstattes ikke (tabeller med rækker springes over) medmindre --wipe er givet.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from portal.dbops import migrate
from portal.dbrouter import router


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True, help="DSN på den gamle, samlede database")
    ap.add_argument("--wipe", action="store_true", help="erstat data i målet")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    await router.start()
    if router.legacy:
        raise SystemExit("DBADMIN_URL er ikke sat – der er ingen mål-databaser at flytte til")
    res = await migrate(args.src, wipe=args.wipe)
    print(f"Færdig: {sum(res.values())} rækker i {len(res)} tabeller")
    await router.stop()


if __name__ == "__main__":
    asyncio.run(main())
