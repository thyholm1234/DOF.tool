// Version: 1.13.16 - 2026-05-15 23.55.20
// © Christian Vemmelund Helligsø


// Navbar
import { renderNavbar, initNavbar, initMobileNavbar, addGruppeLinks } from './navbar.js';
renderNavbar();
initNavbar();
initMobileNavbar();

// ---------- Global state ----------
let lastScoreboardParams = null;
let firstsSortMode = "alphabetical"; // "alphabetical" | "newest" | "oldest"
let cachedGlobalYear = null;
let selfObserkode = null;
let kommuneNameMap = null;
let speciesStyleMap = null;

function escapeHtml(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

async function ensureSpeciesStyleMap() {
  if (speciesStyleMap) return speciesStyleMap;
  speciesStyleMap = new Map();
  try {
    const res = await fetch('/api/species_styles');
    if (!res.ok) return speciesStyleMap;
    const data = await res.json();
    const styles = data && typeof data.styles === 'object' && data.styles !== null ? data.styles : {};
    Object.entries(styles).forEach(([name, kind]) => {
      const key = String(name || '').trim().toLocaleLowerCase();
      if (!key) return;
      const normalizedKind = String(kind || 'normal').toLowerCase();
      if (normalizedKind !== 'su' && normalizedKind !== 'subart') return;
      speciesStyleMap.set(key, normalizedKind);
    });
  } catch (_) {
    return speciesStyleMap;
  }
  return speciesStyleMap;
}

function renderSpeciesLabelHtml(name) {
  const safeName = escapeHtml(name);
  const kind = speciesStyleMap?.get(String(name || '').trim().toLocaleLowerCase()) || 'normal';
  return `<span class="species-name species-name--${kind}">${safeName}</span>`;
}

// Snapshot af originale data pr. scope/gruppe (til robust filtrering)
let masterData = null;            // { rows, koder, matrix, totals, arter }
let lastSelectedKoder = null;     // Husk sidste brugervalg i filteret

async function ensureSelfObserkode() {
  if (selfObserkode) return selfObserkode;
  try {
    const res = await fetch('/api/profile_data');
    if (!res.ok) return null;
    const data = await res.json();
    selfObserkode = String(data?.user?.obserkode || data?.obserkode || '').trim();
    return selfObserkode || null;
  } catch (_) {
    return null;
  }
}

function renderScoreboardCards(rows) {
  const ownCode = String(selfObserkode || '').trim().toUpperCase();
  return (Array.isArray(rows) ? rows : []).map(row => {
    const rowCode = String(row?.obserkode || '').trim().toUpperCase();
    const ownClass = ownCode && rowCode === ownCode ? ' user-card-self' : '';
    return `
      <div class="user-card${ownClass}" data-obserkode="${row.obserkode}">
        <strong>#${row.placering} ${row.navn}</strong><br>
        Antal arter: ${row.antal_arter}<br>
        Sidste art: ${row.sidste_art ? row.sidste_art + (row.sidste_dato ? " (" + row.sidste_dato + ")" : "") : ""}
      </div>
    `;
  }).join("");
}

function parseDmyToTime(value) {
  const parts = (value || "").split("-");
  if (parts.length !== 3) return 0;
  const day = parseInt(parts[0], 10);
  const month = parseInt(parts[1], 10);
  const year = parseInt(parts[2], 10);
  if (!day || !month || !year) return 0;
  const date = new Date(year, month - 1, day);
  const time = date.getTime();
  return Number.isNaN(time) ? 0 : time;
}

function parseDmyToDate(value) {
  const parts = String(value || '').split('-');
  if (parts.length !== 3) return null;
  const day = Number.parseInt(parts[0], 10);
  const month = Number.parseInt(parts[1], 10);
  const year = Number.parseInt(parts[2], 10);
  if (!Number.isFinite(day) || !Number.isFinite(month) || !Number.isFinite(year)) return null;
  const date = new Date(year, month - 1, day);
  if (Number.isNaN(date.getTime())) return null;
  return date;
}

function dateToDmy(dateObj) {
  const dd = String(dateObj.getDate()).padStart(2, '0');
  const mm = String(dateObj.getMonth() + 1).padStart(2, '0');
  const yyyy = String(dateObj.getFullYear());
  return `${dd}-${mm}-${yyyy}`;
}

function dateToIso(dateObj) {
  const yyyy = String(dateObj.getFullYear());
  const mm = String(dateObj.getMonth() + 1).padStart(2, '0');
  const dd = String(dateObj.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

function buildDailyDmyRange(startDate, endDate) {
  const labels = [];
  const cursor = new Date(startDate.getFullYear(), startDate.getMonth(), startDate.getDate());
  while (cursor <= endDate) {
    labels.push(dateToDmy(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }
  return labels;
}

function parseDmyLabelParts(label) {
  const parts = String(label || '').split('-');
  if (parts.length !== 3) return null;
  const day = Number.parseInt(parts[0], 10);
  const month = Number.parseInt(parts[1], 10);
  const year = Number.parseInt(parts[2], 10);
  if (!Number.isFinite(day) || !Number.isFinite(month) || !Number.isFinite(year)) return null;
  return { day, month, year };
}

function createDailyXAxisOptions(labels) {
  const parsed = (labels || []).map(parseDmyLabelParts).filter(Boolean);
  const spanDays = Math.max(1, parsed.length);
  const spanYearsApprox = spanDays / 365.25;
  const years = parsed.map(p => p.year);
  const minYear = years.length ? Math.min(...years) : new Date().getFullYear();
  const maxYear = years.length ? Math.max(...years) : minYear;
  const currentYear = new Date().getFullYear();
  const overFiveYears = spanYearsApprox > 5;

  const gridRules = {
    showDaily: spanYearsApprox <= 1,
    showWeekly: spanYearsApprox > 1 && spanYearsApprox <= 2,
    showMonthly: spanYearsApprox <= 5,
    showYearly: true,
  };

  const isWeekBoundary = (parts) => {
    const date = new Date(parts.year, parts.month - 1, parts.day);
    return date.getDay() === 1;
  };

  const shouldShowYear = (year, day, month) => {
    if (day !== 1 || month !== 1) return false;
    
    const displayMaxYear = (maxYear === currentYear) ? null : maxYear;
    
    if (overFiveYears) {
      return year % 5 === 0 || year === minYear || year === displayMaxYear;
    }
    if (spanYearsApprox > 1) {
      return year === minYear || year === displayMaxYear;
    }
    return year === minYear;
  };

  return {
    title: { display: true, text: 'Dato' },
    ticks: {
      autoSkip: false,
      maxRotation: 0,
      minRotation: 0,
      callback: function(value) {
        const label = this.getLabelForValue(value);
        const parts = parseDmyLabelParts(label);
        if (!parts) return '';
        if (overFiveYears) {
          if (shouldShowYear(parts.year, parts.day, parts.month)) {
            return String(parts.year);
          }
          return '';
        }
        if (parts.day !== 1) return '';
        const monthNames = ['jan', 'feb', 'mar', 'maj', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'dec'];
        const monthName = monthNames[Math.max(0, Math.min(11, parts.month - 1))];
        return parts.month === 1 && shouldShowYear(parts.year, parts.day, parts.month) ? `${monthName} ${parts.year}` : monthName;
      }
    },
    grid: {
      color: (ctx) => {
        const idx = ctx.tick?.value;
        const label = (typeof idx === 'number') ? labels[idx] : null;
        const parts = parseDmyLabelParts(label);
        if (!parts) return 'rgba(0,0,0,0.08)';

        const isYearBoundary = parts.day === 1 && parts.month === 1;
        const isMonthBoundary = parts.day === 1;
        const isWeekLine = isWeekBoundary(parts);

        if (gridRules.showYearly && isYearBoundary) {
          if (overFiveYears && parts.year % 5 === 0) return 'rgba(0,0,0,0.35)';
          return overFiveYears ? 'rgba(0,0,0,0.20)' : 'rgba(0,0,0,0.30)';
        }
        if (gridRules.showMonthly && isMonthBoundary) return 'rgba(0,0,0,0.16)';
        if (gridRules.showWeekly && isWeekLine) return 'rgba(0,0,0,0.10)';
        if (gridRules.showDaily) return 'rgba(0,0,0,0.04)';
        return 'rgba(0,0,0,0)';
      }
    }
  };
}

async function ensureGlobalYear() {
  if (cachedGlobalYear) return cachedGlobalYear;
  try {
    const res = await fetch('/api/get_year');
    const data = await res.json();
    if (data && data.year) {
      cachedGlobalYear = Number(data.year);
      return cachedGlobalYear;
    }
  } catch (err) {
    console.warn('Kunne ikke hente aktuelt år:', err);
  }
  cachedGlobalYear = new Date().getFullYear();
  return cachedGlobalYear;
}

function shouldShowYearSelector(params) {
  return String(params.aar) !== "global";
}

function getSelectedYear(params) {
  const parsed = Number.parseInt(params.aar, 10);
  if (!Number.isNaN(parsed) && String(params.aar) !== "global") return parsed;
  if (cachedGlobalYear) return cachedGlobalYear;
  return new Date().getFullYear();
}

function createYearSelector(params) {
  if (!shouldShowYearSelector(params)) return null;

  const selectedYear = getSelectedYear(params);
  const minYear = 1950;
  const maxYear = new Date().getFullYear();
  const years = [];
  for (let y = maxYear; y >= minYear; y--) years.push(y);

  const wrap = document.createElement("div");
  wrap.id = "yearSelectWrap";
  wrap.style.display = "flex";
  wrap.style.alignItems = "center";
  wrap.style.gap = "0.5em";

  const label = document.createElement("label");
  label.htmlFor = "yearSelect";
  label.textContent = "År:";

  const select = document.createElement("select");
  select.id = "yearSelect";
  select.style.padding = "0.4em 0.6em";
  select.innerHTML = years
    .map(y => `<option value="${y}" ${y === selectedYear ? "selected" : ""}>${y}</option>`)
    .join("\n");

  select.addEventListener("change", () => {
    const urlParams = new URLSearchParams(window.location.search);
    urlParams.set("aar", select.value);
    window.history.replaceState({}, "", "?" + urlParams.toString());
    visSide();
  });

  wrap.appendChild(label);
  wrap.appendChild(select);
  return wrap;
}

function insertYearSelectorRow(params, container) {
  if (!container) return;
  const existing = container.querySelector('#scoreboard-year-row');
  if (existing) existing.remove();

  const yearSelector = createYearSelector(params);
  if (!yearSelector) return;

  const row = document.createElement("div");
  row.id = "scoreboard-year-row";
  row.style.display = "flex";
  row.style.justifyContent = "center";
  row.style.margin = "1em 0";
  row.appendChild(yearSelector);

  container.prepend(row);
}

// Hent grupper til navbar
fetch('/api/get_grupper')
  .then(res => res.json())
  .then(grupper => { addGruppeLinks(grupper); });

// ---------- URL utils ----------
function getParams() {
  const params = {};
  const qs = window.location.search;
  if (!qs || qs.length <= 1) return params;
  const usp = new URLSearchParams(qs);
  usp.forEach((v, k) => { params[k] = v; });
  return params;
}

// Intern matrix over understøttede query-kombinationer (scope + kontekst + år)
const SCOREBOARD_SCOPE_MATRIX = {
  global_alle: { listType: 'Alle kryds', title: 'National rangliste', userPrefixYear: 'Årsarter for', userPrefixGlobal: 'Arter (alle år) for' },
  global_matrikel: { listType: 'Matrikel', title: 'National matrikel-rangliste', userPrefixYear: 'Matrikelarter for', userPrefixGlobal: 'Matrikelarter (alle år) for' },
  gruppe_alle: { listType: 'Alle kryds', title: 'Gruppe – Rangliste' },
  gruppe_matrikel: { listType: 'Matrikel', title: 'Gruppe – Matrikel-rangliste' },
  lokal_alle: { listType: 'Alle kryds', title: 'Lokalafdeling – Rangliste', userPrefixYear: 'Lokalarter for', userPrefixGlobal: 'Lokalarter (alle år) for' },
  lokal_matrikel: { listType: 'Matrikel', title: 'Lokalafdeling – Matrikel-rangliste' },
  kommune_alle: { listType: 'Alle kryds', title: 'Kommune – Rangliste', userPrefixYear: 'Kommunearter for', userPrefixGlobal: 'Kommunearter (alle år) for' },
  kommune_matrikel: { listType: 'Matrikel', title: 'Kommune – Matrikel-rangliste', userPrefixYear: 'Kommune matrikelarter for', userPrefixGlobal: 'Kommune matrikelarter (alle år) for' },
  user_global: { listType: 'Alle kryds', title: 'Brugerliste', userPrefixYear: 'Årsarter for', userPrefixGlobal: 'Arter (alle år) for' },
  user_matrikel: { listType: 'Matrikel', title: 'Brugerliste – Matrikel' },
  user_lokalafdeling: { listType: 'Alle kryds', title: 'Brugerliste – Lokalafdeling' },
  user_kommune_alle: { listType: 'Alle kryds', title: 'Brugerliste – Kommune' },
  user_kommune_matrikel: { listType: 'Matrikel', title: 'Brugerliste – Kommune matrikel' }
};

function setScoreboardSubtitle(text) {
  const pageTitle = document.getElementById('page-title');
  if (!pageTitle) return;
  const subtitleId = 'scoreboard-subtitle';
  let subtitle = document.getElementById(subtitleId);
  if (!text) {
    if (subtitle) subtitle.remove();
    return;
  }
  if (!subtitle) {
    subtitle = document.createElement('div');
    subtitle.id = subtitleId;
    subtitle.style.fontSize = '1.05em';
    subtitle.style.textAlign = 'center';
    subtitle.style.display = 'block';
    subtitle.style.width = '100%';
    subtitle.style.color = 'var(--text-muted)';
    subtitle.style.marginBottom = '1em';
    pageTitle.insertAdjacentElement('afterend', subtitle);
  }
  subtitle.textContent = text;
}

async function ensureKommuneNameById(kommuneId) {
  const id = String(kommuneId || '').trim();
  if (!id) return null;
  if (!kommuneNameMap) {
    try {
      const res = await fetch('/api/afdelinger');
      if (!res.ok) return null;
      const payload = await res.json();
      kommuneNameMap = new Map((payload?.kommuner || []).map(row => [String(row.id), row.navn]));
    } catch (_) {
      return null;
    }
  }
  return kommuneNameMap.get(id) || null;
}

function buildScoreboardHeading(params) {
  const scope = String(params?.scope || '');
  const meta = SCOREBOARD_SCOPE_MATRIX[scope] || { listType: 'Alle kryds', title: 'Scoreboard' };
  const isAllTime = String(params?.aar || '') === 'global';
  const yearText = isAllTime ? 'Alle år' : `År ${params?.aar || cachedGlobalYear || new Date().getFullYear()}`;

  let title = meta.title;
  if (scope === 'gruppe_alle' || scope === 'gruppe_matrikel') {
    title = params?.gruppe ? `${params.gruppe} – ${scope === 'gruppe_matrikel' ? 'Matrikel-rangliste' : 'Rangliste'}` : meta.title;
  } else if (scope === 'lokal_alle' || scope === 'lokal_matrikel') {
    title = params?.afdeling ? `${params.afdeling} – ${scope === 'lokal_matrikel' ? 'Matrikel-rangliste' : 'Rangliste'}` : meta.title;
  } else if (scope === 'kommune_alle' || scope === 'kommune_matrikel') {
    const kommuneLabel = params?.kommune_navn || params?.kommune || 'Kommune';
    title = `${kommuneLabel} – ${scope === 'kommune_matrikel' ? 'Matrikel-rangliste' : 'Rangliste'}`;
  }

  const subtitleParts = [yearText, meta.listType || 'Alle kryds'];
  if (params?.gruppe) subtitleParts.unshift(`Gruppe: ${params.gruppe}`);
  if (params?.afdeling) subtitleParts.unshift(`Lokalafdeling: ${params.afdeling}`);
  if (params?.kommune_navn) subtitleParts.unshift(`Kommune: ${params.kommune_navn}`);

  return { title, subtitle: subtitleParts.join(' · ') };
}

// ---------- API ----------
async function hentData(params) {
  let url, body;

  if (params.scope && params.scope.startsWith("user_")) {
    // Brugerliste hentes separat i visUserFirsts; vi holder denne for konsistens
    url = "/api/obser";
    body = params;
  } else if (params.scope && params.scope.startsWith("gruppe_")) {
    url = "/api/gruppe_scoreboard";
    body = {
      navn: params.gruppe,
      scope: params.scope,
      aar: params.aar
    };
  } else if (params.scope && params.scope.startsWith("lokal_")) {
    // Hvis du har en separat endpoint til lokal, kan det skiftes her
    url = "/api/scoreboard";
    body = params;
  } else {
    url = "/api/scoreboard";
    body = params;
  }

  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {})
  });
  return await res.json();
}

// ---------- Siderender ----------
async function visSide() {
  const params = getParams();
  await ensureSpeciesStyleMap();
  const data = await hentData(params);

  const container = document.getElementById("main");
  const pageTitle = document.getElementById("page-title");

  // ------- SCOREBOARD -------
  if (!params.scope || !params.scope.startsWith("user_")) {
    await ensureSelfObserkode();
    lastScoreboardParams = params;
    await ensureGlobalYear();

    // Gem et master-snapshot som basis for al filtrering
    masterData = {
      rows: Array.isArray(data.rows) ? [...data.rows] : [],
      koder: Array.isArray(data.koder) ? [...data.koder] :
             (Array.isArray(data.rows) ? data.rows.map(r => r.obserkode) : []),
      matrix: Array.isArray(data.matrix) ? data.matrix.map(r => [...r]) : [],
      totals: Array.isArray(data.totals) ? [...data.totals] : [],
      arter: Array.isArray(data.arter) ? [...data.arter] : [],
      scope_year: params.aar || cachedGlobalYear || new Date().getFullYear(),
      trend_points: data && typeof data.trend_points === 'object' && data.trend_points !== null
        ? JSON.parse(JSON.stringify(data.trend_points))
        : {}
    };

    const heading = buildScoreboardHeading(params);
    setResponsiveTitle(pageTitle, heading.title);
    setScoreboardSubtitle(heading.subtitle);

    const rows = masterData.rows;
    if (!rows.length) {
      container.innerHTML = "<p>Ingen brugere fundet.</p>";
      if (!params.scope || !params.scope.startsWith("gruppe_")) {
        insertYearSelectorRow(params, container);
      }
      clearSections();
      return;
    }

    // Cards
    container.innerHTML = renderScoreboardCards(rows);

    if (!params.scope || !params.scope.startsWith("gruppe_")) {
      insertYearSelectorRow(params, container);
    }

    // Filter-knap for grupper
    if (params.scope && params.scope.startsWith("gruppe_")) {
      tilføjGruppeFilterKnappen(masterData, params, container);
    }

    // Sektioner
    visScoreboardMatrix(masterData);
    visScoreboardBlockers(masterData);
    visScoreboardTrend(masterData);

    // Klik på bruger -> vis liste
    container.querySelectorAll('.user-card').forEach(card => {
      card.onclick = async function () {
        const kode = this.getAttribute('data-obserkode');
        const navn = this.querySelector('strong').innerText;
        const currentYear = new Date().getFullYear();
        const scopeYear = lastScoreboardParams?.aar || cachedGlobalYear || currentYear;
        firstsSortMode = (String(scopeYear) === String(currentYear)) ? "newest" : "alphabetical";
        await visUserFirsts(kode, navn, firstsSortMode, lastScoreboardParams);
      };
    });
  }

  // ------- BRUGERLISTE -------
  else {
    setScoreboardSubtitle('');
    firstsSortMode = params.sort || "alphabetical";
    await visUserFirsts(params.obserkode, params.navn || "", firstsSortMode, params);
  }
}

function setResponsiveTitle(el, title) {
  // Mobilombrydning (<=600px)
  if (window.matchMedia("(max-width: 600px)").matches) {
    let splitIdx = title.indexOf(" – ");
    if (splitIdx === -1) splitIdx = title.indexOf(" - ");
    if (splitIdx === -1 && title.includes(" Matrikel-rangliste")) splitIdx = title.indexOf(" Matrikel-rangliste");
    if (splitIdx === -1 && title.includes(" Rangliste")) splitIdx = title.indexOf(" Rangliste");

    if (splitIdx > 0) {
      const first = title.slice(0, splitIdx).trim();
      const rest = title.slice(splitIdx).replace(/^(\s*[-–]\s*)?/, '').trim();
      el.innerHTML = `<span style="display:block;word-break:break-word;">${first}</span><span style="display:block;word-break:break-word;">${rest}</span>`;
    } else {
      el.innerHTML = `<span style="display:block;word-break:break-word;">${title}</span>`;
    }
    el.style.hyphens = "manual";
    el.style.wordBreak = "break-word";
    el.style.overflowWrap = "break-word";
  } else {
    el.innerHTML = "";
    el.textContent = title;
    el.style.hyphens = "";
    el.style.wordBreak = "";
    el.style.overflowWrap = "";
  }
}

function clearSections() {
  const m = document.getElementById('scoreboard-matrix');
  const b = document.getElementById('scoreboard-blockers');
  const t = document.getElementById('scoreboard-trend');
  if (m) m.innerHTML = "";
  if (b) b.innerHTML = "";
  if (t) t.innerHTML = "";
}

function buildUserYearOptions(selectedValue) {
  const currentYear = new Date().getFullYear();
  const minYear = 1950;
  const selected = String(selectedValue || currentYear);
  const options = [`<option value="global" ${selected === "global" ? "selected" : ""}>Total</option>`];
  for (let year = currentYear; year >= minYear; year--) {
    options.push(`<option value="${year}" ${selected === String(year) ? "selected" : ""}>${year}</option>`);
  }
  return options.join("\n");
}

function buildUserGlobalYearOptions(selectedValue, availableYears) {
  const selected = String(selectedValue || new Date().getFullYear());
  const years = Array.isArray(availableYears) && availableYears.length ? availableYears : [new Date().getFullYear()];
  return years.map(year => `<option value="${year}" ${selected === String(year) ? "selected" : ""}>${year}</option>`).join("\n");
}

function buildUserMatrikelOptions(selectedIndex, availableIndexes) {
  const selected = Number(selectedIndex) || 1;
  const normalized = Array.isArray(availableIndexes)
    ? availableIndexes
      .map(v => Number(v))
      .filter(v => Number.isInteger(v) && v >= 1)
      .sort((a, b) => a - b)
    : [];
  const unique = [...new Set(normalized)];
  const source = unique.length ? unique : [1];
  return source
    .map(index => `<option value="${index}" ${index === selected ? "selected" : ""}>Matrikel ${index}</option>`)
    .join("\n");
}

function renderUserTrendChart(targetId, trendPoints, labelText, selectedYearValue) {
  const target = document.getElementById(targetId);
  if (!target) return;

  const points = Array.isArray(trendPoints) ? trendPoints : [];
  target.innerHTML = `<h3 style="margin:0.6em 0;">Udvikling i sete arter</h3><canvas id="userTrendChart" height="140"></canvas>`;

  if (typeof Chart === "undefined") return;

  const today = new Date();
  const selectedIsGlobal = String(selectedYearValue) === 'global';
  const selectedYear = Number.parseInt(String(selectedYearValue), 10);
  const chartYear = Number.isNaN(selectedYear) ? today.getFullYear() : selectedYear;

  let startDate = new Date(chartYear, 0, 1);
  if (selectedIsGlobal) {
    const firstPositiveDates = points
      .filter(point => Number(point?.count || 0) >= 1)
      .map(point => parseDmyToDate(point?.dato))
      .filter(Boolean)
      .sort((a, b) => a - b);

    if (firstPositiveDates.length) {
      startDate = new Date(firstPositiveDates[0].getTime());
      startDate.setDate(startDate.getDate() - 1);
    } else {
      const pointDates = points
        .map(point => parseDmyToDate(point?.dato))
        .filter(Boolean)
        .sort((a, b) => a - b);
      if (pointDates.length) startDate = pointDates[0];
    }
  }
  const yearEndDate = new Date(chartYear, 11, 31);
  const endDate = selectedIsGlobal 
    ? today 
    : (chartYear < today.getFullYear() 
        ? yearEndDate 
        : (chartYear === today.getFullYear() ? today : yearEndDate));

  if (startDate > endDate) {
    target.innerHTML = "";
    return;
  }

  const toIsoKey = (dateObj) => {
    const y = dateObj.getFullYear();
    const m = String(dateObj.getMonth() + 1).padStart(2, '0');
    const d = String(dateObj.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
  };
  const isoToDmy = (isoKey) => {
    const [y, m, d] = String(isoKey).split('-');
    return `${d}-${m}-${y}`;
  };
  const dmyToIso = (dmy) => {
    const parts = String(dmy || '').split('-');
    if (parts.length !== 3) return null;
    const [d, m, y] = parts;
    if (!d || !m || !y) return null;
    return `${y}-${m.padStart(2, '0')}-${d.padStart(2, '0')}`;
  };

  const startKey = toIsoKey(startDate);
  const endKey = toIsoKey(endDate);

  const countByDay = {};
  points.forEach(point => {
    if (!point || !point.dato) return;
    const isoKey = dmyToIso(point.dato);
    if (!isoKey) return;
    if (isoKey < startKey || isoKey > endKey) return;
    const value = Number(point.count || 0);
    countByDay[isoKey] = Math.max(Number(countByDay[isoKey] || 0), value);
  });

  const labels = [];
  const values = [];
  let currentValue = 0;
  const cursor = new Date(startDate.getTime());
  while (cursor <= endDate) {
    const key = toIsoKey(cursor);
    if (Object.prototype.hasOwnProperty.call(countByDay, key)) {
      currentValue = countByDay[key];
    }
    labels.push(isoToDmy(key));
    values.push(currentValue);
    cursor.setDate(cursor.getDate() + 1);
  }

  if (!labels.length) {
    target.innerHTML = "";
    return;
  }

  new Chart(document.getElementById('userTrendChart').getContext('2d'), {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: labelText || 'Arter',
        data: values,
        borderColor: 'hsl(210,70%,45%)',
        fill: false,
        tension: 0
      }]
    },
    options: {
      elements: {
        point: {
          radius: 0,
          hoverRadius: 0
        }
      },
      plugins: { legend: { display: false } },
      scales: {
        x: createDailyXAxisOptions(labels),
        y: { title: { display: true, text: 'Antal arter' }, beginAtZero: true }
      }
    }
  });
}

