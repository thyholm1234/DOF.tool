"""Artsquiz under Læring: «Hvilken art er det?» ud fra fotos i fotogalleriet.

Arterne vælges blandt portalens artsliste (den, der bruges ved indtastning): en forudindstillet artsgruppe (afgrænset på
slægtsnavne i det latinske navn) og/eller egne valgte arter, mindst to. Spørgsmålene er enkelte, publicerede fotos med netop én fugl;
brugeren svarer med multiple choice eller ved at skrive artens navn. Facit ligger kun på serveren (Valkey) til svaret afgives."""

from __future__ import annotations

import json
import random
import secrets

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv, version

N_QUESTIONS = 20
MIN_SPECIES = 2
TOKEN_TTL = 7200

# nøgle: (navn, beskrivelse, slægter) – arter udpeges af slægten i det latinske navn
GROUPS: dict[str, tuple[str, str, str]] = {
    "skraper": ("Skråper og stormfugle", "Skråper, mallemuk, stormsvaler og albatrosser.",
                "Calonectris Ardenna Puffinus Fulmarus Hydrobates Oceanites Thalassarche"),
    "havfugle": ("Havfugle", "Skråper, suler, skarver, kjover, måger, terner, alkefugle, lommer og havænder.",
                 "Fulmarus Calonectris Ardenna Puffinus Hydrobates Oceanites Thalassarche Morus Sula Fregata Phalacrocorax Gulosus Gavia "
                 "Stercorarius Larus Rissa Xema Pagophila Rhodostethia Hydrocoloeus Chroicocephalus Leucophaeus Ichthyaetus "
                 "Sterna Sternula Thalasseus Onychoprion Hydroprogne Alca Alle Uria Cepphus Fratercula Somateria Melanitta Polysticta Clangula Phalaropus"),
    "phylloscopus": ("Løvsangere (Phylloscopus)", "Løvsangere og lignende små, grønlige sangere.", "Phylloscopus"),
    "spurvefugle": ("Spurvefugle", "Alle spurvefugle (Passeriformes) på artslisten.",
                    "Acanthis Acridotheres Acrocephalus Aegithalos Alauda Anthus Bombycilla Bucanetes Calandrella Calcarius Calliope Carduelis "
                    "Carpodacus Catharus Cecropis Certhia Cettia Chloris Cinclus Cisticola Coccothraustes Coloeus Corvus Curruca Cyanistes "
                    "Delichon Emberiza Eremophila Erithacus Ficedula Fringilla Galerida Garrulus Helopsaltes Hippolais Hirundo Iduna Junco "
                    "Lanius Larvivora Linaria Locustella Lophophanes Loxia Lullula Luscinia Melanocorypha Monticola Montifringilla Motacilla "
                    "Muscicapa Nucifraga Oenanthe Oriolus Panurus Parus Passer Pastor Periparus Petronia Pheucticus Phoenicurus Phylloscopus "
                    "Pica Pinicola Plectrophenax Poecile Prunella Ptyonoprogne Pyrrhula Regulus Remiz Riparia Saxicola Serinus Setophaga Sitta "
                    "Spinus Sturnus Sylvia Tarsiger Troglodytes Turdus Vireo Zonotrichia Zoothera"),
    "sangere": ("Sangere", "Rørsangere, græshoppesangere, gulbug, løvsangere og Sylvia/Curruca.",
                "Acrocephalus Locustella Hippolais Iduna Curruca Sylvia Cettia Helopsaltes Phylloscopus Cisticola"),
    "drosler": ("Drosler", "Alle Turdus-arter.", "Turdus"),
    "finker": ("Finker og ammere", "Finker, irisker, korsnæb, dompap og ammere.",
               "Fringilla Chloris Spinus Linaria Acanthis Carduelis Serinus Loxia Pyrrhula Coccothraustes Carpodacus Pinicola Emberiza Calcarius Plectrophenax"),
    "laerker_piber": ("Lærker, piber og vipstjerter", "Lærker, piber og vipstjerter.",
                      "Alauda Lullula Galerida Eremophila Calandrella Melanocorypha Anthus Motacilla"),
    "rovfugle": ("Rovfugle", "Høge, ørne, glenter, våger, falke og lignende.",
                 "Accipiter Astur Aquila Buteo Circus Clanga Elanus Falco Gyps Haliaeetus Hieraaetus Milvus Neophron Pandion Pernis Circaetus"),
    "ugler": ("Ugler", "Alle ugler.", "Strix Asio Athene Bubo Otus Aegolius Glaucidium Surnia Tyto"),
    "vadefugle": ("Vadefugle", "Ryler, klirer, præstekraver, sneppe, spover og vibe.",
                  "Calidris Tringa Charadrius Anarhynchus Pluvialis Numenius Limosa Gallinago Lymnocryptes Scolopax Actitis Arenaria Xenus "
                  "Limnodromus Phalaropus Vanellus Recurvirostra Haematopus Himantopus Thinornis Eudromias Bartramia"),
    "aender": ("Ænder", "Svømmeænder, dykænder, havænder og lignende.",
               "Anas Mareca Spatula Aythya Netta Sibirionetta Tadorna Aix Mergus Mergellus Bucephala Clangula Melanitta Somateria Polysticta Oxyura"),
    "gaes_svaner": ("Gæs og svaner", "Gæs, bramgæs og svaner.", "Anser Branta Cygnus Alopochen"),
    "maager": ("Måger", "Alle måger.", "Larus Chroicocephalus Hydrocoloeus Ichthyaetus Leucophaeus Rissa Xema Pagophila Rhodostethia"),
    "terner": ("Terner", "Alle terner.", "Sterna Sternula Thalasseus Chlidonias Gelochelidon Hydroprogne Onychoprion"),
}


