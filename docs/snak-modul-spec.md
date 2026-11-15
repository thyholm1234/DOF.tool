# Snak – specifikation af nyt modul (til implementering)

Status: implementeret (fase 1–4), 15. november 2026. Oprindelig status: specifikation, 14. november 2026. Skrevet til at blive bygget trin for trin (fase 1–4, §13) af en anden model.
Læs `docs/arkitektur.md` først – alle regler dér gælder. Denne fil siger *hvad* der skal bygges og *hvor*; den
gentager ikke arkitekturreglerne. Arbejdsnavnet er **Snak** (`#/snak`); navnet står ét sted i frontend (`snak/common.js`)
og i funktionsflagets tekst, så det er nemt at ændre (§15).

---

## 0. Kort fortalt

Et socialt modul – **fuglekiggerens Facebook** – hvor brugerne laver **opslag**, som andre kommenterer og svarer på.
Det er en blanding af **forum og social medie**:

- **Som et forum**: hvert opslag har en **kategori** (Artsbestemmelse, Artikler og læsning, Fund og oplevelser …), kan
  have en titel, kan få en **løsning** (fx den rigtige artsbestemmelse), kan fastgøres og låses, og tråden har sin
  egen side (`#/snak/ID`).
- **Som et socialt medie**: forsiden er et **feed**, man ruller ned over. Hvert opslag er en **åben tråd** direkte i feedet:
  man ser de **nyeste kommentarer øverst**, kan skrive en kommentar eller svare uden at forlade feedet, og **reagere** (👍 ❤️ 😂 😮 😢 😡, som på Facebook).
  Når en tråd har mange kommentarer, vises kun de nyeste. **«Se mere»** åbner tråden på dens egen side.
- Det formelle og det uformelle må blandes. En præsentation af en ny artikel står side om side med «Hvad er det for
  en vadefugl?». Diskussioner må gerne opstå.

Interfacet skal ligne **Feedet** (`web/js/views/feed.js`: trådkort, tags, 👍/💬-tællere, sidepanel på desktop) og
**artiklernes kommentarer** (`web/js/artikler/kommentarer.js`: svar, ret, slet, anmeld). Genbrug deres komponenter og
CSS-tokens frem for at opfinde nye.

### Beslutninger (fra ejerens ønske)

| Spørgsmål | Beslutning |
|---|---|
| Hvad er et opslag? | En forumtråd med kategori, vist som et kort i et feed. Tråden er altid åben for kommentarer, medmindre en moderator låser den. |
| Reaktioner | De klassiske Facebook-reaktioner på **opslag og kommentarer**: 👍 Synes godt om, ❤️ Elsker, 😂 Haha, 😮 Wow, 😢 Ked af det, 😡 Vred (§2.9). Én reaktion pr. bruger pr. mål; man kan skifte eller fjerne den. Egen tabel `sn_reactions` – ikke `likes`. |
| Kommentarernes rækkefølge | **Nyeste øverst** (topniveau). Svar under en kommentar står kronologisk, ældste først, så de læses som en samtale – som på Facebook. |
| Hvor mange kommentarer i feedet? | De **3 nyeste** topniveau-kommentarer, hver med højst det **nyeste svar**. Tallet kan ændres under Admin → Indstillinger. Er der flere, vises «Se alle N kommentarer», der åbner trådsiden. |
| Svar på svar | Ét indrykningsniveau. Et svar på et svar lægges under samme topniveau-kommentar med «Svar til *Navn*» øverst (`reply_to_user_id`), som på Facebook. |
| Admin | En **fane i modulet** (`#/snak/admin`), som i Artikler og Fotos. |
| Moderation | Under **Moderation** (menupunktet) → fanen Kommentarer → ny visning **«Snak»**, som «Artikelkommentarer» i dag. Moderatorhandlinger direkte på opslag og kommentarer i feedet. |
| Moderationsform | **Efter-moderation + anmeld** (som Artikler §8.2): alt vises med det samme, brugere kan anmelde, moderatorer har en kø. Eksisterende `comment_bans` gælder også her. |
| Muligheder ved opret | Tekst (let markdown), billeder, link med forhåndsvisning, vedhæft noget fra portalen (artikel, foto, observationstråd, art, lokalitet), arter som emneord, kategori. Se §5. |
| Eksempelopslag | 5 opslag med kommentarer seedes som **eksempler** (§9). De er tydeligt mærket og kan slettes samlet i admin. |

---

## 1. Funktionsflag, rettigheder og menu

### 1.1 Funktionsflag (`services/features.py`)

| Nøgle | Forælder | Standard | Tekst |
|---|---|---|---|
| `snak` | – | `preview` | «Snak (opslag, kommentarer og diskussioner)»: feed med opslag i kategorier, kommentarer med svar, reaktioner (👍 ❤️ 😂 😮 😢 😡), anmeld og moderation. |
| `snak_billeder` | `snak` | `on` | Billeder i opslag (op til 10) og i kommentarer (1). |
| `snak_links` | `snak` | `on` | Forhåndsvisning af eksterne links (titel, beskrivelse og billede hentes af serveren). |
| `snak_notifikationer` | `snak` | `on` | Notifikationer om svar, kommentarer på egne opslag og fulgte tråde. |

Er `snak` ikke synligt for brugeren, svarer hele API'et 404 (mønstret fra `api/fotos.py:need`).

### 1.2 Rettigheder (`services/auth.MODULES`)

- Nyt modul **`snak`**: «Snak: kategorier, indstillinger, fastgørelse og eksempler». Det giver adgang til admin-fanen.
  Tilføj det til rollen `editor` i `DEFAULT_ROLES` (admin får alle moduler automatisk).
- **Moderation** bruger det eksisterende modul **`moderation`**: skjul/vis, lås/åbn, flyt kategori, kø og udelukkelser.
- `services/snak_access.py` efter mønstret i `photo_access.py`/`article_access.py`:
  `Access(user, flags)` med `on(key)`, `admin` (superadmin eller `can(user, "snak")`), `can_moderate`
  (superadmin eller `can(user, "moderation")`) og `out()`. `out()` lægges i `/api/me` som `"snak"`
  (`api/auth.py` ved siden af `"artikler"` og `"fotos"`).

### 1.3 Menu og ruter (frontend)

- `web/js/app.js`: ny post `SNAK = ["#/snak", "chat", "Snak"]`. Den vises i `extra` ved siden af Artikler og Fotos,
  når `state.user?.snak?.features?.snak` er sat. Den skal også med i `NAV` og have en rute:
  `[/^\/snak(?:\/(.*))?$/, () => import("./views/snak.js")]`.
- Ruter i modulet (`views/snak.js` er skallen med faner, som `views/artikler.js`):

| Rute | Side |
|---|---|
| `#/snak` | Feed (alle kategorier) |
| `#/snak/k/<slug>` | Feed for én kategori |
| `#/snak/<id>` | Trådside (opslag + alle kommentarer). `?k=<kommentar-id>` ruller til og fremhæver kommentaren. |
| `#/snak/<id>/rediger` | Ret opslag (samme komponent som opret) |
| `#/snak/ny` | Opret opslag (fuld side; i feedet åbnes samme komponent inline) |
| `#/snak/soeg?q=` | Søgning |
| `#/snak/mine` | Mine opslag og tråde jeg følger |
| `#/snak/admin` | Admin (kun med modulet `snak`). Underfaner: Kategorier, Indstillinger, Eksempler, Statistik |

- Faner: `Feed`, `Søg`, `Mine` og `Admin` (kun med adgang). Der er ingen fane til Moderation her; den ligger under Moderation.
- 🔔-knap i fanelinjen (som Artikler) åbner notifikationsvalgene (§8.3).

---

## 2. Brugerflader

Alle tekster er på dansk, tal formateres med `fmtN`, datoer med `ago()` (under 7 dage) ellers `fmtDate`.
Mobil først: kortene fylder hele bredden under 720 px; på desktop er der et sidepanel som på Feedet.

