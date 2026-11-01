# Netfugl-afløsning – specifikation af Fotos, Krydslister og admin-tilføjelser (til implementering)

Status: specifikation, 1. november 2026. Skrevet til at blive bygget trin for trin (fase 0–7, §16) af en anden
model/udvikler. Læs **`docs/arkitektur.md`** først – alle regler dér gælder. Læs derefter **`docs/artikler-modul-spec.md`**:
Artikler er det modul, der ligner mest, og denne spec henviser til det i stedet for at gentage det. Når denne fil
siger "som i Artikler", så kopiér mønsteret fra de nævnte filer – opfind ikke et nyt.

> **Regel for den, der bygger:** Hver fase slutter med grønne tests (`pytest server/tests`), opdateret
> rute-snapshot (`tests/fixtures/api_routes.json`) og ét commit. Byg ikke videre på en rød fase. Er noget uklart, så vælg
> standardvalget i §17 og skriv det i commit-beskeden – stop ikke.

---

## 0. Kort fortalt

DOF.tool skal kunne **afløse netfugl.dk fuldt ud**. Det, der mangler i forhold til netfugl.dk, bygges som **nye,
selvstændige moduler i appen** – *ikke* i showcasen `#/netfugl` (den røres ikke, bortset fra hvor det står eksplicit).

| Del | Hvad | Rute | Afsnit |
|---|---|---|---|
| A | **Fotos** – fotogalleri med import af hele netfugl-arkivet (~80 500 fotos), upload, moderation, søgning, bannere | `#/fotos` | §1–§10 |
| B | **Krydslister** – krydslister, artslister og World Bird List som eget modul med redigerbare listetyper | `#/krydslister` | §11 |
| C | **Artikler** – forfatterregister (med fletning), sprog og link-artikler | `#/artikler` (findes) | §12 |
| D | **Se som bruger** – superadmin ser appen som en anden bruger (kun læse, logget) | – | §13 |

### Beslutninger truffet med ejeren (må ikke ændres uden at spørge)

| Spørgsmål | Beslutning |
|---|---|
| Mål | Fuld afløser for netfugl.dk. **Første fase er en succes, når hele fotoarkivet er importeret og kan søges med korrekt metadata.** |
| Hvor bygges det | Nye moduler i appen (som Artikler). `#/netfugl`-showcasen og dens `nf_*`-tabeller røres ikke. |
| Fotoarkiv | **Fuld import** af alle *publicerede* fotos via en **crawl** af `https://netfugl.dk/fotos/{id}` uden login. Der tælles ned fra id 125245; der stoppes, når 10 id'er i træk ikke har data (konfigurerbart, se §2.4). Metadata gemmes som `.json` i en mappe sammen med billedet. |
| Fotografer | **Automatisk match på navn** til DOF.tool-brugere/DOFbasen-observatører (kun entydige match, §5). |
| Kobling til DOFbasen | **Valgfri**: et foto kan knyttes til fotografens egen observation; så udfyldes felterne automatisk, og fotoet vises på observationen. |
| Redaktionelle regler | Moderator ser **antal publicerede fotos af arten** (Netfugls grænse: 30) ved godkendelse. Intet dublet-tjek, ingen oprydningsværktøjer. |
| Reklamer | **Nej.** DOF.tool forbliver reklamefri. Byg intet reklame-modul. |
| Admin-tilføjelser | Forfatterregister + sprog (Artikler), bannerbilleder, "se som bruger". |
| Krydslister | Flyttes ud af showcasen som eget modul **samtidig** (del B). |
| Licenser | Kun open source; ingen Google-tjenester (ingen Analytics, Ads, Maps, Firebase i nye dele). |

---

## 1. Kildedata: hvad netfugl.dk har (fundet ved gennemgang 1. nov. 2026)

Netfugls **upload-formular** (`/fotos/opret`) har disse felter – de definerer, hvad vi skal kunne gemme:

| Gruppe | Felter (Netfugls formularnavn) |
|---|---|
| Fil | `birdPhotoFile` (komprimeres til max 1400×1400, hvis > 2 MB eller > 2048×2048) |
| Foto | `date` *, `birdPhoto.countryId` *, `birdPhoto.location` * (fritekst), `birdPhoto.latitude`, `birdPhoto.longitude`, `birdPhoto.title`, `birdPhoto.description` |
| Fugle på fotoet (0–9, op til **10 fugle**) | `birdInPhotos[i].birdId` * (World Bird List-id), `.behaviourId`, `.sexId`, `.ageId`, `.plumageId` |
| Emneord | `tagIds`: *Personer*, *Historisk*, *Tvivl om art*, *Tvivl om køn/alder/dragt* |
| Kamera | `metaModel`, `metaLensModel`, `metaFStop`, `metaFocalLength`, `metaShutterSpeed`, `metaIso`, `metaFlash` (udfyldes fra EXIF) |
| Rettigheder | `copyrightTypeId`: (1) *Reference til fotografen er påkrævet ved brug af foto (kun ikke-kommerciel brug)*, (2) *Personlig aftale med fotografen er påkrævet for brug af foto*, (3) *Henvendelser om brugsret for foto frabedes af fotografen*. Fotografen har en **standard** (på "Mine fotos"), som et foto kan overskrive, og et samtykke *"Jeg tillader at DOF gør brug af mine fotos til brug i DOFs egne medier"*. |

Kodelister (værdierne, som de vises):
- **Køn:** Han, Hun.
- **Alder (16):** 1. kalenderår, 1. kalenderår eller mere, 2. kalenderår, 2. kalenderår eller mere, 3. kalenderår,
  3. kalenderår eller mere, 4. kalenderår, 4. kalenderår eller mere, 5. kalenderår, 5. kalenderår eller mere,
  Adult (udfarvet), Immatur (umoden), Juvenil (ungfugl i første fjerdragt), Pullus (dununge), Subadult.
- **Dragt (9):** Lys form, Mørk form, Mellemform, Overgangsdragt, Sommerdragt/yngledragt, Vinterdragt,
  Eklipsedragt (ænder), Hun-farvet, Pragtdragt.
- **Adfærd:** fx "Siddende", "Flyvende" – den fulde liste kendes ikke; tag værdierne fra arkivet (§2.3).
- **Sjældenhed (5):** Almindelig, Relativ almindelig, Ualmindelig, Sjælden, SU – en egenskab ved **arten på
  artslisten**, ikke ved fotoet.

Fotosiden viser desuden: titel, fotograf (navn + profil-id), lokalitet (link `?location=`), land (`?countryId=`), dato,
uploadet, publiceret, likes, visninger, kommentarer (træ med svar), rettighed (tekst), kameramodel, dimensioner (fx
`2400x1600`), link til original (`/fotos/view/original/{id}`), artens danske/latinske/engelske navn og Netfugl-artsid
(`?birdId=25721`).

**Admin på netfugl.dk** (det, der skal kunne erstattes): kø med nye/upublicerede (85), afviste (239), afpublicér, slet,
ret, lås/oplås (`/admin/fotos/lock/toggle`), gruppér (samme bruger + art + dato + lokalitet), admin-kommentarer (eget
spor), slet kommentar, privilegier pr. bruger (Standard / Månedlig (10 pr. md.) / Ubegrænset / Udelukket; admins er
altid ubegrænsede), afvisning giver besked i brugerens log med begrundelse.

Netfugls retningslinjer (på "Mine fotos") nævner **højst 30 fotos pr. art** (gælder ikke sjældne arter). Teksten
genbruges som tekstside (§9.6).

