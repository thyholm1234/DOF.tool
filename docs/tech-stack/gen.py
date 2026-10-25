"""Genererer flow.dot (diagrammet) og edges.html (den nummererede tabel) fra én liste, så de altid stemmer overens.

Kør via build.sh. Ret forbindelser her – ikke i de genererede filer."""

from html import escape

# Protokol → (farve, stil). Samme farver i diagram og tabel.
PROTO = {
    "HTTPS": ("#1f5fa8", "solid"), "HTTP": ("#2e7d4f", "solid"), "Kald": ("#2e7d4f", "solid"),
    "PostgreSQL": ("#c25400", "solid"), "Replikering": ("#8e5b2e", "dashed"), "RESP": ("#c0392b", "solid"),
    "S3": ("#7d3c98", "solid"), "Docker API": ("#5b6770", "solid"), "Fil": ("#5b6770", "dashed"), "Push": ("#1f5fa8", "dashed"),
}

CLUSTERS = [
    ("clients", "Klienter (internettet)", "#eef3f8", "#7f9cb8", [
        ("browser", "Browser / PWA", [
            ("spa", "SPA (web/js)", "ES-moduler, hash-routing"),
            ("sw", "Service worker", "app-skal, API-cache, push"),
            ("leaf", "Leaflet-kort", "kortvisning"),
        ]),
        ("app", "Android-/iOS-app (Capacitor 8)", [
            ("webview", "WebView", "indlæser samme PWA"),
            ("bgloc", "Baggrundsposition", "@capgo/background-geolocation"),
            ("pushrx", "Push-modtager", "@capacitor/push-notifications"),
        ]),
        (None, None, [
            ("guest", "Gæst", "delelinks, /privatliv"),
            ("thirdp", "Tredjepartsapps", "Showcase-API med token"),
            ("calapp", "Kalenderapps", "ICS-abonnement"),
            ("admin", "Driftsadministrator", "LAN / VPN"),
        ]),
    ]),
    ("server", "Server · docker compose «doftool» · netværk doftool_default", "#f6f9f6", "#7fa287", [
        ("api", "api · FastAPI + Uvicorn :8000", [
            ("mw", "Middleware «guard»", "session, CSRF, CORS, CSP/HSTS"),
            ("rest", "REST /api/*", "auth, feed, lister, ture, admin"),
            ("sse", "SSE /api/stream", "live-opdateringer"),
            ("showapi", "Showcase-API", "/api/dofbasen/v1, /netfugl/v1"),
            ("icsr", "ICS-feeds", "/api/ics/{token}/…"),
            ("mediar", "/media/obs/{navn}", "Range, ETag, login"),
            ("pages", "Server-renderede sider", "/del/…, /privatliv"),
            ("static", "Statisk PWA", "/v/{build}/…"),
            ("apisvc", "Tjenester", "push, imagemeta, konto, diskusage"),
            ("storsvc", "storage.py", "S3-klient (minio-py)"),
            ("router1", "dbrouter", "gruppe + primary/replika"),
        ]),
        ("worker", "worker · præcis én proces", [
            ("sched", "Planlægger", "beregning, prognose, ture, RSS"),
            ("gw", "DOFbasen-gateway", "udtræk ≤ 1/min, opslag"),
            ("cmds", "Kommando-forbruger", "worker:commands"),
            ("arch", "Arkivimport", "månedsfiler (CSV)"),
            ("wpush", "Alarmer → push", "ved nye observationer"),
            ("once", "Engangsjob", "EXIF-rensning, flyt til S3"),
            ("router2", "dbrouter", "som i api"),
        ]),
        ("valkey", "valkey 8.1 :6379", [
            ("vsess", "Sessioner", "sess:*, usess:*"),
            ("vq", "Køer", "gateway, opslag, kommandoer"),
            ("vcache", "Cache + versioner", "scoreboards, feed"),
            ("vps", "Pub/sub", "kanal «feed» m.fl."),
            ("vlim", "Låse + grænser", "login, kald-interval"),
        ]),
        ("db", "PostgreSQL 17 + PostGIS 3.5", [
            ("users1", "users-1 · primary", "brugerdata + reference"),
            ("users2", "users-2 · replika", "hot standby"),
            ("obs1", "obs-1 · primary", "observationer + reference"),
            ("legacy", "postgres · samlet DB", "legacy / migreringskilde"),
        ]),
        ("rustfs", "rustfs :9000 (ingen port på værten)", [
            ("bucket", "Bucket doftool-media", "obs/<uuid>.jpg|mp3 …"),
        ]),
        ("dbadmin", "dbadmin :8100 (kun DBADMIN_BIND)", [
            ("panel", "Panel", "eget login"),
            ("topo", "/api/topology", "noder, roller, tokens"),
            ("probe", "Sundhedstjek", "asyncpg pr. node"),
            ("migr", "Migrering", "legacy → users/obs"),
            ("dctl", "Docker-styring", "opret noder, stop/start"),
            ("sqlite", "Register", "SQLite /data/registry.db"),
        ]),
        (None, None, [
            ("dsock", "Docker Engine", "/var/run/docker.sock"),
            ("vols", "Volumener", "media, weather, archive_upload"),
            ("archdir", "/archive (read-only)", "DOFbasen-månedsudtræk"),
        ]),
    ]),
    ("ext", "Eksterne tjenester", "#fbf6ee", "#c9a96e", [
        (None, None, [
            ("dofb", "DOFbasen", "dofbasen.dk, service.dofbasen.dk"),
            ("dofl", "DOFbasen login", "krydslister.dofbasen.dk"),
            ("dofdk", "dof.dk", "DOF-ture og arrangementer"),
            ("netfugl", "netfugl.dk", "lister + RSS"),
            ("dmi", "DMI Open Data", "klima- og vejrarkiv"),
            ("meteo", "Open-Meteo", "vejrprognose"),
            ("xc", "xeno-canto", "lydoptagelser"),
            ("fcm", "Google FCM", "oauth2 + fcm v1"),
            ("wps", "Browser-push", "Google / Mozilla / Apple"),
            ("osm", "OpenStreetMap", "tile.openstreetmap.org"),
        ]),
    ]),
]

