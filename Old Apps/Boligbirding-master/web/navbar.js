// Version: 1.13.16 - 2026-05-15 23.55.20
// © Christian Vemmelund Helligsø

// -----------------------------------------------------
// 1) Konstant: Lokalafdelinger (direkte i denne fil)
// -----------------------------------------------------
export const AFDELINGER = [
  "DOF København",
  "DOF Nordsjælland",
  "DOF Vestsjælland",
  "DOF Storstrøm",
  "DOF Bornholm",
  "DOF Fyn",
  "DOF Sønderjylland",
  "DOF Sydvestjylland",
  "DOF Sydøstjylland",
  "DOF Vestjylland",
  "DOF Østjylland",
  "DOF Nordvestjylland",
  "DOF Nordjylland"
];

// Lille helper til at lave link HTML på en ensartet måde
function linkHTML(href, label) {
  // Sørg for at alle lister-links peger på scoreboard.html
  if (href.startsWith('?')) {
    href = 'scoreboard.html' + href;
  }
  return `<a href="${href}" class="nav-link">${label}</a>`;
}

function renderSiteFootnote() {
  if (document.getElementById('site-footnote')) return;
  const year = new Date().getFullYear();
  document.body.insertAdjacentHTML('beforeend', `
    <footer id="site-footnote" class="site-footnote" role="contentinfo">
      <span>© ${year} Christian Vemmelund Helligsø</span>
      <span aria-hidden="true">·</span>
      <a href="/gdpr.html">Privatliv & GDPR</a>
    </footer>
  `);
}

