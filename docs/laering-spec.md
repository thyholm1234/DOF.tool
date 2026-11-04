# Læring 2.0 – e-læringsportal for kommende og nuværende feltbisser (specifikation til implementering)

Status: specifikation, 3. november 2026. Skrevet til at blive bygget trin for trin (fase 0–8, §14) af en anden
model/udvikler. Læs **`docs/arkitektur.md`** først – alle regler dér gælder. Læs derefter **`docs/netfugl-afloesning-spec.md`**
§3 (flag, rettigheder, datamodel) – mønstrene derfra genbruges her og gentages ikke.

> **Regel for den, der bygger:** Hver fase slutter med grønne tests (`pytest server/tests`), opdateret
> rute-snapshot (`server/tests/fixtures/api_routes.json`) og ét commit. Byg ikke videre på en rød fase. Er noget uklart,
> så vælg standardvalget i §15 og skriv det i commit-beskeden – stop ikke.

---

## Status (3.–4. november 2026): hvad er bygget, og hvad afviger fra specen

Fase 0–7 er bygget autonomt (commits `612a85a` … «fase 7»; se `git log --grep=Læring`). Alle flag står som i §3.1 (`preview`/`off`), så den gamle Læring er uændret,
til en superadmin sætter **Admin → Funktioner → «Læring 2.0»** til *til*. Trækrutes kurser og øvelser er importeret som **kladder** første gang (redaktøren
gennemlæser og publicerer), sammen med tre eksempelforløb (kladder, kan først publiceres når deres kurser er publiceret).

**Afvigelser og det, der ikke er bygget**

| Spec | Virkelighed |
|---|---|
| §15 #2 anonyme brugere | Hele portalen kræver login (`main.py: PUBLIC_API`), så anonyme kan ikke bruge Læring. Koden tåler `user = None`, men ruterne er login-beskyttede. |
| §4.6 skrifter (Inter, Instrument Serif, DM Mono) | Ikke indlæst; Læring bruger portalens skrifter og designtokens (lys/mørk følger portalen). |
| §5.2 «kun aggregater» | `obs_odds`/`obs_habitat` og «Forbered en tur» læser `observations`/`trips` for **én lokalitet ad gangen** (`loknr`-indekseret, `hemmelig = 0`, kun ≥ 30 ture), fordi aggregaterne ikke har uge-opløsning pr. lokalitet. |
| §9.1 oplåsning «efter forrige» | Betyder det forrige *krævede* punkt – valgfrie punkter blokerer ikke. |
| §5.3 «Se igen», «Rapportér fejl i spørgsmålet», L/M/H-genveje | Ikke bygget (tal 1–8, Enter og mellemrum virker). |
| §13.4 offline | **Ikke bygget.** Facit forlader aldrig serveren (§5.1), så et spørgsmål kan ikke besvares uden net; en offline læseversion af lektionsteksten er udeladt. |
| §12.2 `foto_sammenlign`/`lyd_art` på «Lignende arter», Familiequiz | Ikke seedet (kræver artsnumre og `seed/laering/familier.json`, §15 #4). Byggeren kan lave dem. De tre øvelser Mine fejl, Færdighedstest og Fugl for fugl er i stedet indbyggede funktioner (Repetition, `#/laer/test`, `#/laer/dagens`). |
| §6.2 «uendelig» antal | Ikke bygget (10/20/40). |
| §13.1 hastighedsgrænse | Enkel tæller pr. bruger pr. minut (`_rate` i `api/learn_play.py`) på session, svar, træning og dagens art. |
| §8.4 sonagram, §15 #5 | Udskudt som aftalt. |
| §8.2 medforfattere | Kun ejeren/redaktører kan tilføje (via API; ingen brugerflade endnu). |
| §10 feltopgaver | `periode: "efter_oplaasning"` regnes fra holdets startdato (ellers tilmeldingstidspunktet). Worker-opgaven `learn_field` kører hver time (Admin → Drift). |
| §13.3 GDPR | Udtræk og sletning sker via skemaet (alle `lr_*`-tabeller har fremmednøgle til `users`). Svarloggen slettes efter 3 år i den natlige oprydning (ikke aggregeret). |
| §14 fase 8 | Dokumentation er opdateret. Oprydning (sletning af `views/learn.js`-visningen og `learn-arter.js`, de gamle endpoints) er **ikke gjort**, fordi den gamle Læring stadig er faldbacken, mens flaget ikke er `til` i drift. |

**Kursuspakker (4. nov. 2026):** `seed/laering/*.json` i DOF.tool-formatet importeres én gang som kladder (`learning.seed_packs`, markør pr. pakke).
Første pakke er **«Phyllernes verden»** (`phylloscopus`, 13 lektioner, 132 aktiviteter) + øvelsen `phylloscopus-traening`. Anden pakke er **«Mågernes verden»** (`maager`, 14 lektioner, 114 aktiviteter, kun arter på Den Danske Liste) + øvelsen `maager-traening`. Automatiske fotos vælges blandt alle publicerede fotos; en reference kan kræve en bestemt `alder` (fotobibliotekets aldertekst), så et erstatningsfoto aldrig viser en forkert alder. Startindhold kan ikke kende en
installations foto-/optagelses-id'er, så blokke og aktiviteter kan pege på medier med en **reference** (`ref`/`refs` i blokke, `config.foto_ref`/`lyd_ref`
i aktiviteter): `nf` (netfugl-foto), `xc` (xeno-canto-nr.), `artnr`/`latin` + `type` (sang/kald) + `n`. Referencen løses ved visning
(`learn_play.photo_by_ref`/`rec_by_ref`); finder en reference intet foto, advarer byggeren (gult).
Et fast valgt foto/lyd i byggeren har forrang («Brug automatisk» fjerner det igen). Manglende optagelser hentes hos xeno-canto med lydquiz-nøglen
(også underarter, fx *tristis*, og arter uden artsnummer, der gemmes med `artnr = 0`).

**Før flaget sættes til «til» i drift:** gennemlæs og publicér de importerede kurser/øvelser, tjek at lydquiz-nøglen (Admin → Funktioner → Lydquiz) er sat, og vælg hvem der skal have rettigheden *Læring: underviser* (Admin → Brugere).

---

## 0. Kort fortalt

Læring (`#/laer`) bliver en egentlig e-læringsportal. Udgangspunktet er **Trækrute** – en dansk prototype i
`/home/chris/Dokumenter/Craft Anything/playflyway-com/app/` (ren HTML/CSS/ES-moduler, data i `localStorage`), som er bygget
med *playflyway.com* som inspiration. Trækrute giver os **pædagogikken, sidestrukturen, indholdet og afspilleren**.
DOF.tool giver det, ingen anden læringsside har: **DOFbasens observationer, fotobiblioteket og lydoptagelser fra
xeno-canto/Macaulay Library**.