### 2.1 Feed (`snak/feed.js`)

```
┌ Snak ─────────────────────────────────────────── [Søg i Snak …]  [+ Nyt opslag] ┐
│ Feed · Søg · Mine · Admin                                                  🔔   │
├─────────────────────────────────────────────────────────────┬──────────────────┤
│ [Hvad har du set? Skriv et opslag …]   (åbner composer)       │ Kategorier       │
│ Alle · Artsbestemmelse · Artikler · Fund · Diskussion · …    │  Artsbestemmelse │
│ Sortér: Seneste aktivitet ▾      ☐ Kun uløste bestemmelser   │  12 nye          │
│ ┌─────────────────────────────────────────────────────────┐ │  …               │
│ │ 📌 Fastgjort                                              │ │ Mest aktive i    │
│ │ (A) Navn Navnesen · 2 t · [Artsbestemmelse]       ⋯      │ │ ugen             │
│ │ Ung måge på Amager – sølvmåge eller kaspisk?             │ │ Uløste           │
│ │ Tekst … (klippes efter 6 linjer: «Vis mere»)              │ │ bestemmelser     │
│ │ [billede][billede][+2]                                    │ │                  │
│ │ #Sølvmåge #Kaspisk måge · 📍 Kongelunden                  │ │                  │
│ │ 👍❤️😮 14 · 💬 23 · 👁 310     [👍 Synes godt om] [💬]   │ │                  │
│ │ ─────────────────────────────────────────────────────── │ │                  │
│ │ Se alle 23 kommentarer                                   │ │                  │
│ │ (B) Bo: Vingespidsen taler for kaspisk …     5 min · 👍❤️ 3│ │                  │
│ │     ↳ (C) Cecilie: Enig, se også …           2 min       │ │                  │
│ │ (D) Dorte: Kan vi få et billede af …         1 t         │ │                  │
│ │ (E) Erik: Jeg har set den i …                3 t         │ │                  │
│ │ [Skriv en kommentar …]                                    │ │                  │
│ └─────────────────────────────────────────────────────────┘ │                  │
└─────────────────────────────────────────────────────────────┴──────────────────┘
```

- **Sortering**: `Seneste aktivitet` (standard; nyt opslag eller ny kommentar løfter tråden), `Nyeste opslag`,
  `Populære` (reaktioner + kommentarer de seneste 7 dage; reaktionernes `created_at` tæller). Valget huskes med `store("snak")`.
- **Kategori-chips** (vandret scroll på mobil) og `#/snak/k/<slug>`. Kategorien `Artsbestemmelse` har desuden
  afkrydsningen «Kun uløste».
- **Fastgjorte** opslag står øverst i «Alle» og i deres kategori (højst 3; admin bestemmer).
- **Uendelig rulning** med `IntersectionObserver`, 15 opslag pr. side (cursor-paginering på `(sort_key, id)`, ikke offset).
- **Kortet** (`postCard(p, {inFeed: true})`):
  - Hoved: avatar (initialer, som i dag), navn (link til `#/observator/KODE`, når observatørsider er slået til),
    tid (`ago`, titel = fuld dato), kategori-tag (kategoriens farve), mærker: `Eksempel`, `Løst ✓`, `🔒 Låst`, `Redigeret`.
  - `⋯`-menu: Følg/Følg ikke, Kopiér link, Anmeld. Egne opslag: Ret, Slet. Moderator: Skjul, Lås, Flyt kategori,
    Fastgør (kun admin).
  - Titel (valgfri, men påkrævet i kategorier med `kraever_titel`), brødtekst (renderet HTML) klippet efter
    6 linjer med «Vis mere», billedgitter (1 = stort, 2 = side om side, 3+ = gitter med «+N»; klik åbner lysboksen,
    som i Fotos), vedhæftet kort (§5.3), arter som chips (link til `#/art/ARTNR`) og lokalitet (link til `#/lok/LOKNR`).
  - Tællere og knapper: `reactionWidget("post", id, …)` fra `snak/reaktioner.js` (§2.9), 💬-knap (fokus på kommentarfeltet), 👁 visninger.
  - **Kommentarblok** (§2.3) med de nyeste kommentarer og et kommentarfelt.
- **Nyt i feedet live**: SSE-kanalen `snak` (§8.4). Nye opslag giver en knap øverst, «3 nye opslag – vis», så listen
  ikke hopper. Nye kommentarer på synlige kort indsættes øverst i kortets kommentarblok, og tællerne opdateres.
- **Sidepanel** (desktop): kategorier med antal nye siden sidste besøg, «Uløste bestemmelser» (5 nyeste) og
  «Mest aktive tråde i ugen».
- **Tom tilstand**: «Ingen opslag endnu – vær den første» + knap.

### 2.2 Trådside (`snak/opslag.js`, `#/snak/<id>`)

- Samme kort (`inFeed: false`): hele teksten, alle billeder og alle kommentarer.
- Kommentar-sortering: `Nyeste først` (standard) / `Ældste først` / `Mest populære`. Svar står altid kronologisk.
- Er der over 50 topniveau-kommentarer, hentes de i portioner: «Vis flere kommentarer» nederst.
- `?k=<id>` ruller til kommentaren og fremhæver den i 3 s. Ligger kommentaren i en senere portion, hentes den med
  `around=<id>` (§10).
- **Løsning** (kategorier med `har_loesning`, fx Artsbestemmelse): forfatteren og moderatorer kan markere én
  kommentar som løsning. Den vises fremhævet under opslaget («✓ Løsning: … – markeret af Navn»), og kortet får
  `Løst ✓`. Tilknyttes en art, når løsningen markeres («Hvilken art blev det?», valgfrit), lægges arten på opslaget.
- Titel i fanen: opslagets titel eller de første 60 tegn af teksten. Del-link: `/s/snak/<id>`, hvis `api/share.py`
  udvides (fase 4, valgfrit).
- En visning tælles én gang pr. bruger pr. døgn (som `photos.register_view`).

### 2.3 Kommentarer (`snak/kommentarer.js`)

Byg ud fra `artikler/kommentarer.js` (svar, ret, slet, anmeld, moderatorhandlinger, `banForm`). Forskelle:

- **Rækkefølge**: topniveau nyeste først; svar kronologisk under deres topniveau-kommentar.
- **I feedet**: de `feed_kommentarer` (standard 3) nyeste topniveau-kommentarer, hver med de `feed_svar`
  (standard 1) nyeste svar. Er der skjulte svar: «Se 4 svar mere» udvider inline (henter tråden). Er der flere
  topniveau-kommentarer end vist: «Se alle N kommentarer» → `#/snak/<id>`.
- **Svar**: «Svar» åbner et felt direkte under kommentaren med «@Navn » forudfyldt som tekst (kun visuelt; serveren
  gemmer `reply_to_user_id`). Svar på et svar får samme `parent_id` som det besvarede svar (altid topniveau-kommentaren).
- **Indhold**: markdown-profilen `kommentar` (fed, kursiv, links, lister – ingen overskrifter/billeder i teksten) +
  højst 1 billede (`snak_billeder`). Højst 5.000 tegn.
- **Ret**: inden for 24 timer (`ret_minutter` i indstillinger) for brugeren; vises som «redigeret». Moderatorer kan
  ikke rette andres tekst, kun skjule.
- **Slet egen**: blød sletning. Har kommentaren svar, står «Kommentaren er slettet» tilbage; ellers forsvinder den.
- **Reaktioner** på kommentarer: `reactionWidget("comment", id, …, { small: true })` (§2.9) – som på Facebook en lille
  tekstknap «Synes godt om» under kommentaren, med de mest brugte emojis og en tæller til højre.
- **Låst tråd**: feltet erstattes af «Tråden er låst af en moderator».
- **Udelukket bruger** (`moderation.active_ban`): feltet erstattes af beskeden fra `comment_bans`, som i dag.

