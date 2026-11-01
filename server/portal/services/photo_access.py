"""Hvem må hvad i fotomodulet: funktionsflag (Admin → Funktioner) og rettigheder (modul «fotos», «moderation»)."""

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
        """Har modulet «fotos» (import, privilegier, fotografer, bannere, indstillinger). Ser også forhåndsvisningen."""
        return self.superadmin or can(self.user, "fotos")

    def on(self, key: str = "fotos") -> bool:
        return features.visible(self.flags, key, superadmin=self.superadmin, insider=self.admin)

    @property
    def can_moderate(self) -> bool:
        """Fotomoderator: godkender, afviser, låser og grupperer fotos."""
        return self.admin

    @property
    def can_moderate_comments(self) -> bool:
        return self.superadmin or can(self.user, "moderation")

    def out(self, privilegie: str = "standard") -> dict:
        return {"features": {k: self.on(k) for k in features.FEATURES if k.startswith("fotos")},
                "can_moderate": self.can_moderate and self.on("fotos"),
                "can_moderate_comments": self.can_moderate_comments and self.on("fotos_kommentarer"),
                "can_admin": self.admin, "privilegie": privilegie}


async def context(session: AsyncSession, user: User | None) -> Access:
    return Access(user=user, flags=await features.load(session))