PROXY = ("proxy", "Reverse proxy", "TLS, :443 → api:8000, X-Forwarded-*")

# (fra, til, protokol, kort påskrift, indhold, constraint)
EDGES = [
    # klienter → proxy → api
    ("spa", "proxy", "HTTPS", "HTTPS", "HTML/JS/CSS, REST-JSON, multipart-upload af fotos/lyd, cookie dt_session", True),
    ("sw", "proxy", "HTTPS", "HTTPS", "App-skal (netværk først, cache som nødløsning), API-svar caches offline", True),
    ("webview", "proxy", "HTTPS", "HTTPS", "Samme trafik som browseren – appen indlæser PWA'en fra serveren", True),
    ("bgloc", "proxy", "HTTPS", "HTTPS", "POST /api/location/native: position (lat/lon/nøjagtighed) + positionstoken, også når appen er lukket", True),
    ("guest", "proxy", "HTTPS", "HTTPS", "GET delelinks (/del/traad/…) og /privatliv uden login", True),
    ("thirdp", "proxy", "HTTPS", "HTTPS", "GET JSON fra showcase-API'erne med Authorization: Bearer <token>; åben CORS, kun læsning", True),
    ("calapp", "proxy", "HTTPS", "HTTPS", "GET text/calendar (ICS) med personligt token i adressen", True),
    ("proxy", "mw", "HTTP", "HTTP :8000", "Al klienttrafik; proxyen sætter X-Forwarded-For/-Proto (stoles kun på fra FORWARDED_ALLOW_IPS)", True),
    ("admin", "panel", "HTTP", "HTTP :8100", "db-admin-panelet og dets JSON-API; eget kodeord, cookie med SameSite=Strict", True),
    # internt i api
    ("mw", "rest", "Kald", "", "Godkendte forespørgsler til REST-routerne", True),
    ("mw", "sse", "Kald", "", "Server-sent events til indloggede", True),
    ("mw", "showapi", "Kald", "", "Showcase-API (token kontrolleres i routeren)", True),
    ("mw", "icsr", "Kald", "", "ICS-feeds", True),
    ("mw", "mediar", "Kald", "", "Filer fra observationer – kun med gyldig session", True),
    ("mw", "pages", "Kald", "", "Delelinks og privatlivspolitik", True),
    ("mw", "static", "Kald", "", "Statiske app-filer med build-id i adressen", True),
    ("rest", "apisvc", "Kald", "", "Alarmer/push, metadata-rensning af uploads, udtræk og sletning af konto, lagerforbrug", True),
    ("rest", "storsvc", "Kald", "", "Gem/slet filer ved upload til feltmeldinger og ture", True),
    ("mediar", "storsvc", "Kald", "", "Hent fil (evt. et byte-interval) til afspilning/visning", True),
    ("rest", "router1", "Kald", "", "SQLAlchemy async-sessioner; hver sætning routes til sin databasegruppe", True),
    # dbadmin-topologi
    ("router1", "topo", "HTTP", "HTTP", "GET /api/topology hvert 15. s med Bearer servicetoken → JSON: noder, roller, adresser, adgangskoder", True),
    ("router2", "topo", "HTTP", "HTTP", "Samme som api", True),
    ("panel", "sqlite", "Fil", "SQL", "Registret over noder, indstillinger og sessioner (lokal SQLite-fil)", True),
    # Valkey
    ("mw", "vsess", "RESP", "RESP", "Slår sessionstoken op (sess:<token> → bruger-id)", True),
    ("rest", "vq", "RESP", "RESP", "ZADD gateway-job (fx login – adgangskoden ligger højst 3 min), RPUSH worker-kommandoer", True),
    ("rest", "vcache", "RESP", "RESP", "GET/SET JSON-cache, versionsnumre, admin-målinger", True),
    ("rest", "vlim", "RESP", "RESP", "Tællere for login-forsøg og andre grænser", True),
    ("sse", "vps", "RESP", "RESP", "SUBSCRIBE på live-kanaler; beskeder sendes videre som SSE", True),
    ("gw", "vq", "RESP", "RESP", "ZPOPMIN næste job; LPUSH svar til den api-proces, der venter", True),
    ("gw", "vlim", "RESP", "RESP", "Globalt tidsstempel + lås: højst ét udtræk pr. minut, også ved flere workers", True),
    ("cmds", "vq", "RESP", "RESP", "BLPOP worker:commands (fx genberegning, arkivimport)", True),
    ("wpush", "vps", "RESP", "RESP", "PUBLISH nye fund og ændringer til live-feedet", True),
    ("sched", "vcache", "RESP", "RESP", "Bump cache-versioner, jobstatus til Admin → Drift", True),
    # PostgreSQL
    ("router1", "users1", "PostgreSQL", "SQL", "Læs/skriv brugerdata (konti, lister, feltmeldinger, kommentarer …) via asyncpg", True),
    ("router1", "users2", "PostgreSQL", "SQL (læs)", "Læsninger af brugerdata, når «læs fra replikaer» er slået til", True),
    ("router1", "obs1", "PostgreSQL", "SQL", "Læs observationer, aggregater og referencedata", True),
    ("router1", "legacy", "PostgreSQL", "SQL", "Al data i legacy-tilstand (før opdelingen er slået til)", True),
    ("router2", "obs1", "PostgreSQL", "SQL", "INSERT/COPY af udtræk og arkiv, natlige aggregater", True),
    ("router2", "users1", "PostgreSQL", "SQL", "Alarmregler, notifikationer, oprydning", True),
    ("probe", "users1", "PostgreSQL", "SQL", "SELECT 1 + svartid → nodens status i panelet", True),
    ("probe", "obs1", "PostgreSQL", "SQL", "Samme sundhedstjek", True),
    ("migr", "legacy", "PostgreSQL", "COPY", "COPY ud af den samlede database (kilde)", True),
    ("migr", "users1", "PostgreSQL", "COPY", "COPY ind i brugergruppen; derefter indekser", True),
    ("migr", "obs1", "PostgreSQL", "COPY", "COPY ind i observationsgruppen i parallelle intervaller", True),
    ("users1", "users2", "Replikering", "WAL", "Første gang pg_basebackup, derefter streaming-replikering af WAL", True),
    # S3
    ("storsvc", "bucket", "S3", "S3 :9000", "PUT/GET (med Range)/HEAD/DELETE objekter, signeret med SigV4", True),
    ("once", "bucket", "S3", "S3", "Engang: flyt eksisterende filer fra media/obs til bucket'en", True),
    # Docker / filer
    ("dctl", "dsock", "Docker API", "Docker API", "Opret databasecontainere, stop/start api og worker under migrering", True),
    ("apisvc", "vols", "Fil", "fil", "Netfugl-fotos, læringsfiler og xeno-canto-cache på volumen media", True),
    ("once", "vols", "Fil", "fil", "Engang: fjern EXIF/GPS fra billeder uploadet før rensningen", True),
    ("arch", "archdir", "Fil", "fil", "Læs dofbasen_ÅÅÅÅ_MM.csv (read-only bind-mount)", True),
    # ud på internettet
    ("gw", "dofb", "HTTPS", "HTTPS", "GET observationer, ture, referencedata, medier – højst ét udtræk pr. minut", True),
    ("gw", "dofl", "HTTPS", "HTTPS", "POST JSON obserkode + adgangskode; svaret afgør login (adgangskoden gemmes ikke)", True),
    ("sched", "dofdk", "HTTPS", "HTTPS", "GET HTML med DOF-ture og arrangementer (hver 6. time)", True),
    ("sched", "netfugl", "HTTPS", "HTTPS", "GET RSS/HTML (hver time) og Netfugl-lister", True),
    ("sched", "dmi", "HTTPS", "HTTPS", "GET klimadata og vejrarkiv (JSON/månedsfiler)", True),
    ("sched", "meteo", "HTTPS", "HTTPS", "GET vejrprognose (JSON, hver time)", True),
    ("apisvc", "xc", "HTTPS", "HTTPS", "GET lydfiler til Mobgang; caches på volumen media/xc", True),
    ("wpush", "fcm", "HTTPS", "HTTPS", "OAuth2-JWT til oauth2.googleapis.com + push via FCM v1 (JSON)", True),
    ("apisvc", "fcm", "HTTPS", "HTTPS", "Push ved feltmeldinger, kommentarer og statusmeldinger", True),
    ("wpush", "wps", "HTTPS", "HTTPS", "Web Push: VAPID-signeret, krypteret payload til browserens push-tjeneste", True),
    ("apisvc", "wps", "HTTPS", "HTTPS", "Web Push fra api (samme som worker)", True),
    # tilbage til enheder / direkte fra browseren
    ("fcm", "pushrx", "Push", "push", "Notifikation leveres til appen", False),
    ("wps", "sw", "Push", "push", "push-event i service workeren → notifikation", False),
    ("leaf", "osm", "HTTPS", "HTTPS", "GET kortfliser (PNG) direkte fra browseren – brugerens IP sendes til OSMF", False),
    ("spa", "dofb", "HTTPS", "HTTPS", "Billeder og lyd fra DOFbasen vises direkte i browseren", False),
]


