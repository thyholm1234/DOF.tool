"""Hvilken databasegruppe en tabel hører til. Brugerdata (alt med brugere) og observationsdata ligger i hver sin gruppe
af databaser; referencedata (arter, lokaliteter m.m.) findes i begge, så joins mod dem stadig er lokale."""

from __future__ import annotations

import re

OBS_TABLES = frozenset({
    "observers", "trips", "observations", "day_sync", "archive_files", "species_day", "effort_day", "pheno_metrics",
    "species_season", "climate_month", "area_species_counts", "loc_species_finds", "region_year_species", "region_reach", "region_totals",
    "location_year", "month_sync", "weather_months", "weather_cell_day",
    "dofb_obs_status",  # DKU-status på observationer (joines ind i observationsopslag)
    "species_categories", "category_overrides", "manual_finds",  # artskategorier og fund der påvirker observationer/lister
    "reference_edits",  # Moderation → Arter: egne bemærk-tærskler og fænologivinduer (lægges oven på DOFbasens referencedata)
})
REFERENCE_TABLES = frozenset({
    "species", "afdelinger", "kommuner", "locations", "bemaerk_thresholds", "phenology_windows", "code_texts",
})
# Alt andet (brugere, lister, push, kommentarer, kalendere, dofb_*, nf_* …) er brugerdata
USER_TABLES = frozenset({
    "roles", "users", "user_locations", "push_subscriptions",
    "areas", "alert_rules", "thread_follows", "thread_comments", "thread_statuses", "notifications", "list_groups",
    "list_group_members", "settings", "news", "mob_presets", "courses", "questions", "question_progress", "quiz_results",
    "field_trips", "field_breeding_pairs", "field_disturbances", "field_reports", "field_report_updates", "matrikler",
    "matrikel_perioder", "likes", "activity", "support_threads", "support_messages", "comment_bans", "dof_events",
    "site_calendars", "calendar_visits", "dofb_pages", "dofb_news", "dofb_focus", "dofb_lists", "dofb_areas", "dofb_roles",
    "dofb_user_roles", "dofb_api_grants", "dofb_api_tokens", "dofb_audit", "nf_pages", "nf_articles",
    "nf_comments", "nf_photos", "nf_likes", "nf_species", "nf_families", "nf_links", "nf_feeds", "nf_feed_items", "nf_kryds",
    "nf_auto_lists", "nf_roles", "nf_user_roles", "nf_api_grants", "nf_api_tokens", "nf_audit",
    "dku_members", "dku_reviews", "dku_candidates", "dku_cases", "dku_participants", "dku_messages",
})
ALL_TABLES = OBS_TABLES | REFERENCE_TABLES | USER_TABLES

USERS, OBSERVATIONS = "users", "observations"
GROUPS = (USERS, OBSERVATIONS)

_WORD = re.compile(r"[a-z_][a-z0-9_]*")


def group_of_table(name: str) -> str | None:
    """'users' / 'observations', eller None for referencedata (findes begge steder) og ukendte navne."""
    if name in OBS_TABLES:
        return OBSERVATIONS
    if name in USER_TABLES:
        return USERS
    return None


def tables_in(sql: str) -> set[str]:
    """Kendte tabelnavne nævnt i en SQL-streng (ord for ord, så 'observations' ikke rammes af 'ix_obs_...')."""
    return set(_WORD.findall(sql.lower())) & ALL_TABLES


def group_of_sql(sql: str) -> str | None:
    """Gruppen en sætning skal køres i. None = kun referencedata/ingen tabeller. Blandede sætninger giver ValueError."""
    groups = {g for t in tables_in(sql) if (g := group_of_table(t))}
    if len(groups) > 1:
        raise ValueError("Sætningen blander bruger- og observationstabeller: " + ", ".join(sorted(tables_in(sql) - REFERENCE_TABLES)))
    return next(iter(groups), None)
