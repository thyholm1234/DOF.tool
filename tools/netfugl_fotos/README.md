# netfugl_fotos – arkivering af netfugl.dk's fotogalleri

Henter alle **publicerede** fotos fra `https://netfugl.dk/fotos/{id}` (uden login) til én mappe pr. foto:
`Fotos/{id}/foto.<ext>` + `Fotos/{id}/meta.json` (schema_version 1, se `docs/netfugl-afloesning-spec.md` §2.3).
Importen i DOF.tool (fase 1) læser mapperne eller zip-filerne.

```
python3 -I netfugl_fotos.py hent [--fra 125245] [--til 1] [--stop-efter 10] [--pause 3] [--igen] [--ids …]
python3 -I netfugl_fotos.py kontrol     # tæller mapper mod galleriets totaltal, skriver _mangler.json
python3 -I netfugl_fotos.py indeks      # _index.json, _fotografer.json, _arter.json, _koder.json, manifest.json
python3 -I netfugl_fotos.py zip [--del-gb 20]   # Fotos-001.zip, Fotos-002.zip … ved siden af mappen
```

Kræver `beautifulsoup4`. Standardmappe: `--ud /home/chris/Dokumenter/DOF.tool/Fotos` (`Fotos/` og `Fotos-*.zip` er i `.gitignore`).

## Kørsel

- Der tælles **ned** fra `--fra`. Et id uden data (404, tom side, "endnu ikke godkendt") skrives til `_tomme.txt`.
  Efter `--stop-efter` tomme i træk stopper crawlen med "Stoppet ved id X efter N tomme i træk" (`0` = stop aldrig).
- `meta.json` skrives sidst og atomisk; en mappe uden den hentes igen. Afbryd og genoptag når som helst; `--igen` henter også
  færdige fotos.
- Pause `--pause` sekunder mellem alle HTTP-kald. Ved 429/503 ventes 60 s (højst 5 gange). Log: `Fotos/hent.log`.

## Overslag

~125 000 sider + ~80 000 billeder × 3 s ≈ **7 døgn**. Billederne fylder anslået **40–80 GB**.

## Kendt risiko

Galleriet har ~80 565 publicerede fotos ud af ~125 000 id'er – hvert tredje id er tomt (slettet/afvist/upubliceret), og
huller på mere end 10 i træk er sandsynlige i de ældre id'er. Kør derfor `kontrol` efter `hent`. Viser den, at der mangler
fotos, så fortsæt med `hent --fra <stop-id> --stop-efter 500` (eller `--til 1 --stop-efter 0`).

## Hvad parseren ikke har kunnet bekræfte endnu

Parseren er testet på rigtige sider (`server/tests/fixtures/netfugl_fotos/`): enkelt fugl, underart, kommentarer,
koordinat + kamera, emneord og tomt id. Disse markup-former er **ikke** set i en rigtig side og er gættet:
fotos med **flere fugle** (hver fugl antages at være en `div` med et `/fotos/?birdId=`-link under "Art"-overskriften),
**svarkommentarer** (`.child-comment`, indlejret eller som søskende) og **grupper** (`gruppe` er derfor ofte `[]`). Stikprøve
ved første kørsel: åbn `meta.json` for et foto med flere fugle/svar og ret `parse_foto`, hvis noget mangler.