| Del | Hvad | Rute | Afsnit |
|---|---|---|---|
| A | **Ny brugerflade**: forside, kurser, udforsk, kursus, øvelse, min fremgang | `#/laer…` | §4 |
| B | **Aktivitetsmotor** med statiske og *datadrevne* aktiviteter (foto, lyd, DOFbasen) og sikkerhedsangivelse | – | §5 |
| C | **Egne trænings­sessioner** (foto og lyd) – bevares og udvides | `#/laer/arter`, `#/laer/lyd`, `#/laer/min` | §6 |
| D | **Forbered en tur**: lokalitet + dato → forventede arter → træning | `#/laer/forbered` | §7 |
| E | **Kursusbygger** | `#/laer/byg/kursus/<slug>` | §8 |
| F | **Forløbsbygger** og hold (forløb med kurser, øvelser og feltopgaver; hold med underviser) | `#/laer/byg/forloeb/<slug>`, `#/laer/hold/<id>` | §9 |
| G | **Feltopgaver** der afkrydses af brugerens egne DOFbasen-observationer | – | §10 |
| H | **Fremgang**: færdigheder, artsmestring, kalibrering, repetition, beviser | `#/laer/fremgang` | §11 |

### Beslutninger (fra ejeren)

| Spørgsmål | Beslutning |
|---|---|
| Hvor | Under det eksisterende modul **Læring** (`#/laer`, rettighed `laering`). Ingen ny showcase. |
| Egne fotos/lyd-træning | **Skal blive ved med at virke** (artsquiz `#/laer/arter`, lydquiz `#/laer/lyd`, egne artsvalg, valg/tekst-svar, sang/kald/begge). De bliver en del af den nye portal, ikke erstattet. |
| Datakilder | DOFbasens observationer (via aggregaterne), fotobiblioteket (`gal_*`), lydoptagelser (`snd_recordings`: xeno-canto + Macaulay Library). |
| Nyt | Kursusbygger og forløbsbygger. |
| Originalen | Ingen fotos, lyde, logo, navn eller tekst fra playflyway.com (afklaret i Trækrute-projektet, `investigation.cany/unknowns/unk_7223…`). Trækrutes egen tekst, egne SVG-silhuetter og egen dansk tekst må bruges. |
| Licenser | Kun open source; ingen Google-tjenester, ingen eksterne CDN'er (jf. hukommelse/arkitektur). Skrifterne i Trækrute (Inter, Instrument Serif, DM Mono) er OFL og må selvhostes. |
| Flag | Alt nyt bag funktionsflag (off/preview/on) i Admin → Funktioner (§3.1). |

---

## 1. Hvad findes i dag

### 1.1 DOF.tool (main, 542e224)

| Del | Fil | Indhold |
|---|---|---|
| Tabeller | `models/learning.py` | `courses` (slug, title, summary, topic, level, intro, published), `questions` (prompt, choices JSONB, explanation, media_kind/url/credit, position), `question_progress` (Leitner-kasser), `quiz_results`, `snd_recordings` |
| API | `api/learn.py` | Kurser + spørgsmål (CRUD, `laering`), session (`smart`/`random`), `/answer`, auto-quizzer, artsquiz (`/arter/*`), lydquiz (`/lyd/*`, ML-optagelser), `/result`, medieupload |
| Tjenester | `services/learning.py` | Spørgsmål uden facit til klienten, Leitner, `AUTO_KINDS` (saeson, traek, status, sjaelden) fra `pheno:*`-cachen og `area_species_counts`, seed fra `seed/learning.json` |
| | `services/species_quiz.py` | Artsgrupper (`GROUPS` → slægter), enkeltfugl-fotos fra `gal_*`, svar via token i Valkey (`quiz:art:`) |
| | `services/sound_quiz.py` | xeno-canto-søgning (nøgle + filtre i Admin → Funktioner → Lydquiz), cache af lydfiler, ML-indlæggelse, token-afspilning uden art i URL |
| | `services/learn_access.py` | Flag `artsquiz` (kræver fotomodul) og `lydquiz` |
| Frontend | `views/learn.js`, `views/learn-arter.js` | Oversigt, quiz-afspiller, enkel redigering af kursus/spørgsmål, arts-/lydquiz |

### 1.2 Trækrute (prototypen)

| Fil | Indhold – hvad vi tager med |
|---|---|
| `site/js/seed.js` | 6 kurser (*Iagttag før du navngiver*, *En bestemmelse du kan forklare*, *Kend dine lokale fugle*, *Fuglekig når forholdene skifter*, *Arbejd med vanskelige observationer*, *Selvstændigt feltarbejde*) med trin/stage, læringsmål, lektioner (markdown) og aktiviteter; 14 øvelser/quizzer/spil (Habitatdetektiv, Observationsodds, Sæsonskift, Silhuet først, Relativ størrelse, Glimt, Bevægelseslab, Læs sporene, Lignende arter, Hele metoden, Familiequiz, Fugl for fugl, Mine fejl, Færdighedstest). **Importeres som startindhold (§12).** |
| `site/js/player.js` | Afspilleren: ét spørgsmål ad gangen → vælg svar → **angiv sikkerhed (Lav/Middel/Høj)** → tjek → forklaring → resultat med "for sikker"-tælling og gennemgang af fejl. **Porteres (§5.3).** |
| `site/js/store.js` | Datamodel (kursus → lektioner → spørgsmål; øvelser med `kind` øvelse/quiz/spil, `category`, `skills`, `featured`, `hue`, `sil`), import/eksport i DOF.tool-format. **Erstattes af API'et; formatet bruges til import/eksport (§8.6).** |
| `site/js/admin.js` | Redigering med advarsler (`qWarnings`/`itemWarnings`), flyt op/ned, kopiér, eksport/import. **Ideerne går ind i kursusbyggeren (§8).** |
| `site/js/silhouettes.js` | Egne SVG-silhuetter (rovfugl, hejre, svale, and, spurvefugl, vadefugl …) og farveflader (`hue`). **Genbruges uændret.** |
| `site/js/train.js` | "Udforsk": filtrér på type og færdighed, fremhævede, søg. **Porteres (§4.3).** |
| `site/js/home.js` | Forside: "Hvad vil du i dag?", mini-quiz i toppen, kort over kurserne, metoden, quiz-gitter, forbered-sektion. **Porteres (§4.1).** |
| `site/css/*.css` | Udseende. Tokens oversættes til DOF.tools CSS-variable (§4.6). |

Pædagogikken (bevares i tekst og funktion): **Metoden** i fem trin – *Forudsig, Opdag, Tolk, Adskil, Beslut* – og
færdighederne `SKILLS = Bemærk, Lyt, Genkald, Sammenlign, Afprøv, Vurdér, Opdatér, Forbered, Fasthold`. Grundideen:
*træn beslutningen, ikke kun svaret* – ærlig sikkerhed, bestemmelsesniveau (art/artspar/gruppe/ubestemt), og at
færdigheden skal bevises ude i felten.

---

## 2. Målbillede – hvorfor det bliver "det vildeste"

1. **Rigtige danske data i hvert spørgsmål.** "Observationsodds" spørger ikke om opdigtede sandsynligheder, men om
   DOFbasens faktiske fund på *den* lokalitet i *den* uge. Sæsonspørgsmål bruger indsatskorrigeret fænologi.
2. **Rigtige fotos og rigtige lyde** – tusindvis af danske fotos fra fotobiblioteket og xeno-canto-optagelser – i
   både kurser og egne træninger.
3. **Træn til din næste tur**: vælg lokalitet og dato → træn præcis de arter, du sandsynligvis møder.
4. **Bevis det udenfor**: feltopgaver krydses af automatisk, når du indtaster observationen i DOFbasen.
5. **Forløb og hold**: lokalafdelinger kan køre et begynderkursus over 8 uger med underviser, ugentlig frigivelse og
   holdets fremgang – alt bygget i en visuel bygger uden kode.
