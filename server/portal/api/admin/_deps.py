"""Fælles for admin-modulerne: adgangskrav pr. modul."""

from __future__ import annotations

from portal.api.deps import require_perm, require_role

superadmin = require_role("superadmin")
perm_arter = require_perm("arter")
perm_brugere = require_perm("brugere")
perm_data = require_perm("data")
perm_drift = require_perm("drift")
perm_indhold = require_perm("indhold")
perm_moderation = require_perm("moderation")
perm_vejr = require_perm("vejr")
