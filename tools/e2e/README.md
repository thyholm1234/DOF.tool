# Røgtest af frontend (Playwright)

`smoke.py` åbner alle sider og faner i en headless Chromium mod en kørende portal og registrerer pr. side:
JS-fejl, konsolfejl, API-kald med fejl, `.notice.err`-bokse, overskrifter, faner, antal kort/felter/tabeller og hvor
lang tid hvert API-kald tog. `compare.py` sammenligner to kørsler, så en omstrukturering kan vises at give samme sider.

## Opsætning (én gang)

```sh
python3 -m venv .venv-e2e && .venv-e2e/bin/pip install playwright && .venv-e2e/bin/python -m playwright install chromium
```

## Kørsel

Testen kører som en indlogget bruger. Lav en midlertidig session for en eksisterende (gerne test-)bruger direkte i
Valkey – kun på et udviklingsmiljø – og slet den bagefter:

```sh
TOKEN=e2e$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')
docker exec doftool-valkey-1 valkey-cli SET "sess:$TOKEN" <bruger-id> EX 7200
.venv-e2e/bin/python tools/e2e/smoke.py "$TOKEN" /tmp/e2e/foer.json         # før ændringen
# … ændr koden …
.venv-e2e/bin/python tools/e2e/smoke.py "$TOKEN" /tmp/e2e/efter.json
CLICK=1 .venv-e2e/bin/python tools/e2e/smoke.py "$TOKEN" /tmp/e2e/klik.json  # klik også alle faner og filtre
python3 tools/e2e/compare.py /tmp/e2e/foer.json /tmp/e2e/efter.json
docker exec doftool-valkey-1 valkey-cli DEL "sess:$TOKEN"
```

Første kørsel gemmer de tråde, arter og lokaliteter, den bruger, i `ids.json` ved siden af resultatfilen, så senere
kørsler besøger de samme sider. Med `OLD_WEB=<mappe>` serveres frontenden fra en anden mappe (fx en `git worktree` af
en ældre version) mod samme API – praktisk til før/efter-målinger af frontend alene.

`compare.py` kræver samme overskrifter, faner, kort, felter og tabeller og ingen fejl; antal knapper må variere lidt
(live-data). Tal, der følger live-data (fx antal tabeller i jobstatus), kan give enkelte forventelige forskelle.
