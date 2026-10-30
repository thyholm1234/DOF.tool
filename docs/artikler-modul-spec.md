# Artikler – specifikation af nyt modul (til implementering)

Status: specifikation, 30. oktober 2026. Skrevet til at blive bygget trin for trin (fase 1–4) af en anden model/udvikler.
Læs `docs/arkitektur.md` først – alle regler dér gælder. Denne fil siger *hvad* der skal bygges og *hvor*; den
gentager ikke arkitekturreglerne.

---

## 0. Kort fortalt

Et nyt, selvstændigt modul **Artikler** (`#/artikler`) der:

1. **Indlæser netfugl.dk-artikelarkivet** fra en `.zip`, som en administrator uploader i modulets admin. Importen kan
   køres igen og igen (arkivet vokser stadig) og opdaterer eksisterende artikler uden at lave dubletter.
2. Viser artiklerne for brugerne med en **avisagtig forside** (med billeder), en **artikelside** og faner til
   **søgning i fritekst/råtekst**, **emneord** og **arter**.
3. Gør **arter søgbare i alle artikler**: hver artikel kobles til de arter (artnr), den handler om – fra emneord og fra
   teksten (inkl. OCR-tekst fra PDF'er).
4. Har **kommentarer** med **efter-moderation og anmeld-knap**, og **notifikationer** (nye artikler; nye kommentarer på
   artikler man selv har kommenteret).
5. Har en **fuldt udstyret markdown-editor** (overskrifter, fed/kursiv, lister, tabeller, billeder med upload, gallerier,
   PDF-vedhæftning, sanitiseret indlejret HTML, metadata-panel, forhåndsvisning, revisioner). Editoren er en **fælles
   komponent**, som også bruges til at bygge **portalens eksisterende nyheder** (“Nyt fra portalen”, tabellen `news`)
   ud. Nyhederne beholder deres egen tabel.

**Kun artiklerne** importeres i denne omgang. `Turrapporter/` i zip'en ignoreres (men importeren skal være bygget, så
de kan tilføjes senere som en ny `kind`).

### Beslutninger truffet med brugeren

| Spørgsmål | Beslutning |
|---|---|
| Hvad dækker editoren? | Artikelmodulets egne artikler **og** portalens `news` (samme editor-komponent og samme server-renderer; `news` beholder sin tabel). Netfugl-showcasen (`nf_articles`) røres **ikke**. |
| Gamle kommentarer fra netfugl.dk | Importeres og vises **read-only som arkiv** (“Kommentarer fra netfugl.dk”) med navn og dato, uden profillink. Nye kommentarer kommer under. Moderator kan skjule dem. |
| Moderation af nye kommentarer | **Efter-moderation + anmeld**: kommentarer vises med det samme; brugere kan anmelde; moderatorkø i admin; eksisterende `comment_bans` genbruges. |
| Indlejret HTML | **Sanitiseret server-side (nh3) + iframe-allowlist**, som administratorer vedligeholder. Ingen script/style/on*-attributter. |

---

## 1. Kildedata: artikelarkivet

Se `Artikler/MANIFEST.md` (autoritativ beskrivelse). Det vigtigste for importen:

```
<rod>/                       ← i zip'en ligger alt under en topmappe, fx "netfugl-artikler/"
  MANIFEST.md, manifest.json, _index.json, _emneord.json, _forfattere.json, _mangler.json
  Turrapporter/              ← IGNORÉR i denne omgang
  0012/                      ← 4 cifre = netfugl.dk-artikelnummer (stabil nøgle)
    meta.json                ← schema_version 1; titel, dato, forfattere, emneord, billeder, vedhaeftede_filer …
    artikel.md               ← markdown; første linje "# Titel"
    kommentarer.json         ← træ: [{id, forfatter, forfatter_id, dato, tekst_md, tekst_html, svar:[…]}]
    original.html            ← backup; importeres IKKE
    billeder/NNN-navn.jpg
    filer/navn.pdf, filer/navn.ocr.md
```

**Observationer fra det aktuelle arkiv (vigtige for designet):**

- Mappen `Artikler/` har lige nu **802 artikelmapper** (nr. 12–3580), men `_index.json`/`manifest.json` og test-zip'en
  `Artikler.zip` indeholder kun **6**. Indekset er altså ikke til at stole på → **importeren skal gennemløbe mapperne
  (`^\d{4}/meta.json$`)**, ikke `_index.json`. `_index.json` bruges kun til en krydstjek-linje i importrapporten.
- Kun **3 af 802** har et `forsidebillede`, og der er kun **61 billeder** i alt. **73 artikler er PDF'er** (alle med OCR).
  → Forsiden kan ikke regne med billeder; se fallback-rækkefølgen i §6.1 (PDF-forside-miniature og typografiske
  pladsholdere er nødvendige, ikke pynt).
- **305 emneord**, fordelt på typer (`Nyhed` 727, `Artikel` 37), publikationer (`Pandion`, `DOF NYT`), årstal (`1999`,
  `2020` …), temaer (`Feltbestemmelse`, `Fænologi`, `NFL (Ny For Landet/førstegangsfund)`), steder (`Skagen`) og **arter**
  (`Høgeørn`, `Brilleand`, `Blå Glente` …).
- 18 artikler har `status: "delvis"` (manglende filer) – importeres alligevel; fejlene vises i importrapporten.
- `dato` er dansk lokaltid uden tidszone → konvertér med `portal.timeutil.TZ` (Europe/Copenhagen) til `timestamptz`.
- `artikel.md` starter med `# Titel` og ofte en gentaget `**Titel**`-linje → begge fjernes ved import (titlen ligger i
  feltet). Billeder står som `![](billeder/…)`, vedhæftninger som `[…](filer/…)`, links til andre artikler som
  `../0155/artikel.md`.