class QuizError(Exception):
    pass


async def groups(session: AsyncSession) -> dict:
    """Forudindstillede artsgrupper (opløst til arter på artslisten) og antal fotos pr. art, så brugeren kan se, hvad der kan spørges om."""
    rows = (await session.execute(text("""
        SELECT artnr, split_part(latin, ' ', 1) AS genus FROM species
        WHERE NOT excluded AND art_type = 'art' AND list_artnr = artnr AND latin IS NOT NULL AND latin NOT LIKE '%/%'"""))).all()
    by_genus: dict[str, list[int]] = {}
    for artnr, genus in rows:
        by_genus.setdefault(genus, []).append(artnr)
    out = []
    for key, (navn, besk, genera) in GROUPS.items():
        arter = sorted({a for g in genera.split() for a in by_genus.get(g, [])})
        if len(arter) >= MIN_SPECIES:
            out.append({"key": key, "navn": navn, "beskrivelse": besk, "arter": arter})
    return {"grupper": out, "fotos": await photo_counts(session)}


# enkeltfugl-fotos: publicerede fotos med netop én fugl, så spørgsmålet har ét facit
_SINGLE = """FROM gal_birds b JOIN gal_photos p ON p.id = b.photo_id AND p.status = 'publiceret' JOIN content_media m ON m.id = p.media_id
    WHERE b.artnr IS NOT NULL AND (SELECT count(*) FROM gal_birds x WHERE x.photo_id = p.id) = 1"""


async def photo_counts(session: AsyncSession) -> dict[str, int]:
    key = "quiz:photocounts:" + str(await version("photos"))
    if hit := await kv().get(key):
        return json.loads(hit)
    out = {str(a): n for a, n in (await session.execute(text(f"SELECT b.artnr, count(*) {_SINGLE} GROUP BY b.artnr"))).all()}
    await kv().set(key, json.dumps(out), ex=300)
    return out


async def _store(data: dict) -> str:
    token = secrets.token_urlsafe(9)
    await kv().set(f"quiz:art:{token}", json.dumps(data, ensure_ascii=False), ex=TOKEN_TTL)
    return token


