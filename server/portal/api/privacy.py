"""Privatlivspolitik (/privatliv): offentlig, server-renderet side – oplysningspligten efter art. 13 og 14.

Den dataansvarlige og kontaktadressen sættes i .env (PRIVACY_CONTROLLER, PRIVACY_EMAIL). Opbevaringsperioderne
svarer til oprydningen i worker.nightly_compute og sessionernes levetid; ret teksten her, hvis de ændres.
"""

from __future__ import annotations

from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from portal.api.share import _page
from portal.config import get_settings

router = APIRouter(include_in_schema=False)
VERSION = "24. oktober 2026"


def _section(title: str, *paras: str) -> str:
    return f'<section class="card share-card"><h2>{title}</h2>{"".join(paras)}</section>'


@router.get("/privatliv")
async def privacy() -> HTMLResponse:
    s = get_settings()
    who = escape(s.privacy_controller) if s.privacy_controller else "<b>[Navn på dataansvarlig – sæt PRIVACY_CONTROLLER]</b>"
    mail = (f'<a href="mailto:{escape(s.privacy_email)}">{escape(s.privacy_email)}</a>' if s.privacy_email
            else "<b>[e-mail – sæt PRIVACY_EMAIL]</b>")
    body = "".join([
        f"""<section class="card share-card"><h1>Privatlivspolitik</h1>
        <p class="lead">Sådan behandler DOF.tool dine personoplysninger.</p>
        <p>Dataansvarlig: {who}. Kontakt: {mail}. Gældende fra {VERSION}.</p></section>""",
        _section("Hvilke oplysninger",
            """<ul>
            <li><b>Din konto:</b> obserkode og navn fra DOFbasen, rolle, dine indstillinger (afdelinger, kommuner, notifikationer,
            tema) og – hvis du selv skriver det – et telefonnummer. Tidspunkt for oprettelse og seneste besøg.</li>
            <li><b>Det du laver i portalen:</b> feltmeldinger med fotos og lyd, ture, kommentarer, likes, statusmeldinger,
            kalenderbesøg, lister, grupper, matrikler, områder og alarmregler, Netfugl-fotos, -artikler og -kommentarer,
            supportbeskeder og quizresultater.</li>
            <li><b>DKU-sager:</b> tip om en observation, beskeder og DKU's afgørelse. Den, der tipper, er anonym for
            observatøren. Observatøren inddrages kun, hvis DKU finder grund til det.</li>
            <li><b>Position</b> – kun hvis du slår deling til: din seneste position (ikke en historik).</li>
            <li><b>Push:</b> adressen på din enheds push-abonnement, hvis du slår notifikationer til.</li>
            <li><b>Teknisk:</b> en login-cookie, en aktivitetslog (sidevisninger og handlinger) og serverlogs med IP-adresse.</li>
            <li><b>Observationer fra DOFbasen:</b> art, sted, tid og antal med observatørens navn og obserkode – også for
            observatører, der ikke bruger DOF.tool. Hemmelige observationer vises ikke.</li>
            </ul>
            <p>Fotos renses for metadata (fx GPS-position) ved upload. Vi behandler ingen følsomme oplysninger.</p>"""),
        _section("Formål og retsgrundlag",
            """<ul>
            <li>At levere portalen til dig: databeskyttelsesforordningens art. 6, stk. 1, litra b.</li>
            <li>Sikkerhed, fejlfinding og at forhindre misbrug (logs, aktivitetslog, grænser for login): legitim interesse,
            art. 6, stk. 1, litra f.</li>
            <li>At vise observationer fra DOFbasen, som observatørerne har indtastet til offentlig visning: legitim interesse i
            at formidle fugleobservationer, art. 6, stk. 1, litra f.</li>
            <li>Kvalitetssikring af observationer (DKU-sager): legitim interesse i korrekte data, art. 6, stk. 1, litra f.</li>
            <li>Position og push-notifikationer: dit samtykke, art. 6, stk. 1, litra a. Du kan til enhver tid slå dem fra
            under Profil; det påvirker ikke behandlingen før.</li>
            </ul><p>Der træffes ingen automatiske afgørelser om dig.</p>"""),
        _section("Hvem modtager oplysningerne",
            """<ul>
            <li><b>DOF / DOFbasen:</b> dit login kontrolleres hos DOFbasen. Adgangskoden sendes videre og gemmes ikke i
            DOF.tool.</li>
            <li><b>Andre brugere</b> ser det, du selv offentliggør (kommentarer, fotos, feltmeldinger og – medmindre du fravælger
            det – dine lister og placeringer).</li>
            <li><b>OpenStreetMap Foundation</b> (Storbritannien, som har en tilstrækkelighedsafgørelse fra EU): kortet hentes
            derfra, så din IP-adresse sendes med.</li>
            <li><b>Google Firebase Cloud Messaging</b> (USA, under EU-US Data Privacy Framework): push i Android-appen.
            Push i browseren går gennem din browsers egen tjeneste (fx Google, Mozilla eller Apple).</li>
            <li>Vores hosting- og backupleverandører, som kun behandler data efter aftale.</li>
            </ul><p>Oplysningerne sælges ikke og bruges ikke til reklame. Der er ingen analyse- eller sporingsværktøjer.</p>"""),
        _section("Hvor længe",
            f"""<ul>
            <li>Konto og indhold: indtil du sletter kontoen.</li>
            <li>DKU-sager: gemmes som dokumentation for kvalitetssikringen. Sletter du kontoen, bevares dine beskeder uden
            dit navn.</li>
            <li>Notifikationer: 90 dage. Aktivitetslog: 180 dage. Login: {s.session_days} dage eller til du logger ud.</li>
            <li>Position: til du slår delingen fra, eller 30 dage efter seneste opdatering.</li>
            <li>Serverlogs: roteres løbende og fylder højst nogle få dages trafik.</li>
            <li>Sikkerhedskopier: slettede data forsvinder fra dem senest efter {s.privacy_backup_days} dage.</li>
            </ul>"""),
        _section("Dine rettigheder",
            """<p>Du har ret til indsigt, berigtigelse, sletning, begrænsning, indsigelse og dataportabilitet, og til at trække
            et samtykke tilbage. Under <a href="/#/profil">Profil</a> kan du selv <b>hente alle dine data</b> (JSON) og
            <b>slette din konto</b> – det sletter også dine uploadede filer. Navn og observationer rettes i DOFbasen.
            Skriv til os for alt andet; vi svarer inden for en måned.</p>
            <p>Du kan klage til <a href="https://www.datatilsynet.dk" target="_blank" rel="noopener">Datatilsynet</a>,
            Carl Jacobsens Vej 35, 2500 Valby.</p>"""),
        _section("Cookies",
            """<p>DOF.tool bruger kun én cookie: den, der holder dig logget ind. Den er nødvendig og kræver derfor ikke samtykke.
            Dine valg (fx tema) gemmes lokalt i browseren. Der er ingen cookies fra tredjeparter.</p>"""),
    ])
    return HTMLResponse(_page("Privatlivspolitik", '<meta name="robots" content="noindex">', body, "/#/login"),
                        headers={"Cache-Control": "no-cache"})