// -----------------------------------------------------
// 2) Render: Indsæt navbar + mobil overlay i DOM
// -----------------------------------------------------
export function renderNavbar() {
  const navHtml = `
    <nav class="navbar">
      <a class="navbar-title" href="/">FugleLiga</a>

      <!-- DESKTOP -->
      <div class="navbar-links">

        <!-- Krydslister (all time) -->
        <div class="dropdown" id="dd-krydslister-alltime">
          <button class="nav-link" type="button">Krydslister</button>
          <div class="dropdown-content" style="min-width: 300px;">
            <div class="muted" style="padding:8px 16px;font-weight:700;">Mine grupper</div>
            <div class="js-gruppe-alle-alltime"></div>

            <div class="muted" style="padding:10px 16px 4px;font-weight:700;border-top:1px solid #eee;margin-top:6px;">Danmark</div>
            ${linkHTML('?scope=global_alle&aar=global', 'Danmark')}

            <div class="muted" style="padding:10px 16px 4px;font-weight:700;border-top:1px solid #eee;margin-top:6px;">Lokalafdelinger</div>
            <div class="js-afdeling-alle-alltime"></div>

            <div class="muted" style="padding:10px 16px 4px;font-weight:700;border-top:1px solid #eee;margin-top:6px;">Kommuner</div>
            <div class="js-kommune-alle-alltime"></div>
          </div>
        </div>

        <!-- Årskrydslister -->
        <div class="dropdown" id="dd-krydslister">
          <button class="nav-link" type="button">Årskrydslister</button>
          <div class="dropdown-content" style="min-width: 300px;">
            <div class="muted" style="padding:8px 16px;font-weight:700;">Mine grupper</div>
            <div class="js-gruppe-alle"></div>

            <div class="muted" style="padding:10px 16px 4px;font-weight:700;border-top:1px solid #eee;margin-top:6px;">Danmark</div>
            ${linkHTML('?scope=global_alle', 'Danmark')}

            <div class="muted" style="padding:10px 16px 4px;font-weight:700;border-top:1px solid #eee;margin-top:6px;">Lokalafdelinger</div>
            <div class="js-afdeling-alle"></div>

            <div class="muted" style="padding:10px 16px 4px;font-weight:700;border-top:1px solid #eee;margin-top:6px;">Kommuner</div>
            <div class="js-kommune-alle"></div>
          </div>
        </div>

        <!-- Matrikellister (all time) -->
        <div class="dropdown" id="dd-matrikellister-alltime">
          <button class="nav-link" type="button">Matrikellister</button>
          <div class="dropdown-content" style="min-width: 300px;">
            <div class="muted" style="padding:8px 16px;font-weight:700;">Mine grupper</div>
            <div class="js-gruppe-matrikel-alltime"></div>

            <div class="muted" style="padding:10px 16px 4px;font-weight:700;border-top:1px solid #eee;margin-top:6px;">Danmark</div>
            ${linkHTML('?scope=global_matrikel&aar=global', 'Danmark')}

            <div class="muted" style="padding:10px 16px 4px;font-weight:700;border-top:1px solid #eee;margin-top:6px;">Lokalafdelinger</div>
            <div class="js-afdeling-matrikel-alltime"></div>

            <div class="muted" style="padding:10px 16px 4px;font-weight:700;border-top:1px solid #eee;margin-top:6px;">Kommuner</div>
            <div class="js-kommune-matrikel-alltime"></div>
          </div>
        </div>

        <!-- Årsmatriklister -->
        <div class="dropdown" id="dd-matrikellister">
          <button class="nav-link" type="button">Årsmatriklister</button>
          <div class="dropdown-content" style="min-width: 300px;">
            <div class="muted" style="padding:8px 16px;font-weight:700;">Mine grupper</div>
            <div class="js-gruppe-matrikel"></div>

            <div class="muted" style="padding:10px 16px 4px;font-weight:700;border-top:1px solid #eee;margin-top:6px;">Danmark</div>
            ${linkHTML('?scope=global_matrikel', 'Danmark')}

            <div class="muted" style="padding:10px 16px 4px;font-weight:700;border-top:1px solid #eee;margin-top:6px;">Lokalafdelinger</div>
            <div class="js-afdeling-matrikel"></div>

            <div class="muted" style="padding:10px 16px 4px;font-weight:700;border-top:1px solid #eee;margin-top:6px;">Kommuner</div>
            <div class="js-kommune-matrikel"></div>
          </div>
        </div>

        <!-- MATRIKELARTSDATA -->
        <button class="nav-link" onclick="location.href='/art.html'" type="button">Matrikelartsdata</button>

        <!-- PROFIL -->
        <div class="dropdown" id="dd-profil">
          <button class="nav-link" type="button">Profil</button>
          <div class="dropdown-content" style="min-width: 220px;">
            ${linkHTML('/statistik.html', 'Statistik')}
            ${linkHTML('/settings.html', 'Indstillinger')}
          </div>
        </div>



      </div>

      <!-- MOBIL: hamburger -->
      <button class="hamburger" aria-label="Menu" aria-controls="mobile-menu" aria-expanded="false">
        <span></span><span></span><span></span>
      </button>
    </nav>

    <!-- MOBIL overlay -->
    <div class="mobile-nav-overlay" id="mobile-menu">
      <div class="mobile-nav-content">
        <button class="close-mobile-nav" aria-label="Luk">×</button>
        <div class="mobile-links">

          <!-- Forside -->
          <button class="nav-link" onclick="location.href='/index.html'" type="button">Forside</button>

          <!-- Krydslister (all time, mobil) -->
          <div class="dropdown">
            <button class="nav-link" type="button">Krydslister</button>
            <div class="dropdown-content" style="padding:10px;">
              <div class="muted" style="padding:8px 0 4px 0;font-weight:700;">Mine grupper</div>
              <div class="js-gruppe-alle-alltime"></div>
              <div class="muted" style="padding:10px 0 4px 0;font-weight:700;">Danmark</div>
              ${linkHTML('?scope=global_alle&aar=global', 'Danmark')}
              <div class="muted" style="padding:10px 0 4px 0;font-weight:700;">Lokalafdelinger</div>
              <div class="js-afdeling-alle-alltime"></div>
              <div class="muted" style="padding:10px 0 4px 0;font-weight:700;">Kommuner</div>
              <div class="js-kommune-alle-alltime"></div>
            </div>
          </div>

          <!-- Årskrydslister (mobil) -->
          <div class="dropdown">
            <button class="nav-link" type="button">Årskrydslister</button>
            <div class="dropdown-content" style="padding:10px;">
              <div class="muted" style="padding:8px 0 4px 0;font-weight:700;">Mine grupper</div>
              <div class="js-gruppe-alle"></div>
              <div class="muted" style="padding:10px 0 4px 0;font-weight:700;">Danmark</div>
              ${linkHTML('?scope=global_alle', 'Danmark')}
              <div class="muted" style="padding:10px 0 4px 0;font-weight:700;">Lokalafdelinger</div>
              <div class="js-afdeling-alle"></div>
              <div class="muted" style="padding:10px 0 4px 0;font-weight:700;">Kommuner</div>
              <div class="js-kommune-alle"></div>
            </div>
          </div>

          <!-- Matrikellister (all time, mobil) -->
          <div class="dropdown">
            <button class="nav-link" type="button">Matrikellister</button>
            <div class="dropdown-content" style="padding:10px;">
              <div class="muted" style="padding:8px 0 4px 0;font-weight:700;">Mine grupper</div>
              <div class="js-gruppe-matrikel-alltime"></div>
              <div class="muted" style="padding:10px 0 4px 0;font-weight:700;">Danmark</div>
              ${linkHTML('?scope=global_matrikel&aar=global', 'Danmark')}
              <div class="muted" style="padding:10px 0 4px 0;font-weight:700;">Lokalafdelinger</div>
              <div class="js-afdeling-matrikel-alltime"></div>
              <div class="muted" style="padding:10px 0 4px 0;font-weight:700;">Kommuner</div>
              <div class="js-kommune-matrikel-alltime"></div>
            </div>
          </div>

          <!-- Årsmatriklister (mobil) -->
          <div class="dropdown">
            <button class="nav-link" type="button">Årsmatriklister</button>
            <div class="dropdown-content" style="padding:10px;">
              <div class="muted" style="padding:8px 0 4px 0;font-weight:700;">Mine grupper</div>
              <div class="js-gruppe-matrikel"></div>
              <div class="muted" style="padding:10px 0 4px 0;font-weight:700;">Danmark</div>
              ${linkHTML('?scope=global_matrikel', 'Danmark')}
              <div class="muted" style="padding:10px 0 4px 0;font-weight:700;">Lokalafdelinger</div>
              <div class="js-afdeling-matrikel"></div>
              <div class="muted" style="padding:10px 0 4px 0;font-weight:700;">Kommuner</div>
              <div class="js-kommune-matrikel"></div>
            </div>
          </div>
        
        <!-- MATRIKELARTSDATA -->
        <button class="nav-link" onclick="location.href='/art.html'" type="button">Matrikelartsdata</button>

        <!-- PROFIL -->
        <div class="dropdown">
          <button class="nav-link" type="button">Profil</button>
          <div class="dropdown-content" style="padding:10px;">
            ${linkHTML('/statistik.html', 'Statistik')}
            ${linkHTML('/settings.html', 'Indstillinger')}
          </div>
        </div>


        </div>
      </div>
    </div>
  `;

  // Indsæt i DOM
  document.body.insertAdjacentHTML('afterbegin', navHtml);
  renderSiteFootnote();

  // Fyld lokalafdelinger med det samme (statisk liste)
  addAfdelingerLinks(AFDELINGER);
  initKommuneLinks();
}

