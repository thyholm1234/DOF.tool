"""Hvem må hvad i artikelmodulet: funktionsflag (Admin → Funktioner) og rettigheder (modul «artikler», «moderation»)."""

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
        """Har modulet «artikler» (import, emneord, indstillinger). Ser også forhåndsvisningen."""
        return self.superadmin or can(self.user, "artikler")

    def on(self, key: str = "artikler") -> bool:
        return features.visible(self.flags, key, superadmin=self.superadmin, insider=self.admin)

    @property
    def can_edit(self) -> bool:
        return self.admin and self.on("artikler_editor")

    @property
    def can_moderate(self) -> bool:
        return self.superadmin or can(self.user, "moderation")

    def out(self) -> dict:
        return {"features": {k: self.on(k) for k in features.FEATURES if k.startswith("artikler")}, "can_edit": self.can_edit,
                "can_moderate": self.can_moderate and self.on("artikler_kommentarer"), "admin": self.admin}


async def context(session: AsyncSession, user: User | None) -> Access:
    return Access(user=user, flags=await features.load(session))
