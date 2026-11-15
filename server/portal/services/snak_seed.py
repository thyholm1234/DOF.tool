"""Eksempelopslag i Snak (docs/snak-modul-spec.md §9): fem opslag med kommentarer, så feedet ikke er tomt ved første opstart.

Alle rækker har ``eksempel = true`` og ``user_id = NULL`` og en opdigtet forfatter (``eksempel_navn``). Ingen fotos seedes."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import SnComment, SnPost
from portal.services import content_render, snak_attach
from portal.services.setting_store import get_setting_dict, put_setting

SEEDED = "snak_seeded"

H = lambda **kw: timedelta(**kw)  # noqa: E731 – kort skrivemåde i tabellen nedenfor

# (kategori, forfatter, titel, tekst, arter, lokalitetssøgning, kommentarer, fastgør, vedhæft_artikel)
# kommentar: (forfatter, alder, tekst, [svar], løsning)
EXAMPLES = [
    ("artikler", "Redaktionen (eksempel)", "Ny artikel: Hvorfor kommer der flere og flere sølvhejrer?",
     """Vi har lige lagt en ny artikel op, som gennemgår 30 års fund af [[art:Sølvhejre]] i Danmark – fra sjælden gæst til
en art, man nu kan regne med at se ved de fleste større søer i vinterhalvåret.

Tre ting vi synes er værd at diskutere:

1. Er det klimaet, eller er det bestandsfremgangen i Holland og Frankrig, der driver udviklingen?
2. Hvornår kommer det første sikre ynglefund herhjemme?
3. Bliver arten «for almindelig» til at blive indtastet – og hvad betyder det for statistikken?

Læs artiklen, og fortæl os, hvad du har set i dit eget område 👇""", [], None,
     [("Mette Kragsig (eksempel)", H(days=2), "Fin artikel! Ved Tissø har vi haft op til 14 på én gang i oktober. Det var utænkeligt for 10 år siden.",
       [("Jonas Ravnholt (eksempel)", H(days=2, minutes=-30), "14! Rekorden ved Vejlerne er vist 9 i år. Har I mange ringmærkede fugle imellem?"),
        ("Mette Kragsig (eksempel)", H(days=1, hours=22), "Svar til Jonas: Ja, vi aflæste én med fransk ring sidste vinter.")], False),
      ("Ole Birkedal (eksempel)", H(days=1), "Til punkt 3: Jeg indtaster stadig alle mine – det er jo netop udviklingen, vi gerne vil kunne følge.", [], False)],
     True, True),
    ("artsbestemmelse", "Karen Strandvig (eksempel)", "Ung stormmåge-størrelse måge – sølvmåge eller kaspisk?",
     """Fotograferet i går ved Kongelunden, ca. kl. 14 i fladt lys. Jeg hælder til en 2K [[art:Kaspisk Måge]], men er ikke
sikker. (I et rigtigt opslag ville der være billeder her – vis gerne både siddende og flyvende fugl.)

Det, jeg lagde mærke til:

- Lang, slank og parallelsidet næb uden tydelig gonys-vinkel
- Lille, mørkt øje placeret højt i et «langt» hoved
- Lange ben og en meget oprejst holdning
- Ret ensfarvet, lys underside – men lidt plettet på flankerne

Hvad mener I? Og hvilke kendetegn ville I gerne have set bedre?""", ["Sølvmåge", "Kaspisk Måge"], "Kongelunden",
     [("Anders Kløvborg (eksempel)", H(days=1), "Hovedform og ben peger på kaspisk, men flankerne gør mig lidt usikker. Har du et billede af den udbredte vinge? Mønstret på de inderste håndsvingfjer er ofte det afgørende.",
       [("Karen Strandvig (eksempel)", H(hours=23), "Svar til Anders: Ja – den lettede to gange. Der er et lyst «vindue» på de inderste håndsvingfjer og et smalt mørkt bånd på halen.")], False),
      ("Lise Holmgaard (eksempel)", H(hours=20), "Med det vingemønster og den hovedform er jeg ret sikker: **2K kaspisk måge**. Sølvmåge i samme alder ville have et grovere næb og en mere plettet, «beskidt» fremtoning.", [], True),
      ("Peter Moesgaard (eksempel)", H(hours=18), "Enig med Lise. Husk at den kan kræve SU-behandling afhængigt af tidspunktet – tjek lige listen.", [], False)],
     False, False),
    ("diskussion", "Sofie Lunding (eksempel)", "Skal vi bruge afspilning af fuglestemmer i felten?",
     """Jeg var på tur i weekenden, hvor en anden fuglekigger afspillede sang for at lokke en [[art:Natsanger]] frem. Den kom