6. **Ærlig fremgang**: artsmestring pr. art og sanselag (foto/lyd), kalibrering af sikkerhed, repetition af egne fejl.

---

## 3. Flag, rettigheder, datamodel

### 3.1 Funktionsflag (`services/features.py`)

| Nøgle | Forælder | Standard | Styrer |
|---|---|---|---|
| `laering_portal` | – | `preview` | Den nye brugerflade (§4). `off` → den gamle `views/learn.js` vises uændret. |
| `laering_data` | `laering_portal` | `preview` | Datadrevne aktiviteter fra DOFbasen (§5.2 type `obs_*`) og "Forbered en tur" (§7). |
| `laering_byg` | `laering_portal` | `preview` | Kursusbygger (§8). Uden flaget: den gamle redigering. |
| `laering_forloeb` | `laering_portal` | `off` | Forløb, forløbsbygger og hold (§9). |
| `laering_felt` | `laering_forloeb` | `off` | Feltopgaver (§10). |
| `artsquiz`, `lydquiz` | – | (som nu) | Uændret: foto- og lydaktiviteter følger disse to flag, også inde i kurser. Er flaget slukket, springes aktiviteten over med teksten "Kræver fotos/lyd". |

Ruter og menupunkter gates på flaget som i Fotos (se `learn_access.py`; udvid den med `portal()`, `data()`, `byg()`, `forloeb()`, `felt()`).

### 3.2 Rettigheder (`services/auth.MODULES`)

| Modul | Navn | Må |
|---|---|---|
| `laering` (findes) | Læring: kurser og spørgsmål | Alt indhold: oprette, rette, publicere, slette alle kurser/øvelser/forløb; se alle hold. |
| `laering_undervis` (ny) | Læring: underviser | Oprette **egne** kurser og forløb (kladde), oprette hold på egne eller publicerede forløb, se fremgang for egne hold. Publicering til *alle* kræver `laering`; et undervisers kursus kan altid bruges i underviserens egne hold (synlighed `hold`, §3.3). |

Standardrollen `editor` får også `laering_undervis`. Ejerskab: `lr_*`-rækker har `created_by`; en underviser må kun
rette rækker, hvor `created_by = user.id` eller hvor brugeren er medforfatter (`lr_course_editors`).

### 3.3 Datamodel

Alle nye tabeller er **brugerdata** og skal ind i `USER_TABLES` i `dbgroups.py` (`tests/test_dbrouter.py` og
`tools/sql_groups.py` håndhæver det). Ingen fremmednøgler til observationsgruppen; `artnr`, `loknr`, `obsid` gemmes som
tal. Præfiks `lr_` for nye tabeller. De eksisterende `courses`/`questions`/`question_progress`/`quiz_results`/`snd_recordings`
**udvides** (ingen parallel model, så eksisterende fremgang og indhold bevares).

**`courses` (udvides)** – både kurser og øvelser:

| Kolonne | Type | Bemærkning |
|---|---|---|
| `kind` | str(8) | `kursus` / `ovelse` / `quiz` / `spil` (eksisterende rækker → `kursus`) |
| `number` | int null | Rækkefølge på kursusoversigten |
| `stage` | str(40) | "Grundlæggende", "Regional sikkerhed" … |
| `category` | str(40) | Øvelsernes kategori ("Kontekst", "Sandsynlighed" …) |
| `card` | text | Kort tekst til kortet |
| `audience` | text | "For alle, der …" |
| `learn` | JSONB | `[{title, text}]` – læringsmål |
| `skills` | text[] | Delmængde af `SKILLS` |
| `hours` | str(20) | "4–5 timer" |
| `hue`, `sil` | str(16) | Farveflade og silhuet (Trækrute) |
| `cover_media_id` | int null | Forsidebillede (content_media) |
| `featured` | bool | Fremhævet på Udforsk |
| `visibility` | str(8) | `alle` / `login` / `hold` (kun i hold) – erstatter ikke `published`, men supplerer |
| `version` | int | Øges ved hver publicering (§8.5) |
| `published_snapshot` | JSONB null | Låst kopi af det publicerede indhold (§8.5) |
| `updated_at`, `updated_by` | | |

`topic`, `level`, `summary`, `intro`, `published`, `created_by` bevares.

**`lr_units`** (ny) – lektioner i et kursus: `id`, `course_id` FK cascade, `position`, `title`, `lesson_md` (læsetekst,
markdown som Artikler – renderes af serveren med samme sanitizer), `blocks` JSONB (ekstra indholdsblokke, §8.3),
`min_score_pct` smallint null (krav for at låse næste lektion op; null = intet krav), `est_minutes` smallint.

**`questions` (udvides)** = aktiviteter:

| Kolonne | Type | Bemærkning |
|---|---|---|
| `unit_id` | FK `lr_units` null | Null for øvelser uden lektioner og for gamle kursusspørgsmål indtil migrering (§12.1) |
| `type` | str(16) | §5.2. Eksisterende rækker → `valg` |
| `config` | JSONB | Typeafhængig opsætning (§5.2) |
| `accept` | text[] | Tekstsvar der godkendes (type `tekst`) |
| `skills` | text[] | Færdigheder aktiviteten træner (arves fra kurset, hvis tom) |
| `media_photo_id` | int null | Foto fra fotobiblioteket (`gal_photos.id`) i stedet for `media_url` |
| `media_rec_id` | int null | Optagelse (`snd_recordings.id`) i stedet for `media_url` |
| `ask_confidence` | bool | Default true |

**`lr_paths`** (forløb): `id`, `slug`, `title`, `summary`, `intro_md`, `cover_media_id`, `level`, `audience`,
`published`, `visibility` (`alle`/`login`/`hold`), `certificate` bool, `certificate_text`, `created_by`, `created_at`, `updated_at`.

**`lr_path_items`**: `id`, `path_id` FK, `position`, `section` str(80) (overskrift/uge i forløbet), `kind`
(`kursus`/`ovelse`/`felt`/`tekst`/`traening`), `course_id` FK null, `field_task` JSONB null (§10), `text_md` null,
`training` JSONB null (gemt egen træning, §6.2), `required` bool, `min_score_pct` smallint null,
`unlock` JSONB (`{"after": "previous" | "none" | [item_id…], "days_after_start": int|null}`).

**`lr_cohorts`** (hold): `id`, `path_id` FK, `title`, `afdeling_id` null, `starts_on` date, `ends_on` date null,
`join_code` str(12) unique, `open_join` bool, `max_members` int null, `created_by`, `created_at`, `archived` bool.
**`lr_cohort_members`**: `cohort_id`, `user_id`, `role` (`deltager`/`underviser`), `joined_at`. PK (cohort_id, user_id).

**`lr_enrollments`**: `user_id`, `path_id` null, `course_id` null, `cohort_id` null, `started_at`, `completed_at` null,
`certificate_no` str(16) null. Unik på (user_id, path_id, cohort_id) og (user_id, course_id) hvor path_id er null.

**`lr_unit_progress`**: `user_id`, `unit_id`, `best_score`, `total`, `attempts`, `completed_at`. PK (user_id, unit_id).

**`lr_answers`** (svarlog – grundlag for fremgang og kalibrering): `id` bigint, `user_id`, `source`
(`kursus`/`ovelse`/`arter`/`lyd`/`obs`/`forbered`/`min`), `question_id` null, `artnr` null, `modality` (`foto`/`lyd`/`tekst`/`data`/`silhuet`),
`correct` bool, `confidence` smallint null (0/1/2), `skills` text[], `ms` int null (svartid), `created_at`.
Indeks (user_id, created_at), (user_id, artnr, modality). Ryddes ikke automatisk; slettes ved fuld sletning af bruger (GDPR, §13.3).

