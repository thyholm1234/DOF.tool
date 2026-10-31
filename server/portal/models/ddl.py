"""Ekstra DDL, der køres efter create_all (indekser, kolonner tilføjet efter oprettelsen, SQL-funktioner)."""

# Ekstra DDL der ikke kan udtrykkes pænt i modellerne
EXTRA_DDL = [
    "CREATE INDEX IF NOT EXISTS ix_location_shapes_geom ON location_shapes USING gist (geom)",
    # Artikelmodulet: råtekst-søgning (ILIKE) bruger trigram-indekser; fuldtekst-indekset ligger på modellen
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE INDEX IF NOT EXISTS ix_art_articles_body_trgm ON art_articles USING gin (body_text gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_art_articles_ocr_trgm ON art_articles USING gin (ocr_text gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_art_articles_title_trgm ON art_articles USING gin (title gin_trgm_ops)",
    # Portalens nyheder bruger den fælles markdown-editor
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS lead text",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS body_html text NOT NULL DEFAULT ''",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS render_version integer NOT NULL DEFAULT 0",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS cover_media_id integer",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS status varchar(12) NOT NULL DEFAULT 'publiceret'",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS publish_at timestamptz",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS updated_at timestamptz",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS updated_by integer",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1",
    # Netfugls automatiske kryds ligger nu i nf_auto_lists (én række pr. deltager og liste); den gamle tabel var afledt data
    "DROP TABLE IF EXISTS nf_auto",
    # Portalen gemmer ikke længere noget om adgangskoder – login tjekkes altid hos DOFbasen
    "ALTER TABLE users DROP COLUMN IF EXISTS password_hash",
    "ALTER TABLE notifications ALTER COLUMN pushed SET DEFAULT false",
    "ALTER TABLE notifications ALTER COLUMN read SET DEFAULT false",
    "CREATE INDEX IF NOT EXISTS ix_locations_geom ON locations USING gist (geom)",
    "CREATE INDEX IF NOT EXISTS ix_kommuner_geom ON kommuner USING gist (geom)",
    "CREATE INDEX IF NOT EXISTS ix_areas_geom ON areas USING gist (geom)",
    "CREATE INDEX IF NOT EXISTS ix_obs_afdelinger ON observations USING gin (afdelinger)",
    "CREATE INDEX IF NOT EXISTS ix_obs_notable ON observations (dato) WHERE kategori <> 'alm'",
    "CREATE INDEX IF NOT EXISTS ix_trips_bbtag ON trips (obserkode, dato) WHERE turnoter LIKE '%#%'",
    # 10 km-kvadrat (DKN, EPSG:25832) for hver lokalitet – nøgle til vejrarkivet
    "ALTER TABLE locations ADD COLUMN IF NOT EXISTS cell10 integer",
    "ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS first_only boolean NOT NULL DEFAULT false",
    # Lokale hits efter estimerede fund: eksisterende regler talte i hele området og gør det fortsat
    "ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS local_basis varchar(10) NOT NULL DEFAULT 'omraade'",
    "ALTER TABLE alert_rules ALTER COLUMN local_basis SET DEFAULT 'lokalitet'",
    "ALTER TABLE area_species_counts ADD COLUMN IF NOT EXISTS n_finds integer NOT NULL DEFAULT 0",
    "ALTER TABLE field_reports ADD COLUMN IF NOT EXISTS retningslinjer boolean NOT NULL DEFAULT false",
    "ALTER TABLE field_reports ALTER COLUMN trip_id DROP NOT NULL",
    "ALTER TABLE field_reports ADD COLUMN IF NOT EXISTS antalmetode smallint",
    "ALTER TABLE field_reports ADD COLUMN IF NOT EXISTS maerker varchar(16)[] NOT NULL DEFAULT '{}'",
    "ALTER TABLE field_reports ADD COLUMN IF NOT EXISTS client_id varchar(40)",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS client_id varchar(40)",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS metode_id smallint NOT NULL DEFAULT 1",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS komplet boolean",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS spontan boolean NOT NULL DEFAULT false",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS lat double precision",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS lon double precision",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS temperatur smallint",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS skydaekke smallint",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS vindretning varchar(4)",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS vindstyrke smallint",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS nedboer smallint",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS nedboertype varchar(8)",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS sigtbarhed integer",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS gps_aktiv boolean NOT NULL DEFAULT false",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS gps_track jsonb",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_field_trips_client ON field_trips (user_id, client_id) WHERE client_id IS NOT NULL",
    "ALTER TABLE dof_events ADD COLUMN IF NOT EXISTS source varchar(8) NOT NULL DEFAULT 'dof'",
    "ALTER TABLE dof_events ADD COLUMN IF NOT EXISTS created_by integer REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE calendar_visits ADD COLUMN IF NOT EXISTS lat double precision",
    "ALTER TABLE calendar_visits ADD COLUMN IF NOT EXISTS lon double precision",
    "ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS kommuner varchar(4)[] NOT NULL DEFAULT '{}'",
    "ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS afdelinger smallint[] NOT NULL DEFAULT '{}'",
    "ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS missing varchar(8)",
    # Alarmniveau som afkrydsninger: de gamle trinvise niveauer omskrives til deres kategorier
    "ALTER TABLE alert_rules ALTER COLUMN level TYPE varchar(32)",
    "UPDATE alert_rules SET level = CASE level WHEN 'SUB' THEN 'SU,SUB' WHEN 'bemaerk' THEN 'SU,SUB,bemaerk'"
    " WHEN 'local' THEN 'SU,SUB,local' ELSE level END WHERE level IN ('SUB', 'bemaerk', 'local')",
    # Portalens egne ture får id'er langt fra dof.dk's event_id'er
    "CREATE SEQUENCE IF NOT EXISTS portal_event_seq START 900000000",
    # Listekategori for en observation: serie-overstyring → artens kategori på datoen (underarten før listearten)
    # → DOFbasens [klammer] om artsnavnet (ikke krydsbar) → A
    """CREATE OR REPLACE FUNCTION bird_category(a integer, la integer, l integer, d date) RETURNS varchar
    LANGUAGE sql STABLE AS $$
      SELECT coalesce(
        (SELECT kategori FROM category_overrides WHERE artnr = la AND loknr = l AND d BETWEEN date_from AND date_to
           AND removed_at IS NULL ORDER BY created_at DESC, id DESC LIMIT 1),
        (SELECT kategori FROM species_categories WHERE artnr IN (a, la) AND removed_at IS NULL
           AND coalesce(valid_from, '-infinity'::date) <= d ORDER BY (artnr = a) DESC, valid_from DESC NULLS LAST, id DESC LIMIT 1),
        (SELECT 'D' FROM species WHERE artnr IN (a, la) AND navn LIKE '[%' LIMIT 1),
        'A')
    $$""",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS permissions jsonb",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS approved boolean NOT NULL DEFAULT true",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_test boolean NOT NULL DEFAULT false",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS test_pw_hash varchar(200)",
    "ALTER TABLE mob_presets ADD COLUMN IF NOT EXISTS official boolean NOT NULL DEFAULT false",
    "ALTER TABLE mob_presets ALTER COLUMN user_id DROP NOT NULL",
    "UPDATE locations SET cell10 = (floor(ST_Y(ST_Transform(geom, 25832)) / 10000)::int * 1000"
    " + floor(ST_X(ST_Transform(geom, 25832)) / 10000)::int) WHERE cell10 IS NULL AND geom IS NOT NULL",
    # Nyt admin-modul: administratorrollen får det én gang (markør i settings), så superadmin bagefter kan fjerne det igen
    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM settings WHERE key = 'module_added_dku') THEN"
    " UPDATE roles SET modules = array_append(modules, 'dku') WHERE key = 'admin' AND NOT ('dku' = ANY(modules));"
    " INSERT INTO settings (key, value) VALUES ('module_added_dku', 'true'); END IF; END $$",
    # DKU: adskilte kanaler i stedet for «alle»; sagens oprindelse (tip eller DKU selv)
    "ALTER TABLE dku_messages ALTER COLUMN visibility TYPE varchar(10)",
    "ALTER TABLE dku_cases ADD COLUMN IF NOT EXISTS origin varchar(6) NOT NULL DEFAULT 'tip'",
    "UPDATE dku_messages SET visibility = 'tipper' WHERE visibility = 'alle' AND role = 'tipper'",
    "UPDATE dku_messages m SET visibility = CASE WHEN c.observer_involved THEN 'observer' ELSE 'tipper' END"
    " FROM dku_cases c WHERE m.case_id = c.id AND m.visibility = 'alle'",
]