frem – men den blev også ved med at synge i lang tid bagefter.

Jeg er selv i tvivl. Det er et effektivt værktøj, når man fx tæller ynglepar, men hvor går grænsen?

- Er det i orden uden for yngletiden?
- Hvad med sjældne arter, som mange vil se?
- Bør DOF have en klar anbefaling?

Hold tonen venlig – jeg ved, at vi ikke er enige om det her 🙂""", [], None,
     [("Henrik Aagaard (eksempel)", H(days=2), "Til optællinger med en fast metode er det fint, men til «lad os lige se den» synes jeg nej. Fuglen bruger energi på at forsvare et territorium mod en højttaler.",
       [("Mads Thygesen (eksempel)", H(days=1, hours=20), "Svar til Henrik: Jeg er enig om ynglesæsonen, men om vinteren kan jeg ikke se problemet med kort afspilning."),
        ("Henrik Aagaard (eksempel)", H(days=1, hours=18), "Svar til Mads: Fair – men så skal det være kort og ikke ved samme fugl hele dagen.")], False),
      ("Birgitte Rask (eksempel)", H(days=1), "Der findes faktisk retningslinjer i flere lande. Måske kunne nogen lave et opslag under *Artikler og læsning* med en oversigt?", [], False),
      ("Kasper Nørholm (eksempel)", H(hours=5), "Nej. Bare nej. 😄 Men god diskussion.", [], False)],
     False, False),
    ("fund", "Thomas Engbjerg (eksempel)", None,
     """Sikke en morgen på Skagen Gren! Stille vind fra sydøst, og fra solopgang var der fuldt drøn på: hundredvis af
[[art:Bogfinke]] og [[art:Kvækerfinke]], et par [[art:Rødstrubet Piber]] over hovedet og som prikken over i'et en
[[art:Sortstrubet Bynkefugl]] i klitten.

Kaffen blev kold, men det var det hele værd ☕🐦""", ["Sortstrubet Bynkefugl", "Rødstrubet Piber"], "Grenen",
     [("Nanna Vestergaard (eksempel)", H(hours=3), "Misundelig! Var der også lærkefalke?",
       [("Thomas Engbjerg (eksempel)", H(hours=2, minutes=30), "Svar til Nanna: Én kort over Grenen ved 9-tiden.")], False),
      ("Rune Fogh (eksempel)", H(hours=1), "Det er de morgener, man husker. God tur hjem!", [], False)],
     False, False),
    ("ture", "Lokalafdelingen (eksempel)", "Fælles tur til Vejlerne søndag – samkørsel fra Aalborg",
     """Vi tager til Vejlerne på søndag og håber på [[art:Rørdrum]], [[art:Skægmejse]] og de første [[art:Sædgås]].

**Praktisk**

- Mødested: Parkeringspladsen ved Aalborg Station kl. 7.00
- Hjemme igen: ca. kl. 15
- Tag varmt tøj, madpakke og gerne en teleskop med
- Der er plads til 8 i bilerne – skriv i kommentarerne, om du kører eller skal med