### 2.4 Opret og ret opslag (`snak/composer.js`)

Én komponent bruges tre steder: inline øverst i feedet (udvides ved fokus), som fuld side (`#/snak/ny`) og ved
rettelser (`#/snak/<id>/rediger`). Se §5 for felterne og validering.

- Kladden gemmes i `localStorage` (`snak:kladde`, try/catch) hvert 2. sekund og ryddes ved afsendelse.
- «Forhåndsvis» renderer via serveren (`POST /api/snak/render`), som Artikler.
- Afsendelse → opslaget sættes øverst i feedet uden genindlæsning.
- Kategori skal vælges (vælger med farveprik og beskrivelse). Kategorier med `kun_staff` vises kun for brugere med
  modulet `snak` eller `moderation`.

### 2.5 Søg (`snak/soeg.js`)

Fritekst i titel, tekst og kommentarer (dansk tsvector, som Artikler §5.1), filtre: kategori, art, periode, «kun
uløste», «med billeder». Resultater som kompakte kort med fremhævet uddrag (`ts_headline`). Træf i en kommentar linker
til `#/snak/<id>?k=<kid>`.

### 2.6 Mine (`snak/mine.js`)

To lister: «Mine opslag» og «Tråde jeg følger» (med antal nye kommentarer siden sidst; «Følg ikke»-knap).

### 2.7 Admin (`snak/admin.js`, fane i modulet)

- **Kategorier**: tabel med navn, slug, beskrivelse, farve (fra paletten), ikon (et navn fra `icon()`), sortering
  (træk eller op/ned), og flag: `kraever_titel`, `har_loesning`, `kun_staff` og `arkiveret`. Opret, ret og arkivér.
  En kategori med opslag kan ikke slettes, kun arkiveres; dens opslag kan flyttes samlet til en anden kategori.
- **Indstillinger** (`settings`-nøgle `snak` via `setting_store`): `feed_kommentarer` (3), `feed_svar` (1),
  `max_billeder_opslag` (10), `ret_minutter` (1440), `opslag_pr_time` (5), `kommentarer_pr_minut` (5) og
  `link_preview` (til/fra).
- **Eksempler**: liste over seedede eksempelopslag med «Slet alle eksempler» (`ask()`-bekræftelse) og «Gendan eksempler».
- **Statistik**: opslag og kommentarer pr. uge (søjler via `charts.js`), aktive brugere, fordeling på kategorier.

### 2.8 Moderation (under menupunktet Moderation)

- `views/admin/moderation.js`: tilføj `["snak", "Snak"]` til `seg(...)`, når `state.user?.snak?.can_moderate`, og
  `modSnak(body)` henter `commentQueue()`/`postQueue()` fra `snak/admin.js` (som `modArticles` gør med artikler).
- Køen har faner: `Anmeldte` (standard; sorteret efter antal anmeldelser), `Skjulte` og `Alle nye` (seneste 7 dage).
  Hver række viser opslag eller kommentar, forfatter, kategori, anmeldelser (grund + note) og handlinger: Skjul (med
  grund, `askText`), Vis igen, Afvis anmeldelser, Lås tråd, Skriv til bruger (`messageForm`, genbrug) og Udeluk (`banForm`).
- Alle handlinger logges i `activity` (som artikelmoderationen).

### 2.9 Reaktioner (`snak/reaktioner.js`)

Sættet er fast og defineres ét sted på serveren (`services/snak_reactions.TYPES`) og ét sted i frontend (`REACTIONS`):

| `type` | Emoji | Dansk navn | Farve (designtoken) |
|---|---|---|---|
| `like` | 👍 | Synes godt om | `--accent` |
| `love` | ❤️ | Elsker | `--danger` |
| `haha` | 😂 | Haha | `--warn` |
| `wow` | 😮 | Wow | `--warn` |
| `sad` | 😢 | Ked af det | `--warn` |
| `angry` | 😡 | Vred | `--danger` |

**Interaktion** (`reactionWidget(kind, id, state, { small })`, afløser `likeWidget` i Snak):

- **Knappen** viser «👍 Synes godt om». Et **kort tryk** sætter 👍, eller fjerner brugerens reaktion, hvis den allerede er sat.
  Er en anden reaktion sat, viser knappen den med dens emoji, navn og farve; et kort tryk fjerner den.
- **Vælgeren** (reaktionsbjælken) åbnes ved **hover i 500 ms** (mus), **langt tryk** (400 ms, touch) eller **piletast ned / Enter
  på knappen med modifier** – og lukkes med Esc eller ved klik udenfor. Den viser de seks emojis i en række; at vælge én
  sætter eller **skifter** reaktionen; at vælge den, man allerede har, fjerner den. På touch må et langt tryk ikke udløse
  tekstmarkering eller kontekstmenu (`touch-callout: none`, `user-select: none`).
- **Optimistisk opdatering** som i `likes.js`; ruller tilbage med `toast` ved fejl. Ikke logget ind → videre til `#/login`.
- **Opsummering**: de op til tre mest brugte reaktioners emojis side om side (overlappende) + samlet tal. Ved opslag
  efterfulgt af «Navn, Navn og 12 andre»; ved kommentarer (`small`) kun emojis og tal.
- **Hvem reagerede**: tryk på opsummeringen åbner en native `<dialog>` (`modal()`) med faner **Alle · 👍 12 · ❤️ 3 · …**
  (kun typer med mindst én) og en liste med navn, obserkode, tid og brugerens emoji.
- **Tilgængelighed**: knappen er en `button` med `aria-haspopup="true"`, `aria-expanded` og `aria-pressed`; vælgeren er en
  `role="menu"` med `role="menuitemradio"` pr. emoji (`aria-label` = dansk navn, `aria-checked`), piletaster flytter, Enter
  vælger, Esc lukker og returnerer fokus. Emojis har aldrig farven som eneste signal (navnet vises i knappen).
  `prefers-reduced-motion`: ingen pop-animation.
- **Live**: `reaction`-eventen (§8.4) opdaterer opsummeringen på synlige kort uden at ændre den, brugeren selv har sat.

---

## 3. Datamodel (`server/portal/models/snak.py`, brugergruppen)

Alle tabeller hører til **brugerdata**: tilføj dem til `USER_TABLES` i `dbgroups.py`. Ingen SQL må blande `sn_*` med
observationstabeller (de ligger i en anden databasegruppe). Kobling til observationer sker via nøgler + separate
forespørgsler (§5.3). Nye kolonner efter første udrulning skrives som `ALTER TABLE … ADD COLUMN IF NOT EXISTS` i
`models/ddl.py:EXTRA_DDL`.