def node(nid, title, sub):
    return f'{nid} [label=<<b>{escape(title)}</b><br/><font point-size="8" color="#555555">{escape(sub)}</font>>];'


def dot() -> str:
    out = ['digraph DOFtool {',
           '  graph [rankdir=LR, compound=true, splines=true, nodesep=0.12, ranksep=0.55, fontname="DejaVu Sans", fontsize=13,',
           '         pad=0.2, bgcolor="white", outputorder=edgesfirst];',
           '  node [shape=box, style="rounded,filled", fillcolor="#ffffff", color="#5b6770", fontname="DejaVu Sans", fontsize=10,',
           '        margin="0.08,0.03", height=0.38, penwidth=0.9];',
           '  edge [fontname="DejaVu Sans", fontsize=8, arrowsize=0.55, penwidth=1.0];']
    for cid, label, fill, color, groups in CLUSTERS:
        out.append(f'  subgraph cluster_{cid} {{ label=<<b>{escape(label)}</b>>; style="rounded,filled"; fillcolor="{fill}"; '
                   f'color="{color}"; fontsize=13;')
        for gid, glabel, nodes in groups:
            if gid:
                out.append(f'    subgraph cluster_{gid} {{ label=<<b>{escape(glabel)}</b>>; style="rounded,filled"; fillcolor="#ffffff"; '
                           f'color="{color}"; fontsize=11;')
            out += [("      " if gid else "    ") + node(*n) for n in nodes]
            if gid:
                out.append("    }")
        out.append("  }")
    out.append("  " + node(*PROXY).replace("];", ', fillcolor="#f2f2f2"];'))
    for i, (a, b, proto, short, _, constraint) in enumerate(EDGES, 1):
        color, style = PROTO[proto]
        lbl = f"{i}" + (f" {short}" if short else "")
        extra = "" if constraint else ", constraint=false"
        out.append(f'  {a} -> {b} [label="{lbl}", color="{color}", fontcolor="{color}", style={style}{extra}];')
    out.append("}")
    return "\n".join(out)