Alle er velkomne, også nybegyndere. Vi viser gerne, hvordan man indtaster i DOFbasen undervejs.""", [], None,
     [("Signe Bach (eksempel)", H(days=1), "Jeg kører og har 3 ledige pladser.", [], False),
      ("Emil Krogh (eksempel)", H(hours=20), "Jeg vil gerne med! Første gang i Vejlerne – har ikke teleskop.",
       [("Signe Bach (eksempel)", H(hours=19), "Svar til Emil: Du kan køre med mig, og der er altid nogen, der deler teleskop.")], False),
      ("Lokalafdelingen (eksempel)", H(hours=3), "Opdatering: Vi er nu 7 tilmeldte. Én plads tilbage.", [], False)],
     False, False),
]


async def _species(session: AsyncSession, navn: str) -> int | None:
    r = (await session.execute(text("SELECT artnr FROM species WHERE lower(navn) = lower(:n) LIMIT 1"), {"n": navn})).first()
    return r[0] if r else None


async def _body(session: AsyncSession, md: str) -> str:
    """[[art:Navn]] → [[art:ARTNR]]; ukendt art skrives som almindelig tekst."""
    out, last = [], 0
    for m in re.finditer(r"\[\[art:([^\]]+)\]\]", md):
        out.append(md[last:m.start()])
        nr = await _species(session, m[1].strip())
        out.append(f"[[art:{nr}]]" if nr else m[1].strip())
        last = m.end()
    out.append(md[last:])
    return "".join(out)


async def _render(session: AsyncSession, md: str):
    return await content_render.render_for(session, md, "kommentar")


async def has_examples(session: AsyncSession) -> bool:
    return bool((await session.execute(text("SELECT 1 FROM sn_posts WHERE eksempel LIMIT 1"))).first())


async def seed_examples(session: AsyncSession) -> int:
    """Lægger de fem eksempelopslag ind (ikke hvis de allerede findes). Returnerer antal opslag."""
    if await has_examples(session):
        return 0
    cats = {r[1]: r[0] for r in (await session.execute(text("SELECT id, slug FROM sn_categories"))).all()}
    now = datetime.now(timezone.utc)
    made = 0
    for i, (cat, forfatter, titel, md, arter, lok, comments, pin, attach) in enumerate(EXAMPLES):
        if cat not in cats:
            continue
        md = await _body(session, md)
        nrs = [n for n in [await _species(session, a) for a in arter] if n]
        loknr = lok_navn = None
        if lok:
            r = (await session.execute(text("SELECT loknr, navn FROM locations WHERE navn ILIKE :n AND merged_into IS NULL ORDER BY loknr LIMIT 1"), {"n": f"%{lok}%"})).first()
            if r:
                loknr, lok_navn = r[0], r[1][:200]
        att = None
        if attach:
            a = (await session.execute(text("SELECT id FROM art_articles WHERE status = 'publiceret' ORDER BY dato DESC LIMIT 1"))).first()
            if a:
                try:
                    att = await snak_attach.resolve(session, 0, "artikel", str(a[0]))
                except snak_attach.AttachError:
                    att = None
            if att is None:
                md = "(Her vises en artikel, når modulet har artikler.)\n\n" + md
        created = min([now - c[1] for c in comments] + [now]) - H(hours=3 + i)
        r = await _render(session, md)
        post = SnPost(category_id=cats[cat], user_id=None, eksempel_navn=forfatter, eksempel=True, titel=titel, body_md=md, body_html=r.html, body_text=r.text,
                      attachment=att, arter=nrs, loknr=loknr, lok_navn=lok_navn, created_at=created, last_activity_at=created,
                      pinned_at=now if pin else None)
        session.add(post)
        await session.flush()
        n, last, solution = 0, created, None
        for navn, age, body, replies, wins in comments:
            cr = await _render(session, body)
            c = SnComment(post_id=post.id, user_id=None, eksempel_navn=navn, body_md=body, body_html=cr.html, body_text=cr.text, created_at=now - age)
            session.add(c)
            await session.flush()
            n += 1
            last = max(last, c.created_at)
            solution = c.id if wins else solution
            for rnavn, rage, rbody in replies:
                rr = await _render(session, rbody)
                session.add(SnComment(post_id=post.id, parent_id=c.id, user_id=None, eksempel_navn=rnavn, body_md=rbody, body_html=rr.html, body_text=rr.text, created_at=now - rage))
                n += 1
                last = max(last, now - rage)
        post.n_comments, post.last_activity_at, post.solution_comment_id = n, last, solution
        made += 1
    await session.commit()
    return made


async def delete_examples(session: AsyncSession) -> int:
    """Sletter alle eksempelopslag med kommentarer (og evt. medier – eksempler har ingen)."""
    ids = [r[0] for r in (await session.execute(text("SELECT id FROM sn_posts WHERE eksempel"))).all()]
    if ids:
        await session.execute(text("DELETE FROM sn_posts WHERE id = ANY(:i)"), {"i": ids})  # kommentarer, følg og visninger følger med (CASCADE)
        await session.execute(text("DELETE FROM sn_reactions WHERE kind = 'post' AND target_id = ANY(:i)"), {"i": ids})
    await session.commit()
    return len(ids)


async def seed_once(session: AsyncSession) -> None:
    """Ved opstart: kun første gang (nøglen snak_seeded). «Slet alle eksempler» sætter den ikke tilbage – kun «Gendan» lægger dem ind igen."""
    cur = await get_setting_dict(session, SEEDED, {"done": False})
    if cur.get("done"):
        return
    await seed_examples(session)
    await put_setting(session, SEEDED, {"done": True})