async function initKommuneLinks() {
  try {
    const [prefsRes, afdelingerRes] = await Promise.all([
      fetch('/api/get_userprefs'),
      fetch('/api/afdelinger')
    ]);
    if (!prefsRes.ok || !afdelingerRes.ok) {
      addKommuneLinks([]);
      return;
    }
    const prefs = await prefsRes.json();
    const afdelinger = await afdelingerRes.json();
    const kommunerMap = new Map((afdelinger.kommuner || []).map(k => [String(k.id), k.navn]));
    const selectedIds = Array.isArray(prefs.kommuner) ? prefs.kommuner.map(v => String(v)) : [];
    const items = selectedIds
      .filter(id => kommunerMap.has(id))
      .map(id => ({ id, navn: kommunerMap.get(id) }));
    addKommuneLinks(items);
  } catch (_) {
    addKommuneLinks([]);
  }
}

// -----------------------------------------------------
// 3) Init: Desktop dropdowns (show) + klik udenfor
// -----------------------------------------------------
export function initNavbar() {
  // Tilføj denne linje:
  const mql = window.matchMedia('(min-width: 801px)');

  document.addEventListener('click', (e) => {
    const trigger = e.target.closest('.navbar .dropdown > .nav-link');
    const allContents = document.querySelectorAll('.navbar .dropdown .dropdown-content');

    if (trigger) {
      const dd = trigger.parentElement; // .dropdown
      const content = dd.querySelector('.dropdown-content');
      const wasOpen = content.classList.contains('show');
      // Luk alle først
      allContents.forEach(dc => dc.classList.remove('show'));
      // Toggle: kun åbn hvis ikke allerede åben
      if (!wasOpen) content.classList.add('show');
      e.stopPropagation();
      return;
    }

    // Klik udenfor lukker også (ovenstående lukning er nok)
  });

  // NYT: Klik på et link i dropdown lukker dropdown (desktop)
  // Dette skal bindes én gang til alle links i dropdowns
  document.querySelectorAll('.navbar .dropdown .dropdown-content a.nav-link').forEach(link => {
    if (!link.dataset.boundClose) {
      link.addEventListener('click', function (e) {
        // Find nærmeste åbne dropdown-content og luk den
        const content = link.closest('.dropdown-content');
        if (content && content.classList.contains('show')) {
          content.classList.remove('show');
        }
      });
      link.dataset.boundClose = '1';
    }
  });

  // Luk dropdown når der klikkes på et link i dropdown-content (desktop)
  document.addEventListener('click', (e) => {
    if (!mql.matches) return; // kun desktop
    const link = e.target.closest('.navbar .dropdown .dropdown-content a.nav-link');
    if (link) {
      // Find nærmeste dropdown-content og luk den
      const content = link.closest('.dropdown-content');
      if (content && content.classList.contains('show')) {
        content.classList.remove('show');
      }
      // Stop event så dropdown ikke åbner igen
      e.stopPropagation();
    }
  });
}