def table() -> str:
    names = {n[0]: n[1] for _, _, _, _, gs in CLUSTERS for _, _, ns in gs for n in ns} | {"proxy": PROXY[1]}
    owner = {}
    for _, _, _, _, gs in CLUSTERS:
        for gid, glabel, ns in gs:
            for n in ns:
                owner[n[0]] = (glabel or "").split(" ·")[0].split(" :")[0].split(" (")[0]
    rows = []
    for i, (a, b, proto, _, content, _) in enumerate(EDGES, 1):
        color = PROTO[proto][0]
        where = lambda x: f"{escape(names[x])}" + (f' <span class="muted">({escape(owner[x])})</span>' if owner.get(x) else "")
        rows.append(f'<tr><td class="num">{i}</td><td>{where(a)}</td><td>{where(b)}</td>'
                    f'<td><span class="proto" style="border-color:{color};color:{color}">{escape(proto)}</span></td>'
                    f'<td>{escape(content)}</td></tr>')
    legend = "".join(f'<span class="proto" style="border-color:{c};color:{c}">{escape(p)}</span> ' for p, (c, _) in PROTO.items())
    return (f'<p class="legend">{legend}</p><table class="edges"><colgroup><col style="width:7mm"><col style="width:36mm">'
            f'<col style="width:36mm"><col style="width:23mm"><col></colgroup><thead><tr><th>#</th><th>Fra</th><th>Til</th><th>Type</th>'
            f'<th>Indhold</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')


if __name__ == "__main__":
    open("flow.dot", "w").write(dot())
    open("edges.html", "w").write(table())
    print(f"{len(EDGES)} forbindelser")
