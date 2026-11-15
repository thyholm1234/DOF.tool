"""Hvem må hvad i Snak: funktionsflag (Admin → Funktioner) og rettigheder (modulerne «snak» og «moderation»)."""

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
        """Har modulet «snak» (kategorier, indstillinger, fastgørelse, eksempler). Ser også forhåndsvisningen."""
        return self.superadmin or can(self.user, "snak")

    @property
    def can_moderate(self) -> bool:
        return self.superadmin or can(self.user, "moderation")

    @property
    def staff(self) -> bool:
        return self.admin or self.can_moderate

    def on(self, key: str = "snak") -> bool:
        return features.visible(self.flags, key, superadmin=self.superadmin, insider=self.admin)

    def out(self) -> dict:
        return {"features": {k: self.on(k) for k in features.FEATURES if k.startswith("snak")},
                "can_moderate": self.can_moderate and self.on("snak"), "can_admin": self.admin}


async def context(session: AsyncSession, user: User | None) -> Access:
    return Access(user=user, flags=await features.load(session))