---

## 2. Fase 0 – Crawleren (`tools/netfugl_fotos/`)

Et selvstændigt script, der kører på en almindelig maskine (ikke på serveren). Det **ligner `tools/netfugl_arkiv/
netfugl_arkiv.py`** (artikel-crawleren) – genbrug dens `hent_bytes`, `kod_url`, `UA`, pausemønster og
kommandolinjestil (kopiér funktionerne ind; importér ikke på tværs af `tools/`-mapper).

### 2.1 Fil og kommandoer

`tools/netfugl_fotos/netfugl_fotos.py` – køres med `python3 -I netfugl_fotos.py <kommando>` (kræver `beautifulsoup4`).

```
hent     [--fra 125245] [--til 1] [--stop-efter 10] [--pause 3] [--igen] [--ids 125244 125238 …]
kontrol                       tæller mapper, sammenligner med galleriets totaltal, skriver _mangler.json
indeks                        bygger _index.json, _fotografer.json, _arter.json, _koder.json, manifest.json
zip      [--del-gb 20]        pakker til Fotos-001.zip, Fotos-002.zip … (højst --del-gb pr. zip)
```

Standard-uddatamappe: `--ud /home/chris/Dokumenter/DOF.tool/Fotos` (læg `Fotos/` og `Fotos-*.zip` i `.gitignore`).

### 2.2 `hent`

- Tæl **ned** fra `--fra` til `--til`. For hvert id hent `https://netfugl.dk/fotos/{id}`.
- **Har data** = siden indeholder et foto (billede + mindst én art). Netfugl svarer nogle gange 500 med fuld side –
  brug kroppen, som artikel-crawleren gør. 404, tom side eller en side uden foto = **ingen data**.
- Ved data: opret mappen `Fotos/{id}/`, hent originalen fra `/fotos/view/original/{id}` (fald tilbage til den største
  `<img>` på siden), gem som `foto.<ext>` (ext efter filsignatur, ikke URL), og skriv `meta.json` (§2.3).
  Skriv `meta.json` **sidst** og atomisk (`meta.json.tmp` → rename): en mappe uden `meta.json` er ufærdig og hentes igen.
- `--igen` henter også id'er, der allerede har `meta.json`; uden `--igen` springes de over (genoptagelse efter afbrud).
- **Stopregel:** tæl id'er i træk uden data; når tallet når `--stop-efter`, stop og skriv i loggen
  `"Stoppet ved id X efter N tomme i træk"`. Tælleren nulstilles ved hvert fund. Id'er uden data skrives til
  `Fotos/_tomme.txt` (én pr. linje), så `kontrol` kan bruge dem.
- Pause `--pause` sekunder mellem *alle* HTTP-kald (side og billede). Ved 429/503: vent 60 s og prøv igen (højst 5 gange).
- Log til `Fotos/hent.log`: én linje pr. id (`125245 ok 1.2 MB`, `125240 tom`, `125239 fejl: …`).
- Overslag (skriv det i `--help`): ~125 000 sider + ~80 000 billeder × 3 s ≈ 7 døgn; billederne fylder anslået
  40–80 GB. Scriptet skal derfor kunne afbrydes og genoptages når som helst.