async def build(session: AsyncSession, artnrs: list[int], method: str, n: int = N_QUESTIONS) -> list[dict]:
    """Et sæt spørgsmål ud fra de valgte arter. Arter uden foto springes over; er der færre end ``n`` arter, bruges flere fotos pr. art."""
    if method not in ("valg", "tekst"):
        raise QuizError("Ukendt svarmetode")
    artnrs = sorted(set(artnrs))
    if len(artnrs) < MIN_SPECIES:
        raise QuizError(f"Vælg mindst {MIN_SPECIES} arter")
    if len(artnrs) > 600:
        raise QuizError("For mange arter")
    rng = random.Random()
    sp = {r["artnr"]: r for r in (await session.execute(text(
        "SELECT artnr, navn, latin, list_artnr FROM species WHERE artnr = ANY(:a) AND NOT excluded"), {"a": artnrs})).mappings()}
    artnrs = [a for a in artnrs if a in sp]
    k = -(-n // max(1, len(artnrs))) + 1  # fotos pr. art, når der er få arter
    rows = (await session.execute(text(f"""
        SELECT * FROM (SELECT p.id, b.artnr, m.medium_key, m.storage_key, (SELECT ph.navn FROM gal_photographers ph WHERE ph.id = p.photographer_id) AS fotograf,
                              row_number() OVER (PARTITION BY b.artnr ORDER BY random()) AS rn
                       {_SINGLE} AND b.artnr = ANY(:a)) x WHERE rn <= :k"""), {"a": artnrs, "k": k})).mappings().all()
    per: dict[int, list] = {}
    for r in rows:
        per.setdefault(r["artnr"], []).append(r)
    have = [a for a in artnrs if per.get(a)]
    if len(have) < MIN_SPECIES:
        raise QuizError("Der er ikke fotos nok af de valgte arter endnu – vælg flere arter.")
    picked: list[tuple[int, dict]] = []
    if len(have) >= n:
        picked = [(a, per[a][0]) for a in rng.sample(have, n)]
    else:  # få arter: gå rundt om arterne, så hver får lige mange (forskellige) fotos
        for rnd in range(k):
            order = have[:]
            rng.shuffle(order)
            picked += [(a, per[a][rnd]) for a in order if len(per[a]) > rnd]
        picked = picked[:n]
    rng.shuffle(picked)
    names = {a: sp[a]["navn"] for a in have}
    if method == "valg" and len(have) < 4:  # få arter i udvalget: fyld op med andre arter, der har fotos, så der er fire svarmuligheder
        extra = (await session.execute(text("SELECT DISTINCT s.artnr, s.navn FROM species s JOIN gal_birds b ON b.artnr = s.artnr WHERE s.art_type = 'art' AND NOT s.excluded AND s.artnr <> ALL(:x)"),
                                        {"x": have})).all()
        rng.shuffle(extra)
        names.update({x: nm for x, nm in extra[:4 - len(have)]})
    out = []
    for a, r in picked:
        s_ = sp[a]
        data = {"artnr": a, "liste": s_["list_artnr"] or a, "navn": s_["navn"], "latin": s_["latin"], "foto": r["id"]}
        q = {"id": await _store(data), "media_url": "/media/art/" + (r["medium_key"] or r["storage_key"]), "media_credit": r["fotograf"], "metode": method}
        if method == "valg":
            opts = [a] + rng.sample([o for o in names if o != a], min(3, len(names) - 1))
            rng.shuffle(opts)
            q["choices"] = [{"id": o, "text": names[o]} for o in opts]
        out.append(q)
    return out


async def answer(session: AsyncSession, token: str, artnr: int) -> dict | None:
    raw = await kv().get(f"quiz:art:{token}")
    if not raw:
        return None
    d = json.loads(raw)
    ok = artnr == d["artnr"]
    if not ok and artnr:  # en underart/race af samme listeart tæller som rigtigt
        chosen = (await session.execute(text("SELECT list_artnr FROM species WHERE artnr = :a"), {"a": artnr})).scalar()
        ok = chosen is not None and chosen == d["liste"]
    return {"correct": ok, "artnr": d["artnr"], "navn": d["navn"], "latin": d["latin"], "photo_id": d["foto"]}