// -----------------------------------------------------
// 4) Init: Mobil overlay + mobil dropdowns (open)
// -----------------------------------------------------
// Helper: sæt korrekt max-højde (CSS var(--dropdown-max)) ud fra faktisk indhold
function setDropdownMaxHeight(contentEl) {
  if (!contentEl) return;
  contentEl.style.setProperty('--dropdown-max', '0px');
  // Force reflow
  void contentEl.offsetHeight;
  const h = contentEl.scrollHeight;
  contentEl.style.setProperty('--dropdown-max', `${h}px`);
}

// Initier accordion-adfærd for alle knapper i mobil-overlayet
function setupMobileAccordion(root = document) {
  const overlay = root.querySelector('.mobile-nav-overlay');
  if (!overlay) return;

  // Toggle på alle dropdowns (også indlejrede)
  overlay.querySelectorAll('.dropdown > .nav-link').forEach(btn => {
    if (!btn.dataset.bound) {
      btn.addEventListener('click', () => {
        const dd = btn.parentElement;
        const content = dd.querySelector(':scope > .dropdown-content');
        const willOpen = !dd.classList.contains('open');
        dd.classList.toggle('open', willOpen);
        if (willOpen) {
          setDropdownMaxHeight(content);
        } else {
          content?.style.setProperty('--dropdown-max', '0px');
        }
      });
      btn.dataset.bound = '1';
    }
  });

  // Justér højde hvis vindue ændrer størrelse
  window.addEventListener('resize', () => {
    overlay.querySelectorAll('.dropdown.open > .dropdown-content').forEach(setDropdownMaxHeight);
  }, { passive: true });
}