- Svar i kommentarer har `id: null` (netfugl har to niveauer: hovedkommentar og svar).
- Fuld zip vil blive **flere GB** (545 MB for 802 artikler i dag, PDF'er på op til 15 MB).

---

## 2. Funktionsflag, roller og menu

### 2.1 Funktionsflag (`services/features.py`)

| Nøgle | Navn | Forælder | Standard |
|---|---|---|---|
| `artikler` | Artikler (netfugl-arkivet og nye artikler) | – | `preview` |
| `artikler_kommentarer` | Artikler: kommentarer og anmeldelser | `artikler` | `on` |
| `artikler_notifikationer` | Artikler: notifikationer om nye artikler og kommentarer | `artikler` | `on` |
| `artikler_editor` | Artikler: oprettelse og redigering af artikler | `artikler` | `on` |

- `insider` (ser forhåndsvisning) = superadmin eller har admin-modulet `artikler`.
- API'et svarer **404** på alle `/api/artikler/*`-ruter, når `artikler` ikke er synligt for brugeren; kommentar-ruter
  svarer 404, når `artikler_kommentarer` ikke er synligt, osv.
- `/api/me` udvides med `"artikler": {"features": {...synlige flag...}, "can_edit": bool, "can_moderate": bool}`,
  på samme måde som `netfugl`/`dofbasen` (se `api/auth.py`, linje ~76).
- Editor-opgraderingen af **portalens nyheder** er ikke et nyt modul og ligger **ikke** bag flaget (den er kun synlig i
  admin for dem med modulet `indhold`).

### 2.2 Rettigheder (`services/auth.MODULES`)

- Nyt modul: `"artikler": "Artikler: import, redigering, emneord og indstillinger"`.
- **Kommentarmoderation** bruger det eksisterende modul `moderation` (moderatorer har det allerede).
- Nyheder bruger fortsat `indhold`.
- `DEFAULT_ROLES` seedes kun første gang; tilføj `artikler` til `editor`-rollens standardsæt, men skriv i PR'en, at
  eksisterende installationer skal tildele modulet manuelt i Admin → Roller.

### 2.3 Menu og ruter (frontend)

- `app.js`: ny post `ARTIKLER = ["#/artikler", "<ikon>", "Artikler"]`, vises når `state.user.artikler.features.artikler`
  (samme mønster som `NETFUGL`). Rute: `[/^\/artikler(?:\/(.+))?$/, () => import("./views/artikler.js")]`.
- Hash-ruter i modulet:

| Rute | Side |
|---|---|
| `#/artikler` | Forside (avis) |
| `#/artikler/soeg?q=&mode=&tag=&art=&fra=&til=&type=&sort=&side=` | Søgning |
| `#/artikler/emneord` · `#/artikler/emneord/<tag-id>` | Emneordsoversigt / artikler med emneord |
| `#/artikler/arter` · `#/artikler/arter/<artnr>` | Artsoversigt / artikler om art |
| `#/artikler/forfattere` · `#/artikler/forfattere/<navn>` | Forfattere (billig ekstra fane) |
| `#/artikler/<id>` (evt. `<id>-<slug>`) | Artikelside |
| `#/artikler/n/<netfugl_id>` | Slår op og omdirigerer til artiklen (gamle links) |
| `#/artikler/ny` · `#/artikler/<id>/rediger` | Editor |
| `#/artikler/admin[/import\|/artikler\|/emneord\|/arter\|/indstillinger]` | Modulets admin |
| `#/nyheder/<id>` | Ny side: en portalnyhed i fuld længde |

---

## 3. Datamodel

Alle nye tabeller er **brugerdata** → tilføj dem til `USER_TABLES` i `server/portal/dbgroups.py`. Arter slås op i
`species` (referencedata, findes i begge grupper, så joins er lokale). Ingen SQL må blande grupperne.
Ny modelfil: `server/portal/models/articles.py` (eksportér i `models/__init__.py`). Indeks/udvidelser, der ikke kan
udtrykkes i modellen, lægges i `models/ddl.py`.

### 3.1 Tabeller

**`art_articles`** – én række pr. artikel
| Kolonne | Type | Note |
|---|---|---|
| `id` | serial PK | portalens id (bruges i URL'er) |
| `netfugl_id` | int, unique, null | artikelnummer fra arkivet; null for nye artikler |
| `kind` | varchar(12) | `artikel` (standard), `pdf` (artiklen *er* en PDF), senere `turrapport` |
| `title` | varchar(300) | |
| `slug` | varchar(120) | afledt af titel, kun til pæne URL'er |
| `lead` | text, null | manchet/underrubrik (import: tom; editor: valgfri; ellers bruges første afsnit som teaser) |
| `body_md` | text | markdown med portable referencer (§4.4) |
| `body_html` | text | renderet + sanitiseret HTML (cache, regenereres) |
| `body_text` | text | ren tekst (til søgning og teaser) |
| `ocr_text` | text, null | samlet OCR-tekst fra PDF'er (til søgning) |
| `render_version` | int | rendererens version ved sidste rendering |
| `dato` | timestamptz | artiklens dato |
| `authors` | jsonb | `[{navn, netfugl_id?, user_id?}]` |
| `uploaded_by`, `last_edited_netfugl` | jsonb, null | fra `uploadet_af`/`sidst_redigeret` (vises diskret) |
| `cover_media_id` | FK `content_media`, null | forsidebillede |
| `cover_caption` | varchar(300), null | billedtekst/kreditering |
| `status` | varchar(12) | `kladde` / `planlagt` / `publiceret` / `skjult` |
| `publish_at`, `published_at` | timestamptz, null | planlagt / første gang publiceret |
| `featured_until` | timestamptz, null | fremhævet på forsiden |
| `comments_open` | bool, default true | |
| `source` | varchar(10) | `netfugl` / `lokal` |
| `source_url` | varchar(400), null | `kilde_url` |
| `views_imported` | int, default 0 | `antal_laest_ved_hentning` |
| `views` | int, default 0 | læsninger i portalen |
| `locally_edited` | bool, default false | sættes, når en redaktør gemmer en importeret artikel |
| `version` | int, default 1 | optimistisk låsning (409 ved konflikt) |
| `import_id` | FK `art_imports`, null | seneste import, der rørte artiklen |
| `import_errors` | jsonb, default `[]` | `fejl` fra meta.json + egne importfejl |
| `search` | tsvector, GENERATED STORED | se §5.1 |
| `created_by`, `updated_by` | FK users, null, ON DELETE SET NULL | |
| `created_at`, `updated_at` | timestamptz | |

Indeks: `GIN(search)`, `GIN(body_text gin_trgm_ops)` og `GIN(ocr_text gin_trgm_ops)` (råtekst-søgning, kræver
`CREATE EXTENSION IF NOT EXISTS pg_trgm` i `ddl.py`), `(status, dato DESC)`, `(published_at DESC)`.

**`art_tags`** – emneord
| `id` serial · `navn` varchar(120) · `navn_lc` unique (lower) · `netfugl_tag_id` int unique null · `kind` varchar(12): `type` / `publikation` / `aar` / `emne` / `sted` / `art` · `artnr` int null (når `kind='art'`) · `hidden` bool (skjul i oversigter, fx typer) |

**`art_article_tags`** – `(article_id, tag_id)` PK, begge FK ON DELETE CASCADE.

**`art_species`** – arter i artiklen
| `article_id` FK · `artnr` int · `source` varchar(10): `emneord` / `tekst` / `ocr` / `redaktoer` / `fravalgt` · `hits` int · `first_pos` int null · PK `(article_id, artnr)` · indeks `(artnr, article_id)` |

`fravalgt` = en redaktør har fjernet en falsk positiv; genberegning må **ikke** tilføje arten igen.

**`content_media`** – fælles medietabel for artikler *og* nyheder
| `id` serial · `owner_kind` varchar(10): `artikel` / `nyhed` · `owner_id` int null (null mens en ny artikel endnu ikke er gemt) · `kind`: `billede` / `pdf` / `fil` · `storage_key` varchar(200) · `thumb_key`, `medium_key` varchar null · `filename` · `mime` · `size` · `width`, `height` null · `alt` varchar(300) · `caption` varchar(300) · `credit` varchar(160) · `original_url` varchar(500) null · `pages` int null · `text_md` text null (OCR) · `text_source` varchar(16) null · `sha256` char(64) (dedup) · `sort` int · `created_by` · `created_at` |

**`art_comments`**
| `id` serial · `article_id` FK CASCADE · `parent_id` FK self null (kun ét niveau: svar på hovedkommentar) · `user_id` FK users null (null = arkivkommentar) · `archive_author` varchar(160) null · `archive_netfugl_user` int null · `archive_key` char(40) null unique (sha1 af article+forfatter+dato+tekst, for idempotent import) · `body_md` text · `body_html` text · `status`: `synlig` / `skjult` / `slettet` · `hidden_reason` varchar(500) null · `hidden_by` FK null · `report_count` int default 0 · `edited_at` null · `created_at` |

**`art_comment_reports`**
| `id` · `comment_id` FK CASCADE · `user_id` FK · `reason` varchar(40) (`chikane`, `spam`, `forkert`, `andet`) · `note` varchar(500) · `created_at` · `resolved_at`, `resolved_by`, `resolution` (`skjult`/`afvist`) · unique `(comment_id, user_id)` |

**`art_subscriptions`**
| `user_id` FK CASCADE · `kind` varchar(10): `alle` (nye artikler) / `artikel` / `emneord` / `art` · `ref` int (0 for `alle`) · `via` varchar(10): `manuel` / `kommentar` · `muted` bool default false · `created_at` · PK `(user_id, kind, ref)` |

`muted=true` på `artikel` betyder “følg ikke længere” og forhindrer, at en ny kommentar tilmelder igen.
Fase 2 bruger kun `alle` og `artikel`; `emneord`/`art` er klar i modellen og kan slås til i fase 4.

**`art_revisions`** – `id` · `article_id` FK CASCADE · `version` · `title` · `body_md` · `meta` jsonb (lead, tags, arter, cover, status) · `user_id` · `created_at`. Behold de seneste 50 pr. artikel.

**`art_imports`**
| `id` · `filename` · `size` · `sha256` · `parts_received` int · `uploaded_by` · `status`: `uploader` / `klar` / `koerer` / `faerdig` / `fejl` · `options` jsonb · `stats` jsonb (oprettet, opdateret, uændret, sprunget over pga. lokal redigering, fejl, billeder, pdf'er, kommentarer) · `log` jsonb (liste af `{netfugl_id, niveau, besked}`, højst 2000 linjer) · `created_at`, `started_at`, `finished_at` |

**`news`** (eksisterende) – nye kolonner via `ddl.py` (`ALTER TABLE news ADD COLUMN IF NOT EXISTS …`):
`lead text`, `body_html text`, `render_version int default 0`, `cover_media_id int`, `status varchar(12) default 'publiceret'`,
`publish_at timestamptz`, `updated_at timestamptz`, `updated_by int`, `version int default 1`. `body` bliver markdown.
Engangsmigrering (i `bootstrap`/`_once`): eksisterende `body` er ren tekst → erstat enkelt-linjeskift med markdown-hårde
linjeskift (`"  \n"`), render `body_html`, sæt `status = CASE WHEN published THEN 'publiceret' ELSE 'kladde' END`.
`published` bevares og holdes i sync (gamle klienter).

### 3.2 Indstillinger (`settings`-tabellen via `services/setting_store.py`, nøgle `artikler`)

```json
{
  "embed_hosts": ["xeno-canto.org", "player.vimeo.com", "dofbasen.dk", "www.dof.dk"],
  "report_threshold": 3,
  "comments_enabled": true,
  "comment_edit_minutes": 15,
  "ambiguous_species": ["Ravn", "Krage", "Due", "Stær", "Høg", "Spurv", "Musvit", "Svale"],
  "front": {"hero_count": 1, "secondary_count": 3, "on_this_day": true}
}
```

`embed_hosts` bruges af den fælles renderer – også for nyheder.

---

## 4. Import af zip

### 4.1 Upload (chunked)

En fuld zip er flere GB, så upload sker i dele; et enkelt POST vil ramme proxy-/timeout-grænser.

- `POST /api/admin/artikler/import` `{filename, size, sha256?}` → `{id, chunk_size}` (chunk_size = 32 MiB).
- `PUT /api/admin/artikler/import/{id}/del/{n}` (rå body, `application/octet-stream`) → gemmes i
  `ARTICLE_IMPORT_DIR/<id>.part` på offset `n*chunk_size`. Idempotent (samme del kan sendes igen).
- `POST /api/admin/artikler/import/{id}/start` `{options}` → tjekker størrelse (+ sha256 hvis givet), omdøber til
  `<id>.zip`, status `klar`, og sender kommandoen `articles_import` til workeren (Valkey-listen, som `archive_import`).
- `GET /api/admin/artikler/import` (liste) og `GET …/{id}` (status, stats, log, fremdrift fra `jobstatus`).
- `DELETE …/{id}` – fjerner fil og række (ikke artiklerne).
- Frontend: læs filen med `File.slice()`, send 3 dele parallelt, vis fremdriftsbjælke, genoptag efter netfejl.
- Ny indstilling i `config.py`: `article_import_dir` (standard `<media_dir>/../article_imports`), og nævn i
  `docs/drift.md`, at reverse proxy'ens body-grænse skal være ≥ 32 MiB for den rute.
- Zip-filen slettes efter en vellykket import, medmindre `options.keep_zip`.

Importvalg (`options`), vist som afkrydsning i admin:
- `overwrite_local` (standard **fra**): overskriv også artikler, en redaktør har rettet.
- `import_comments` (standard **til**).
- `publish` (standard **til**): nye artikler får status `publiceret`; ellers `kladde`.
- `dry_run`: gennemløb og rapportér uden at skrive.

### 4.2 Sikkerhed ved udpakning (skal testes)

Læs direkte fra zip'en med `zipfile` (ingen fuld udpakning til disk):
- Afvis medlemmer med absolutte stier, `..`, backslash-tricks eller symlinks (zip-slip).
- Grænser: højst 200 000 medlemmer, højst 300 MB ukomprimeret pr. medlem, højst 20 GB i alt, og komprimeringsforhold
  ≤ 100 pr. medlem (zip-bombe). Overskrid → stop med fejl i loggen.
- Tilladte filtyper under en artikel: `meta.json`, `artikel.md`, `kommentarer.json`, `billeder/*.{jpg,jpeg,png,gif,webp}`,
  `filer/*` (pdf, doc, docx, xls, xlsx, txt, `.ocr.md`). `original.html` springes over. Alt andet logges og ignoreres.
- Find roden automatisk: mappen, der indeholder `MANIFEST.md` eller `manifest.json` (fx `netfugl-artikler/`). Spring
  `Turrapporter/` over.
- Valider `meta.json`: `schema_version == 1`, `id` matcher mappenavnet; ellers spring artiklen over med fejl i loggen.
- Billeder: verificér filsignaturen (ikke kun filendelsen), fjern EXIF/GPS med `services/imagemeta.strip`.

### 4.3 Importforløb (worker: `services/article_import.py`)

Kør i `jobstatus.track("articles_import")` og meld fremdrift med `step()` (“Artikel 312 af 3 412”). Én transaktion pr.
artikel, så en fejl i én artikel ikke stopper resten.

For hver artikelmappe:
1. Læs `meta.json`, `artikel.md`, `kommentarer.json`.
2. Find eksisterende række på `netfugl_id`. Er den `locally_edited` og `overwrite_local` er fra → opdater kun
   medier, der mangler, og arkivkommentarer; tæl som “sprunget over (lokalt redigeret)”.
3. **Medier:** for hver fil beregn sha256; findes samme sha256 allerede for artiklen, genbruges rækken. Ellers gem i
   objektlageret (§4.5) og opret `content_media`. Billeder: læs dimensioner og lav varianter `thumb` (400 px bred) og
   `medium` (1200 px) som WebP eller JPEG. PDF: gem, læs `antal_sider`, læg `.ocr.md`-teksten i `text_md` og lav en
   miniature af side 1 (`thumb_key`) til forsiden. Alt-tekst: `alt` fra meta.json, ellers tom (redaktøren kan udfylde).
4. **Forsidebillede:** billedet med `forsidebillede: true`; ellers første billede; ellers PDF-miniaturen; ellers null.
5. **Markdown-omskrivning** (`body_md`):
   - fjern første `# …`-linje og en efterfølgende linje, der kun er `**<titel>**` (normaliseret sammenligning);
   - `![alt](billeder/x.jpg)` → `![alt](media:<id>)`; `[t](filer/x.pdf)` → `[t](media:<id>)`;
   - `../NNNN/artikel.md` (og absolutte `https://www.netfugl.dk/artikler/NNNN/...`) → `netfugl:NNNN`;
   - fjern forsidebilledet fra toppen af teksten, hvis det også er `cover_media_id` (vises allerede som cover).
6. `kind = 'pdf'`, hvis artiklen har en PDF og `body_md` (efter omskrivning) har under ~200 tegn tekst.
7. `ocr_text` = alle `.ocr.md` samlet; `body_text` = tekstudtræk af den renderede HTML.
8. **Emneord:** upsert i `art_tags` på `netfugl_tag_id` (ellers `navn_lc`), klassificér nye (§4.6), erstat
   artiklens `art_article_tags`.
9. **Arkivkommentarer** (hvis `import_comments`): flad træet ud (hovedkommentar → `parent_id` null; `svar` → parent =
   hovedkommentaren; dybere svar hægtes også på hovedkommentaren). Upsert på `archive_key`; bevar `status`, hvis en
   moderator har skjult en kommentar. Brug `tekst_md` (render med kommentar-profilen). `avatar_url` gemmes **ikke** og
   hentes aldrig. Kommentarer, der ikke længere findes i arkivet, slettes kun, hvis de er arkivkommentarer.
10. Render `body_html` (§7.3) og kør **artsindeksering** (§5.3).
11. Sæt `status`/`published_at`. **Importer sender aldrig notifikationer** – heller ikke ved nye artikler.

Efter løkken: sammenlign med `_index.json`/`_mangler.json` og skriv en linje i loggen (“index nævner 6, mappen har 802”).
Til sidst: tæl `cache`-versionen op for artikler, så forside og søgning genberegnes.

### 4.4 Portable referencer i markdown

`body_md` indeholder ingen fysiske adresser. Rendereren oversætter:
- `media:<id>` → `/media/art/<storage_key>` (billeder vises som `medium`, med `srcset` til `thumb`/original, og
  `width`/`height` for at undgå layoutspring; PDF-links får ikon og størrelse).
- `netfugl:<NNNN>` → `#/artikler/n/NNNN` (virker også, hvis artiklen importeres senere).
- `artikel:<id>` → `#/artikler/<id>`.
- `[[art:<artnr>]]` (inline) → artschip med navn, link til `#/arter/<artnr>`.

Så kan lageret flyttes (disk ↔ S3) uden at røre teksterne.

### 4.5 Objektlager

`services/storage.py` har i dag fast præfiks `obs/` og ruten `/media/obs/{name}`. Udvid med en parameter for præfiks
(`put(name, data, ct, prefix="obs/")` osv.) uden at ændre eksisterende kald, og tilføj ruten
`GET|HEAD /media/art/{name}` i `api/media.py` efter samme mønster som `obs_media` (Range, ETag, lang cache, ingen GZip).
Nøgler: `art/<sha256[:2]>/<sha256>.<ext>` (content-addressed). Adgang: kræver at `artikler` er synligt for brugeren,
*eller* at mediet ejes af en publiceret nyhed (nyheder ses af alle indloggede).

### 4.6 Klassificering af emneord (ved første import, kan rettes i admin)

1. `^\d{4}$` → `aar`.
2. `Nyhed`, `Artikel`, `Kort nyt` → `type` (skjult i emneordsskyen, bruges som filter).
3. `Pandion`, `DOF NYT`, `Fugle og Natur`, `DOFT` → `publikation`.
4. Navnet matcher en art (§5.3 trin 1) → `art` + `artnr`.
5. Ellers `emne`.

Admin → Emneord kan omdøbe, flette to emneord (flyt artikler, slet det ene), skifte `kind`, koble til art og skjule.

---

## 5. Søgning og arter

### 5.1 Fuldtekst (“Ord”)

```sql
search tsvector GENERATED ALWAYS AS (
  setweight(to_tsvector('danish', coalesce(title, '')), 'A') ||
  setweight(to_tsvector('danish', coalesce(lead, '')), 'B') ||
  setweight(to_tsvector('danish', coalesce(body_text, '')), 'C') ||
  setweight(to_tsvector('danish', coalesce(ocr_text, '')), 'D')) STORED
```
Forespørgsel med `websearch_to_tsquery('danish', :q)` (understøtter `"præcis frase"`, `-udeluk`, `or`), rangering med
`ts_rank_cd`, uddrag med `ts_headline` (på `body_text`, ellers `ocr_text`, markér “fra PDF”).

### 5.2 Råtekst (“Præcis tekst”)

Tilstanden `mode=praecis`: `body_text ILIKE '%…%' OR ocr_text ILIKE '%…%' OR title ILIKE …` med trigram-indeks.
Ingen stemming, finder også delord og stavemåder (“Aztekerterne”). Uddrag laves i Python omkring første træf (±120 tegn).
Escape `%`/`_` i input.

### 5.3 Artsindeksering (`services/article_species.py`)

Kører ved import, ved hver gem i editoren og som admin-handling “Genberegn arter i alle artikler” (worker-kommando).

1. **Fra emneord:** emneord med `kind='art'` → `source='emneord'`.
2. **Fra tekst** (titel + lead + body_text) og **OCR** (`source='ocr'`, kun hvis ikke allerede fundet i teksten):
   - Byg en matcher én gang pr. proces (cache med `species`-versionen) fra `species.navn` og `species.latin` for
     `NOT excluded`; inkl. underarter/racer, men mapp til artens artnr hvor DOFbasen har en sådan relation – ellers
     taxonets eget artnr.
   - Generér danske bøjningsformer pr. navn: grundform, `+e`, `+er`, `+r`, `+n`, `+en`, `+erne`, `+ne`, `+s`, `+ens`,
     `+ernes` (kun for sidste ord i flerordsnavne: “Stribede Ryler”, “Dværgterner”). Varianter: `Alm.`/`Almindelig`,
     bindestreg/mellemrum.
   - Én samlet regex (længste navne først, `\b…\b`, case-insensitiv), så “Sortgrå Ryle” vinder over “Ryle”.
   - **Tvetydige navne** (`ambiguous_species` i indstillingerne – navne der også er almindelige ord eller efternavne):
     tæller kun, hvis ordet står med stort begyndelsesbogstav midt i en sætning, eller arten også er et emneord.
   - Gem `hits` og `first_pos`. Behold rækker med `source in ('redaktoer','fravalgt')` urørt.
3. Artikelsiden viser arterne som chips (sorteret efter `species.sortering`), med OCR-fund markeret svagere.
   Redaktører kan fjerne (→ `fravalgt`) og tilføje (→ `redaktoer`).

### 5.4 Søge-API

`GET /api/artikler` med `q`, `mode` (`ord`|`praecis`), `tag` (gentagelig, AND), `art` (gentagelig, OR – “nævner én
af”), `forfatter`, `fra`/`til` (år), `type`, `kind`, `har_billeder`, `har_pdf`, `sort` (`relevans`|`nyeste`|`aeldste`|
`mest_laest`|`mest_kommenteret`), `side` (20 pr. side). Svar: `{total, rows:[{id, title, dato, authors, teaser|headline,
cover:{thumb, w, h}|null, tags:[…3], arter:[…5], kommentarer, views, kind}], facets:{aar:[…], type:[…]}}`.
Brugeren ser kun `publiceret` (og `planlagt`/`kladde` hvis `can_edit`, markeret).

---

## 6. Brugerflader

Én skal (`web/js/views/artikler.js`) med faner **Forside · Søg · Emneord · Arter · Forfattere** (+ **Admin** for dem med
modulet `artikler`), og én fil pr. fane i `web/js/artikler/` (`forside.js`, `soeg.js`, `emneord.js`, `arter.js`,
`forfattere.js`, `artikel.js`, `kommentarer.js`, `admin.js`, `import.js`). CSS i `web/css/artikler.css`, afgrænset
til `.art`. Brug `h()`, `api()`, `fmtDate`, `modal`, `toast`, `seg` fra `core.js`. Ingen eksterne skrifttyper eller
CDN'er; overskrifter i en serif-systemstak (`Georgia, "Times New Roman", serif`), brødtekst i Inter som resten af appen.
Skal virke på telefon (én kolonne) og i Capacitor-appen. Lazy-load billeder (`loading="lazy"`).

### 6.1 Forside (avis)

`GET /api/artikler/forside` (cachet i Valkey 5 min, invalideres ved publicering) leverer færdige sektioner:

```
┌──────────────────────────────────────────────────────────────┐
│ ARTIKLER                                 fredag 30. okt. 2026 │  ← “masthead”: titel, dato, søgefelt
│ Nyhed · Feltbestemmelse · Fænologi · Pandion · …              │  ← emneordsstrimmel (mest brugte)
├──────────────────────────────────┬───────────────────────────┤
│  [STORT BILLEDE]                 │  Seneste                  │
│  Hovedhistorie (titel, manchet,  │  • titel   dato           │
│  byline, 💬 n)                   │  • titel   dato           │
├───────────┬───────────┬──────────┤  …                        │
│ [billede] │ [billede] │ [billede]│───────────────────────────│
│ titel     │ titel     │ titel    │  Mest læst / kommenteret  │
├───────────┴───────────┴──────────┴───────────────────────────┤
│ PÅ DENNE DAG  – artikler fra samme dag/måned tidligere år     │
├──────────────────────────────────────────────────────────────┤
│ ARTER I FOKUS – mest omtalte arter (seneste 12 mdr / hele arkivet) │
├──────────────────────────────────────────────────────────────┤
│ FRA ARKIVET – PDF'er fra Pandion / DOF NYT med forsideminiature │
└──────────────────────────────────────────────────────────────┘
```

- **Hovedhistorie:** nyeste `featured_until > now()`, ellers nyeste publicerede *med* billede, ellers nyeste.
- **Billeder (“husk billeder”)** – rækkefølge for hvert kort: `cover_media_id` → første billede i artiklen →
  PDF-miniature af side 1 → **typografisk pladsholder** (CSS: farvet flade i en af portalens farver, valgt
  deterministisk ud fra emneord/id, med artiklens første art eller emneord sat stort, og et lille fugleikon). Ingen
  hotlinks til eksterne billeder.
- Da næsten hele arkivet er gammelt, skal “Seneste” sortere på `coalesce(published_at, dato)`; nye lokale artikler
  kommer dermed øverst, og importerede ligger efter deres oprindelige dato.

### 6.2 Artikelside

Titel (serif, stor) · manchet · byline (forfattere, dato i dansk format, “Læst N gange” = `views_imported + views`) ·
cover med billedtekst/kreditering · brødtekst (`body_html`) · **PDF-artikler:** indlejret visning med
`<iframe src="/media/art/…pdf">` på desktop; i appen/på mobil (hvor webview ikke viser PDF) en stor “Åbn PDF”-knap og
foldbar “Tekst fra PDF'en (maskinlæst, kan indeholde fejl)” med `text_md` renderet · galleri med lysboks (pile,
Escape, swipe) for alle billeder · **Arter** (chips) · **Emneord** (chips → `#/artikler/emneord/<id>`) ·
“Oprindeligt bragt på netfugl.dk” med `source_url` · **Relaterede artikler** (flest fælles arter/emneord, 4 stk) ·
**Følg/Følg ikke**-knap · kommentarer (§8).
Redaktører ser “Redigér”, “Revisioner” og status. Læsninger tælles én gang pr. bruger pr. døgn (Valkey `SET NX` med
udløb), batch-skrives til `views`.

### 6.3 Søg

Søgefelt + skift **Ord / Præcis tekst**, filtre (år fra–til, type, kun med billeder, kun PDF), sortering, resultatliste
med miniature, titel, dato, forfattere, uddrag med fremhævede træf, chips for emneord/arter. Tilstanden ligger i
URL'en (`setParams`), så en søgning kan deles.

### 6.4 Emneord

Grupperet efter `kind` (Emner, Steder, Publikationer, År, Arter), A–Å-bogstavfilter, tal pr. emneord, filterfelt.
Klik → artikelliste (genbrug søgekomponenten med `tag=<id>`); flere emneord kan kombineres (AND).

### 6.5 Arter

Autosuggest på art (genbrug `/api/species?q=` / `services/species_info.search`) + liste over alle arter med antal
artikler i systematisk rækkefølge (`species.sortering`), med filterfelt. Klik → artikler om arten, sorteret efter
`hits` (artikler hvor arten er emneord eller nævnt mest først) eller dato.

**Integration:** på portalens artsside (`web/js/views/species.js`) vises en boks “Artikler om <art>” (5 seneste + “se
alle”), når `artikler` er synligt. Endpoint: `GET /api/artikler/arter/{artnr}?limit=5`.

---

## 7. Fælles markdown-editor og renderer

### 7.1 Komponent (`web/js/editor/md-editor.js`, `web/js/editor/media-picker.js`, `web/css/editor.css`)

Egen, afhængighedsfri komponent (ingen build-trin i projektet; CodeMirror/ProseMirror kræver bundling): en `<textarea>`
med værktøjslinje og forhåndsvisning.

```js
const ed = mdEditor({ value, ownerKind: "artikel" | "nyhed", ownerId, renderUrl: "/api/content/render",
                      features: { species: true, articleLinks: true, embeds: true }, onChange });
ed.el; ed.value; ed.focus(); ed.destroy();
```

Værktøjslinje (med tastaturgenveje og `title`-tekster på dansk):
- Overskrift H2 / H3 / H4 (H1 er forbeholdt titlen) · **Fed** (Ctrl+B) · *Kursiv* (Ctrl+I) · ~~Gennemstreget~~ ·
  Link (Ctrl+K, dialog med tekst + adresse) · Citat · Punktliste · Nummereret liste · Tabel (vælg rækker × kolonner) ·
  Vandret streg · Kode/kodeblok.
- **Billede:** upload via knap, træk-og-slip og indsæt fra udklipsholder; skaleres i browseren til højst 2560 px
  (som Netfugl-fotos), fremdrift, **alt-tekst påkrævet** i en lille dialog, valgfri billedtekst/kreditering;
  indsætter `![alt](media:ID "billedtekst")`. Medievælger viser artiklens/nyhedens eksisterende medier.
- **Galleri:** vælg flere billeder → `:::galleri` … `:::`-blok.
- **Faktaboks:** `:::boks Titel` … `:::` (avisens klassiske faktaboks).
- **Vedhæft PDF/fil:** → `[Titel (PDF, 3,2 MB)](media:ID)`.
- **Indlejr:** indsæt en adresse (xeno-canto, Vimeo …) eller rå HTML; editoren advarer, hvis værten ikke er i
  `embed_hosts` (serveren fjerner den alligevel).
- **Art** (kun artikler): autosuggest → `[[art:ARTNR]]`. **Artikellink:** søg artikel → `[titel](artikel:ID)`.
- Tilstande: **Skriv / Vis / Side om side** (side om side kun ≥ 1000 px), fuld skærm, ord- og tegntal, læsetid.
- Indsæt tekst med `document.execCommand("insertText")`, så browserens fortryd (Ctrl+Z) virker; fald tilbage til
  `setRangeText` hvor det ikke findes. Tab/Shift+Tab ind-/udrykker listepunkter; Enter i en liste fortsætter listen.
- Forhåndsvisning renderes af **serveren** (`POST /api/content/render`, debounce 400 ms), så forhåndsvisning og
  resultat altid er ens og sanitiseringen kun findes ét sted.
- Kladde gemmes i `localStorage` (nøgle pr. artikel/nyhed) hvert 5. sekund og tilbydes gendannet ved åbning.
- Advarsel ved forsøg på at forlade siden med ugemte ændringer.

### 7.2 Metadata-panel (artikler; kollapses til et sidepanel på desktop, faneblad på mobil)

Titel · manchet · forfattere (fri tekst + valgfri kobling til portalbruger, flere) · dato · emneord (autosuggest, opret
nyt) · arter (automatisk fundne vises med kilde og afkrydsning; tilføj manuelt) · forsidebillede (vælg blandt
artiklens medier eller upload) + billedtekst/kreditering · type · status (kladde / publiceret / planlagt med
`publish_at`) · fremhæv på forsiden til dato · kommentarer åbne · kilde-URL.
Gem: `PUT` med `version`; svarer serveren 409, vises “Artiklen er ændret af <navn> imens – åbn ny version / overskriv”.
Revisioner: liste, se en tidligere version renderet, gendan (laver en ny revision).

For **nyheder** (Admin → Indhold, `web/js/views/admin/indhold.js`): samme editor, et mindre panel (titel, manchet,
forsidebillede, status/planlagt, publiceret), liste over nyheder med Redigér/Slet i stedet for kun Slet.

### 7.3 Server-renderer (`services/content_render.py`)

- **markdown-it-py** (MIT) med `mdit-py-plugins` (MIT): CommonMark + tabeller, gennemstreget, autolinks, fodnoter og
  containere (`galleri`, `boks`); egne regler for `media:`, `netfugl:`, `artikel:` og `[[art:…]]`.
  `html=True` for artikler/nyheder, `html=False` for kommentarer.
- **nh3** (MIT, Rust/ammonia) til sanitisering efter rendering. Profiler:
  - `artikel`/`nyhed`: tilladte tags `p h2 h3 h4 h5 strong em del s a ul ol li blockquote code pre table thead tbody tr
    th td img figure figcaption hr br sup sub span div details summary iframe`; attributter: `a[href title]`,
    `img[src alt title width height srcset sizes loading]`, `iframe[src width height allow allowfullscreen]`,
    `class` kun med værdier fra en fast liste (`art-galleri`, `art-boks`, `art-chip`, `art-pdf` …),
    `td/th[colspan rowspan align]`. URL-skemaer: `https`, `mailto`, relative. Ingen `style`, `on*`, `script`, `form`,
    `object`, `embed`.
  - `iframe` kun hvis værten står i `embed_hosts` og `src` er `https`; tilføj `sandbox="allow-scripts
    allow-same-origin allow-popups"`, `loading="lazy"`, `referrerpolicy="no-referrer"`; ellers erstattes den af et link.
  - Billeder kun fra `/media/…` (eksterne billedadresser bliver til et link + i editoren tilbuddet “Hent og gem lokalt”)
    – undgår at læsere afslører deres IP til tredjeparter.
  - Eksterne links: `rel="noopener noreferrer"` og `target="_blank"`; i kommentarer også `nofollow ugc`.
  - `kommentar`: kun `p strong em del a ul ol li blockquote code br`, ingen billeder/HTML.
- `RENDER_VERSION` konstant; øg den ved ændringer. Admin-handling “Render alle igen” (worker) gen-renderer artikler,
  nyheder og kommentarer med `render_version < RENDER_VERSION`.
- `POST /api/content/render {md, profile, owner_kind, owner_id}` → `{html, species:[…detected], warnings:[…]}`
  (kræver `artikler` eller `indhold`; højst 200 kB input; rate-limit).
- `POST /api/content/media` (multipart: fil, owner_kind, owner_id?) → `{id, url, thumb, width, height}`; samme
  validering som importen (signatur, størrelse ≤ 25 MB billede / 50 MB PDF, EXIF-strip, varianter).

### 7.4 Nyhedsvisning

- `GET /api/news` beholder sine felter (`body` = markdown, gamle klienter viser stadig tekst) og får `lead`, `html`,
  `cover`. `GET /api/news/{id}` er ny.
- “Nyt fra portalen” i `web/js/views/feed.js`: titel, dato, miniature (hvis cover), manchet eller de første ~200 tegn af
  teksten, “Læs mere” → `#/nyheder/<id>` (ny side `web/js/views/nyhed.js`, viser `html`).
- Planlagte nyheder publiceres af samme planlagte opgave som artikler (§9).

---

## 8. Kommentarer, moderation og notifikationer

### 8.1 Kommentarer

- Under artiklen: først “Kommentarer fra netfugl.dk (arkiv)” (read-only, navn + dato, ingen svar-knap), derefter
  portalens kommentarer med navn/obserkode som i trådene.
- Skriv: tekstfelt med let markdown (fed, kursiv, link, liste), højst 5 000 tegn, forhåndsvisning via
  `profile=kommentar`. Svar på en hovedkommentar (ét niveau, som netfugl).
- Ret egen kommentar inden for `comment_edit_minutes` (vises “redigeret”); slet egen (status `slettet`, vises som
  “Kommentaren er slettet”, hvis den har svar).
- Afvises med forklaring, hvis brugeren har en aktiv udelukkelse (`services/moderation.active_ban`), hvis
  `comments_open` er falsk, eller ved mere end 5 kommentarer pr. minut (Valkey-tæller).
- Kommentarer fra udelukkede brugere med `hide_comments` filtreres med `moderation.VISIBLE`-betingelsen.
- Live: efter ændringer publiceres en SSE-hændelse (som `moderation.comments_changed`, type `art_comment`, med
  `article`), og åbne artikelsider henter sporet igen. Se `web/js/live.js` for, hvilke kanaler strømmen sender.

### 8.2 Anmeld og moderation

- “Anmeld” på hver kommentar (ikke egne): årsag (chikane, spam, forkert/vildledende, andet) + valgfri note.
  Én anmeldelse pr. bruger pr. kommentar.
- Når `report_count >= report_threshold`, skjules kommentaren automatisk (`status='skjult'`, `hidden_reason='Anmeldt –
  afventer moderator'`), og moderatorerne får en notifikation.
- **Admin → Moderation** (eksisterende side `web/js/views/admin/moderation.js`) får sektionen **Artikelkommentarer**:
  faner *Anmeldte* · *Skjulte* · *Seneste*; for hver kommentar: artikel, forfatter, tekst, anmeldelser; handlinger
  **Skjul** (årsag; valgfrit “giv brugeren besked”), **Gendan**, **Slet**, **Afvis anmeldelser**, **Udeluk bruger**
  (genbrug `banForm` fra `web/js/moderation.js`). Arkivkommentarer kan skjules/gendannes.
- Moderatorer kan også skjule direkte fra artikelsiden. Alle handlinger logges (aktivitetsloggen,
  `services/activity.py`).

### 8.3 Notifikationer

**Fælles leveringsfunktion først:** `api/social.py:_notify` og `services/alerts.deliver` gør det samme (gem i
`notifications` med dedup, SSE til `user:<id>`, push med stille timer). Lav `services/notify.py` med
`async def notify(session, user_ids, *, title, body, url, dedup, push=True)` oven på `alerts.deliver`, og lad
`social._notify` kalde den (det fjerner samtidig en krydsimport fra API-laget). Artikelmodulet bruger kun den.

Regler:
| Hændelse | Modtagere | Dedup-nøgle |
|---|---|---|
| Artikel publiceres første gang (manuelt eller planlagt) – **aldrig ved import** | `art_subscriptions(kind='alle', muted=false)` | `art:ny:<id>` |
| Ny kommentar | brugere med `art_subscriptions(kind='artikel', ref=<id>, muted=false)` undtagen forfatteren | `art:k:<article>:<ÅÅÅÅMMDDTT>` (højst én pr. artikel pr. time pr. bruger) |
| Svar på din kommentar | forfatteren af hovedkommentaren (også selvom den har slået følg fra) | `art:svar:<comment_id>` |
| Kommentar automatisk skjult efter anmeldelser | brugere med modulet `moderation` | `art:anm:<comment_id>` |
| Din kommentar er skjult af moderator (hvis valgt) | forfatteren | `art:skjult:<comment_id>` |

- Første kommentar på en artikel opretter `art_subscriptions(kind='artikel', via='kommentar')`, medmindre brugeren har
  en `muted`-række.
- Indstillinger: på artikelsiden (Følg/Følg ikke) og et lille panel “Notifikationer” i modulet (+ på profilen):
  *Nye artikler* (standard **fra**) og *Nye kommentarer på artikler jeg har kommenteret* (standard **til**, slår alle
  `via='kommentar'`-abonnementer fra/til). Push følger brugerens eksisterende push-indstilling og stille timer.
- Afsendelse sker i baggrunden (`BackgroundTasks` eller worker), ikke i request'en, og kun hvis
  `artikler_notifikationer` er slået til.

---

## 9. Worker og planlagte opgaver

- Kommandoer (i `worker.command_listener` + `jobstatus.LABELS`): `articles_import` (`{import_id}`),
  `articles_species` (genberegn arter for alle), `content_rerender`.
- Planlagt opgave `articles_publish` hvert 5. minut: publicér artikler og nyheder med `status='planlagt'` og
  `publish_at <= now()`, og send “ny artikel”-notifikationer. Tilføj i `services/schedule.TASKS` **og**
  `worker.SCHEDULED`, med `worker.FEATURE_OF["articles_publish"] = "artikler"` (nyheder skal dog publiceres uanset flag
  – lad opgaven publicere nyheder altid og kun artikler, når flaget er til; eller lav to opgaver).
- `views`-tællere skrives fra Valkey til databasen hvert 10. minut (samme opgave er fin).

---

## 10. API-oversigt

Nyt API-modul `server/portal/api/artikler.py` (brugerdelen) og `server/portal/api/admin/artikler.py` (admin), plus
`server/portal/api/content.py` (render + medieupload, fælles for artikler og nyheder). **Ingen SQL i API-modulerne** –
tilføj filerne til listen i `tests/test_architecture.py`. Services: `services/articles.py` (læsning, søgning, forside,
redigering), `services/article_import.py`, `services/article_species.py`, `services/article_comments.py`,
`services/content_render.py`, `services/content_media.py`, `services/notify.py`.

| Metode og sti | Adgang |
|---|---|
| `GET /api/artikler/forside` | `artikler` synligt |
| `GET /api/artikler` (søg, §5.4) | 〃 |
| `GET /api/artikler/{id}` · `GET /api/artikler/n/{netfugl_id}` | 〃 |
| `GET /api/artikler/emneord` · `/arter` · `/arter/{artnr}` · `/forfattere` | 〃 |
| `GET /api/artikler/{id}/kommentarer` | 〃 + `artikler_kommentarer` |
| `POST /api/artikler/{id}/kommentarer` · `PATCH`/`DELETE /api/artikler/kommentarer/{cid}` | 〃, egen kommentar |
| `POST /api/artikler/kommentarer/{cid}/anmeld` | 〃 |
| `PUT`/`DELETE /api/artikler/{id}/foelg` · `GET`/`PUT /api/artikler/notifikationer` | 〃 + `artikler_notifikationer` |
| `POST /api/admin/artikler` · `PUT`/`DELETE /api/admin/artikler/{id}` | modul `artikler` + `artikler_editor` |
| `GET /api/admin/artikler/{id}/revisioner` · `POST …/revisioner/{rev}/gendan` | 〃 |
| `PUT /api/admin/artikler/{id}/arter` | 〃 |
| `GET`/`PUT`/`POST /api/admin/artikler/emneord[/{id}]` · `POST …/emneord/{id}/flet` | modul `artikler` |
| `POST`/`GET /api/admin/artikler/import` · `PUT …/import/{id}/del/{n}` · `POST …/{id}/start` · `GET`/`DELETE …/{id}` | 〃 |
| `POST /api/admin/artikler/genberegn-arter` · `POST /api/admin/content/render-alle` | 〃 |
| `GET`/`PUT /api/admin/artikler/indstillinger` | 〃 |
| `GET /api/admin/artikler/kommentarer?status=` · `POST …/kommentarer/{cid}/{skjul,gendan,slet,afvis}` | modul `moderation` |
| `POST /api/content/render` · `POST /api/content/media` · `DELETE /api/content/media/{id}` | modul `artikler` eller `indhold` |
| `GET /api/news/{id}` · eksisterende `/api/news`, `/api/admin/news` udvides (+ `PUT /api/admin/news/{id}`) | som i dag |
| `GET|HEAD /media/art/{name}` | se §4.5 |

Pydantic-modeller til alle inputs med længdegrænser. Opdatér rutesnapshot:
`UPDATE_ROUTES=1 python -m pytest tests/test_routes.py`.

---

## 11. Afhængigheder (bekræft licenser i PR'en)

| Pakke | Brug | Licens |
|---|---|---|
| `markdown-it-py`, `mdit-py-plugins` | markdown → HTML | MIT |
| `nh3` | HTML-sanitisering | MIT |
| `Pillow` | billedstørrelser og varianter | MIT-CMU (HPND, OSI-godkendt) |
| `poppler-utils` (`pdftoppm`, `pdfinfo`, apt i `server/Dockerfile`) | miniature af PDF side 1, sideantal | GPL-2/3 – køres som separat program, linkes ikke |

Brug **ikke** `pypdfium2` (Googles PDFium) eller PyMuPDF (AGPL). Ingen JS-biblioteker til editoren. Ingen Google-tjenester.

---

## 12. Test

Pytest i `server/tests/`:
- `test_article_import.py`: byg en lille zip i `tmp_path` (3 artikler: én med billeder og forsidebillede, én PDF med
  `.ocr.md`, én med indlejrede kommentarsvar og links `../0155/artikel.md`). Tjek: rod-detektion, `_index.json`
  ignoreres, titel-linjer fjernes, referencer omskrives til `media:`/`netfugl:`, kommentartræ flades korrekt ud,
  **gentaget import er idempotent** (ingen dubletter, `archive_key`), `locally_edited` bevares uden
  `overwrite_local`, skjult arkivkommentar forbliver skjult, **ingen notifikationer**, zip-slip (`../x`, absolut sti,
  symlink) og zip-bombe afvises, ukendte filtyper ignoreres, `schema_version != 1` springes over.
- `test_article_species.py`: “Dværgterner” → Dværgterne, “Sortgrå Ryle” vinder over “Ryle”, “Alm. Ryle”, latinsk navn,
  tvetydigt navn (“Ravn” som efternavn tælles ikke, “… så en Ravn …” tælles), `fravalgt` overlever genberegning,
  emneord → art.
- `test_content_render.py`: `<script>`, `onerror=`, `javascript:`-links, `style`, iframe fra ukendt vært fjernes;
  iframe fra tilladt vært får `sandbox`; eksternt billede bliver link; `media:`/`netfugl:`/`[[art:…]]` oversættes;
  kommentar-profil fjerner HTML og billeder; galleri- og boks-containere.
- `test_article_search.py`: `websearch_to_tsquery` med frase og udelukkelse, præcis-tilstand escaper `%`/`_`,
  OCR-træf markeres, kladder skjules for almindelige brugere.
- `test_article_comments.py`: udelukket bruger afvises, rate-limit, auto-skjul ved tærskel, følg oprettes ved første
  kommentar og respekterer `muted`, dedup pr. time.
- Eksisterende tests skal fortsat være grønne: `test_architecture.py` (tilføj de nye API-moduler), `test_dbrouter.py`
  (nye tabeller i `USER_TABLES`), `test_routes.py` (snapshot), `test_schedule.py`.
- `tools/e2e/`: tilføj artikelmodulets faner, en artikelside, editoren og Admin → Indhold til røgtesten.

---

## 13. Faser (byg og commit i denne rækkefølge)

**Fase 1 – Data og læsning**
Modeller + DDL, `dbgroups`, funktionsflag, `MODULES`, `/api/me`, storage-præfiks + `/media/art`, chunked upload,
import-worker med rapport, emneordsklassificering, artsindeksering, renderer (kun læsning), forside, artikelside,
Søg (begge tilstande), Emneord, Arter, Forfattere, Admin → Import/Emneord/Indstillinger. Test for import, arter,
renderer, søgning.
*Færdig når:* `Artikler.zip` (6 artikler) og derefter en zip af hele `Artikler/` kan importeres to gange i træk uden
dubletter, og alle 802 artikler kan findes på titel, råtekst, emneord og art.

**Fase 2 – Kommentarer, moderation og notifikationer**
`services/notify.py` (+ omlægning af `social._notify`), kommentarer (arkiv + nye), anmeld, auto-skjul,
moderatorsektion, følg/notifikationsindstillinger, SSE-opdatering. Test.

**Fase 3 – Editor**
`md-editor.js`, `media-picker.js`, `/api/content/*`, artikeleditor med metadata-panel, revisioner, optimistisk låsning,
planlagt publicering (`articles_publish`), nyheder i Admin → Indhold med samme editor, nyhedsmigrering, `#/nyheder/<id>`
og ny “Nyt fra portalen”-boks.

**Fase 4 – Finish**
“Artikler om arten” på artssiden, relaterede artikler, “På denne dag”, “Arter i fokus”, følg emneord/arter
(`art_subscriptions` kind `emneord`/`art`), Render-alle, e2e, opdatér `docs/funktioner.md`, `README.md` og
`docs/arkitektur.md` (nyt modul i listen over services).

---

## 14. Acceptkriterier (samlet)

- [ ] Modulet er usynligt (menu og 404 i API'et), når `artikler` er `off`, og kun synligt for superadmin/modul
      `artikler` i `preview`.
- [ ] Admin kan uploade en zip på flere GB med fremdrift, genoptage efter afbrudt forbindelse, og se en importrapport
      (oprettet/opdateret/uændret/sprunget over/fejl pr. artikel).
- [ ] Gentaget import giver ingen dubletter af artikler, medier, emneord eller arkivkommentarer, og overskriver ikke
      lokalt redigerede artikler uden at blive bedt om det.
- [ ] Ingen notifikationer under import.
- [ ] Forsiden viser altid et billede eller en pæn pladsholder på hvert kort; PDF-artikler viser forsiden af PDF'en.
- [ ] Søgning finder ord i titel, brødtekst og OCR-tekst; “Præcis tekst” finder delord; emneord kan kombineres;
      resultater kan filtreres på år og sorteres.
- [ ] Hver artikel viser de arter, den nævner; “Arter”-fanen lister alle arter med antal artikler; artssiden i portalen
      viser artikler om arten.
- [ ] Arkivkommentarer vises read-only; nye kommentarer kan skrives, rettes, slettes, besvares og anmeldes; moderatorer
      kan skjule/gendanne/slette og udelukke; udelukkede brugere kan ikke kommentere.
- [ ] Brugere kan slå notifikationer til for nye artikler og for kommentarer på artikler, de har kommenteret; push
      respekterer stille timer; højst én kommentar-notifikation pr. artikel pr. time.
- [ ] Editoren kan: H2–H4, fed/kursiv/gennemstreget, links, lister, tabeller, citater, kode, billeder (upload, træk,
      indsæt, alt-tekst), gallerier, faktaboks, PDF-vedhæftning, indlejring fra tilladte værter, arter og
      artikellinks, forhåndsvisning, kladde i browseren, revisioner, planlagt publicering og metadata.
- [ ] Al HTML (artikler, nyheder, kommentarer) er sanitiseret server-side; testen for XSS-vektorer er grøn.
- [ ] Portalens nyheder bruger samme editor; eksisterende nyheder vises korrekt efter migreringen; gamle klienter, der
      læser `body`, virker stadig.
- [ ] `python -m pytest` i `server/` er grøn, inkl. arkitektur-, rute-, databasegruppe- og planlægningstests.

---

## 15. Åbne punkter (afklar med ejeren undervejs – byg med standardvalget)

1. **Rettigheder til indholdet.** Netfugl-showcasen kopierede bevidst kun uddrag. Fuld visning af artikler og
   arkivkommentarer forudsætter, at Netfugl/DOF har givet lov. Standard: modulet er `preview`, til det er afklaret.
2. **Forfattere ↔ brugere.** Ingen mapping fra `netfugl_forfatter_id` til portalbrugere findes. Standard: kun navne;
   `authors[].user_id` kan sættes manuelt i editoren. En mapping-tabel kan komme senere (også for arkivkommentarer).
3. **Omdirigering af gamle netfugl.dk-links** (`/artikler/<id>/…`) ligger uden for portalen; `#/artikler/n/<id>` er
   klar til at modtage dem.
4. **Turrapporter** importeres ikke nu; `kind='turrapport'` og et ekstra trin i importeren er den forventede udvidelse.