```
sn_categories
  id serial PK, slug varchar(40) UNIQUE, navn varchar(60), beskrivelse varchar(300) default '',
  farve varchar(16) default 'brand', ikon varchar(24) default 'chat', sort int default 100,
  kraever_titel bool default false, har_loesning bool default false, kun_staff bool default false,
  arkiveret bool default false, created_at timestamptz default now()

sn_posts
  id serial PK, category_id FK sn_categories (RESTRICT), user_id FK users (SET NULL) NULL,
  eksempel_navn varchar(80) NULL           -- forfatter på eksempelopslag (user_id NULL); vises med mærket «Eksempel»
  titel varchar(200) NULL, body_md text, body_html text, body_text text,
  attachment jsonb NULL                    -- §5.3: {type, ref, title, teaser, image, url, host}
  arter int[] default '{}', loknr int NULL, lok_navn varchar(200) NULL,
  status varchar(10) default 'synlig'      -- synlig / skjult / slettet
  hidden_reason text NULL, hidden_by int NULL, hidden_at timestamptz NULL,
  locked bool default false, pinned_at timestamptz NULL,
  solution_comment_id int NULL             -- ingen FK (cirkulær); valideres i service
  n_comments int default 0, n_reactions int default 0, views int default 0,   -- denormaliseret, opdateres i service
  last_activity_at timestamptz default now(),
  eksempel bool default false,
  created_at, edited_at NULL, search tsvector GENERATED (titel A, body_text B, 'danish')
  INDEX (status, last_activity_at DESC, id DESC), INDEX (category_id, status, last_activity_at DESC),
  INDEX (user_id), GIN (search), GIN (arter)

sn_comments
  id serial PK, post_id FK sn_posts CASCADE, parent_id FK sn_comments CASCADE NULL (altid topniveau),
  user_id FK users CASCADE NULL, eksempel_navn varchar(80) NULL, reply_to_user_id int NULL,
  reply_to_navn varchar(80) NULL,          -- til eksempler og slettede brugere
  body_md text, body_html text, body_text text, media_id FK content_media SET NULL NULL,
  status varchar(10) default 'synlig', hidden_reason, hidden_by, hidden_at,
  n_reactions int default 0, created_at, edited_at NULL,
  search tsvector GENERATED (body_text, 'danish')
  INDEX (post_id, parent_id, created_at DESC), INDEX (user_id), GIN (search)

sn_reports
  id serial PK, kind varchar(8) ('post' | 'comment'), target_id int, user_id FK users CASCADE,
  reason varchar(20), note varchar(500) default '', created_at, handled_at NULL, handled_by NULL,
  UNIQUE (kind, target_id, user_id)

sn_reactions
  id serial PK, user_id FK users CASCADE, kind varchar(7) ('post' | 'comment'), target_id int,
  type varchar(5) ('like' | 'love' | 'haha' | 'wow' | 'sad' | 'angry'),    -- CHECK-constraint på kind og type
  created_at, changed_at NULL,
  UNIQUE (user_id, kind, target_id),            -- én reaktion pr. bruger pr. mål
  INDEX (kind, target_id, type)

sn_follows
  post_id FK sn_posts CASCADE, user_id FK users CASCADE, created_at, last_seen_at timestamptz,
  muted bool default false, PK (post_id, user_id)

sn_views
  post_id, user_id, dag date, PK (post_id, user_id, dag)    -- visninger én gang pr. døgn; ryddes efter 30 dage
```

- **Billeder** i opslag og kommentarer gemmes i `content_media` med en ny `owner_kind`: `'snak'` (opslag) og
  `'snakk'` (kommentar). Pas på: kolonnen er `varchar(10)`. Opslagets billeder er dets medier sorteret efter `sort`.
  Genbrug `content_media.store_image` (EXIF/GPS fjernes, WebP-varianter, signaturtjek).
- **Adgang til medier**: `api/media.py` `/media/art/…` skal for `owner_kind` `snak`/`snakk` kræve login, at
  `snak` er synligt, og at opslaget ikke er skjult (moderatorer og forfatteren undtaget). Følg mønstret for fotos
  (`photos.media_info`, caching 60 s).
- **Reaktioner** gemmes i `sn_reactions` (ikke i den fælles `likes`-tabel, som kun kan rumme én slags og bruges af Feedet).
  `services/snak_reactions.py`: `TYPES`, `set_reaction(user, kind, id, type|None)`, `summary(kind, ids, user)` og `who(kind, id, type?)`.
  `set_reaction` er en upsert på `(user_id, kind, target_id)`: ny type skifter reaktionen (`changed_at`), `None` eller samme
  type sletter den. `n_reactions` på opslag/kommentar holdes opdateret i **samme transaktion** (ny +1, skift ±0, fjern −1).
  Mål skal findes, være synligt og ikke tilhøre et skjult opslag; brugeren må ikke være udelukket. Reaktioner på eget
  indhold er tilladt. Rate limit: 60 pr. minut pr. bruger.
- `summary` returnerer pr. mål: `{count, counts: {like: n, …}, mine: type|null, top: [op til 3 typer], names: [første 2 navne]}`
  fra **én** gruppering pr. kind (`GROUP BY target_id, type`), så feedet henter alle opslag og viste kommentarer i to kald.
- **Kategorier seedes** ved opstart, hvis tabellen er tom (`services/snak.py:seed_categories`, kaldt fra `bootstrap.py`):

| slug | navn | flag | beskrivelse |
|---|---|---|---|
| `artsbestemmelse` | Artsbestemmelse | `har_loesning` | Hjælp til at bestemme en fugl – vis billeder, lyd og omstændigheder. |
| `artikler` | Artikler og læsning | – | Nye artikler, bøger, rapporter og videnskab – del og diskutér. |
| `fund` | Fund og oplevelser | – | Dagens tur, gode fund og stemningsbilleder. |
| `diskussion` | Diskussion | `kraever_titel` | Fuglebeskyttelse, taksonomi, etik i felten, DOFbasen og meget mere. |
| `ture` | Ture og arrangementer | – | Fælles ture, samkørsel og arrangementer. |
| `udstyr` | Udstyr og foto | – | Kikkerter, kameraer, optagere, apps og teknik. |
| `spoergsmaal` | Spørgsmål og hjælp | `har_loesning` | Alt det andet – også spørgsmål om DOF.tool. |
| `meddelelser` | Meddelelser | `kun_staff` | Nyt fra redaktionen og moderatorerne. |

---

## 4. Server: services og regler

- `services/snak.py`: feed, tråd, opret, ret, slet, løsning, fastgør, lås, flyt, følg, søg, visninger og seed.
- `services/snak_comments.py`: list, add, edit, remove_own, report, queue og moderate. **Start med at kopiere
  `article_comments.py`** og tilpas; behold `CommentError(status, message)`, `REASONS` og rate limits via Valkey.
- `services/snak_links.py`: forhåndsvisning af links (§5.4).
- `services/snak_access.py`: §1.2.
- `api/snak.py`: routeren `/api/snak` (§10). Registrér den i `main.py` sammen med de andre routere.

**Regler, der skal håndhæves på serveren** (ikke kun i UI):

1. Skrivning kræver login, at `snak` er synligt, og at brugeren ikke er udelukket (`moderation.active_ban`).
2. Kategori skal findes og ikke være arkiveret. Er den `kun_staff`, kræves modulet `snak` eller `moderation`.
   Kræver kategorien titel (`kraever_titel`), skal der være en titel på 5–200 tegn.
3. Teksten er påkrævet (mindst 1 tegn efter trim), medmindre der er billeder eller et vedhæftet kort. Højst 10.000 tegn i
   opslag og 5.000 i kommentarer.
4. Rate limits: `opslag_pr_time` og `kommentarer_pr_minut` pr. bruger (Valkey-tæller med TTL). Overskridelse → 429
   med en dansk besked.
5. `parent_id` skal tilhøre samme opslag. Peger det på et svar, erstattes det af svarets `parent_id` (altid topniveau).
6. Låst eller skjult opslag → ingen nye kommentarer (403). Moderatorer må dog kommentere låste tråde.
7. `last_activity_at` sættes ved nyt opslag og ny synlig kommentar, ikke ved reaktioner eller rettelser.
   `n_comments` tæller synlige kommentarer.
8. Skjult indhold: andre brugere ser hverken tekst eller billeder. Forfatteren ser «Skjult af en moderator: *grund*».
   Moderatorer ser alt med markering.
9. Brugerens visningsnavn og obserkode hentes fra `users` ved læsning (ikke kopieret), så navneændringer slår igennem.
10. Al HTML kommer fra `content_render.render(..., profile="kommentar")`, også i opslag (se §5.1). Sanitering med nh3 sker
    dér. Frontend sætter kun server-renderet HTML ind via den eksisterende Trusted Types-policy (`trustedHTML`).

---

## 5. Opret opslag: felter

### 5.1 Tekst

Let markdown, profilen `kommentar` i `content_render.py` (fed, kursiv, links, lister, citat, kode, linjeskift). Tilføj
to ting til profilen, så de også virker i kommentarer:

