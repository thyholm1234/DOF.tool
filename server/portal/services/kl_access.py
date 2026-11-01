"""Hvem må hvad i krydslistemodulet: funktionsflag (Admin → Funktioner) og rettigheder (modul «krydslister»)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import User
from portal.services import features
from portal.services.auth import SUPERADMIN, can


@dataclass
class Access:
    user: User | None
    flags: dict[str, str]

    @property
    def superadmin(self) -> bool:
        return bool(self.user) and self.user.role == SUPERADMIN

    @property
    def admin(self) -> bool:
        """Har modulet «krydslister» (listetyper, artslister, World Bird List, godkendelse). Ser også forhåndsvisningen."""
        return self.superadmin or can(self.user, "krydslister")

    def on(self, key: str = "krydslister") -> bool:
        return features.visible(self.flags, key, superadmin=self.superadmin, insider=self.admin)

    def out(self) -> dict:
        return {"features": {k: self.on(k) for k in features.FEATURES if k.startswith("krydslister")}, "can_admin": self.admin}


async def context(session: AsyncSession, user: User | None) -> Access:
    return Access(user=user, flags=await features.load(session))