export function initMobileNavbar() {
  const hamburger = document.querySelector('.hamburger');
  const overlay   = document.querySelector('.mobile-nav-overlay');
  const closeBtn  = document.querySelector('.close-mobile-nav');
  const content   = overlay?.querySelector('.mobile-nav-content');

  if (!overlay || !hamburger || !content) return;

  const openOverlay = () => {
    overlay.classList.add('active');
    overlay.classList.remove('closing');
    content.classList.remove('closing');
    hamburger.setAttribute('aria-expanded', 'true');
    setupMobileAccordion(document);
    overlay.querySelectorAll('.dropdown.open > .dropdown-content').forEach(setDropdownMaxHeight);
  };

  const closeOverlay = () => {
    overlay.classList.add('closing');
    overlay.classList.remove('active');
    content.classList.add('closing');
    hamburger.setAttribute('aria-expanded', 'false');
    // Luk alle åbne sektioner og nulstil højde
    overlay.querySelectorAll('.dropdown.open').forEach(dd => {
      dd.classList.remove('open');
      dd.querySelectorAll(':scope > .dropdown-content').forEach(c => c.style.setProperty('--dropdown-max', '0px'));
    });
    // Vent på animation (0.3s) før du skjuler overlayet helt
    setTimeout(() => {
      overlay.classList.remove('closing');
      content.classList.remove('closing');
    }, 300);
  };

  // Åbn/luk overlay
  if (!hamburger.dataset.bound) {
    hamburger.addEventListener('click', openOverlay);
    hamburger.dataset.bound = '1';
  }
  if (closeBtn && !closeBtn.dataset.bound) {
    closeBtn.addEventListener('click', closeOverlay);
    closeBtn.dataset.bound = '1';
  }
  if (!overlay.dataset.bound) {
    overlay.addEventListener('click', (e) => { if (e.target === overlay) closeOverlay(); });
    overlay.querySelectorAll('a[href]').forEach(a => a.addEventListener('click', closeOverlay));
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && overlay.classList.contains('active')) closeOverlay();
    });
    overlay.dataset.bound = '1';
  }
}

// -----------------------------------------------------
// 5) Indhold: Grupper (krydslister + matrikellister)
// -----------------------------------------------------
export function addGruppeLinks(grupper) {
  // Normaliser: accepter enten ["A","B"] eller [{navn:"A"}, {name:"B"}, …]
  const norm = (g) =>
    (typeof g === 'string'
      ? { navn: g }
      : { navn: g.navn ?? g.name ?? g.title ?? String(g) });

  const items = (Array.isArray(grupper) ? grupper : [])
    .map(norm)
    .filter(x => x.navn && x.navn.trim() !== '');

  // Kun gruppenavn som label
  const htmlAlle = items.length
    ? items.map(it => {
        const enc = encodeURIComponent(it.navn);
        return linkHTML(`?scope=gruppe_alle&gruppe=${enc}`, it.navn);
      }).join('')
    : `<span class="muted" style="display:block;padding:8px 16px;">Ingen grupper</span>`;

  const htmlAlleAllTime = items.length
    ? items.map(it => {
        const enc = encodeURIComponent(it.navn);
        return linkHTML(`?scope=gruppe_alle&gruppe=${enc}&aar=global`, it.navn);
      }).join('')
    : `<span class="muted" style="display:block;padding:8px 16px;">Ingen grupper</span>`;

  const htmlMatrikel = items.length
    ? items.map(it => {
        const enc = encodeURIComponent(it.navn);
        return linkHTML(`?scope=gruppe_matrikel&gruppe=${enc}`, it.navn);
      }).join('')
    : `<span class="muted" style="display:block;padding:8px 16px;">Ingen grupper</span>`;

  const htmlMatrikelAllTime = items.length
    ? items.map(it => {
        const enc = encodeURIComponent(it.navn);
        return linkHTML(`?scope=gruppe_matrikel&gruppe=${enc}&aar=global`, it.navn);
      }).join('')
    : `<span class="muted" style="display:block;padding:8px 16px;">Ingen grupper</span>`;

  // Skriv til alle containere (desktop + mobil)
  document.querySelectorAll('.js-gruppe-alle').forEach(c => c.innerHTML = htmlAlle);
  document.querySelectorAll('.js-gruppe-matrikel').forEach(c => c.innerHTML = htmlMatrikel);
  document.querySelectorAll('.js-gruppe-alle-alltime').forEach(c => c.innerHTML = htmlAlleAllTime);
  document.querySelectorAll('.js-gruppe-matrikel-alltime').forEach(c => c.innerHTML = htmlMatrikelAllTime);
}