- **Artsreferencer** `[[art:ARTNR]]`: genbrug `_art_ref_rule`, så de bliver til links til artssiden. Composeren får en
  «Indsæt art»-knap med artssøgning (`searchSpecies` fra `core.js`).
- **Automatiske links** til `https://…` (linkify, med `rel="noopener noreferrer nofollow ugc"`, som profilen allerede gør).

Overskrifter, billeder i teksten, tabeller og HTML forbliver slået fra.

### 5.2 Billeder (`snak_billeder`)

Op til `max_billeder_opslag` (10) pr. opslag og 1 pr. kommentar. Træk-og-slip eller vælg, med miniature,
rækkefølge og alt-tekst pr. billede. Upload sker straks til `POST /api/snak/media` (multipart; `owner_id` NULL, indtil
opslaget gemmes; samme oprydning af forældreløse medier efter 24 t som Artikler). Højst 25 MB pr. billede.
Kun billedformater (`content_media.sniff_image`). Der er ingen video i denne omgang (§15).

### 5.3 Vedhæft fra portalen

Ét vedhæftet kort pr. opslag. Vælgeren («📎 Vedhæft») har faner:

| type | Vælges via | `ref` | Kortet viser |
|---|---|---|---|
| `artikel` | søgning i artikler (`/api/artikler/soeg`) | artikel-id | forsidebillede, titel, teaser, forfattere |
| `foto` | galleriets søgning eller «Mine fotos» | foto-id | billede, art, fotograf |
| `traad` | observationstråd: indsæt et link `#/traad/DATO/ARTNR/LOKNR` (eller vælg blandt egne seneste observationer) | `"DATO/ARTNR/LOKNR"` | art, antal, lokalitet, dato |
| `art` | artssøgning | artnr | artsnavn, latin, eventuelt galleriets topfoto |
| `lokalitet` | lokalitetssøgning | loknr | navn, kommune |
| `link` | et eksternt link indsat i teksten eller i feltet | URL | §5.4 |

- Kortets indhold slås op på serveren ved gem og gemmes som et øjebliksbillede i `attachment` (så feedet ikke slår
  op i fire moduler pr. kort). Ved visning tjekker serveren kun, at målet stadig er synligt for brugeren. Fx
  vises en upubliceret artikel som «Artiklen er ikke tilgængelig». Er modulet slået fra for brugeren, vises et
  neutralt kort uden link.
- `traad` og `lokalitet` hentes fra observationsgruppen via separate forespørgsler (`dataset`/`ObsSession`); hemmelige
  observationer kan ikke vedhæftes.
- Indsættes et link til en portalside i teksten (`#/artikler/123`, `/s/…`), foreslår composeren at vedhæfte det.

### 5.4 Forhåndsvisning af eksterne links (`snak_links`)

`POST /api/snak/link-preview {url}`: serveren henter siden med `httpx` og `egress.check_request`-hooket (SSRF-reglen
i `services/egress.py` er obligatorisk) med timeout 5 s, højst 1 MB og kun `text/html`. Den læser `og:title`,
`og:description`, `og:image` og `<title>`. Billedet **hentes og gemmes ikke**; det vises som `<img>` med den eksterne URL.
CSP `img-src https:` tillader det allerede. Resultatet caches 24 t i Valkey pr. URL. Fejl giver et kort med kun
værtsnavnet. Kald må højst ske 20 gange i minuttet pr. bruger.

### 5.5 Arter og lokalitet

Op til 5 arter (`arter int[]`, artssøgning) og én valgfri lokalitet (`loknr` + navn til visning). De bruges til chips,
filtre og søgning. Ingen kobling til observationsdata ud over opslag ved gem.

---

## 6. Feed-forespørgslen

`GET /api/snak/feed?kategori=&sort=aktivitet|nyeste|populaere&uloeste=0|1&cursor=` returnerer
`{rows: [post], next: cursor|null, settings: {feed_kommentarer, feed_svar}}`.

- En forespørgsel efter opslagene (15) og derefter **én** forespørgsel efter kommentar-forhåndsvisningen for alle 15
  opslag: de N nyeste synlige topniveau-kommentarer pr. opslag (`ROW_NUMBER() OVER (PARTITION BY post_id ORDER BY
  created_at DESC)`) + de M nyeste svar pr. af dem + antal svar pr. topniveau-kommentar. Ingen N+1.
- Reaktioner for opslag og viste kommentarer hentes samlet med `snak_reactions.summary` (§3) – to kald i alt.
- Fastgjorte opslag lægges foran på første side (ikke med i cursoren).
- Cursor: base64 af `[sort_key, id]`. `populaere` bruger en score beregnet i SQL; cursoren bærer scoren.
- Svaret caches **ikke** pr. bruger (reaktioner/følg er personlige). Optimér med indekserne i §3; mål < 150 ms ved
  10.000 opslag (testdata i `tests/`).

---

## 7. Kommentar-API

- `GET /api/snak/{id}/kommentarer?sort=nyeste|aeldste|populaere&cursor=&around=` → topniveau i portioner af 50, hver
  med alle sine svar (kronologisk) og `n_svar`.
- `POST /api/snak/{id}/kommentarer {body, parent_id?, reply_to_user_id?, media_id?}` → den nye kommentar (renderet).
- `PATCH /api/snak/kommentarer/{cid} {body}`, `DELETE /api/snak/kommentarer/{cid}`,
  `POST /api/snak/kommentarer/{cid}/anmeld {reason, note}`.
- Moderator: `POST /api/snak/kommentarer/{cid}/moderer {action: skjul|vis|afvis_anmeldelser, reason}`.

---

## 8. Notifikationer, følg og live

### 8.1 Følg

Man følger automatisk sine egne opslag og tråde, man har kommenteret. «Følg/Følg ikke» står i `⋯`-menuen. `muted` =
følger ikke længere og bliver ikke automatisk tilmeldt igen.

### 8.2 Hvornår der sendes besked (`services/notify.notify`, `snak_notifikationer`)

| Hændelse | Modtager | Titel | dedup |
|---|---|---|---|
| Svar på min kommentar | forfatteren af den besvarede kommentar (og `reply_to_user_id`) | «Navn svarede på din kommentar» | `snak:svar:<kid>` |
| Kommentar på mit opslag | opslagets forfatter | «Navn kommenterede dit opslag» | `snak:kom:<kid>` |
| Ny kommentar i en fulgt tråd | følgere (ikke `muted`, ikke forfatteren selv) – **samles**: højst én besked pr. tråd pr. time | «Nye kommentarer: *titel*» | `snak:foelg:<post>:<time>` |
| Min kommentar markeret som løsning | kommentarens forfatter | «Din kommentar blev markeret som løsning» | `snak:loes:<kid>` |
| Reaktion på mit opslag/min kommentar | indholdets forfatter (ikke ved reaktion på eget indhold) – **samles**: højst én besked pr. mål pr. time | «Navn og 3 andre reagerede på dit opslag» (emoji for den mest brugte) | `snak:reak:<kind>:<id>:<time>` |
| Moderator skjulte mit indhold | forfatteren | «Dit opslag/din kommentar er skjult» + grund | `snak:skjul:<kind>:<id>` |

En bruger får aldrig flere beskeder om samme kommentar (prioritet: svar > kommentar på opslag > fulgt tråd).
URL: `#/snak/<post>?k=<kid>`.

### 8.3 Valg (🔔 i modulet; `GET/PUT /api/snak/notifikationer`)

Afkrydsning for hver hændelsestype i §8.2 (gemmes i `users.prefs.snak`) og en liste over fulgte tråde.

### 8.4 Live

`cache.publish("snak", {...})` ved nyt opslag (`{type: "post", id, category_id}`), ny kommentar
(`{type: "comment", post_id, id, parent_id}`), reaktion (`{type: "reaction", kind, id, count, counts}`) og moderation
(`{type: "mod", kind, id}`). Frontend lytter med `live.on("snak", …)` (som `feed.js`) og henter kun det, der er
synligt. Eventen indeholder ikke selve teksten; den hentes via API'et, så adgang og skjulte kommentarer respekteres.