function buildTrendPointsFromFirsts(firsts) {
  const sorted = Array.isArray(firsts) ? [...firsts] : [];
  sorted.sort((a, b) => parseDmyToTime(a?.dato) - parseDmyToTime(b?.dato));
  const trendPoints = [];
  sorted.forEach((item, idx) => {
    if (!item?.dato) return;
    trendPoints.push({
      dato: item.dato,
      count: idx + 1
    });
  });
  return trendPoints;
}

function normalizeDisplayName(navn, obserkode) {
  const raw = String(navn || '').trim();
  if (!raw) return obserkode;
  const withoutRank = raw.replace(/^#\d+\s*/i, '').trim();
  return withoutRank || obserkode;
}

function buildUserScopeSubtitle({ apiScope, scope, gruppe, afdeling, kommune, kommuneNavn, aar, matrikelIndex }) {
  const parts = [];
  const isAllTime = String(aar) === 'global';

  if (kommuneNavn) {
    parts.push(`${kommuneNavn} Kommune`);
  } else if (kommune) {
    parts.push(`Kommune ${kommune}`);
  }

  if (apiScope === 'user_lokalafdeling' && afdeling) {
    parts.push(`Lokalafdeling: ${afdeling}`);
  }

  if (String(scope).startsWith('gruppe') && gruppe) {
    parts.push(`Gruppe: ${gruppe}`);
  }

  if (apiScope === 'user_matrikel') {
    parts.push(`Matrikel ${matrikelIndex}`);
  }

  parts.push(isAllTime ? 'Alle år' : `År ${aar}`);

  if (apiScope === 'user_kommune_matrikel' || (apiScope === 'user_matrikel' && matrikelIndex >= 1)) {
    parts.push('Matrikel');
  } else {
    parts.push('Alle kryds');
  }

  return parts.join(' · ');
}

function buildUserHeading({ apiScope, visNavn, aar, gruppe, afdeling, kommune, kommuneNavn, matrikelIndex }) {
  const meta = SCOREBOARD_SCOPE_MATRIX[apiScope] || SCOREBOARD_SCOPE_MATRIX.user_global;
  const isAllTime = String(aar) === 'global';
  const prefix = isAllTime
    ? (meta.userPrefixGlobal || 'Første observationer for')
    : (meta.userPrefixYear || 'Første observationer for');
  const title = `${prefix} ${visNavn}`;
  const subtitle = buildUserScopeSubtitle({
    apiScope,
    scope: apiScope,
    gruppe,
    afdeling,
    kommune,
    kommuneNavn,
    aar,
    matrikelIndex,
  });
  return { title, subtitle };
}

function buildReturnScoreboardUrl(parentParams = {}, apiScope = 'user_global') {
  const sourceScope = String(parentParams?.scope || apiScope || 'user_global');
  const isUserScope = sourceScope.startsWith('user_');

  let targetScope = sourceScope;
  if (isUserScope) {
    if (sourceScope === 'user_kommune_matrikel') targetScope = 'kommune_matrikel';
    else if (sourceScope === 'user_kommune_alle') targetScope = 'kommune_alle';
    else if (sourceScope === 'user_lokalafdeling') targetScope = 'lokal_alle';
    else if (sourceScope === 'user_matrikel') targetScope = 'global_matrikel';
    else targetScope = 'global_alle';

    if (parentParams.gruppe) {
      targetScope = sourceScope.includes('matrikel') ? 'gruppe_matrikel' : 'gruppe_alle';
    } else if (parentParams.afdeling) {
      targetScope = sourceScope.includes('matrikel') ? 'lokal_matrikel' : 'lokal_alle';
    } else if (parentParams.kommune) {
      targetScope = sourceScope.includes('matrikel') ? 'kommune_matrikel' : 'kommune_alle';
    }
  }

  const url = new URL(window.location.pathname, window.location.origin);
  url.searchParams.set('scope', targetScope);

  if (parentParams.gruppe) url.searchParams.set('gruppe', parentParams.gruppe);
  if (parentParams.afdeling) url.searchParams.set('afdeling', parentParams.afdeling);
  if (parentParams.kommune) url.searchParams.set('kommune', parentParams.kommune);
  if (parentParams.kommune_navn) url.searchParams.set('kommune_navn', parentParams.kommune_navn);
  if (parentParams.aar) url.searchParams.set('aar', parentParams.aar);

  return url.pathname + url.search;
}

// ---------- Brugerliste ----------
async function visUserFirsts(obserkode, navn, sortMode = firstsSortMode, parentParams = {}) {
  clearSections();

  // Parent scope -> vælg API-scope
  const scope = parentParams.scope || 'global_alle';
  const gruppe = parentParams.gruppe;
  const afdeling = parentParams.afdeling;
  const kommune = parentParams.kommune;
  const kommuneNavn = parentParams.kommune_navn;
  const matrikel = parentParams.matrikel || parentParams.matrikel_index || 1;
  const period = parentParams.period;
  const aar = parentParams.aar || new Date().getFullYear();
  const matrikelIndex = Number(matrikel) || 1;
  const isPersonalMatrikelPage = scope === "user_matrikel";
  const isExtraMatrikelView = scope.endsWith("matrikel") && matrikelIndex > 1;
  const showPersonalMatrikelControls = isPersonalMatrikelPage;

  let apiScope = "user_global";
  const body = { scope: apiScope, obserkode };

  if (scope.endsWith("matrikel")) {
    apiScope = "user_matrikel";
    body.scope = apiScope;
    if (showPersonalMatrikelControls || isExtraMatrikelView) {
      body.matrikel = matrikelIndex;
      if (period) body.period = period;
    }
  } else if (scope.startsWith("lokal")) {
    apiScope = "user_lokalafdeling";
    body.scope = apiScope;
    if (afdeling) body.afdeling = afdeling;
  } else if (scope.startsWith("kommune")) {
    apiScope = scope === "kommune_matrikel" ? "user_kommune_matrikel" : "user_kommune_alle";
    body.scope = apiScope;
    if (kommune) body.kommune = kommune;
  }
  if (aar) body.aar = aar;

  // Opdater URL (så back/forward virker)
  const urlParams = new URLSearchParams({
    scope: apiScope,
    obserkode: obserkode,
    navn: navn,
    sort: sortMode
  });
  if (gruppe) urlParams.set('gruppe', gruppe);
  if (afdeling) urlParams.set('afdeling', afdeling);
  if (kommune) urlParams.set('kommune', kommune);
  if (aar) urlParams.set('aar', aar);
  if (scope.endsWith("matrikel") && (showPersonalMatrikelControls || isExtraMatrikelView)) {
    urlParams.set('matrikel', String(matrikelIndex));
    if (period) urlParams.set('period', period);
  }
  window.history.pushState({}, '', '?' + urlParams.toString());

  // Hent data
  const res = await fetch('/api/obser', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  const data = await res.json();
  const firsts = Array.isArray(data.firsts) ? data.firsts : [];
  const availableMatrikler = Array.isArray(data.available_matrikler)
    ? data.available_matrikler
      .map(v => Number(v))
      .filter(v => Number.isInteger(v) && v >= 1)
      .sort((a, b) => a - b)
    : [];
  const availableYears = Array.isArray(data.available_years)
    ? data.available_years.filter(v => Number.isInteger(v))
    : [];
  const periodOptions = showPersonalMatrikelControls && Array.isArray(data.period_options) ? data.period_options : [];
  const selectedPeriodName = showPersonalMatrikelControls ? (data.selected_period_name || "") : "";
  const activePeriodName = showPersonalMatrikelControls ? (data.active_period_name || "") : "";
  const showPersonalTrend = apiScope.startsWith("user_");
  const trendPointsForGraph = (showPersonalMatrikelControls && Array.isArray(data.trend_points))
    ? data.trend_points
    : buildTrendPointsFromFirsts(firsts);
  const showYearSelector = (apiScope === 'user_global' && String(aar) !== 'global' && availableYears.length > 0);

  // Render
  const container = document.getElementById("main");
  const pageTitle = document.getElementById("page-title");
  let resolvedKommuneNavn = kommuneNavn;
  if (!resolvedKommuneNavn && kommune) {
    resolvedKommuneNavn = await ensureKommuneNameById(kommune);
  }

  const visNavn = normalizeDisplayName(navn, obserkode);
  const userHeading = buildUserHeading({
    apiScope,
    visNavn,
    aar,
    gruppe,
    afdeling,
    kommune,
    kommuneNavn: resolvedKommuneNavn,
    matrikelIndex,
  });
  pageTitle.textContent = userHeading.title;
  setScoreboardSubtitle(userHeading.subtitle);

  let sortLabel = "";
  if (sortMode === "alphabetical") sortLabel = "Alfabetisk";
  if (sortMode === "newest") sortLabel = "Nyeste";
  if (sortMode === "oldest") sortLabel = "Ældste";

  const statistikLink = `statistik.html?obserkode=${encodeURIComponent(obserkode)}`;

  const matrikelControlHtml = showPersonalMatrikelControls ? `
    <label for="userMatrikelSelect">Matrikel:</label>
    <select id="userMatrikelSelect" style="padding:0.4em 0.6em;">
      ${buildUserMatrikelOptions(matrikelIndex, availableMatrikler)}
    </select>
  ` : "";

  const yearControlHtml = (showPersonalMatrikelControls || showYearSelector) ? `
    <label for="userYearSelect">År:</label>
    <select id="userYearSelect" style="padding:0.4em 0.6em;">
      ${showYearSelector ? buildUserGlobalYearOptions(aar, availableYears) : buildUserYearOptions(aar)}
    </select>
  ` : "";

  const periodControlHtml = showPersonalMatrikelControls && periodOptions.length
    ? `
      <label for="userPeriodSelect">Periode:</label>
      <select id="userPeriodSelect" style="padding:0.4em 0.6em;max-width:260px;">
        ${periodOptions.map(p => {
          const name = p.name || "Periode";
          const selected = selectedPeriodName && name === selectedPeriodName;
          return `<option value="${name}" ${selected ? "selected" : ""}>${name}</option>`;
        }).join("\n")}
      </select>
    `
    : "";

  let html = `
    ${(showPersonalMatrikelControls || showYearSelector) ? `<div style="display:flex;flex-wrap:wrap;gap:0.5em;align-items:center;margin-bottom:0.8em;">${matrikelControlHtml}${yearControlHtml}${periodControlHtml}</div>` : ""}
    ${showPersonalMatrikelControls && selectedPeriodName ? `<div style="font-size:0.95em;color:var(--text-muted);margin-bottom:0.8em;">Valgt periode: <b>${selectedPeriodName}</b>${activePeriodName && activePeriodName !== selectedPeriodName ? ` (Aktuel: ${activePeriodName})` : ""}</div>` : ""}
    ${showPersonalTrend ? `<div id="userTrendWrap"></div>` : ""}
    <div style="display:flex;flex-wrap:wrap;gap:0.4em;margin:0.8em 0 1em;">
      <button id="sortBtn" type="button">Sortering: ${sortLabel}</button>
      <button id="statistikBtn" type="button">Observatør statistik</button>
    </div>
  `;
  if (!firsts.length) {
    html += "<p>Ingen observationer fundet.</p><button id='tilbageBtn'>Tilbage</button>";
    container.innerHTML = html;
  } else {
    html += `<div id="firstsCards"></div><button id="tilbageBtn">Tilbage</button>`;
    container.innerHTML = html;
    renderFirsts(firsts, sortMode, apiScope);
  }

  if (showPersonalTrend) {
    renderUserTrendChart("userTrendWrap", trendPointsForGraph, visNavn, aar);
  }

  const userMatrikelSelect = showPersonalMatrikelControls ? document.getElementById('userMatrikelSelect') : null;
  if (userMatrikelSelect) {
    userMatrikelSelect.onchange = () => {
      const nextParams = { ...parentParams, scope: 'user_matrikel', matrikel: userMatrikelSelect.value };
      delete nextParams.period;
      visUserFirsts(obserkode, navn, sortMode, nextParams);
    };
  }

  const userYearSelect = (showPersonalMatrikelControls || showYearSelector) ? document.getElementById('userYearSelect') : null;
  if (userYearSelect) {
    userYearSelect.onchange = () => {
      const nextParams = { ...parentParams, aar: userYearSelect.value };
      if (showPersonalMatrikelControls && apiScope === 'user_matrikel' && nextParams.scope && String(nextParams.scope).startsWith('user_')) {
        nextParams.scope = 'user_matrikel';
      }
      visUserFirsts(obserkode, navn, sortMode, nextParams);
    };
  }

  const userPeriodSelect = showPersonalMatrikelControls ? document.getElementById('userPeriodSelect') : null;
  if (userPeriodSelect) {
    userPeriodSelect.onchange = () => {
      const nextParams = { ...parentParams, period: userPeriodSelect.value };
      if (showPersonalMatrikelControls && apiScope === 'user_matrikel' && nextParams.scope && String(nextParams.scope).startsWith('user_')) {
        nextParams.scope = 'user_matrikel';
      }
      visUserFirsts(obserkode, navn, sortMode, nextParams);
    };
  }

  // Tilbage -> genskab scoreboard
  document.getElementById('tilbageBtn').onclick = () => {
    const returnUrl = buildReturnScoreboardUrl(parentParams, apiScope);
    window.history.replaceState({}, '', returnUrl);
    firstsSortMode = "alphabetical";
    visSide();
  };

  // Togle-sort
  document.getElementById('sortBtn').onclick = () => {
    if (sortMode === "alphabetical") sortMode = "newest";
    else if (sortMode === "newest") sortMode = "oldest";
    else sortMode = "alphabetical";
    firstsSortMode = sortMode;
    visUserFirsts(obserkode, navn, sortMode, parentParams);
  };

  const statistikBtn = document.getElementById('statistikBtn');
  if (statistikBtn) {
    statistikBtn.onclick = () => {
      window.location.href = statistikLink;
    };
  }
}

// Sortér og vis cards
function renderFirsts(firsts, sortMode, scope) {
  let sorted = [...firsts];
  if (sortMode === "alphabetical") {
    sorted.sort((a, b) => a.artnavn.localeCompare(b.artnavn));
  } else if (sortMode === "newest") {
    sorted.sort((a, b) => parseDmyToTime(b.dato) - parseDmyToTime(a.dato));
  } else if (sortMode === "oldest") {
    sorted.sort((a, b) => parseDmyToTime(a.dato) - parseDmyToTime(b.dato));
  }
  const hideLokalitet = scope === "user_matrikel";
  const buildObsLink = (obsid) => {
    const cleanObsid = String(obsid || '').trim();
    if (!cleanObsid) return '';
    return `https://dofbasen.dk/popobs.php?obsid=${encodeURIComponent(cleanObsid)}&summering=tur&obs=obs`;
  };
  const cards = sorted.map(f => {
    const obsLink = buildObsLink(f.obsid);
    return `
      <div class="user-card user-card-compact${obsLink ? ' user-card--has-link' : ''}"${obsLink ? ` data-obs-link="${escapeHtml(obsLink)}"` : ''}>
        <div><strong>${renderSpeciesLabelHtml(f.artnavn)}</strong></div>
        ${!hideLokalitet ? `<div>Lokalitet: ${escapeHtml(f.lokalitet)}</div>` : ""}
        <div>Dato: ${escapeHtml(f.dato)}</div>
      </div>
    `;
  }).join("");
  const target = document.getElementById("firstsCards");
  if (target) {
    target.innerHTML = cards;
    target.querySelectorAll('.user-card--has-link').forEach(card => {
      card.onclick = () => {
        const obsLink = card.getAttribute('data-obs-link');
        if (obsLink) {
          window.open(obsLink, '_blank', 'noopener,noreferrer');
        }
      };
    });
  }
}

// ---------- Matrix / Blockers / Trend ----------
function visScoreboardMatrix(data) {
  const matrixDiv = document.getElementById('scoreboard-matrix');
  if (!matrixDiv) return;

  const arter = Array.isArray(data.arter) ? data.arter : [];
  const koder = Array.isArray(data.koder) ? data.koder : [];
  const matrix = Array.isArray(data.matrix) ? data.matrix : [];

  if (!arter.length || !koder.length || !matrix.length) {
    matrixDiv.innerHTML = "";
    return;
  }

  const sortedKoder = sortKoderByPlacering(data);

  // Tilføj matrix-table--plain hvis kun én kode vises
  const plainClass = (sortedKoder.length === 1) ? "matrix-table--plain" : "";

  let html = `<div class="matrix-table-wrap"><table class="matrix-table ${plainClass}"><thead><tr>`;
  html += `<th>#</th><th class="matrix-art-header" style="white-space:nowrap;cursor:pointer" title="Vis alle observatører">Art</th>`;
  sortedKoder.forEach(k => 
    html += `<th class="matrix-kode-header" data-obserkode="${k}" style="cursor:pointer">${k}</th>`
  );
  html += `</tr></thead><tbody>`;

  for (let i = 0; i < arter.length; i++) {
    // Tæl forekomster pr. art (antal brugere med dato)
    let antalObservationer = 0;
    sortedKoder.forEach(k => {
      const origIdx = koder.indexOf(k);
      if (origIdx >= 0 && matrix[i] && matrix[i][origIdx]) antalObservationer++;
    });

    // Farvelægning
    let rowClass = "";
    if (antalObservationer >= 8) rowClass = "bg-green";
    else if (antalObservationer >= 5) rowClass = "bg-lightgreen";
    else if (antalObservationer >= 2) rowClass = "bg-orange";
    else rowClass = "bg-red";

    // Fjern farveklasser i single obserkode mode
    if (plainClass) rowClass = "";

    html += `<tr class="${rowClass}"><td>${i + 1}</td><td>${renderSpeciesLabelHtml(arter[i])}</td>`;
    sortedKoder.forEach(k => {
      const origIdx = koder.indexOf(k);
      const val = (origIdx >= 0 && matrix[i]) ? (matrix[i][origIdx] || "") : "";
      html += `<td>${val}</td>`;
    });
    html += `</tr>`;
  }
  html += `</tbody></table></div>`;
  matrixDiv.innerHTML = `<h3>Matrix</h3>${html}`;

  // --- NYT: Klik på kode-header ---

  matrixDiv.querySelectorAll('.matrix-kode-header').forEach(th => {
    th.addEventListener('click', function () {
      // Hvis kun én bruger vises, gå tilbage til masterData
      if (data.koder && data.koder.length === 1) {
        visScoreboardMatrix(masterData);
      } else {
        const kode = this.getAttribute('data-obserkode');
        const filtered = buildSingleUserMatrixData(kode);
        if (filtered) {
          visScoreboardMatrix(filtered);
        }
      }
    });
  });

  const artHeader = matrixDiv.querySelector('.matrix-art-header');
  if (artHeader) {
    artHeader.addEventListener('click', function () {
      visScoreboardMatrix(masterData);
    });
  }
}

function buildSingleUserMatrixData(obserkode) {
  if (!masterData) return null;
  const idx = masterData.koder.indexOf(obserkode);
  if (idx === -1) return null;
  // Find bruger-row
  const row = (masterData.rows || []).find(r => r.obserkode === obserkode);
  if (!row) return null;
  // Filtrer matrix og totals til kun denne bruger
  const koder = [obserkode];
  const rows = [row];
  const matrix = (masterData.matrix || []).map(r => [r[idx]]);
  const totals = [masterData.totals ? masterData.totals[idx] : null];
  // Find arter hvor brugeren har en observation
  const arter = [];
  const matrixFiltered = [];
  for (let i = 0; i < masterData.arter.length; i++) {
    if (matrix[i][0]) {
      arter.push(masterData.arter[i]);
      matrixFiltered.push([matrix[i][0]]);
    }
  }
  // Sortér arter efter dato (nyeste øverst)
  const combined = arter.map((art, i) => ({ art, dato: matrixFiltered[i][0], idx: i }));
  combined.sort((a, b) => parseDmyToTime(b.dato) - parseDmyToTime(a.dato));
  const arterSorted = combined.map(x => x.art);
  const matrixSorted = combined.map(x => [x.dato]);
  return {
    arter: arterSorted,
    koder,
    rows,
    matrix: matrixSorted,
    totals
  };
}

function visScoreboardBlockers(data) {
  const blockersDiv = document.getElementById('scoreboard-blockers');
  if (!blockersDiv) return;

  const arter = Array.isArray(data.arter) ? data.arter : [];
  const koder = Array.isArray(data.koder) ? data.koder : [];
  const matrix = Array.isArray(data.matrix) ? data.matrix : [];
  const totals = Array.isArray(data.totals) ? data.totals : [];

  if (!arter.length || !koder.length || !matrix.length) {
    blockersDiv.innerHTML = "";
    return;
  }

  const sortedKoder = sortKoderByPlacering(data);

  // Blockers-lister
  const blockers = {};
  sortedKoder.forEach(k => blockers[k] = []);

  for (let i = 0; i < arter.length; i++) {
    const seenBy = [];
    sortedKoder.forEach(k => {
      const origIdx = koder.indexOf(k);
      if (origIdx >= 0 && matrix[i] && matrix[i][origIdx]) {
        seenBy.push(k);
      }
    });
    if (seenBy.length === 1) {
      blockers[seenBy[0]].push(arter[i]);
    }
  }

  // Seneste 5 kryds pr. kode
  const latestCrossings = {};
  sortedKoder.forEach(kode => {
    const origIdx = koder.indexOf(kode);
    const kryds = [];
    for (let i = 0; i < arter.length; i++) {
      const val = (origIdx >= 0 && matrix[i]) ? matrix[i][origIdx] : null;
      if (val) kryds.push({ art: arter[i], dato: val });
    }
    kryds.sort((a, b) => parseDmyToTime(b.dato) - parseDmyToTime(a.dato));
    latestCrossings[kode] = kryds.slice(0, 5);
  });

  // Tabel
  let head = `<tr>${sortedKoder.map(k => `<th>${k}</th>`).join("")}</tr>`;
 let totalsRow = `<tr>${sortedKoder.map((k, i) => {
  // Brug i som fallback hvis idx ikke findes
  const idx = koder.indexOf(k);
  let tot = '';
  if (idx >= 0 && Array.isArray(totals) && typeof totals[idx] !== 'undefined') {
    tot = totals[idx];
  } else if (Array.isArray(totals) && typeof totals[i] !== 'undefined') {
    tot = totals[i];
  }
  return `<td><b>Antal:</b> ${tot}</td>`;
}).join("")}</tr>`;

  let blockersCountRow = `<tr>${sortedKoder.map(k => `<td><b>Blockers:</b> ${blockers[k].length}</td>`).join("")}</tr>`;
  let blockersListRow = `<tr>${sortedKoder.map(k => {
    const lines = blockers[k].map(art => renderSpeciesLabelHtml(art)).join('<br>');
    return `<td>${blockers[k].length ? lines : '<span style="color:#888">Ingen</span>'}</td>`;
  }).join("")}</tr>`;
  let latestRow = `<tr>${sortedKoder.map(k => {
    const lines = latestCrossings[k].map(x => `${x.dato}: ${renderSpeciesLabelHtml(x.art)}`).join('<br>');
    return `<td><b>Seneste 5 kryds:</b><br>${lines}</td>`;
  }).join("")}</tr>`;

  const table = `
    <div class="matrix-table-wrap">
      <table class="matrix-table blockers-table" style="margin-top:0px">
        <thead>${head}</thead>
        <tbody>
          ${totalsRow}
          ${blockersCountRow}
          ${blockersListRow}
          ${latestRow}
        </tbody>
      </table>
    </div>`;

  blockersDiv.innerHTML = `<h3>Blockers & Seneste kryds</h3>${table}`;
}

function visScoreboardTrend(data) {
  const trendDiv = document.getElementById('scoreboard-trend');
  if (!trendDiv) return;

  const today = new Date();
  const selectedAarRaw = String(data?.scope_year || '');

  const getDailyAxisLabels = (rawDmyDates, firstPositiveGlobalDate = null) => {
    const validDates = (rawDmyDates || [])
      .map(parseDmyToDate)
      .filter(Boolean)
      .sort((a, b) => a - b);
    if (!validDates.length) return [];

    let startDate = validDates[0];
    let endDate = today;

    const selectedYear = Number.parseInt(selectedAarRaw, 10);
    if (selectedAarRaw && selectedAarRaw !== 'global' && Number.isFinite(selectedYear)) {
      startDate = new Date(selectedYear, 0, 1);
      const selectedYearEnd = new Date(selectedYear, 11, 31);
      endDate = selectedYear < today.getFullYear() ? selectedYearEnd : today;
    } else if (selectedAarRaw === 'global' || firstPositiveGlobalDate) {
      startDate = firstPositiveGlobalDate
        ? new Date(firstPositiveGlobalDate.getTime())
        : new Date(startDate.getTime());
      startDate.setDate(startDate.getDate() - 1);
    } else {
      startDate = new Date(startDate.getTime());
      startDate.setDate(startDate.getDate() - 1);
    }

    if (startDate > endDate) return [];
    return buildDailyDmyRange(startDate, endDate);
  };

  const matrix = Array.isArray(data.matrix) ? data.matrix : [];
  const koderMatrix = Array.isArray(data.koder) ? data.koder : [];
  const arter = Array.isArray(data.arter) ? data.arter : [];

  const trendPoints = data && typeof data.trend_points === 'object' && data.trend_points !== null
    ? data.trend_points
    : null;
  const koderFromRows = Array.isArray(data.rows) ? data.rows.map(row => row.obserkode) : [];

  if (trendPoints && Object.keys(trendPoints).length) {
    const sortedKoder = sortKoderByPlacering(data);
    const koder = sortedKoder.length ? sortedKoder : koderFromRows;

    let firstPositiveGlobalDate = null;
    // Always search for first positive observation for trend_points
    koder.forEach(kode => {
      const points = Array.isArray(trendPoints[kode]) ? trendPoints[kode] : [];
      points.forEach(point => {
        if (!point?.dato) return;
        if (Number(point.count || 0) < 1) return;
        const pointDate = parseDmyToDate(point.dato);
        if (!pointDate) return;
        if (!firstPositiveGlobalDate || pointDate < firstPositiveGlobalDate) {
          firstPositiveGlobalDate = pointDate;
        }
      });
    });

    const allDates = new Set();
    koder.forEach(kode => {
      const points = Array.isArray(trendPoints[kode]) ? trendPoints[kode] : [];
      points.forEach(point => {
        if (!point || !point.dato) return;
        allDates.add(point.dato);
      });
    });

    // Sørg for at brugere uden perioder (ingen trend_points) stadig får gammel adfærd
    for (let i = 0; i < matrix.length; i++) {
      for (let j = 0; j < koderMatrix.length; j++) {
        const d = matrix[i][j];
        if (d) allDates.add(d);
      }
    }

    const sortedDates = getDailyAxisLabels(Array.from(allDates), firstPositiveGlobalDate);

    if (!sortedDates.length || !koder.length) {
      trendDiv.innerHTML = "";
      return;
    }

    const sortedDateSet = new Set(sortedDates);

    const datasets = koder.map((kode, index) => {
      const rawPoints = Array.isArray(trendPoints[kode]) ? trendPoints[kode] : [];

      // Ingen perioder for denne bruger -> brug gammel matrix-baseret progression
      if (!rawPoints.length) {
        const origIdx = koderMatrix.indexOf(kode);
        const seenDates = new Map();
        for (let i = 0; i < matrix.length; i++) {
          const dato = (origIdx >= 0 && matrix[i]) ? matrix[i][origIdx] : null;
          if (dato && sortedDateSet.has(dato)) {
            seenDates.set(dato, (seenDates.get(dato) || 0) + 1);
          }
        }

        let running = 0;
        const values = sortedDates.map(d => {
          running += seenDates.get(d) || 0;
          return running;
        });

        return {
          label: kode,
          data: values,
          borderColor: `hsl(${index * 60},70%,50%)`,
          fill: false,
          tension: 0
        };
      }

      const points = [...rawPoints].sort((left, right) => {
        const [leftDay, leftMonth, leftYear] = String(left?.dato || '').split('-');
        const [rightDay, rightMonth, rightYear] = String(right?.dato || '').split('-');
        return new Date(`${leftYear}-${leftMonth}-${leftDay}`) - new Date(`${rightYear}-${rightMonth}-${rightDay}`);
      });

      const countsByDate = {};
      points.forEach(point => {
        if (!point?.dato) return;
        if (!sortedDateSet.has(point.dato)) return;
        countsByDate[point.dato] = Number(point.count || 0);
      });

      // Find first actual observation to avoid leading zero line
      let current = null;
      const values = sortedDates.map(dateValue => {
        if (Object.prototype.hasOwnProperty.call(countsByDate, dateValue)) {
          current = countsByDate[dateValue];
        }
        // Only return value if we've found a real observation
        return current !== null ? current : null;
      });

      return {
        label: kode,
        data: values,
        borderColor: `hsl(${index * 60},70%,50%)`,
        fill: false,
        tension: 0
      };
    });

    trendDiv.innerHTML = `<h3>Udvikling i sete arter</h3><canvas id="trendChart" height="200"></canvas>`;
    if (typeof Chart !== "undefined") {
      new Chart(document.getElementById('trendChart').getContext('2d'), {
        type: 'line',
        data: { labels: sortedDates, datasets },
        options: {
          elements: {
            point: {
              radius: 0,
              hoverRadius: 0
            }
          },
          plugins: { legend: { display: true } },
          scales: {
            x: createDailyXAxisOptions(sortedDates),
            y: { title: { display: true, text: 'Antal arter' }, beginAtZero: true }
          }
        }
      });
    }
    return;
  }

  const koder = koderMatrix;

  if (!matrix.length || !koder.length || !arter.length) {
    trendDiv.innerHTML = "";
    return;
  }

  const sortedKoder = sortKoderByPlacering(data);

  // Saml alle datoer i matrixen
  const dateSet = new Set();
  for (let i = 0; i < matrix.length; i++) {
    for (let j = 0; j < koder.length; j++) {
      const d = matrix[i][j];
      if (d) dateSet.add(d);
    }
  }

  // Sorter dd-mm-yyyy
  const sortedDates = getDailyAxisLabels(Array.from(dateSet));
  if (!sortedDates.length) {
    trendDiv.innerHTML = "";
    return;
  }

  const sortedDateSet = new Set(sortedDates);
  const datasets = sortedKoder.map((kode, idx) => {
    const origIdx = koder.indexOf(kode);
    const seenDates = new Map();
    for (let i = 0; i < matrix.length; i++) {
      const dato = (origIdx >= 0 && matrix[i]) ? matrix[i][origIdx] : null;
      if (dato && sortedDateSet.has(dato)) {
        seenDates.set(dato, (seenDates.get(dato) || 0) + 1);
      }
    }

    let running = 0;
    const dataPoints = sortedDates.map(d => {
      running += seenDates.get(d) || 0;
      return running;
    });
    return {
      label: kode,
      data: dataPoints,
      borderColor: `hsl(${idx * 60},70%,50%)`,
      fill: false,
      tension: 0
    };
  });

  trendDiv.innerHTML = `<h3>Udvikling i sete arter</h3><canvas id="trendChart" height="200"></canvas>`;
  // Chart forudsætter at Chart.js er inkluderet på siden
  if (typeof Chart !== "undefined") {
    new Chart(document.getElementById('trendChart').getContext('2d'), {
      type: 'line',
      data: { labels: sortedDates, datasets },
      options: {
        elements: {
          point: {
            radius: 0,
            hoverRadius: 0
          }
        },
        plugins: { legend: { display: true } },
        scales: {
          x: createDailyXAxisOptions(sortedDates),
          y: { title: { display: true, text: 'Antal arter' }, beginAtZero: true }
        }
      }
    });
  }
}

// Sortér koder efter placering (brug data.rows til mapping)
function sortKoderByPlacering(data) {
  const rows = Array.isArray(data.rows) ? data.rows : [];
  const koder = Array.isArray(data.koder) ? data.koder : [];
  if (!rows.length || !koder.length) return koder;

  const placeringMap = {};
  rows.forEach(row => placeringMap[row.obserkode] = row.placering);
  return [...koder].sort((a, b) => (placeringMap[a] ?? 999) - (placeringMap[b] ?? 999));
}

// ---------- Filtrering (robust) ----------
function buildFilteredData(selectedKoder) {
  if (!masterData) return null;

  const selectedSet = new Set(selectedKoder);
  // Filtrér rows ud fra master.rows
  const rows = (masterData.rows || []).filter(r => selectedSet.has(r.obserkode));

  // Sortér efter placering for konsistent visning/kolonner
  rows.sort((a, b) => (a.placering ?? 999) - (b.placering ?? 999));

  // Koder i samme rækkefølge som rows
  const koder = rows.map(r => r.obserkode);

  // Map til originale kolonneindekser (fra masterData.koder)
  const colIdxs = koder.map(k => masterData.koder.indexOf(k)).filter(idx => idx >= 0);

  // Skær matrix og totals ud efter korrekte kolonner
  const matrix = (masterData.matrix || []).map(r => colIdxs.map(idx => r[idx]));
  const totals = colIdxs.map(idx => (masterData.totals ? masterData.totals[idx] : null));

  const trendPoints = {};
  if (masterData.trend_points && typeof masterData.trend_points === 'object') {
    koder.forEach(kode => {
      if (Array.isArray(masterData.trend_points[kode])) {
        trendPoints[kode] = [...masterData.trend_points[kode]];
      }
    });
  }

  return {
    arter: masterData.arter ? [...masterData.arter] : [],
    rows,
    koder,
    matrix,
    totals,
    scope_year: masterData.scope_year,
    trend_points: trendPoints
  };
}

function filtrerScoreboardPåKoder(valgteKoder, params) {
  const container = document.getElementById("main");
  const filtered = buildFilteredData(valgteKoder);
  if (!filtered) return;

  if (!filtered.rows.length) {
    container.innerHTML = `<p>Ingen brugere fundet for det valgte filter.</p>`;
    tilføjGruppeFilterKnappen(filtered, params, container);

    // Ryd/vis tomme sektioner
    visScoreboardMatrix({ arter: [], koder: [], matrix: [] });
    visScoreboardBlockers({ arter: [], koder: [], matrix: [], totals: [] });
    visScoreboardTrend({ arter: [], koder: [], matrix: [] });
    return;
  }

  // Cards
  container.innerHTML = renderScoreboardCards(filtered.rows);

  // Filter-knap igen
  tilføjGruppeFilterKnappen(filtered, params, container);

  // Sektioner
  visScoreboardMatrix(filtered);
  visScoreboardBlockers(filtered);
  visScoreboardTrend(filtered);

  // Klik på bruger
  container.querySelectorAll('.user-card').forEach(card => {
    card.onclick = async function () {
      const kode = this.getAttribute('data-obserkode');
      const navn = this.querySelector('strong').innerText;
      const currentYear = new Date().getFullYear();
      const scopeYear = params?.aar || cachedGlobalYear || currentYear;
      firstsSortMode = (String(scopeYear) === String(currentYear)) ? "newest" : "alphabetical";
      await visUserFirsts(kode, navn, firstsSortMode, params);
    };
  });
}

// ---------- Filter-knap & modal ----------
function tilføjGruppeFilterKnappen(data, params, container) {
  // Undgå duplikat
  const existing = container.querySelector('#gruppeFilterBtn');
  if (existing) existing.remove();
  const existingYearWrap = container.querySelector('#yearSelectWrap');
  if (existingYearWrap) existingYearWrap.remove();
  const existingRow = container.querySelector('#scoreboard-year-row');
  if (existingRow) existingRow.remove();

  const filterBtn = document.createElement("button");
  filterBtn.id = "gruppeFilterBtn";
  filterBtn.textContent = "Filtrer medlemmer";
  filterBtn.style.margin = "1em 0";
  filterBtn.onclick = () => visGruppeFilterModal(data, params);

  // Wrap i centreret div
  const wrapper = document.createElement("div");
  wrapper.style.display = "flex";
  wrapper.style.justifyContent = "center";
  wrapper.style.gap = "0.75em";
  wrapper.style.flexWrap = "wrap";
  wrapper.appendChild(filterBtn);

  const yearSelector = createYearSelector(params);
  if (yearSelector) wrapper.appendChild(yearSelector);

  container.prepend(wrapper);
}

function visGruppeFilterModal(_data, params) {
  if (!masterData) return;

  // Modal baggrund
  const modalBg = document.createElement("div");
  modalBg.style.position = "fixed";
  modalBg.style.top = 0;
  modalBg.style.left = 0;
  modalBg.style.width = "100vw";
  modalBg.style.height = "100vh";
  modalBg.style.background = "rgba(0,0,0,0.3)";
  modalBg.style.zIndex = 1000;

  // Modal boks
  const modal = document.createElement("div");
  modal.style.background = "#fff";
  modal.style.padding = "2em";
  modal.style.borderRadius = "10px";
  modal.style.maxWidth = "380px";
  modal.style.margin = "5vh auto";
  modal.style.position = "relative";
  modal.style.top = "10vh";
  modal.style.boxShadow = "0 2px 16px rgba(0,0,0,0.15)";
  modal.innerHTML = `<h3>Vælg medlemmer</h3>`;

  const allRows = masterData.rows || [];
  const defaultSelection = lastSelectedKoder || (_data.koder && _data.koder.length ? _data.koder : allRows.map(r => r.obserkode));
  const currentSelection = new Set(defaultSelection);

  modal.innerHTML += `
    <div style="display:flex; gap:.5em; margin:.5em 0 1em">
      <button id="selectAllBtn" style="flex:1">Vælg alle</button>
      <button id="clearAllBtn" style="flex:1">Fravælg alle</button>
    </div>
  `;

  allRows.forEach(row => {
    modal.innerHTML += `
      <label style="display:block;margin-bottom:0.5em;">
        <input type="checkbox" class="kode-filter" value="${row.obserkode}" ${currentSelection.has(row.obserkode) ? "checked" : ""}>
        ${row.navn}
      </label>
    `;
  });

  modal.innerHTML += `
    <div style="display:flex; gap:0.5em; margin-top:1.5em; justify-content:flex-end;">
      <button id="applyFilterBtn" style="flex:1; padding:0.6em 0.8em; border-radius:6px; border:1px solid #007bff; background:#007bff; color:#fff; font-weight:bold; cursor:pointer;">Vis valgte</button>
      <button id="resetFilterBtn" style="flex:1; padding:0.6em 0.8em; border-radius:6px; border:1px solid #6c757d; background:#f8f9fa; color:#333; font-weight:bold; cursor:pointer;">Nulstil</button>
      <button id="closeFilterBtn" style="flex:1; padding:0.6em 0.8em; border-radius:6px; border:1px solid #dc3545; background:#fff; color:#dc3545; font-weight:bold; cursor:pointer;">Luk</button>
    </div>
  `;

  modalBg.appendChild(modal);
  document.body.appendChild(modalBg);

  // Bind events lokalt i modalen (undgår null/ID-kollisioner)
  const selectAllBtn = modal.querySelector('#selectAllBtn');
  const clearAllBtn = modal.querySelector('#clearAllBtn');
  const applyBtn = modal.querySelector('#applyFilterBtn');
  const resetBtn = modal.querySelector('#resetFilterBtn');
  const closeBtn = modal.querySelector('#closeFilterBtn');

  if (!selectAllBtn || !clearAllBtn || !applyBtn || !resetBtn || !closeBtn) {
    console.warn('Filter-modal: knapperne blev ikke oprettet som forventet.');
    modalBg.remove();
    return;
  }

  selectAllBtn.addEventListener('click', () => {
    modal.querySelectorAll('.kode-filter').forEach(cb => (cb.checked = true));
  });

  clearAllBtn.addEventListener('click', () => {
    modal.querySelectorAll('.kode-filter').forEach(cb => (cb.checked = false));
  });

  closeBtn.addEventListener('click', () => modalBg.remove());

  resetBtn.addEventListener('click', () => {
    const alleKoder = (masterData.rows || []).map(r => r.obserkode);
    lastSelectedKoder = alleKoder;
    modalBg.remove();
    filtrerScoreboardPåKoder(alleKoder, params);
  });

  applyBtn.addEventListener('click', () => {
    const checked = Array.from(modal.querySelectorAll('.kode-filter:checked')).map(cb => cb.value);
    lastSelectedKoder = checked.length ? checked : [];
    modalBg.remove();
    filtrerScoreboardPåKoder(checked, params);
  });
}

// ---------- Lifecycle ----------
window.onload = visSide;
window.onpopstate = visSide;