// -----------------------------------------------------
// 6) Indhold: Lokalafdelinger (krydslister + matrikellister)
// -----------------------------------------------------
export function addAfdelingerLinks(afdelinger) {
  // Normaliser: accepter enten ["Østjylland", …] eller [{navn:"Østjylland"}]
  const norm = (a) =>
    (typeof a === 'string'
      ? { navn: a }
      : { navn: a.navn ?? a.name ?? a.title ?? String(a) });

  const items = (Array.isArray(afdelinger) ? afdelinger : [])
    .map(norm)
    .filter(x => x.navn && x.navn.trim() !== '');

  const htmlAlle = items.length
    ? items.map(it => {
        const enc = encodeURIComponent(it.navn);
        return linkHTML(`?scope=lokal_alle&afdeling=${enc}`, it.navn);
      }).join('')
    : `<span class="muted" style="display:block;padding:8px 16px;">Ingen lokalafdelinger</span>`;

  const htmlAlleAllTime = items.length
    ? items.map(it => {
        const enc = encodeURIComponent(it.navn);
        return linkHTML(`?scope=lokal_alle&afdeling=${enc}&aar=global`, it.navn);
      }).join('')
    : `<span class="muted" style="display:block;padding:8px 16px;">Ingen lokalafdelinger</span>`;

  const htmlMatrikel = items.length
    ? items.map(it => {
        const enc = encodeURIComponent(it.navn);
        return linkHTML(`?scope=lokal_matrikel&afdeling=${enc}`, it.navn);
      }).join('')
    : `<span class="muted" style="display:block;padding:8px 16px;">Ingen lokalafdelinger</span>`;

  const htmlMatrikelAllTime = items.length
    ? items.map(it => {
        const enc = encodeURIComponent(it.navn);
        return linkHTML(`?scope=lokal_matrikel&afdeling=${enc}&aar=global`, it.navn);
      }).join('')
    : `<span class="muted" style="display:block;padding:8px 16px;">Ingen lokalafdelinger</span>`;

  // Skriv til alle containere (desktop + mobil)
  document.querySelectorAll('.js-afdeling-alle').forEach(c => c.innerHTML = htmlAlle);
  document.querySelectorAll('.js-afdeling-matrikel').forEach(c => c.innerHTML = htmlMatrikel);
  document.querySelectorAll('.js-afdeling-alle-alltime').forEach(c => c.innerHTML = htmlAlleAllTime);
  document.querySelectorAll('.js-afdeling-matrikel-alltime').forEach(c => c.innerHTML = htmlMatrikelAllTime);
}

export function addKommuneLinks(kommuner) {
  const items = (Array.isArray(kommuner) ? kommuner : [])
    .map(k => ({ id: String(k?.id || ''), navn: String(k?.navn || '').trim() }))
    .filter(k => k.id && k.navn);

  const htmlAlle = items.length
    ? items.map(it => linkHTML(`?scope=kommune_alle&kommune=${encodeURIComponent(it.id)}&kommune_navn=${encodeURIComponent(it.navn)}`, it.navn)).join('')
    : `<span class="muted" style="display:block;padding:8px 16px;">Ingen kommuner valgt</span>`;

  const htmlAlleAllTime = items.length
    ? items.map(it => linkHTML(`?scope=kommune_alle&kommune=${encodeURIComponent(it.id)}&kommune_navn=${encodeURIComponent(it.navn)}&aar=global`, it.navn)).join('')
    : `<span class="muted" style="display:block;padding:8px 16px;">Ingen kommuner valgt</span>`;

  const htmlMatrikel = items.length
    ? items.map(it => linkHTML(`?scope=kommune_matrikel&kommune=${encodeURIComponent(it.id)}&kommune_navn=${encodeURIComponent(it.navn)}`, it.navn)).join('')
    : `<span class="muted" style="display:block;padding:8px 16px;">Ingen kommuner valgt</span>`;

  const htmlMatrikelAllTime = items.length
    ? items.map(it => linkHTML(`?scope=kommune_matrikel&kommune=${encodeURIComponent(it.id)}&kommune_navn=${encodeURIComponent(it.navn)}&aar=global`, it.navn)).join('')
    : `<span class="muted" style="display:block;padding:8px 16px;">Ingen kommuner valgt</span>`;

  document.querySelectorAll('.js-kommune-alle').forEach(c => c.innerHTML = htmlAlle);
  document.querySelectorAll('.js-kommune-alle-alltime').forEach(c => c.innerHTML = htmlAlleAllTime);
  document.querySelectorAll('.js-kommune-matrikel').forEach(c => c.innerHTML = htmlMatrikel);
  document.querySelectorAll('.js-kommune-matrikel-alltime').forEach(c => c.innerHTML = htmlMatrikelAllTime);
}