---

## 9. Eksempelopslag (seed)

`services/snak_seed.py:seed_examples(session)` kaldes fra `bootstrap.py` **kun første gang** (settings-nøglen
`snak_seeded`), og igen fra «Gendan eksempler» i admin. Alle rækker får `eksempel = true`, `user_id = NULL` og
`eksempel_navn`. Navnene er **opdigtede** og må ikke være rigtige personer eller observatørkoder. De vises med mærket
**«Eksempel»** og uden link til en observatørside. Tidsstemplerne fordeles over de seneste 3 dage, så feedet ser levende
ud. «Slet alle eksempler» sletter opslag, kommentarer og deres medier.

Teksterne herunder er færdige og skal bruges ordret (markdown). `[[art:…]]` slås op på **navn** i `species` ved
seed (artnr kendes ikke på forhånd). Findes arten ikke, skrives navnet som almindelig tekst.

### 9.1 Artikler og læsning – præsentation af en artikel

- Forfatter: «Redaktionen (eksempel)». Vedhæftet: den **nyeste publicerede artikel** i `art_articles` (`type: artikel`).
  Er der ingen artikler, udelades vedhæftningen, og første linje bliver «(Her vises en artikel, når modulet har artikler.)».
- Titel: «Ny artikel: Hvorfor kommer der flere og flere sølvhejrer?»

```
Vi har lige lagt en ny artikel op, som gennemgår 30 års fund af [[art:Sølvhejre]] i Danmark – fra sjælden gæst til
en art, man nu kan regne med at se ved de fleste større søer i vinterhalvåret.

Tre ting vi synes er værd at diskutere:

1. Er det klimaet, eller er det bestandsfremgangen i Holland og Frankrig, der driver udviklingen?
2. Hvornår kommer det første sikre ynglefund herhjemme?
3. Bliver arten «for almindelig» til at blive indtastet – og hvad betyder det for statistikken?

Læs artiklen, og fortæl os, hvad du har set i dit eget område 👇
```

Kommentarer (nyeste sidst i listen; de skal vises nyeste øverst):
1. «Mette Kragsig (eksempel)», 2 dage siden: «Fin artikel! Ved Tissø har vi haft op til 14 på én gang i oktober.
   Det var utænkeligt for 10 år siden.»
   - Svar fra «Jonas Ravnholt (eksempel)», 2 dage siden: «14! Rekorden ved Vejlerne er vist 9 i år. Har I mange
     ringmærkede fugle imellem?»
   - Svar fra «Mette Kragsig (eksempel)»: «Svar til Jonas: Ja, vi aflæste én med fransk ring sidste vinter.»
2. «Ole Birkedal (eksempel)», 1 dag siden: «Til punkt 3: Jeg indtaster stadig alle mine – det er jo netop
   udviklingen, vi gerne vil kunne følge.»

### 9.2 Artsbestemmelse – med løsning

- Forfatter: «Karen Strandvig (eksempel)». Kategori: Artsbestemmelse. Arter: Sølvmåge, Kaspisk Måge. Lokalitet:
  slå `Kongelunden` op i `locations` (navn indeholder), ellers udelad.
- Billeder: **ingen** (der må ikke seedes fotos, vi ikke har rettigheder til). I stedet står der i teksten, at der
  ville være billeder.
- Titel: «Ung stormmåge-størrelse måge – sølvmåge eller kaspisk?»

```
Fotograferet i går ved Kongelunden, ca. kl. 14 i fladt lys. Jeg hælder til en 2K [[art:Kaspisk Måge]], men er ikke
sikker. (I et rigtigt opslag ville der være billeder her – vis gerne både siddende og flyvende fugl.)

Det, jeg lagde mærke til:

- Lang, slank og parallelsidet næb uden tydelig gonys-vinkel
- Lille, mørkt øje placeret højt i et «langt» hoved
- Lange ben og en meget oprejst holdning
- Ret ensfarvet, lys underside – men lidt plettet på flankerne

Hvad mener I? Og hvilke kendetegn ville I gerne have set bedre?
```

Kommentarer:
1. «Anders Kløvborg (eksempel)», 1 dag siden: «Hovedform og ben peger på kaspisk, men flankerne gør mig lidt usikker.
   Har du et billede af den udbredte vinge? Mønstret på de inderste håndsvingfjer er ofte det afgørende.»
   - Svar fra «Karen Strandvig (eksempel)»: «Svar til Anders: Ja – den lettede to gange. Der er et lyst «vindue» på
     de inderste håndsvingfjer og et smalt mørkt bånd på halen.»
2. «Lise Holmgaard (eksempel)», 20 timer siden: «Med det vingemønster og den hovedform er jeg ret sikker: **2K
   kaspisk måge**. Sølvmåge i samme alder ville have et grovere næb og en mere plettet, «beskidt» fremtoning.»
   → **markeres som løsning** (art: Kaspisk Måge).
3. «Peter Moesgaard (eksempel)», 18 timer siden: «Enig med Lise. Husk at den kan kræve SU-behandling afhængigt af
   tidspunktet – tjek lige listen.»

### 9.3 Diskussion – formelt og uformelt

- Forfatter: «Sofie Lunding (eksempel)». Kategori: Diskussion (kræver titel).
- Titel: «Skal vi bruge afspilning af fuglestemmer i felten?»

```
Jeg var på tur i weekenden, hvor en anden fuglekigger afspillede sang for at lokke en [[art:Natsanger]] frem. Den kom
frem – men den blev også ved med at synge i lang tid bagefter.

Jeg er selv i tvivl. Det er et effektivt værktøj, når man fx tæller ynglepar, men hvor går grænsen?

- Er det i orden uden for yngletiden?
- Hvad med sjældne arter, som mange vil se?
- Bør DOF have en klar anbefaling?

Hold tonen venlig – jeg ved, at vi ikke er enige om det her 🙂
```

Kommentarer:
1. «Henrik Aagaard (eksempel)», 2 dage siden: «Til optællinger med en fast metode er det fint, men til
   «lad os lige se den» synes jeg nej. Fuglen bruger energi på at forsvare et territorium mod en højttaler.»
   - Svar fra «Mads Thygesen (eksempel)»: «Svar til Henrik: Jeg er enig om ynglesæsonen, men om vinteren kan jeg ikke
     se problemet med kort afspilning.»
   - Svar fra «Henrik Aagaard (eksempel)»: «Svar til Mads: Fair – men så skal det være kort og ikke ved samme fugl
     hele dagen.»
2. «Birgitte Rask (eksempel)», 1 dag siden: «Der findes faktisk retningslinjer i flere lande. Måske kunne nogen lave
   et opslag under *Artikler og læsning* med en oversigt?»
3. «Kasper Nørholm (eksempel)», 5 timer siden: «Nej. Bare nej. 😄 Men god diskussion.»

### 9.4 Fund og oplevelser – uformelt

- Forfatter: «Thomas Engbjerg (eksempel)». Kategori: Fund og oplevelser. Arter: Sortstrubet Bynkefugl, Rødstrubet Piber.
  Lokalitet: slå `Skagen` op (navn indeholder «Grenen»), ellers udelad. Ingen billeder.
- Ingen titel.

```
Sikke en morgen på Skagen Gren! Stille vind fra sydøst, og fra solopgang var der fuldt drøn på: hundredvis af
[[art:Bogfinke]] og [[art:Kvækerfinke]], et par [[art:Rødstrubet Piber]] over hovedet og som prikken over i'et en
[[art:Sortstrubet Bynkefugl]] i klitten.

Kaffen blev kold, men det var det hele værd ☕🐦
```

