"""Administration og moderation – ét modul pr. panel; routerne samles her i den oprindelige rækkefølge.

drift (lager, overblik, gateway, worker, planer, arkiv, talemodel, job) · indstillinger (indstillinger, funktioner, FCM) ·
backup · brugere · roller · moderation (kommentarer, udelukkelser, beskeder) · indhold (nyheder) · data (DOFbasen, vejr)
"""

from fastapi import APIRouter

from portal.api.admin import (
    backup,
    brugere,
    data,
    drift,
    indhold,
    indstillinger,
    moderation,
    roller,
)

router = APIRouter()
for module in (drift, indstillinger, backup, brugere, moderation, indhold, data, roller):
    router.include_router(module.router)