> **Kendt risiko (skal stå i README'en):** galleriet har ~80 565 publicerede fotos ud af ~125 000 id'er – hvert tredje
> id er tomt (slettet/afvist/upubliceret), og huller på mere end 10 i træk er sandsynlige i de ældre id'er. Kør derfor
> `kontrol` efter `hent`. Viser den, at der mangler fotos, så fortsæt med `hent --fra <stop-id> --stop-efter 500`
> (eller `--til 1 --stop-efter 0`, hvor 0 betyder "stop aldrig").

### 2.3 `meta.json` (schema_version 1)

Gem **både** den viste tekst og Netfugls id'er, hvor de findes – importen bruger teksten til opslag og id'et til at
genkende ting igen.

```json
{
  "schema_version": 1,
  "id": 125244,
  "url": "https://netfugl.dk/fotos/125244",
  "hentet": "2026-11-02T10:11:12Z",
  "fil": "foto.jpg",
  "fil_sha256": "…",
  "bredde": 2400, "hoejde": 1600,
  "titel": "Ørkenstenpikker",
  "beskrivelse_html": "<p>…</p>",
  "dato": "2026-10-27",
  "uploadet": "2026-10-27", "publiceret": "2026-10-28",
  "land": {"navn": "Danmark", "netfugl_id": 1},
  "lokalitet": "Tisvildekysten",
  "koordinat": {"lat": 56.05, "lon": 12.0},
  "fotograf": {"navn": "Leif Bolding", "netfugl_bruger_id": 3221},
  "fugle": [
    {"art": {"dansk": "Ørkenstenpikker", "latin": "Oenanthe deserti", "engelsk": "Desert Wheatear", "netfugl_id": 25721},
     "adfaerd": "Siddende", "koen": null, "alder": "1. kalenderår", "dragt": null, "sjaeldenhed": "SU"}
  ],
  "emneord": ["Tvivl om art"],
  "kamera": {"model": "NIKON", "linse": null, "blaende": null, "braendvidde": null, "lukkertid": null, "iso": null, "flash": null},
  "rettighed": {"id": 2, "tekst": "Personlig aftale med fotografen er påkrævet for brug af foto"},
  "likes": 4, "visninger": 352,
  "gruppe": [125245, 125238],
  "kommentarer": [
    {"id": 991, "forfatter": "Navn", "forfatter_id": 123, "dato": "2026-10-28T09:12", "tekst_html": "<p>…</p>",
     "svar": [{"id": 992, "forfatter": "…", "forfatter_id": 456, "dato": "…", "tekst_html": "…", "svar": []}]}
  ]
}
```

- Felter, der ikke findes på siden, er `null` (eller `[]`) – udelad dem aldrig.
- `koordinat` kun hvis siden viser den; ellers `null`.
- `gruppe`: andre foto-id'er i samme gruppe/karrusel, hvis siden viser dem; ellers `[]`.
- `beskrivelse_html`/`tekst_html`: rå HTML fra siden (importen sanitiserer). Avatarbilleder hentes **ikke**.
- Datoer som ISO (`YYYY-MM-DD` / `YYYY-MM-DDTHH:MM`), parset med en `parse_dato` som i artikel-crawleren.

### 2.4 Parseren skal testes på rigtige sider

**Første opgave i fase 0:** hent med hånden 4 sider og læg dem i `server/tests/fixtures/netfugl_fotos/`:
et foto med én fugl, et med flere fugle, et med kommentarer og svar, og et tomt id. Skriv
`server/tests/test_netfugl_foto_parser.py`, der importerer parseren via `importlib` fra `tools/netfugl_fotos/` (se
hvordan `test_article_import.py` finder sine fixtures) og tjekker hvert felt i §2.3. Parserfunktionen skal være ren:
`parse_foto(html: str, id: int) -> dict | None` (None = ingen data), uden netværk.

### 2.5 `kontrol` og `indeks`

- `kontrol`: hent `https://netfugl.dk/fotos` én gang og læs "Søgeresultater (N fotos)". Skriv antal mapper med
  `meta.json`, N, forskellen, og laveste id hentet. Hvis forskellen er > 0,5 %: skriv en tydelig advarsel med den
  anbefalede kommando fra §2.2.
- `indeks`: gennemløb alle `meta.json` og skriv
  - `_index.json`: `[{id, titel, dato, arter: [latin…], fotograf}]`
  - `_fotografer.json`: `{netfugl_bruger_id: {navn, antal}}`
  - `_arter.json`: `{netfugl_art_id: {dansk, latin, engelsk, antal}}`
  - `_koder.json`: alle forskellige værdier af adfærd, køn, alder, dragt, emneord, rettighed, land (med antal) –
    bruges til at seede kodelisterne (§3.3)
  - `manifest.json`: `{schema_version: 1, antal, hentet_fra, hentet_til, lavest_id, hoejest_id, laget}`

### 2.6 `zip`

Pak mapperne i id-orden i dele på højst `--del-gb` GB: `Fotos-001.zip`, `Fotos-002.zip` …; **hver** zip indeholder
`manifest.json` og de fire `_*.json`-filer og en rod-mappe `netfugl-fotos/`. Billeder lagres med `ZIP_STORED`
(de er allerede komprimeret), json med `ZIP_DEFLATED`.

**Accept fase 0:** parser-testene er grønne; `hent --ids …` på 20 rigtige id'er giver 20 korrekte mapper; `kontrol`
og `indeks` virker; README i `tools/netfugl_fotos/README.md` forklarer kørsel, overslag og den kendte risiko.

---

## 3. Fotos – funktionsflag, rettigheder, datamodel

### 3.1 Funktionsflag (`services/features.py`)

| Nøgle | Navn | Forælder | Standard |
|---|---|---|---|
| `fotos` | Fotos (fotogalleri med netfugl-arkivet) | – | `preview` |
| `fotos_upload` | Fotos: upload af egne fotos | `fotos` | `on` |
| `fotos_kommentarer` | Fotos: kommentarer og likes | `fotos` | `on` |
| `fotos_dofbasen` | Fotos: kobling til egne DOFbasen-observationer | `fotos` | `on` |
| `fotos_bannere` | Fotos: bannerbilleder på forsiden | `fotos` | `on` |

Skriv beskrivelser i samme stil som de eksisterende. `insider` = superadmin eller admin-modulet `fotos`. API'et svarer
404, når flaget ikke er synligt. `/api/me` udvides med `"fotos": {"features": {…}, "can_moderate": bool,
"can_admin": bool, "privilegie": "standard"|…}` som for `artikler`.

### 3.2 Rettigheder (`services/auth.MODULES`)

- Nyt modul `"fotos": "Fotos: moderation, privilegier, import, fotografer og bannere"`.
- Kommentarmoderation bruger det eksisterende modul `moderation`.
- Ny fil `services/photo_access.py` – **kopi af `services/article_access.py`** med `Access.admin` = `fotos`,
  `can_moderate` = `fotos` (fotomoderation) og `can_moderate_comments` = `moderation`.
- `api/fotos.py` får sin egen `need(feature)` som i `api/artikler.py`.

### 3.3 Datamodel

Alle tabeller er **brugerdata** → tilføj dem til `USER_TABLES` i `server/portal/dbgroups.py`. Ny modelfil
`server/portal/models/gallery.py` (eksportér i `models/__init__.py`). Præfiks `gal_`. Arter slås op i `species`
(referencedata, findes i begge grupper). **Ingen SQL må joine `gal_*` med `observations`** – se §8.

**`gal_photos`** – ét foto
| Kolonne | Type | Note |
|---|---|---|
| id | int PK | |
| netfugl_id | int unique null | id på netfugl.dk (importerede) |
| user_id | int FK users null | ejer (null = ikke matchet arkivfotograf) |
| photographer_id | int FK gal_photographers | altid sat; se §5 |
| media_id | int FK content_media | billedet (original + varianter, §4.4) |
| width, height | int | |
| titel | varchar(200) null | |
| beskrivelse_md | text null | sanitiseret, renderes med kommentar-profilen i `content_render` |
| dato | date null | fotodato |
| land_id | int FK gal_countries | |
| lokalitet | varchar(250) null | fritekst som på Netfugl |
| loknr | int null | DOFbasen-lokalitet, hvis kendt (match eller valgt) |
| lat, lon | double null | |
| tags | varchar(24)[] | `personer`, `historisk`, `tvivl_art`, `tvivl_kad` |
| kamera | jsonb | `{model, linse, blaende, braendvidde, lukkertid, iso, flash}` |
| rettighed | smallint null | 1–3; null = fotografens standard |
| status | varchar(12) | `afventer` / `publiceret` / `afvist` / `afpubliceret` |
| afvist_grund | text null | vises for fotografen |
| behandlet_af, behandlet_at | int null, timestamptz null | |
| locked_by, locked_at | int null, timestamptz null | låst = ejer kan ikke rette/slette |
| group_id | int FK gal_groups null | |
| obsid | bigint null | egen DOFbasen-observation (§8) |
| likes, views, comments | int default 0 | tællere (opdateres i samme transaktion som like/visning/kommentar) |
| views_imported, likes_imported | int default 0 | fra Netfugl (lægges til i visningen) |
| source | varchar(12) | `netfugl` / `upload` |
| source_hash | char(64) null | sha256 af meta.json → spring uændrede over ved genimport |
| uploaded_at, published_at | timestamptz | |
| created_at, updated_at | timestamptz | |

Indeks: `(status, published_at desc)`, `(status, dato desc)`, `(user_id)`, `(photographer_id)`, `(land_id)`,
`(group_id)`, `(obsid)`, GIN på `tags`, og et `tsvector`-indeks (i `models/ddl.py`) over titel + lokalitet + beskrivelse
(dansk konfiguration, som Artiklers fuldtekst).

**`gal_birds`** – fugle på fotoet (1–10 pr. foto)
| Kolonne | Type | Note |
|---|---|---|
| id | int PK | |
| photo_id | FK gal_photos cascade | |
| pos | smallint | 0 = hovedarten (bruges i overskrift og kort) |
| artnr | int null | DOFbasens art (via latin, §4.3) |
| taxon_id | int FK kl_taxa null | World Bird List (§11); kan være en art, der ikke findes i DOFbasen |
| navn, latin | varchar(200) | som vist på Netfugl (så intet tabes, hvis opslag fejler) |
| adfaerd, koen, alder, dragt | varchar(16) null | **koder** fra `gal_codes` |

Indeks: `(artnr, photo_id)`, `(taxon_id)`.

**`gal_codes`** – kodelister: `(kind, code)` PK, `text`, `sort`, `dofbasen_code` null. `kind` ∈ `adfaerd`, `koen`,
`alder`, `dragt`. Seedes fra §1's lister + `_koder.json` ved første import; `dofbasen_code` kobles til `code_texts`
(adfærd/alder/dragt fra DOFbasen) med en håndlavet tabel i `seed/gallery_codes.json`, så felterne kan udfyldes fra en
observation (§8). Kan redigeres i admin.

**`gal_countries`** – `id`, `navn`, `netfugl_id` null unique, `iso2` null, `vp` bool. Seedes fra `_koder.json`
(landene fra Netfugl, inkl. "Rusland (indenfor VP)" osv. – de er Netfugls egne "lande" og bevares som de er).

**`gal_photographers`** – én pr. fotograf (også dem uden bruger)
`id`, `navn`, `navn_norm` (index), `netfugl_bruger_id` null unique, `user_id` FK users null unique, `obserkode` null,
`match` (`auto` / `manuel` / `ingen` / `tvetydig`), `standard_rettighed` smallint default 2, `dof_samtykke` bool default
false, `created_at`.

**`gal_groups`** – `id`, `photographer_id`, `artnr` null, `dato` null, `cover_photo_id` null.

**`gal_comments`** – som `art_comments` i Artikler (samme kolonner: `parent_id`, `user_id`, `archive_author`,
`archive_netfugl_user`, `archive_key`, `body_md`, `body_html`, `status`, `hidden_reason`, `hidden_by`,
`report_count`, `edited_at`) + `photo_id` og `intern` bool (admin-noter, §7.5). Og `gal_comment_reports` som
`art_comment_reports`. **Genbrug `services/article_comments.py`'s logik** ved at generalisere den med en parameter for
tabel/ejer frem for at kopiere den; kan det ikke lade sig gøre uden at ændre Artiklers adfærd, så kopiér til
`services/photo_comments.py`.

**`gal_likes`** – `(user_id, photo_id)` PK, `created_at`.

**`gal_privileges`** – `user_id` PK FK users, `niveau` (`standard`/`maanedlig`/`ubegraenset`/`udelukket`), `note`,
`set_by`, `set_at`. Ingen række = `standard`.

**`gal_banners`** – `id`, `photo_id` FK, `crop` jsonb (`{x,y,w,h}` i brøkdele 0–1), `tekst` null, `fra` date null,
`til` date null, `sort`, `active` bool.

**`gal_imports`** – som `art_imports` (status, options, stats, log, timestamps) + `kilde` (`zip`/`mappe`) og `sti`.

**Indstillinger** (`settings`, nøgle `fotos`, via `setting_store`):
`{"maanedlig_kvote": 10, "max_pr_art": 30, "afvis_grunde": ["Tvivl om artsbestemmelsen", "Teknisk kvalitet",
"Arten er godt dækket i galleriet", "Findes allerede i et andet galleri", "Overholder ikke retningslinjerne"],
"upload_max_mb": 25, "default_per_side": 24}`.

---

## 4. Fase 1 – Import af fotoarkivet (fasens mål)

Kopiér strukturen fra Artiklers import (`services/article_import.py`, `api/admin/artikler.py`, `web/js/artikler/import.js`):
job i workeren, `jobstatus.track`, én transaktion pr. foto, stats og log, kan køres igen uden dubletter.

### 4.1 Kilde: zip eller mappe

Arkivet er 40–80 GB. Understøt **begge**:
1. **Mappe på serveren** (primær): ny indstilling `photo_import_dir` i `config.py` (standard
   `<media_dir>/../photo_imports`). Admin vælger en undermappe (listes af `GET /api/admin/fotos/import/mapper`), der
   indeholder `netfugl-fotos/` eller en eller flere `Fotos-*.zip`. Nævn i `docs/drift.md`, hvordan man rsync'er
   `Fotos/` derop.
2. **Upload af zip** med chunked upload – **genbrug** Artiklers chunk-endpoints-mønster (32 MiB dele, genoptag).

Sikkerhed ved zip (som Artikler §4.2): ingen absolutte stier/`..`/symlinks; højst 300 MB pr. medlem; tilladte filer:
`manifest.json`, `_*.json`, `<id>/meta.json`, `<id>/foto.{jpg,jpeg,png,webp,gif}`. Alt andet logges og ignoreres.
I mappe-tilstand: afvis stier uden for `photo_import_dir` (resolve + `is_relative_to`).

### 4.2 Forløb (`services/photo_import.py`, worker-kommando `photos_import`)

Registrér kommandoen i `worker.py` ved siden af `articles_import`. For hver `<id>/meta.json` i id-orden:
1. Valider `schema_version == 1` og `id` == mappenavn; ellers fejl i loggen og videre.
2. Beregn sha256 af meta.json. Findes `gal_photos.netfugl_id == id` med samme `source_hash` → tæl "uændret", videre.
3. **Billede:** verificér signaturen, kald `content_media.store_image(session, data, filename=…, owner_kind="foto",
   owner_id=<photo.id>)` – den fjerner EXIF/GPS og laver varianter. Samme sha256 → genbrug mediet.
   *Læs kamerafelterne fra EXIF før stripning* kun hvis `meta.kamera.model` er null (§6.2's `read_camera`).
4. **Fotograf:** find/opret `gal_photographers` på `netfugl_bruger_id` (§5 matcher bagefter).
5. **Land:** find/opret `gal_countries` på `netfugl_id`, ellers navn.
6. **Fugle:** for hver fugl: `artnr` via `species.latin` (case-insensitivt, også synonymer, hvis `species` har dem –
   brug samme opslag som `services/article_species.py`), ellers via dansk navn; `taxon_id` via `kl_taxa.netfugl_id`
   (findes først efter fase 5 – lad den være null og udfyld i en efterkørsel `photos_link_taxa`). Koder: slå teksten op
   i `gal_codes` (opret ukendte med `sort=999` og log dem).
7. **Lokalitet:** `loknr` = DOFbasen-lokalitet med *præcis* samme navn (normaliseret) og, hvis koordinat findes,
   inden for 5 km; ellers null. (Lokaliteter er referencedata – opslaget er lokalt.)
8. Tekster: sanitisér `beskrivelse_html` → markdown/HTML som Artiklers kommentar-profil (nh3).
9. **Kommentarer** (option `import_comments`, standard til): som Artiklers arkivkommentarer (flad ud til 2 niveauer,
   upsert på `archive_key = "nf:<kommentar-id>"`, bevar `status` hvis skjult).
10. Status `publiceret`, `published_at` = `publiceret`-datoen, `uploaded_at` = `uploadet`, `views_imported`,
    `likes_imported`, `rettighed` = meta-id, `source = 'netfugl'`.
11. **Ingen notifikationer** fra importen.

Efter løkken: **grupper** – for hvert foto med `gruppe: [...]`, læg dem i samme `gal_groups` (første id = cover).
Kør så fotografmatch (§5). Log en sammenfatning: `oprettet / opdateret / uændret / fejl / billeder genbrugt / fugle uden
artnr / ukendte koder`. Tæl cache-versionen for fotos op.

Importvalg: `import_comments` (til), `overwrite_local` (fra – fotos rettet i DOF.tool efter import overskrives ikke;
kræver en `locally_edited`-kolonne – tilføj den), `dry_run`, `limit` (kun de første N, til test).

### 4.3 Medier og adgang

- Brug `content_media` (owner_kind `foto`) og nøglerne under `art/` – **ingen ny lagerkode**.
- I `api/media.py` udvides adgangstjekket for `/media/art/…`: et medie med `owner_kind == 'foto'` må ses, når flaget
  `fotos` er synligt for brugeren **og** fotoet er publiceret (eller brugeren ejer det eller kan moderere).
  Slå ejerskab op via `content_media.owner_kind/owner_id` → `gal_photos.status/user_id`. Cache svaret 60 s i Valkey
  pr. medie-id (galleriet henter mange miniaturer).
- Varianter: `-t` (400 px) i oversigter, `-m` (1200 px) på fotosiden, original via "Vis i fuld størrelse".

**Accept fase 1:** en import af 200 rigtige crawlede fotos (`limit`) giver 200 `gal_photos` med billede, fugle, koder,
land, fotograf og kommentarer; en ny kørsel giver 0 nye/200 uændrede; `dry_run` skriver intet; test
`test_photo_import.py` med en lille fixture-zip (3 fotos, heraf ét med 2 fugle og ét med kommentarsvar) og test af
zip-slip. **Plus fase 2's læsesider, så arkivet kan søges** – først da er fasens mål nået.

---

## 5. Fotografmatch (`services/photo_photographers.py`)

Beslutning: **automatisk match på navn**, men kun når det er entydigt.

1. `navn_norm` = navn → casefold, fjern punktum/komma/bindestreg-varianter, sammenpres mellemrum, fjern parentes-
   tillæg som "(fsh)". "Knud-ERIK Andersen" og "Knud-Erik Andersen" giver samme `navn_norm`.
2. **Trin 1 – portalbrugere:** `users.navn` normaliseret. Præcis én bruger → `user_id`, `obserkode`, `match='auto'`.
3. **Trin 2 – DOFbasen-observatører:** hvis ingen bruger, slå op i `observers` (observationsgruppen – **separat
   forespørgsel**, ikke join med `gal_*`). Præcis én → gem `obserkode`, `match='auto'`. Når en bruger med den obserkode
   senere logger ind, kobles `user_id` (kald `photographers.claim_by_obserkode(user)` fra login-flowet, hvor
   brugerrækken oprettes/opdateres).
4. Flere kandidater → `match='tvetydig'`; ingen → `match='ingen'`. Fotos har så `user_id = null` og vises med
   fotografens navn.
5. En `user_id` må kun sidde på én fotograf (unique). To Netfugl-profiler med samme navn → begge `tvetydig`.
6. Når `user_id` sættes, opdateres `gal_photos.user_id` for fotografens fotos i samme transaktion.
7. `match='manuel'` røres aldrig af automatikken igen.

Admin-fanen **Fotografer** (§9.7) viser alle fotografer med antal fotos og match-status, filter på `tvetydig`/`ingen`,
og lader admin koble til en bruger/obserkode, fjerne en kobling eller flette to fotografer. Alle ændringer auditeres.

**Accept:** test med navnesammenfald (to brugere med samme navn → tvetydig), stavevarianter, og at `manuel` ikke
overskrives.

---

## 6. Fase 3 – Upload af nye fotos

### 6.1 Formular (`#/fotos/ny`, `#/fotos/<id>/ret`)

Felter som Netfugls (§1), i appens stil (`web/js/fotos/upload.js`):
- Billedfil (træk-og-slip; forhåndsvisning). Ingen skalering i browseren – serveren laver varianter.
- Dato *, Land * (søgbar liste, standard Danmark), Lokalitet * (fritekst **med forslag** fra DOFbasen-lokaliteter;
  vælges et forslag, sættes `loknr`), Koordinat (valgfri; "Sæt på kort" med `maps.js`), Titel, Beskrivelse (fælles
  markdown-editor i let tilstand, som kommentarer).
- **Fugle på fotoet:** rækker med Art * (søg i `kl_taxa`; tjekboks "Kun VP-arter"), Adfærd, Køn, Alder, Dragt.
  "+ Tilføj fugl" (højst 10). Tab-rækkefølge række for række.
- Emneord (4 tjekbokse), Kamera (7 felter, udfyldt fra EXIF – knap "Ryd metadata"), Rettighed (standard + 3 valg).
- **Knyt til min observation** (kun med `fotos_dofbasen`): når art og dato er valgt, vis brugerens egne observationer
  af arten ±1 dag (§8). Vælges en, udfyldes lokalitet, loknr, koordinat (lokalitetens), køn/alder/dragt/adfærd.

### 6.2 Server (`POST /api/fotos`, multipart)

1. Tjek privilegie (§7.1) **før** filen læses færdig: `udelukket` → 403 "Du kan ikke uploade fotos".
2. Læs højst `upload_max_mb`; tjek signatur (`content_media.sniff_image`).
3. `read_camera(data) -> dict` i `services/imagemeta.py` (ny funktion): læs Model, LensModel, FNumber, FocalLength,
   ExposureTime, ISOSpeedRatings, Flash med Pillow **før** `strip`. GPS læses **ikke** ud og gemmes ikke. Brugerens
   egne kamerafelter vinder over EXIF.
4. Gem via `content_media.store_image` (owner_kind `foto`).
5. Status: `ubegraenset`, eller admin/fotomoderator → `publiceret`. `maanedlig`: publiceret, hvis brugeren har færre
   end `maanedlig_kvote` fotos publiceret uden godkendelse denne kalendermåned (Europe/Copenhagen), ellers `afventer`.
   `standard` → `afventer`.
6. Fotografen: find/opret `gal_photographers` for `user_id` (navn = `users.navn`).
7. Svar `{id, status}`. Er status `afventer`, viser frontenden "Dit foto afventer godkendelse".

`PUT /api/fotos/{id}`: ejer (hvis ikke låst) eller fotomoderator. Ejer, der retter et `publiceret` foto, ændrer *ikke*
status. `DELETE`: ejer (hvis ikke låst) eller fotomoderator; sletter mediet, hvis intet andet bruger det; auditeres,
når det ikke er ejeren.

**Accept fase 3:** upload med 2 fugle og EXIF giver korrekte rækker og kamera; GPS findes ikke i den gemte fil (test);
kvoten virker over månedsskiftet (test med fast tid); låst foto kan ikke rettes af ejer (test).

---

## 7. Fase 4 – Moderation (`#/fotos/admin`)

### 7.1 Privilegier

Fanen **Privilegier**: søg bruger (genbrug brugersøgningen fra `web/js/netfugl/admin.js` → Roller), vælg niveau,
note. Liste over alle med andet end `standard`. Forklaringstekst som på Netfugl (§1). Admin/`fotos`-modul er altid
ubegrænset (skriv det i teksten).

### 7.2 Kø

Faner **Afventer** (standard), **Afvist**, **Afpubliceret**. Hvert kort: miniature (klik = stort), art(er), dato,
lokalitet, land, fotograf (link), uploadet, og **"N publicerede fotos af arten (grænse 30)"** – rødt når N ≥
`max_pr_art`, og *ikke* rødt hvis arten er sjælden/SU på DK-listen (§11). Knapper: **Godkend**, **Afvis…**, **Ret**.
Tastatur: `j/k` næste/forrige, `g` godkend, `a` afvis.

**Afvis…** åbner en dialog med standardgrunde (`afvis_grunde`) + fritekst. Ved afvisning: `status='afvist'`,
`afvist_grund`, `behandlet_*`, og **besked til fotografen**: genbrug `message_user`-mønstret i
`api/admin/moderation.py` (opret `SupportThread` med `kind='moderation'` og emnet "Dit foto af <art> blev ikke
godkendt", link til fotoet) – flyt den fælles del til en service-funktion `services/moderation.send_message(...)`, så
begge steder kalder den. Ved godkendelse: notifikation "Dit foto af <art> er nu i galleriet" via den eksisterende
`notify`-kanal (ikke support-tråd). Fotos uden bruger (arkiv) får ingen besked.

### 7.3 På fotosiden (kun fotomoderatorer)

Boks "Administration": **Afpublicér** (→ `afpubliceret`, med grund → besked som ved afvisning), **Lås/Oplås**,
**Gruppér** (foreslår fotos fra samme fotograf + art + dato ± 0 dage + samme `loknr`/lokalitet; vælg hvilke),
**Fjern fra gruppe**, **Ret**, **Slet**. Status "Fotoet er låst af X den Y".

### 7.4 Gruppering ved upload

Når et nyt foto gemmes, og der findes et andet foto fra samme fotograf med samme `obsid` (eller samme art + dato +
`loknr`), så vis i upload-svaret "Skal fotoet grupperes med dine 2 andre fotos fra samme fund?" – brugeren kan sige ja.
Moderatorer kan altid ændre det.

### 7.5 Admin-noter

`gal_comments.intern = true` vises kun for fotomoderatorer, i en gul boks under fotoet ("Interne noter"), med egen
skriveboks. De tæller ikke i `comments`, sendes ikke som notifikationer til fotografen og eksporteres aldrig i API'et.

**Accept fase 4:** fuldt forløb afventer → afvist (besked i support) → ret → godkendt (notifikation); tælleren pr. art
er korrekt; interne noter er usynlige for ejeren (test på API-niveau).

---

## 8. Kobling til DOFbasen (flag `fotos_dofbasen`)

Databasegrupperne må ikke blandes i én SQL. Derfor:
- `GET /api/fotos/mine-observationer?artnr=&dato=`: forespørgsel i **observationsgruppen** (som
  `netfugl_content.my_observations`) – returnér obsid, dato, antal, loknr, lokalitet, koordinat (lokalitetens), køn,
  alderkode, dragtkode, adfærd.
- Når et foto gemmes med `obsid`: tjek ejerskab (observationsgruppen: `obserkode` = fotografens), og **kopiér** felterne
  til `gal_photos`/`gal_birds[0]` (koder oversættes via `gal_codes.dofbasen_code`). `obsid` gemmes som tal uden
  fremmednøgle.
- **Fotoet vises på observationen:** DOFbasen-modulets observationsvisning (`web/js/dofbasen/…` og tråd-/turvisning, hvor
  `obsid` kendes) kalder `GET /api/fotos/til-observationer?obsid=1,2,3` (højst 200 id'er, svarer `{obsid: [{id, thumb}]}`)
  og viser miniaturer med link til `#/fotos/<id>`. To kald, ingen join.
- Fotosiden viser "Observation i DOFbasen" med link til `#/dofbasen/obs/<obsid>` (eller hvad den eksisterende
  observationsrute hedder – find den i `web/js/views/dofbasen.js`).

---

## 9. Fase 2 – Brugerflader (`#/fotos`)

Følg Artiklers skal: `web/js/views/fotos.js` (faner, lazy `import()`), `web/js/fotos/*.js` (én fil pr. side), CSS i
`web/css/fotos.css` hentet ved første besøg. App-menu: `FOTOS = ["#/fotos", "<ikon>", "Fotos"]` når
`state.user.fotos.features.fotos`. Rute i `ROUTES`: `[/^\/fotos(?:\/(.+))?$/, () => import("./views/fotos.js")]`.

| Rute | Side | Fil |
|---|---|---|
| `#/fotos` | Galleri (søgning + gitter) | `fotos/galleri.js` |
| `#/fotos/<id>` | Fotoside | `fotos/foto.js` |
| `#/fotos/n/<netfugl_id>` | Slår op og omdirigerer (gamle links) | `views/fotos.js` |
| `#/fotos/mine` | Mine fotos + indstillinger | `fotos/mine.js` |
| `#/fotos/ny` · `#/fotos/<id>/ret` | Upload/ret | `fotos/upload.js` |
| `#/fotos/fotograf/<pid>` | En fotografs fotos (= galleri filtreret) | `fotos/galleri.js` |
| `#/fotos/retningslinjer` | Tekstside | `fotos/sider.js` |
| `#/fotos/admin[/koe\|/privilegier\|/fotografer\|/import\|/bannere\|/koder\|/indstillinger]` | Admin | `fotos/admin.js` + én fil pr. fane |

### 9.1 Galleri

- Øverst: banner (§10), søgefelt (fritekst: titel, lokalitet, beskrivelse, fotograf, artsnavn).
- **Filtre** (sammenklappet "Avanceret søgning"): fra/til dato, måned, art/race (søg i `kl_taxa`/`species`; inkl.
  underarter), fotograf, land, lokalitet (fritekst *eller* valgt DOFbasen-lokalitet), sjældenhed (5 niveauer – via
  arten på DK-listen), køn, alder, dragt, adfærd, emneord. "Ryd filter".
- **Sortering:** Fotodato (standard), Senest tilføjet, Flest likes, Senest kommenteret, Set af flest.
- Per side 24/48/96. Uendelig rul *eller* sider – brug det mønster, Artiklers søgning bruger.
- Kort i gitter: miniature (`-t`, `loading="lazy"`, `width/height` sat), hovedart (+ "+2 arter"), dato, lokalitet,
  fotograf, likes, visninger. Grupper vises som ét kort med "+N".
- Filtre i URL'en (`?q=&art=&fra=&til=&md=&land=&lok=&loknr=&sj=&koen=&alder=&dragt=&adf=&tag=&fotograf=&sort=&side=`),
  så Tilbage og deling virker (se commit e2b60ab om rulleposition – brug samme mekanisme).

**API:** `GET /api/fotos?…` – én SQL i `services/photos.py` med de valgte betingelser (EXISTS mod `gal_birds` for
art/køn/alder/dragt/adfærd), `count(*) OVER ()` kun på første side, cache i Valkey 60 s pr. normaliseret
forespørgsel (versionsnøgle, der tælles op ved ændringer). Svartid < 300 ms på 80 000 fotos med de nævnte indeks –
skriv en `EXPLAIN`-note i PR'en for art-filter + fotodato-sortering.

### 9.2 Fotoside

Stort billede (`-m`, klik → original i lysboks med zoom), forrige/næste i gruppen, alle fugle (art med link til
`#/art/<artnr>`, adfærd, køn, alder, dragt, sjældenhed), titel, beskrivelse, dato, lokalitet (link til
lokalitetssiden, hvis `loknr`), land, koordinat på lille kort (hvis sat), fotograf (link til fotografens fotos og til
`#/observator/<kode>`, hvis kendt og synlig), rettighed (fuld tekst), kamera, dimensioner, uploadet/publiceret,
likes (knap), visninger, DOFbasen-link (§8), kommentarer (som Artikler: svar i ét niveau, anmeld, arkivkommentarer
mærket "fra netfugl.dk"), interne noter og admin-boks (§7). Visninger tælles én gang pr. bruger pr. døgn (Valkey-sæt).

### 9.3 Mine fotos

- Liste over egne fotos med status (afventer/afvist med grund/publiceret/afpubliceret), ret/slet (ikke låste).
- **Indstillinger:** standard-rettighed (3 valg) og samtykke "DOF må bruge mine fotos i DOF's egne medier". Ændring
  påvirker alle fotos uden egen rettighed (det sker automatisk, fordi `rettighed = null` betyder standard).
- Link til retningslinjer og "Tilføj nyt foto" (skjult, hvis `udelukket` eller `fotos_upload` ikke er synligt).

### 9.4 Notifikationer

Nye i notifikationsindstillingerne (som Artikler §8.3): "Svar på mine kommentarer på fotos", "Kommentarer på mine
fotos", "Mine fotos er godkendt/afvist" (sidste kan ikke slås fra). Højst én pr. time pr. foto for kommentarer.

### 9.5 Søg i hele appen

Hvis appen har en fælles søgning (`web/js/views/soeg.js`), tilføj en sektion "Fotos" (5 første træf) bag flaget.

### 9.6 Tekstside "Retningslinjer"

Gem i `settings` (nøgle `fotos_tekster`) som markdown, redigerbar for `fotos`-admin med den fælles editor. Seed med
Netfugls tekst fra "Mine fotos" (ejeren har adgang til den; kopiér fra `~/Hentet/Netfugl Admin/Netfugl - Mine
fotos.html`).

### 9.7 Admin-faner

| Fane | Indhold |
|---|---|
| Kø | §7.2 |
| Privilegier | §7.1 |
| Fotografer | §5 |
| Import | §4 (start, status, log, historik) |
| Bannere | §10 |
| Koder | redigér `gal_codes` (tekst, sort, DOFbasen-kode), flet to koder |
| Indstillinger | `settings.fotos`, retningslinjer |

---

## 10. Bannerbilleder (flag `fotos_bannere`)

- På fotosiden har moderatorer "Brug som banner": vælg beskæring i en dialog (fast forhold 3:1, træk/zoom; ingen ny
  afhængighed – en simpel `<canvas>`-beskærer i `fotos/bannere.js`), evt. tekst, fra/til-dato.
- Fanen Bannere: liste, rækkefølge, aktiv, slet.
- Visning: galleriets top og appens forside, hvis forsiden har en plads til det (ellers kun galleriet). Ét banner pr.
  visning, valgt blandt aktive i datointervallet (roterer pr. sidevisning). Teksten "Foto: <fotograf>" vises altid, og
  banneret linker til fotoet.
- Beskæringen gemmes som brøkdele; serveren laver et `-b` billede (1800×600 WebP) ved gem, gemt via
  `content_media.store_image` (owner_kind `banner`).

---

## 11. Del B – Krydslister-modulet (`#/krydslister`)

Formål: krydslister, artslister og World Bird List som rigtigt modul med listetyper, der **kan redigeres i admin**.
Showcasens kode (`services/netfugl.py`, `netfugl_list.py`, `api/netfugl.py`, `web/js/netfugl/kryds.js`, `arter.js`) er
udgangspunktet: **kopiér logikken** til nye filer og rens den for showcase-specifikke ting. Showcasen beholder sine
`nf_*`-tabeller og må ikke gå i stykker.

### 11.1 Flag og rettigheder
- Flag `krydslister` (preview), `krydslister_manuel` (brugere kan selv krydse, on), `krydslister_wbl` (World Bird List
  og artssider, on).
- Admin-modul `"krydslister": "Krydslister: listetyper, artslister, World Bird List og godkendelse af kryds"`.

### 11.2 Datamodel (præfiks `kl_`, alle i `USER_TABLES`)

- **`kl_taxa`** – World Bird List: `id`, `rang` (`art`/`underart`), `parent_id` null, `orden`, `familie`, `latin`
  (unique pr. version), `engelsk`, `dansk` null, `netfugl_id` null unique, `avilist_id` null, `artnr` null (DOFbasen,
  via latin), `sort`, `aktiv` bool, `version` (fx "AviList 2025"). Indeks på `lower(latin)`, `artnr`.
- **`kl_species_lists`** – `id`, `key` unique (`dk`, `vp`, …), `navn`, `beskrivelse`, `sort`.
- **`kl_species_list_entries`** – `list_id`, `taxon_id`, `kategorier` varchar(1)[] (A–F), `sjaeldenhed`
  (`almindelig`/`relativ_almindelig`/`ualmindelig`/`sjaelden`/`su`), `sort`. PK (list_id, taxon_id).
- **`kl_list_defs`** – listetyper (erstatter den hårdkodede `LISTS`): `key` unique, `navn` (med `{p}`), `ref_list_id`,
  `auto` bool, `manuel` bool, `param` (`year`/`month`/`afdeling`/`kommune`/null), `regler` jsonb
  (`{"kategorier": ["A","B","C"], "kun_ringmaerkning": false, "land": "DK"}`), `sort`, `aktiv`, `beskrivelse_md`.
  Seedes med de 10 fra `services/netfugl.LISTS`.
- **`kl_kryds`** – manuelle kryds (som `nf_kryds`) + `status` (`ok`/`afventer`/`afvist`) og `behandlet_af`.
- **`kl_auto_lists`** – som `nf_auto_lists` (arrays pr. deltager og liste).

### 11.3 Import og seed
- **AviList-import** (`services/kryds_taxa.py`): admin uploader AviList-CSV'en (åben licens – bekræft licensen i PR'en og
  skriv den i `docs/`). Importen viser en **diff** før den anvendes: nye arter, ændrede navne, splits/lumps (samme
  `avilist_id`, ny latin), forsvundne. "Anvend" skriver i én transaktion. `artnr` kobles via latin; ukendte vises i en
  liste til manuel kobling.
- `netfugl_id` på `kl_taxa` udfyldes fra fotoarkivets `_arter.json` (Netfugls `birdId` ↔ latin) – kør
  `photos_link_taxa` (§4.2 trin 6) bagefter.
- DK- og VP-listen seedes fra `nf_species` (kategori → array, `su` → `sjaeldenhed='su'`, ellers `almindelig`), og
  sjældenhed rettes derefter i admin (eller med CSV-import, som emneord i Artikler: eksport → ret → import med diff).

### 11.4 Sider (kopiér fra showcasen)
Om krydslister · Krydsliste (pr. listetype og parameter) · Seneste kryds · Hvem har set hvad? · Blockers · Blockers-
statistik · Årstophits · Mine kryds · Alle krydslister · Krydsregler (tekstside) · Artslister (DK, VP, … med
kategorier og sjældenhed, CSV/Print) · World Bird List (orden → familie → art, søg) · Artsside (fotos fra galleriet,
fund i DOFbasen som i showcasen).

### 11.5 Admin
- **Listetyper:** opret/ret/deaktivér `kl_list_defs` (navn, referenceliste, auto/manuel, parameter, regler). Ændring
  → genberegn auto-lister i baggrunden (worker-kommando `kryds_recompute`).
- **Artslister:** opret liste, tilføj art (søg i `kl_taxa`), kategorier (flervalg A–F), sjældenhed, sortér, fjern,
  CSV-eksport/-import.
- **World Bird List:** import (§11.3), søg, ret dansk navn, koble artnr.
- **Kryds til godkendelse:** manuelle kryds på lister med `regler.godkendelse = true` (fx Falsk VP) – godkend/afvis med
  besked (samme `send_message`).
- Workeren: tilføj `kryds_auto` til `services/schedule.TASKS` *og* `worker.SCHEDULED` (hver 6. time, kun når flaget ikke
  er `off`), samme algoritme som `nf_auto_lists`, men drevet af `kl_list_defs.regler`.

**Accept fase 5–6:** de 10 seedede listetyper giver de samme tal som showcasen for de samme deltagere (test med
fixture-observationer); en ny listetype oprettet i admin beregnes uden kodeændring; AviList-diff vises og anvendes.

---

## 12. Del C – Artikler: forfatterregister, sprog, link-artikler (fase 7)

- **`art_authors`**: `id`, `navn`, `navn_norm` (index), `netfugl_forfatter_id` null unique, `user_id` null,
  `bio_md` null, `skjult` bool. **`art_article_authors`**: `(article_id, author_id, sort)`.
- Migration: læs `art_articles.authors` (JSON `[{navn, netfugl_id}]`) og opret forfattere + koblinger. Behold
  JSON-kolonnen i én release (læses ikke længere), fjern den i en senere migration.
  **Splitning:** et navn som "Lars Witting og Sakari Kauppinen" eller "A, B og C" deles *ikke* automatisk – vis dem i
  admin med en knap "Del op i flere forfattere".
- **Admin → Forfattere** i Artikler: samme UI-mønster som Admin → Emneord (commit 130e724 og 5629040: alle rettelser gemmes
  samlet, omdøbning til eksisterende navn **fletter**, CSV-eksport/-import). Flet = flyt koblinger, slet den ene.
- Editoren vælger forfattere fra registret (søg, "opret ny").
- **Sprog:** `art_articles.sprog` varchar(5) default `da` (`da`/`en`), vist som filter i søgning og som
  `lang`-attribut på artiklens HTML.
- **Link-artikler:** `kind='link'` med `source_url` (påkrævet) og `lead`; kortet åbner kilden i ny fane, og artikelsiden
  viser lead + "Læs på <domæne>".

---

## 13. Del D – Se som bruger (fase 7)

Kun **superadmin**. Formål: support ("hvad ser brugeren?") uden at kunne handle i brugerens navn.

- `POST /api/admin/se-som {user_id, grund}` → gemmer i sessionen (server-side, Valkey) `view_as = {user_id, until:
  now+15 min, by: superadmin_id}` og skriver audit-post. `DELETE /api/admin/se-som` afslutter.
- I `require_user`: hvis sessionen har gyldig `view_as`, returnér **målbrugeren** til læsekald, men sæt
  `request.state.view_as = True`.
- **Alle ikke-GET/HEAD-kald afvises med 403** "Du ser appen som en anden bruger – kun læsning", undtagen
  `DELETE /api/admin/se-som`. Implementér som middleware, så intet endpoint kan glemme det. SSE og push-abonnementer
  oprettes ikke i tilstanden.
- Frontend: rødt fast banner øverst "Du ser appen som <navn> (<kode>) – afslut" med nedtælling. Startes fra Admin →
  Brugere (knap pr. bruger).
- Test: POST/PUT/DELETE afvises; tilstanden udløber; audit skrives; en almindelig admin får 403 på start.

---

## 14. API-oversigt (nye ruter)

Fotos (`api/fotos.py`, `api/admin/fotos.py`):
```
GET    /api/fotos                         søg/liste (§9.1)
GET    /api/fotos/{id}                    fotoside
GET    /api/fotos/n/{netfugl_id}          → {id}
POST   /api/fotos                         upload (multipart)
PUT    /api/fotos/{id}                    ret
DELETE /api/fotos/{id}
POST   /api/fotos/{id}/like               toggle
GET    /api/fotos/{id}/kommentarer · POST · PUT/DELETE /api/fotos/kommentarer/{cid} · POST …/{cid}/anmeld
GET    /api/fotos/mine                    egne fotos + indstillinger
PUT    /api/fotos/mine/indstillinger      standard-rettighed, samtykke
GET    /api/fotos/mine-observationer      §8
GET    /api/fotos/til-observationer       §8
GET    /api/fotos/koder                   kodelister + lande (cache længe)
GET    /api/fotos/banner                  aktuelt banner
GET    /api/fotos/sider/retningslinjer
POST   /api/fotos/{id}/gruppe             forslag/sæt gruppe (ejer ved upload, moderator altid)

GET    /api/admin/fotos/koe?status=       §7.2
PUT    /api/admin/fotos/{id}/status       {status, grund?}
PUT    /api/admin/fotos/{id}/laas         {locked: bool}
GET|PUT|DELETE /api/admin/fotos/privilegier[/{user_id}]
GET|PATCH /api/admin/fotos/fotografer[/{pid}] · POST /api/admin/fotos/fotografer/flet
GET    /api/admin/fotos/import/mapper · POST /api/admin/fotos/import · PUT …/{id}/del/{n} · POST …/{id}/start · GET …[/{id}] · DELETE …/{id}
GET|POST|PATCH|DELETE /api/admin/fotos/bannere[/{id}]
GET|PUT /api/admin/fotos/koder · GET|PUT /api/admin/fotos/indstillinger
```
Krydslister (`api/krydslister.py`, `api/admin/krydslister.py`): kopiér showcasens `/kryds/*`, `/mine-kryds/*`,
`/arter/*`, `/art/*`-ruter under `/api/krydslister/…` + admin-ruter for listetyper, artslister, taxa-import og
godkendelse. Artikler: `/api/admin/artikler/forfattere[...]` som emneord. Se som: §13.

Husk `tests/fixtures/api_routes.json` (rute-snapshot) i hver fase.

---

## 15. Test (ud over de nævnte)

- `test_architecture.py` skal fortsat være grøn: SQL kun i services; ingen SQL blander grupper (`tools/sql_groups.py`).
- Adgang: hver ny rute giver 404 med flaget `off`, og 403 uden rettighed (parametriseret test som for Artikler).
- Fotos: privilegier (4 niveauer + admin), kvote, lås, afvisning → support-tråd, interne noter skjult, medieadgang til
  upubliceret foto (ejer ja, andre 404).
- Import: idempotens, `overwrite_local`, zip-slip, ukendte koder logges, fotografmatch.
- Søgning: hvert filter for sig og kombineret; sortering; gruppe vises som ét kort.
- Krydslister: tal = showcasens for samme data; ny listetype; AviList-diff.
- Se som: §13.

---

## 16. Faser (byg og commit i denne rækkefølge)

| Fase | Indhold | Afhænger af |
|---|---|---|
| 0 | Crawler, parser-tests, README (§2). Ejeren starter crawlen, mens resten bygges. | – |
| 1 | Flag, rettigheder, `models/gallery.py`, migration, import fra mappe + zip, fotografmatch (§3–§5) | 0 (fixtures) |
| 2 | Læsesider: galleri med alle filtre, fotoside, kommentarer/likes, medieadgang (§9.1–9.2, §4.3) → **fasens mål nået** | 1 |
| 3 | Upload, ret, slet, EXIF, privilegier ved upload, Mine fotos (§6, §9.3) | 2 |
| 4 | Moderation: kø, afvisning med besked, lås, gruppér, interne noter, privilegier-fane, fotografer-fane (§7) | 3 |
| 5 | Krydslister: model, seed fra showcase, AviList-import, artslister-admin (§11.2–11.3, 11.5 første to punkter) | – |
| 6 | Krydslister: sider, listetyper i admin, worker, godkendelse; `photos_link_taxa`; DOFbasen-kobling for fotos (§8, §11.4–11.5) | 5, 2 |
| 7 | Bannere (§10), Artikler-forfattere/sprog/link (§12), Se som (§13), notifikationer (§9.4) | 2 |

Hver fase: opdatér `docs/funktioner.md` (kort afsnit) og `README.md`'s funktionsliste, når en del bliver synlig for
brugerne.

---

## 17. Åbne punkter (byg med standardvalget, nævn det i commit'en)

| Punkt | Standardvalg |
|---|---|
| Netfugl-brugeres eksisterende *krydslister* (deres manuelle kryds) | Importeres **ikke** i denne omgang. Byg `kl_kryds` så en senere import kan udfylde den (felt `kilde`). |
| Fotos, der er upublicerede/afviste på Netfugl | Crawles ikke (kun offentlige sider). |
| Rettigheder til arkivet | Ejeren afklarer med Netfugl/DOF, før `fotos` sættes til `on`. Indtil da `preview`. |
| Sjældenhed for VP-listen | Kun DK-listens sjældenhed bruges til filter og 30-grænsen. |
| Original i fuld størrelse | Vises for alle med adgang til fotoet (som på Netfugl). |
| Kort over fotos | Ikke i denne omgang (kun lille kort på fotosiden). |
| Lagerplads | Fotos ligger under `art/` og tælles derfor allerede med i `services/diskusage.measure` (Admin → Drift). Tilføj en opdeling pr. `owner_kind`, hvis det er billigt. |