Kommentarer:
1. «Nanna Vestergaard (eksempel)», 3 timer siden: «Misundelig! Var der også lærkefalke?»
   - Svar fra «Thomas Engbjerg (eksempel)»: «Svar til Nanna: Én kort over Grenen ved 9-tiden.»
2. «Rune Fogh (eksempel)», 1 time siden: «Det er de morgener, man husker. God tur hjem!»

### 9.5 Ture og arrangementer – praktisk

- Forfatter: «Lokalafdelingen (eksempel)». Kategori: Ture og arrangementer.
- Titel: «Fælles tur til Vejlerne søndag – samkørsel fra Aalborg»

```
Vi tager til Vejlerne på søndag og håber på [[art:Rørdrum]], [[art:Skægmejse]] og de første [[art:Sædgås]].

**Praktisk**

- Mødested: Parkeringspladsen ved Aalborg Station kl. 7.00
- Hjemme igen: ca. kl. 15
- Tag varmt tøj, madpakke og gerne en teleskop med
- Der er plads til 8 i bilerne – skriv i kommentarerne, om du kører eller skal med

Alle er velkomne, også nybegyndere. Vi viser gerne, hvordan man indtaster i DOFbasen undervejs.
```

Kommentarer:
1. «Signe Bach (eksempel)», 1 dag siden: «Jeg kører og har 3 ledige pladser.»
2. «Emil Krogh (eksempel)», 20 timer siden: «Jeg vil gerne med! Første gang i Vejlerne – har ikke teleskop.»
   - Svar fra «Signe Bach (eksempel)»: «Svar til Emil: Du kan køre med mig, og der er altid nogen, der deler teleskop.»
3. «Lokalafdelingen (eksempel)», 3 timer siden: «Opdatering: Vi er nu 7 tilmeldte. Én plads tilbage.»

Det første opslag (§9.1) **fastgøres** i «Alle», så det står øverst.

---

## 10. API-oversigt (`api/snak.py`, præfiks `/api/snak`)

| Metode og sti | Adgang | Beskrivelse |
|---|---|---|
| `GET /kategorier` | snak | Aktive kategorier med antal nye siden sidste besøg |
| `GET /feed` | snak | §6 |
| `GET /{id}` | snak | Opslag (fuldt) |
| `POST /` | snak | Opret `{category_id, titel?, body, media_ids[], attachment?, arter[], loknr?}` |
| `PUT /{id}` | forfatter | Ret (samme felter; kategori kun af moderator) |
| `DELETE /{id}` | forfatter / moderator | Slet (blødt) |
| `POST /{id}/loesning` | forfatter / moderator | `{comment_id\|null, artnr?}` |
| `POST /{id}/foelg` / `DELETE /{id}/foelg` | snak | Følg / følg ikke |
| `POST /{id}/visning` | snak | Registrér visning (trådsiden) |
| `POST /{id}/anmeld` | snak | Anmeld opslag |
| `PUT /{id}/reaktion` | snak | Sæt eller skift reaktion `{type: like\|love\|haha\|wow\|sad\|angry}`; svar: `summary` for målet |
| `DELETE /{id}/reaktion` | snak | Fjern egen reaktion; svar: `summary` |
| `GET /{id}/reaktioner?type=` | snak | Hvem reagerede (side à 50; `type` filtrerer; inkl. `counts` til fanerne) |
| `PUT\|DELETE /kommentarer/{kid}/reaktion`, `GET /kommentarer/{kid}/reaktioner` | snak | Som ovenfor, for kommentarer |
| `POST /{id}/moderer` | moderation | `{action: skjul\|vis\|laas\|aabn\|flyt\|afvis_anmeldelser, reason?, category_id?}` |
| `POST /{id}/fastgoer` | snak (admin) | `{pinned: bool}` |
| `GET/POST/PATCH/DELETE …/kommentarer…` | | §7 |
| `POST /media` | snak + snak_billeder | Upload af billede (`owner_kind` snak/snakk) |
| `POST /render` | snak | Forhåndsvisning af markdown |
| `POST /link-preview` | snak + snak_links | §5.4 |
| `GET /vedhaeft/soeg?type=&q=` | snak | Søgning til vedhæft-vælgeren (samler artikel/foto/art/lokalitet) |
| `GET /soeg` | snak | §2.5 |
| `GET /mine` | snak | Mine opslag og fulgte tråde |
| `GET/PUT /notifikationer` | snak_notifikationer | §8.3 |
| `GET /admin/koe?tab=` | moderation | Moderationskø (opslag og kommentarer) |
| `GET/POST/PATCH /admin/kategorier` | snak (admin) | Kategorier |
| `POST /admin/kategorier/{id}/flyt` | snak (admin) | Flyt alle opslag til en anden kategori |
| `GET/PUT /admin/indstillinger` | snak (admin) | §2.7 |
| `POST /admin/eksempler` / `DELETE /admin/eksempler` | snak (admin) | Gendan / slet eksempler |
| `GET /admin/statistik` | snak (admin) | §2.7 |

Alle svar på dansk ved fejl (`HTTPException(status, "…")`). Pydantic-modeller med `max_length` på alle strenge.

---

## 11. Frontend-filer

```
web/js/views/snak.js          skal: header, faner, rute → side (kopiér strukturen fra views/artikler.js)
web/js/snak/common.js         BASE, ctx(), on(), loadCss, postCard(), categoryTag(), attachmentCard(), avatar
web/js/snak/feed.js           §2.1
web/js/snak/opslag.js         §2.2
web/js/snak/kommentarer.js    §2.3 (ud fra artikler/kommentarer.js)
web/js/snak/reaktioner.js     §2.9: REACTIONS, reactionWidget(), reactionSummary(), showReactors()
web/js/snak/composer.js       §2.4 + vedhæft-vælger + billedupload
web/js/snak/soeg.js, mine.js  §2.5, §2.6
web/js/snak/admin.js          §2.7 + commentQueue()/postQueue() til Moderation (§2.8)
web/css/snak.css              afgrænset til .snak; brug designtokens (lys/mørk), ingen hårdkodede farver
```

- Brug `h()`, `modal()`, `ask()`, `askText()`, `toast()`, `seg()`, `reactionWidget()` (§2.9) og `banForm()`. Brug **ingen** `innerHTML`
  med brugerdata; server-HTML indsættes via `trustedHTML`.
- Tilgængelighed som resten af appen (jf. commit 773dfc8): kort er `<article>` med `aria-labelledby`, knapper har
  `aria-label`, lysboksen og modaler er native `<dialog>`, fokus flyttes til nyt kommentarfelt, og `prefers-reduced-motion`
  respekteres.
- Billeder: `loading="lazy"`, `decoding="async"`, bredde/højde sat (ingen layout-hop).

---

## 12. Test (`server/tests/test_snak*.py`)

1. Feedets rækkefølge for alle tre sorteringer, cursor uden dubletter eller huller, og fastgjorte opslag kun på side 1.
2. Kommentar-forhåndsvisningen: N nyeste topniveau, M nyeste svar, korrekte `n_svar`; skjulte tælles ikke.
3. `parent_id` normaliseres til topniveau, og et fremmed opslags `parent_id` afvises.
4. Regler §4: kategori-flag, låst tråd, udelukket bruger, rate limit (429) og tom tekst uden billeder/vedhæftning.
5. Skjult indhold: andre ser ingen tekst eller medier, forfatteren ser grunden, og moderatoren ser alt.
6. Medieadgang for `snak`/`snakk` (login, skjult opslag).
7. Link-preview afviser interne adresser (genbrug testene for `egress`) og respekterer størrelse/timeout.
8. Notifikationer: rigtige modtagere, ingen dubletter pr. kommentar og samling pr. time for følgere.
9. Seed: idempotent, kun fiktive navne, ingen fotos, og «Slet alle eksempler» rydder alt.
10. Reaktioner: sæt → skift → fjern giver korrekt `n_reactions` og `counts` på opslag og kommentar; én række pr. bruger pr.
    mål (samtidige kald giver ingen dublet); ugyldig `type` → 422; skjult opslag, ukendt mål og udelukket bruger afvises;
    `who` filtrerer på type; notifikationen samles pr. time; `mine` er kun sat for den, der kalder.