**`lr_species_mastery`** (Leitner pr. art og sanselag): `user_id`, `artnr`, `modality` (`foto`/`lyd`), `box` 0–5,
`correct`, `wrong`, `due_at`, `last_at`. PK (user_id, artnr, modality).

**`lr_trainings`** (gemte egne træninger, §6.2): `id`, `user_id`, `title`, `config` JSONB, `share_token` str(16) null
unique, `created_at`, `last_used_at`.

**`lr_field_done`** (§10): `user_id`, `path_item_id`, `cohort_id` null, `obsid` bigint null, `done_at`, `how`
(`obs`/`selv`/`underviser`). PK (user_id, path_item_id, cohort_id).

**`lr_course_editors`**: `course_id`, `user_id` (medforfattere til et kursus).

Skema: nye tabeller oprettes af `create_all` via modellerne; nye kolonner på eksisterende tabeller lægges som
`ALTER TABLE … ADD COLUMN IF NOT EXISTS …` i `models/ddl.py` (`EXTRA_DDL`) – projektet har ingen Alembic.

---

## 4. Del A – brugerfladen

Frontend som et modul med mappe (som Fotos/Artikler, jf. `arkitektur.md`): `views/laer.js` (skal + ruter) og
`laer/*.js` med én fil pr. side. CSS i `web/css/laer.css`, hentes ved første besøg. `views/learn.js` og
`views/learn-arter.js` bevares, indtil `laering_portal` er `on` i drift; derefter flyttes arts-/lydquizzen ind i
`laer/traening.js` (§6), og de gamle filer slettes i fase 8.

### 4.1 Forside `#/laer`

Port af Trækrutes `home.js`, med disse blokke i rækkefølge:

1. **Topbånd** med mini-quiz (ét datadrevet spørgsmål, fx "Hvilken art har du størst chance for at se i din afdeling i
   denne uge?"), og knapperne *Start et kursus* / *Træn arter*.
2. **"Hvad vil du i dag?"** – fire kort: *Lær at iagttage* (kurser), *Arbejd med det svære* (Mine fejl, Lignende arter),
   *Forbered din næste tur* (§7), *Træn foto og lyd* (§6).
3. **Fortsæt hvor du slap** (kun logget ind): seneste kursus/forløb med fremgangslinje, antal arter til repetition i dag.
4. **Kurserne** – kortene med nummer, trin, silhuet/farveflade, timer, færdigheder.
5. **Metoden** – de fem trin, kort forklaret (tekst fra Trækrute).
6. **Quizzer og spil** – gitter: Artsquiz (foto), Lydquiz, Familiequiz, Årstidens fugle, Hvornår trækker de?,
   SU/SUB/almindelig, Hvor sjælden lokalt, Mine fejl, Fugl for fugl (dagens spil), Færdighedstest.
7. **Forløb og hold** (flag `laering_forloeb`) – publicerede forløb og "Har du en holdkode?".

### 4.2 Kurser `#/laer/kurser`

Port af `courses.js`: "Sådan virker et kursus" + kortene grupperet efter `stage`. Hvert kort viser læringsmål, målgruppe,
fremgang (x af y lektioner).

### 4.3 Udforsk `#/laer/udforsk`

Port af `train.js`: filtrér på type (alle/øvelse/quiz/spil/feltværktøj/kursus), færdighed (`SKILLS`) og fritekst;
"Fremhævede" øverst; derefter sektionerne *Øvelser*, *Quizzer og spil*, *Feltværktøjer* (Forbered en tur, artssider,
fænologi `#/pheno`, lokaliteter, kort – links til eksisterende moduler), *Kurser*. Filtrene ligger i URL'en
(`#/laer/udforsk?type=quiz&skill=Lyt`).

### 4.4 Kursus `#/laer/kursus/<slug>[/<unit>]`

Port af `kursus.js`: venstre spalte med lektionsliste (låst/åben/færdig), højre med læsetekst og derefter lektionens
aktiviteter i afspilleren. Efter sidste lektion: kursusresultat, "Gennemgå fejl", "Næste kursus/forløbspunkt".
Anonyme brugere kan læse og svare på statiske aktiviteter; intet gemmes, og der vises "Log ind for at gemme fremgang".
Foto-/lyd-/dataaktiviteter kræver login (rettigheder til fotos/lyd og belastning) – de vises som et låst kort.

### 4.5 Øvelse `#/laer/ovelse/<slug>`

Port af `ovelse.js`: en øvelse er et `courses`-række med `kind ≠ kursus` og ingen lektioner. Indstillinger før start:
antal spørgsmål (5/10/20), *Øv* eller *Tidsudfordring* (nedtælling pr. spørgsmål, §5.3).

### 4.6 Udseende

Trækrutes udtryk (lyse papirflader, mørkegrøn accent, serif-overskrifter, mono-etiketter) oversættes til variable i
`laer.css` (`--lr-paper`, `--lr-ink`, `--lr-accent` …) **oven på** DOF.tools tema, så mørkt tema virker. Skrifterne
lægges i en ny mappe `web/fonts/` og indlæses kun af `laer.css`. Mobil først (bundmenuen i DOF.tool skal stadig være synlig);
tastaturbetjening i afspilleren (1–8 vælger svar, Enter tjekker/næste, L/M/H sikkerhed, mellemrum afspiller lyd).

---

## 5. Del B – aktivitetsmotoren

### 5.1 Princip

- **Facit forlader aldrig serveren** før svaret er afgivet. Statiske aktiviteter sendes som i dag via
  `learning.public_question` (uden `correct`); datadrevne får et token i Valkey (`quiz:lr:<token>`, TTL 2 t) som
  `species_quiz.store_token`. Ét fælles svar-endpoint (§5.4).
- En aktivitet er enten **statisk** (redaktøren har skrevet spørgsmål og svar) eller **generator** (redaktøren angiver
  regler; serveren laver et nyt konkret spørgsmål ved hver visning).

### 5.2 Aktivitetstyper (`questions.type`)

| Type | Statisk/generator | `config` | Bemærkning |
|---|---|---|---|
| `valg` | statisk | – | Ét eller flere rigtige svar (som i dag). |
| `tekst` | statisk | `{fuzzy: bool}` | Fritekst mod `accept`; med `fuzzy` godtages artsnavne med stavefejl via samme matcher som artsquizzens tekstsvar. |
| `raekkefoelge` | statisk | `{items: [..]}` (korrekt orden) | Træk i rækkefølge (fx ankomstrækkefølge, størrelse). Point: alt eller intet; forklaring viser rigtig orden. |
| `billedpunkt` | statisk | `{photo_id|media_url, zones: [{x,y,r,label,correct}]}` | Klik på kendetegnet i et foto ("Klik på det, der skiller den fra …"). Fase 7. |
| `silhuet` | statisk | `{sil, hue}` | Trækrutes silhuetter som medie; svar som `valg`. |
| `foto_art` | generator | `{arter: [artnr], gruppe: key, metode: valg|tekst, tags_ud: [...]}` | Ét foto fra fotobiblioteket af en tilfældig art i sættet. Genbruger `species_quiz` (enkeltfugl-fotos, `fill_names`). Kræver flag `artsquiz`. |
| `foto_sammenlign` | generator | `{par: [artnr, artnr]}` | To fotos side om side: "Hvilket er {art}?" – til *Lignende arter*. |
| `lyd_art` | generator | `{arter, gruppe, metode, rtype: sang|kald|begge}` | Én optagelse; genbruger `sound_quiz.build`. Kræver flag `lydquiz`. |
| `obs_odds` | generator | `{loknr|afdeling_id|null, uge: int|"nu", n_valg: 2..4}` | "Hvilken af disse er mest sandsynlig her i uge X?" fra `loc_species_finds`/`species_day`/`area_species_counts`. |
| `obs_saeson` | generator | `{artnr|null}` | "Hvornår ankommer/topper/forlader …" fra `pheno_metrics`/`species_season` (som `AUTO_KINDS.traek`). |
| `obs_habitat` | generator | `{loknr|lokalitetstype}` | "Du står ved {lokalitet} i {måned}. Hvilken art hører mest til her?" – rigtigt svar en art med mange fund på lokaliteten i måneden, forkerte arter med 0 fund dér men almindelige i DK. Viser lokaliteten på et lille kort. |
| `obs_sjaelden` | generator | `{afdeling_id|null}` | Som `AUTO_KINDS.sjaelden`. |
| `obs_status` | generator | – | Som `AUTO_KINDS.status` (SU/SUB/Alm). |
| `refleksion` | statisk | `{min_chars}` | Fritekst uden facit ("Beskriv hvad du så"); gemmes kun hos brugeren; vises bagefter med redaktørens eksempelsvar. Tæller ikke i score. |

Datadrevne generatorer bruger **kun aggregater** (`area_species_counts`, `loc_species_finds`, `species_day`,
`pheno_metrics`, `species_season`, `pheno:*`-cachen) – aldrig rå `observations`, og aldrig noget med `hemmelig > 0`
(aggregaterne skal allerede udelade hemmelige; verificér i `services/aggregates*` og skriv en test). Spørgsmål om en
lokalitet laves kun, hvis lokaliteten har ≥ 30 ture i perioden (konstant `MIN_TRIPS_LOC`), så svaret ikke er tilfældigt.
Generatorerne ligger i `services/learn_gen.py`, én funktion pr. type, og de læser observationsgruppen med `ObsSession`
og brugergruppen med den almindelige session – **aldrig i samme SQL** (arkitekturreglen).

Bestemmelsesniveau: `valg`, `foto_art`, `lyd_art` kan have `config.niveau = true`; så får brugeren også valget
"Kun til slægt/gruppe" og "Lad den stå ubestemt". Er sikkerhed *Lav* og svaret "ubestemt", tæller det som et
**ærligt svar** (vises grønt-gråt, ikke forkert) – pædagogisk kerne fra Trækrute.

### 5.3 Afspilleren (`web/js/laer/player.js`)

Port af Trækrutes `player.js` til `h()` og API-kald:

- Spørgsmål ét ad gangen, fremgangslinje, medie (foto med kreditering, lyd med egen afspiller uden filnavn – som lydquizzen, silhuet).
- **Sikkerhed** Lav/Middel/Høj (kan slås fra pr. aktivitet). Feedback: "Du var meget sikker – læg mærke til, hvad der
  narrede dig" / "Rigtigt, men usikker – værd at øve igen" (Trækrutes tekster).
- Efter svar: rigtigt/forkert, forklaring, for fotos/lyde: art, fotograf/optager, licens, link til artssiden
  (`#/art/<artnr>`) og til fotoet/optagelsen.
- *Se igen* (gentag samme medie efter svar), *Rapportér fejl i spørgsmålet* (opretter en supportsag med
  aktivitetens id – genbrug `support`).
- Resultat: score, "N svar var forkerte, selvom du var meget sikker", gennemgang af fejl, *Prøv igen*, *Træn fejlene*
  (laver en session af netop de missede, §6.3).
- *Tidsudfordring*: 15 s pr. spørgsmål (konfigurerbart pr. øvelse), ingen sikkerhed, point for hurtighed vises separat.

### 5.4 API (afspilning)

| Metode | Sti | Hvad |
|---|---|---|
| GET | `/api/learn/courses/{slug}/units` | Lektioner med læsetekst (renderet HTML) og låsestatus |
| GET | `/api/learn/units/{id}/session` | Lektionens aktiviteter, klar til afspilning (statiske uden facit, generatorer udfoldet til konkrete spørgsmål med token) |
| GET | `/api/learn/ovelser/{slug}/session?n=10&mode=smart\|random\|tid` | Øvelse |
| POST | `/api/learn/svar` | `{question_id?|token?, chosen?: [int], text?: str, order?: [int], point?: {x,y}, confidence?, ms?, niveau?}` → `{correct, honest?, correct_ids, explanation, reveal:{art, foto…}}`. Skriver `lr_answers`, `question_progress`/`lr_species_mastery`. Erstatter på sigt `/answer`, `/auto/answer`, `/arter/svar`, `/lyd/svar` (de gamle beholdes som tynde indpakninger, til de gamle visninger er væk). |
| POST | `/api/learn/units/{id}/faerdig` | `{score,total}` → `lr_unit_progress`, låser op |
| GET | `/api/learn/lyd/audio/{token}` | Findes – bruges også til `lyd_art` i kurser |

Rate limit på `svar` og `session` som de øvrige quiz-endpoints (genbrug den eksisterende limiter).

---

## 6. Del C – egne trænings­sessioner (foto og lyd)

### 6.1 Bevares

`#/laer/arter` og `#/laer/lyd` beholder **alt**, de kan i dag: artsgrupper (`species_quiz.GROUPS`), egne arter,
valg/tekst, sang/kald/begge, 20 spørgsmål, ML-panel for redaktører, ingen art i lyd-URL. De flyttes ind i den nye skal
som faner på `#/laer/traen` (`?m=foto|lyd`), og de gamle adresser omdirigerer.

### 6.2 Udvidelser

- **Artsvælger med flere kilder**: artsgruppe · egne arter · *mine arter* (arter brugeren selv har observeret – læses
  fra observationsgruppen via `users.obserkode`) · *min afdeling i denne måned* (`area_species_counts` for
  `afd:<id>`) · *en lokalitet* (`loc_species_finds`) · *et kursus' arter* · *arter jeg tit tager fejl af*
  (`lr_species_mastery` box 0–1).
- **Blandet foto + lyd** i samme session (`m=begge`).
- **Antal**: 10/20/40/uendelig (uendelig = ny runde hentes, når der er 3 tilbage).
- **Gem træning** (`lr_trainings`): titel + konfiguration; vises under "Mine træninger". **Del**: opret `share_token` →
  `#/laer/traen/delt/<token>` åbner samme opsætning for en anden logget-ind bruger (kun opsætningen deles, ikke resultater).
- En gemt træning kan **indsættes i et forløb** (`lr_path_items.kind = traening`).

### 6.3 Repetition – "Mine fejl" og "Dagens repetition"

- Statiske spørgsmål: `question_progress` (findes).
- Arter: `lr_species_mastery` pr. (art, foto|lyd) med Leitner-kasser 0–5 og intervaller 0, 1, 3, 7, 16, 35 dage.
- `#/laer/repetition` laver en blandet session af det, der er forfaldent i dag (højst 20), og forsiden viser tallet.

API: `GET/POST /api/learn/traeninger`, `DELETE /api/learn/traeninger/{id}`, `POST /api/learn/traeninger/{id}/del`,
`GET /api/learn/traeninger/delt/{token}`, `POST /api/learn/traen` (`{kilde, arter?, gruppe?, loknr?, afdeling_id?, m, metode, rtype, n}` → session), `GET /api/learn/repetition`.

---

## 7. Del D – Forbered en tur (`#/laer/forbered`, flag `laering_data`)

1. Vælg **lokalitet** (lokalitetssøgning som i `#/lokaliteter`) eller afdeling, og **dato** (standard: i dag; ±7 dage).
2. Serveren beregner **forventede arter**: fundfrekvens på lokaliteten i ugerne omkring datoen (`loc_species_finds`
   + `species_day`/ture – frekvens = andel af ture med arten), opdelt i *næsten sikker* (≥ 50 %), *sandsynlig*
   (10–50 %), *mulig* (2–10 %) og *sjælden her* (SU/SUB eller < 2 %). Under 30 ture: brug afdelingen og sig det.
3. Siden viser listen med foto (fotobibliotekets mest likede, som artssidens galleri) og lyd-knap; *Træn disse arter*
   starter en foto/lyd-session på de valgte, *Lignende arter* laver `foto_sammenlign`-par af forvekslingsarter i listen
   (par fra `species_quiz.GROUPS` + konfigurerbar liste `seed/laering/forvekslinger.json`).
4. *Gem som træning* og *Del*.

API: `GET /api/learn/forbered?loknr=&afdeling_id=&dato=` (cache i Valkey 24 t pr. (sted, uge)).

---

## 8. Del E – kursusbyggeren (`#/laer/byg`, flag `laering_byg`)

### 8.1 Oversigt `#/laer/byg`

Faner: *Kurser* · *Øvelser* · *Forløb* (flag) · *Hold* (flag) · *Medier* · *Import/eksport*. Tabel med titel, type,
status (kladde/publiceret/ændret siden publicering), ejer, antal aktiviteter, advarsler, sidst rettet. Knapper:
Nyt kursus, Ny øvelse, Kopiér, Slet (kun kladder for undervisere).

### 8.2 Kursuseditoren `#/laer/byg/kursus/<slug>`

Tre zoner: venstre **struktur** (lektioner og deres aktiviteter som træ, træk-og-slip for rækkefølge og flytning mellem
lektioner), midten **redigering** af det valgte, højre **forhåndsvisning** (den rigtige afspiller i en
"prøv"-tilstand, der ikke gemmer fremgang).

- **Grundoplysninger**: titel, slug (låses efter første publicering), trin, nummer, emne, niveau, timer, færdigheder,
  målgruppe, kortets tekst, intro, læringsmål (liste), silhuet/farve eller forsidebillede, synlighed.
- **Lektion**: titel, læsetekst (DOF.tools fælles `editor/md-editor.js` med servergenereret forhåndsvisning), ekstra
  blokke (§8.3), krav for at låse op (min. score), forventet tid.
- **Aktivitet**: vælg type (§5.2) fra et galleri med eksempel; typeafhængig formular; forklaring; færdigheder; sikkerhed
  til/fra. Generatortyper har knappen **"Vis 5 eksempler"**, som kalder generatoren og viser fem konkrete spørgsmål, så
  redaktøren ser, hvad brugerne får.
- **Advarsler** (port af `qWarnings`/`itemWarnings`): intet rigtigt svar, kun ét svar, tomt spørgsmål, manglende
  forklaring, medie uden kreditering, generator der giver < 5 forskellige spørgsmål,
  lektion uden aktiviteter, kursus uden læringsmål. Publicering blokeres ved fejl (røde), ikke ved advarsler (gule).
- **Autogem** hver 3 s efter ændring (kladde), med "Gemt"/"Ikke gemt"-indikator; samtidig redigering: optimistisk lås
  med `updated_at` (409 → "Kurset er ændret af X – genindlæs").

### 8.3 Indholdsblokke i lektioner (`lr_units.blocks`)

`tekst` (markdown) · `foto` (fra fotobiblioteket, med kreditering automatisk) · `fotorække` (2–6 fotos til
sammenligning med billedtekst) · `lyd` (optagelse fra `snd_recordings` eller upload) · `sonagram`? (se §15) ·
`silhuet` · `artskort` (artens navn, latin, status, fænologikurve fra `#/pheno` som lille graf, link) · `kort` (en
lokalitet eller afdeling) · `artikel` (link-kort til en artikel i Artikler) · `tip` / `advarsel` (fremhævet boks) ·
`metode` (de fem trin med ét trin fremhævet).

### 8.4 Mediepickere

- **Fotobibliotek**: søg på art, lokalitet, fotograf, alder/køn/dragt (gal-koderne), kun publicerede; viser rettighed.
  Genbrug søgningen fra `fotos/galleri.js` og pickeren `editor/media-picker.js` som skabelon.
- **Lyd**: søg i `snd_recordings` på art/type; *Hent fra xeno-canto* kalder `sound_quiz.ensure_species` for arten og
  viser resultaterne med afspiller, optager, licens; ML-optagelser via det eksisterende ML-panel.
- **Upload** af egne billeder/lyde til `/media/` (findes i `api/learn.py`, `MEDIA_TYPES`), med krav om kreditering.

### 8.5 Publicering og versioner

*Publicér* lægger en låst kopi af kurset (grundoplysninger, lektioner, aktiviteter) i `published_snapshot` og øger
`version`. Brugerne afspiller altid snapshot; redaktøren arbejder i de levende rækker. *Ændret siden publicering* vises,
til der publiceres igen. *Fortryd ændringer* lægger snapshot tilbage i rækkerne. Fremgang knyttes til aktivitetens id;
slettede aktiviteter fjernes fra fremgangen ved næste publicering (vis antal i bekræftelsen).

### 8.6 Import og eksport

- **Trækrute-format** (`store.js`: `{courses:[…units…], drills:[…]}`) – importeres 1:1 inkl. lektioner, silhuetter,
  færdigheder, `kind`, `category`, `featured`.
- **DOF.tool-format** (`seed/learning.json` – det, Trækrutes knap "Eksportér til DOF.tool" laver) – kurser med én lektion.
- Eksport af ét kursus, en øvelse, et forløb eller alt som JSON (medier som URL'er/ids, ikke filer).
- Import viser en forhåndsvisning: nye/ændrede/uændrede pr. slug; vælg *Opret som kopi* eller *Overskriv kladde*.

API (byg): `GET/POST/PUT/DELETE /api/learn/byg/kurser[/{slug}]`, `…/{slug}/enheder[/{id}]`, `…/aktiviteter[/{id}]`,
`POST …/{slug}/flyt` (`{kind, id, til_unit, position}`), `POST …/{slug}/publicer`, `POST …/{slug}/fortryd`,
`POST /api/learn/byg/eksempler` (`{type, config}` → 5 eksempler), `POST /api/learn/byg/import`, `GET /api/learn/byg/eksport`,
`GET /api/learn/byg/fotos?q=`, `GET /api/learn/byg/lyde?artnr=`. Alle kræver `laering` eller `laering_undervis` + ejerskab.

---

## 9. Del F – forløbsbygger og hold (flag `laering_forloeb`)

### 9.1 Forløb

Et forløb er en rækkefølge af punkter i **sektioner** (fx "Uge 1 – Iagttag", "Uge 2 – Levesteder"). Punkttyper:
kursus · øvelse · gemt træning · feltopgave (§10) · tekst (fx mødested, praktisk info). Pr. punkt: krævet/valgfrit,
min. score, oplåsning (efter forrige · fri · efter bestemte punkter · N dage efter holdstart).

Forløbsbyggeren `#/laer/byg/forloeb/<slug>`: venstre sektioner/punkter (træk-og-slip), højre punktets indstillinger;
*Tilføj punkt* søger i kurser/øvelser/træninger; tidslinje-visning (uge for uge, når der er `days_after_start`).
Advarsler: punkt peger på ikke-publiceret kursus; cirkulær oplåsning; forløb uden krævede punkter.

Forløbssiden `#/laer/forloeb/<slug>`: intro, sektioner med punkter og status (låst/åben/i gang/færdig), samlet fremgang,
*Tilmeld* (opretter `lr_enrollments`). Bevis ved gennemført forløb (`certificate`): side `#/laer/bevis/<nr>` med navn,
forløb, dato, holdnavn og underviser – kan printes (print-CSS) og verificeres af andre via nummeret (kun navn, forløb, dato).

Eksempler på forløb, der lægges ind som startindhold (kladde, ejer skal godkende teksten):
**"Fra nysgerrig til feltbisse"** (de 6 Trækrute-kurser + udvalgte øvelser + 4 feltopgaver), **"Lær 50 almindelige
fugle"** (foto+lyd-træninger i 5 sektioner fra have til kyst), **"Vadefugle i trækket"** (forvekslingspar + Forbered en
tur til en vadefuglelokalitet + feltopgave).

### 9.2 Hold

En underviser opretter et hold på et forløb: titel, afdeling, startdato, slutdato, *åben tilmelding* eller holdkode.
Deltagere tilmelder sig via `#/laer/hold/tilmeld/<kode>` eller "Har du en holdkode?". Holdets side `#/laer/hold/<id>`:

- **Deltagere** ser forløbet med holdets oplåsning (dage efter start) og en holdliste med fornavne (ingen scorer for andre deltagere).
- **Undervisere** ser en matrix deltager × punkt (færdig/score/dato), holdets svagest besvarede aktiviteter og arter,
  feltopgaver der venter på godkendelse (§10), kan markere en feltopgave som godkendt, sende en besked til holdet
  (genbrug notifikationer) og eksportere fremgangen som CSV.

API: `/api/learn/forloeb[...]`, `/api/learn/byg/forloeb[...]`, `/api/learn/hold[...]` (oprette, tilmeld, forlad, medlemmer,
fremgang, eksport, besked, arkivér).

---

## 10. Del G – feltopgaver (flag `laering_felt`)

En feltopgave er "prove it outside": `field_task` JSONB på forløbspunktet:

```json
{ "titel": "Find og notér en vadefugl", "tekst_md": "...",
  "regel": { "arter": [artnr…] | null, "gruppe": "vadefugle" | null, "min_arter": 1,
             "loknr": null, "afdeling_id": null, "periode": "efter_oplaasning" | {"fra": "...", "til": "..."},
             "kraev_noter": false },
  "godkendelse": "auto" | "underviser" | "selv" }
```

- **auto**: et job (worker, hver time, og når brugeren åbner punktet) slår brugerens observationer op i
  observationsgruppen via `users.obserkode` (`observations` WHERE obserkode = … AND dato i perioden AND artnr i reglen)
  og skriver `lr_field_done` i brugergruppen (to forespørgsler, ikke én). Kun brugerens egne observationer; hemmelige
  observationer tæller med for brugeren selv, men deres sted vises ikke for underviseren.
- **underviser**: som auto, men underviseren skal godkende (ser artens navn, dato og lokalitet).
- **selv**: brugeren krydser selv af (til opgaver uden observation, fx "Skriv en beskrivelse").
- Brugere uden obserkode får teksten "Knyt din DOFbasen-profil" med link til profilen; opgaven kan så kun klares med `selv`.

---

## 11. Del H – fremgang `#/laer/fremgang`

- **Færdigheder**: pr. færdighed (`SKILLS`) andel rigtige de seneste 90 dage (fra `lr_answers.skills`), som et
  radar-/stjernediagram (brug `charts.js`; følg `dataviz`-principperne, ingen ny biblioteksafhængighed) med tal i en tabel ved siden af.
- **Artsmestring**: liste over arter med kasse for foto og lyd (0–5), filtrér på gruppe; "Mestrer" = kasse ≥ 4 i
  begge eller den eneste modalitet. Tal: "Du kan kende 132 arter på foto og 41 på lyd".
- **Kalibrering**: for Lav/Middel/Høj sikkerhed – hvor ofte var du rigtig? (Godt kalibreret: Høj ≈ 90 %+, Lav ≈ under 60 %.)
  Kort forklaring af hvorfor det er vigtigt (fra Trækrutes metode-tekst).
- **Aktivitet**: dage i træk med træning, seneste 40 hændelser (som Trækrutes `log`), gennemførte kurser/forløb, beviser.
- **Mål** (valgfrit): "Lær N arter på lyd før 1. april" – vises på forsiden.
- Ingen offentlige ranglister i første omgang (§15). Fremgang er privat; kun holdets undervisere ser holdets tal.

API: `GET /api/learn/fremgang`, `GET /api/learn/fremgang/arter?gruppe=`, `PUT /api/learn/maal`.

---

## 12. Startindhold og migrering

### 12.1 Migrering af det eksisterende

`EXTRA_DDL` + `services/learning.migrate_v2()` (kører idempotent ved opstart, som `seed_courses`): hver eksisterende
`courses`-række får `kind='kursus'` og én `lr_units`-række ("Spørgsmål", `lesson_md = intro`), og dens `questions`
får `unit_id`, `type='valg'`. `question_progress` og `quiz_results` røres ikke. Publicerede kurser får et snapshot.

### 12.2 Trækrute-indholdet

Konvertér `seed.js` én gang til `server/portal/seed/laering/traekrute.json` (Trækrute-format, §8.6) med et lille script
i `tools/laering_import_traekrute.py` (læser `seed.js` via en simpel JS→JSON-transformation *eller* via Trækrutes egen
eksport fra admin – foretræk eksporten, den er i forvejen JSON). Importér ved første opstart med flaget på, som
**kladder** markeret "Startindhold – kræver faglig gennemlæsning". Ejeren/redaktøren publicerer.

Opgradér undervejs disse Trækrute-øvelser fra statiske til generatorer (beholder de statiske spørgsmål som fallback):

| Øvelse | Bliver |
|---|---|
| Habitatdetektiv | + `obs_habitat` på rigtige lokaliteter |
| Observationsodds | `obs_odds` |
| Sæsonskift | `obs_saeson` |
| Lignende arter | `foto_sammenlign` + `lyd_art` på forvekslingspar |
| Familiequiz | `foto_art` med svar på familieniveau (kræver familie pr. art – §15) |
| Fugl for fugl (dagens spil) | Dagens art (samme for alle pr. dato): ledetråde afsløres én ad gangen – silhuet → fænologi → lyd → foto – færre ledetråde = flere point |
| Mine fejl | Repetition (§6.3) |
| Færdighedstest | 20 spørgsmål på tværs af færdigheder → anbefalet startkursus/forløb |

---

## 13. Tværgående

### 13.1 Ydelse

Generatorer læser kun aggregater og cacher kandidatlister i Valkey (nøgle med datastatus-version, som
`quiz:photocounts:`). En session må højst lave 3 databasekald pr. generatortype. Lydfiler forhåndshentes som i
`sound_quiz.prefetch`. Forsidens mini-quiz caches 1 time pr. afdeling.

### 13.2 Rettigheder til medier

- Fotos: kun `status='publiceret'`. **Ændret 4. nov. 2026 (ejeren):** DOF.tool overtager netfugl-arkivet, så alle publicerede fotos må bruges – også fast indsat i kurser; byggeren tjekker ikke længere rettighed 2/3.
- Fotograf/optager og licens vises altid ved svaret og i blokke. xeno-canto: respektér licensen (ND = ingen beskæring;
  NC = ingen kommerciel brug – DOF.tool er ikke kommerciel). Macaulay Library: kun indlagt af redaktører som i dag.
- Ingen medier i URL'er, der afslører facit (som lydquizzen).

### 13.3 GDPR

`lr_answers`, `lr_unit_progress`, `lr_species_mastery`, `lr_enrollments`, `lr_cohort_members`, `lr_field_done`,
`lr_trainings` med i dataudtrækket (`api/privacy.py`) og i fuld sletning. Undervisere ser kun egne holds data. Svarlog
ældre end 3 år aggregeres (konstant) – job i worker.

### 13.4 Tilgængelighed og offline

Alt kan betjenes med tastatur og skærmlæser (roller som i Trækrutes afspiller: radiogroup/checkbox, `aria-live` på
feedback). Fase 8: *Hent kursus til offline* – service workeren (`web/sw.js`) cacher et kursus' snapshot og statiske
medier; svar køes i `offline.js` og sendes, når der er net. Generatorer kræver net.

### 13.5 Tests (mindst)

- Model/migrering: gamle kurser bliver til kursus med én lektion; fremgang bevaret.
- Facit lækker ikke: ingen `correct`/`artnr`/`ext_id` i session-svar for nogen type.
- Generatorer: deterministiske med fast seed; mindst 4 forskellige valg; ingen hemmelige data; ingen blandet SQL
  (`test_dbrouter`).
- Rettigheder: underviser kan ikke rette andres kurser eller se andres hold; anonym kan ikke få foto/lyd.
- Publicering/snapshot, flyt, import (Trækrute-format + gammelt format), feltopgave auto-afkrydsning.
- JS-syntakstjek og browser-røgtest (se testmiljø-noten) af forside → kursus → aktivitet → resultat og af byggeren.

---

## 14. Faser

| Fase | Indhold | Færdig når |
|---|---|---|
| 0 | Flag, rettighed `laering_undervis`, datamodel (§3.3: udvidelser + `lr_units`, `lr_answers`, `lr_species_mastery`, `lr_unit_progress`), migrering §12.1, `dbgroups.py` | Tests grønne; gamle `#/laer` virker uændret |
| 1 | Ny skal `views/laer.js` + forside, kurser, udforsk, kursus, øvelse, afspiller (statiske typer `valg`, `tekst`, `silhuet`, `raekkefoelge`, `refleksion`), `/svar`, sikkerhed, Trækrute-import som kladder | Et Trækrute-kursus kan gennemføres med fremgang |
| 2 | Generatorer `foto_art`, `foto_sammenlign`, `lyd_art`, `obs_*` i kurser/øvelser; opgradering af Trækrute-øvelser (§12.2) | Observationsodds/Sæsonskift bruger rigtige data |
| 3 | Egne træninger §6 (flyt arts-/lydquiz ind, nye kilder, gem/del, repetition) + Forbered en tur §7 | `#/laer/arter`/`lyd` omdirigerer og virker som før |
| 4 | Kursusbygger §8 (editor, blokke, pickere, advarsler, eksempler, publicering/snapshot, import/eksport) | En redaktør bygger et kursus med foto- og lydaktiviteter uden at røre JSON |
| 5 | Forløb og forløbsbygger §9.1, beviser | Forløbet "Fra nysgerrig til feltbisse" kan gennemføres |
| 6 | Hold §9.2 + feltopgaver §10 | En underviser kører et hold med auto-afkrydsede feltopgaver |
| 7 | Fremgang §11, `billedpunkt`, Fugl for fugl, Færdighedstest, tidsudfordring | Fremgangssiden viser færdigheder, mestring og kalibrering |
| 8 | Offline §13.4, oprydning (slet `learn.js`/`learn-arter.js`, gamle endpoints som indpakninger), dokumentation i `docs/funktioner.md` og `arkitektur.md` | `laering_portal` kan sættes til `on` |

---

## 15. Åbne spørgsmål – med standardvalg (byg standardvalget, hvis ejeren ikke har svaret)

| # | Spørgsmål | Standardvalg |
|---|---|---|
| 1 | Skal portalen hedde "Læring" eller have et navn (fx "Trækrute")? | Menupunkt "Læring"; forsidens overskrift kan sættes i Admin → Funktioner → Læring (standard "Læring"). |
| 2 | Må anonyme brugere tage statiske kurser? | Ja, uden fremgang. Foto/lyd/data kræver login. |
| 3 | Fast indsatte fotos i kurser: hvilke rettigheder? | ~~Kun rettighed 1 eller DOF-samtykke~~ – ændret 4. nov. 2026: alle publicerede fotos må bruges (DOF.tool overtager netfugl). |
| 4 | Familie pr. art (til Familiequiz) | Tilføj `seed/laering/familier.json` (latin-slægt → familie) og brug den; ingen ændring af `species`. |
| 5 | Sonagram-visning af lyde | Udskydes; lav ikke selv FFT i fase 1–8. |
| 6 | Ranglister/konkurrencer mellem brugere | Ikke bygget. Kun inden for et hold, og kun hvis underviseren slår det til (fase 7+, valgfrit). |
| 7 | Hvem må være underviser? | Brugere med `laering_undervis` (tildeles af superadmin/admin under Brugere og rettigheder). |
| 8 | Beviser – skal DOF stå som udsteder? | Teksten "Udstedt via DOF.tool" uden DOF-logo, indtil DOF har godkendt. |
| 9 | Faglig gennemlæsning af Trækrute-teksterne | Importeres som kladder; ejeren publicerer. Faktuelle påstande (datoer, sjældenhed) markeres til tjek. |

---

## 16. Filer (forventet)

Server: `models/learning.py` (udvidet), `services/learning.py` (migrering, sessioner), `services/learn_gen.py`
(generatorer), `services/learn_build.py` (bygger, snapshot, import/eksport), `services/learn_paths.py` (forløb, hold,
feltopgaver), `services/learn_progress.py`, `services/learn_access.py` (udvidet), `api/learn.py` (afspilning),
`api/learn_build.py`, `api/learn_paths.py`, `seed/laering/*.json`, `tools/laering_import_traekrute.py`, worker-job
`learn_field_check`, nye linjer i `models/ddl.py`.

Web: `views/laer.js`, `laer/{forside,kurser,udforsk,kursus,ovelse,player,traening,forbered,fremgang,forloeb,hold,bevis}.js`,
`laer/byg/{oversigt,kursus,aktivitet,blokke,pickere,forloeb,hold,import}.js`, `laer/silhouettes.js` (fra Trækrute),
`css/laer.css`, `fonts/*`.