11. Rendering: `[[art:…]]` og autolinks i profilen `kommentar`; script/HTML fjernes.

E2E (valgfrit, `tools/e2e`): opret opslag med billede → kommentér → svar → se det live i en anden session.

---

## 13. Faser (byg og commit i denne rækkefølge)

1. **Fundament**: flag, rettigheder, `snak_access`, modeller + `dbgroups`, kategori-seed, opret/vis opslag (tekst),
   feed med sortering og cursor, trådside, kommentarer med svar (nyeste øverst, forhåndsvisning i feedet, «Se mere»),
   reaktioner (§2.9), menu og skal. *Accept: §14 punkt 1–6.*
2. **Indhold**: billeder (opslag og kommentar), vedhæft fra portalen, link-preview, arter/lokalitet, løsning,
   eksempelopslag (§9). *Accept: punkt 7–10.*
3. **Fællesskab**: følg, notifikationer, live (SSE), anmeld, Moderation → Snak, moderatorhandlinger i feedet, admin-fanen
   (kategorier, indstillinger, eksempler). *Accept: punkt 11–15.*
4. **Polering**: søg, Mine, statistik, sidepanel, del-link via `api/share.py`, ydelsesmål §6. *Accept: punkt 16–18.*

Kør `pytest` og `ruff` før hvert commit. Opdatér `docs/funktioner.md` med modulet i fase 3.

---

## 14. Acceptkriterier

1. Med `snak` = `off` svarer alle `/api/snak`-kald 404, og menupunktet er væk.
2. Et opslag kræver kategori. En kategori med `kraever_titel` kræver titel.
3. Feedet viser opslag sorteret efter seneste aktivitet. En ny kommentar løfter tråden til toppen.
4. Hvert kort viser de 3 nyeste topniveau-kommentarer (nyeste øverst) med højst 1 svar hver. «Se alle N kommentarer»
   vises, når der er flere, og åbner `#/snak/<id>`.
5. Man kan kommentere og svare direkte i feedet. Svar på svar havner under samme topniveau-kommentar med «Svar til Navn».
6. Alle seks reaktioner (👍 ❤️ 😂 😮 😢 😡) virker på opslag og kommentarer: kort tryk sætter/fjerner 👍, hover/langt tryk åbner
   vælgeren, man kan skifte reaktion, opsummeringen viser de mest brugte emojis og tallet, og dialogen viser hvem der
   reagerede pr. type. Fungerer med tastatur og skærmlæser.
7. Op til 10 billeder pr. opslag og 1 pr. kommentar; EXIF/GPS fjernes; lysboks.
8. Et vedhæftet artikel-, foto-, observations-, arts-, lokalitets- og linkkort vises korrekt og respekterer adgang.
9. I Artsbestemmelse kan forfatteren markere en løsning; kortet får «Løst ✓», og filteret «Kun uløste» virker.
10. De 5 eksempelopslag (§9) findes efter første opstart med kommentarer og svar, er mærket «Eksempel», og løsningen
    i §9.2 er markeret.
11. Svar, kommentarer på egne opslag og fulgte tråde giver notifikationer efter §8.2 – uden dubletter.
12. Nye opslag og kommentarer dukker op live uden genindlæsning.
13. Anmeldt indhold står i Moderation → Kommentarer → Snak; skjul/vis/lås/udeluk virker og logges.
14. Admin-fanen kan oprette, rette, sortere og arkivere kategorier og ændre indstillingerne (fx 3 → 5 kommentarer i feedet).
15. «Slet alle eksempler» fjerner eksemplerne; «Gendan» lægger dem ind igen.
16. Søgning finder ord i titler, tekst og kommentarer, med links direkte til kommentaren.
17. Feedet svarer < 150 ms ved 10.000 opslag / 100.000 kommentarer (måling i test).
18. Fungerer i lys/mørk tilstand, på 360 px bredde og med tastatur alene.

---

## 15. Åbne punkter (byg med standardvalget, og spørg ejeren undervejs)

| Punkt | Standardvalg |
|---|---|
| Modulets navn | «Snak» (`#/snak`) |
| Reaktionssæt | De seks klassiske (👍 ❤️ 😂 😮 😢 😡). «Omsorg» 🥰 (Facebooks syvende) og fuglespecifikke reaktioner kan komme senere – `TYPES` er ét sted, og `type` er `varchar(5)` + CHECK, så tilføjelse kræver en migrering |
| Afstemninger i opslag | Ikke i denne omgang |
| Video og lyd i opslag | Ikke i denne omgang (lyd kunne komme fra xeno-canto-link som vedhæftning senere) |
| @-omtaler med notifikation | Ikke i denne omgang; «Svar til Navn» dækker svar |
| Lokalafdelings-grupper (lukkede rum) | Ikke i denne omgang; datamodellen kan senere få `sn_categories.afdeling_id` |
| Integration med Feedet (fx «💬 3 opslag om denne art» på artssider) | Fase 5 – ikke i denne spec |
| Nyheder («Nyt fra portalen») som opslag i Meddelelser | Nej; nyheder beholder deres egen side |


## 13. Artiklernes opslag (`services/snak_articles.py`)

Artikler og Snak er smeltet sammen: hver publiceret artikel har ét opslag i «Artikler og læsning» (`sn_posts.article_id`,
unikt), og opslagets kommentarer er artiklens kommentarspor.

- **Indhold:** titel = artiklens titel; tekst = `art_articles.snak_tekst` (boksen «Opslag i Snak» i artikel-editoren, markdown,
  højst 2 000 tegn) eller, hvis den er tom, et uddrag af artiklens første tekst (ca. 300 tegn, skåret ved et ord). Artiklen er
  vedhæftet som kort; op til 5 arter fra artiklen (og arter tagget i teksten) kommer på chipsene.
- **Afsender:** artiklens forfattere (`eksempel_navn`, `user_id` er null) – vist med navn uden profillink og chippen «📰 Artikel».
- **Dato:** netfugl-arkivets artikler dateres til artiklens dato, nye til publiceringen. Gamle artikler ligger derfor langt
  nede i feedet og kommer kun op, når nogen kommenterer.
- **Livscyklus:** `sync` kaldes, når artiklen gemmes (titel, tekst, forfattere, arter, `locked = not comments_open`).
  Trækkes artiklen tilbage, skjules opslaget (`hidden_reason = «Artiklen er ikke publiceret»`); publiceres den igen, vises
  det igen – medmindre en moderator har skjult det. Slettes artiklen, slettes opslaget (FK med CASCADE).
  `backfill` (workerens job `articles_publish`, hvert 5. minut) opretter opslag til publicerede artikler, der mangler et:
  første gang hele arkivet, siden planlagt publicerede og nyimporterede. Den flytter også gamle «følg artikel»
  (`art_subscriptions` kind `artikel`) over på opslaget.
- **Adgang:** kommentar- og reaktionskaldene på et artikelopslag (`need_thread`/`need_comment` i `api/snak.py`) virker,
  når enten `snak` eller `artikler_kommentarer` er synlig – så artiklernes kommentarer virker for alle, mens Snak er i preview.
- **Notifikationer:** svar og nye kommentarer i fulgte artikeltråde følger artiklernes indstillinger (`artikler_notifikationer`
  og valget «nye kommentarer på artikler», `users.prefs.art_comments`) og linker til artikelsiden (`#/artikler/<id>?sk=<kommentar>`).
- **Arkivet:** kommentarerne fra netfugl.dk (`art_comments`) indgår ikke i Snak; de bliver på artikelsiden, kun til læsning.
- **Tæller:** artiklens «💬 N» = arkivets synlige kommentarer + opslagets `n_comments`.

