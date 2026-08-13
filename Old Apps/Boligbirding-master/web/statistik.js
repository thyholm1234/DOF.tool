// Version: 1.13.16 - 2026-05-15 23.55.20
// © Christian Vemmelund Helligsø
import { renderNavbar, initNavbar, initMobileNavbar, addGruppeLinks } from './navbar.js';

renderNavbar();
initNavbar();
initMobileNavbar();

fetch('/api/get_grupper')
  .then(res => res.json())
  .then(grupper => { addGruppeLinks(grupper); });

const chartInstances = {};
let primaryStatData = null;
let compareStatData = null;
let userScoreboardData = null;
let lokalafdelingOverviewSelection = null;
let kommuneOverviewSelection = null;
const lokalafdelingYearRowCache = new Map();
const kommuneYearRowCache = new Map();

function normalizeCode(value) {
  return String(value || '').trim().toUpperCase();
}

function formatNumber(value) {
  const num = Number(value);
  if (Number.isNaN(num)) return value;
  return num.toLocaleString('da-DK');
}

function renderYearList(targetId, years, user, scope, totalCount = 0, totalRank = null, compareYears = [], compareUser = null, compareTotalCount = null, compareTotalRank = null) {
  const target = document.getElementById(targetId);
  if (!target) return;
  const filtered = (years || []).filter(y => {
    const count = Number.parseInt(y.count, 10);
    return Number.isFinite(count) && count > 0;
  }).sort((a, b) => Number(b.year) - Number(a.year));
  const compareMap = new Map((compareYears || []).map(y => [String(y.year), y]));
  const hasTotal = Number(totalCount) > 0;
  if (!filtered.length && !hasTotal) {
    target.innerHTML = '<div class="muted">Ingen årsdata fundet.</div>';
    return;
  }
  const totalParams = new URLSearchParams({
    scope,
    obserkode: user.obserkode || '',
    navn: user.navn || user.obserkode || '',
    aar: 'global'
  });
  const totalLink = `scoreboard.html?${totalParams.toString()}`;
  const totalCompareCell = compareUser
    ? `<td>${compareTotalCount !== null && compareTotalCount !== undefined ? `${formatNumber(compareTotalCount)} (${compareTotalRank ? `#${compareTotalRank}` : '-'})` : '-'}</td>`
    : '';
  const totalRankText = totalRank ? `#${totalRank}` : '-';
  const totalRow = hasTotal
    ? `
      <tr style="font-weight:700;">
        <td><b>Total</b></td>
        <td><a href="${totalLink}">Se listen</a></td>
        <td><b>${formatNumber(totalCount)}</b></td>
        <td>${totalRankText}</td>
        ${totalCompareCell}
      </tr>
    `
    : '';

  const rows = filtered.map(y => {
    const params = new URLSearchParams({
      scope: scope,
      obserkode: user.obserkode || '',
      navn: user.navn || user.obserkode || '',
      aar: String(y.year)
    });
    const link = `scoreboard.html?${params.toString()}`;
    const count = formatNumber(y.count);
    const rank = y.rank ? `#${y.rank}` : '-';
    const compareRow = compareMap.get(String(y.year));
    const compareCell = compareUser
      ? `<td>${compareRow ? `${formatNumber(compareRow.count)} (${compareRow.rank ? `#${compareRow.rank}` : '-'})` : '-'}</td>`
      : '';
    return `
      <tr>
        <td>${y.year}</td>
        <td><a href="${link}">Se listen</a></td>
        <td>${count}</td>
        <td>${rank}</td>
        ${compareCell}
      </tr>
    `;
  }).join('');

  const compareHeader = compareUser ? `<th>${compareUser.obserkode || 'Sammenligning'}</th>` : '';

  target.innerHTML = `
    <table class="profile-table">
      <thead>
        <tr>
          <th>År</th>
          <th>Liste</th>
          <th>X</th>
          <th>Placering</th>
          ${compareHeader}
        </tr>
      </thead>
      <tbody>${totalRow}${rows}</tbody>
    </table>
  `;
}

function renderMatrikelYearList(targetId, data, user, compareData = null, compareUser = null) {
  const target = document.getElementById(targetId);
  if (!target) return;

  const primaryIndexes = Array.isArray(data?.matrikel_available_indexes)
    ? data.matrikel_available_indexes
      .map(v => Number(v))
      .filter(v => Number.isInteger(v) && v >= 1)
      .sort((a, b) => a - b)
    : [];
  const compareIndexes = Array.isArray(compareData?.matrikel_available_indexes)
    ? compareData.matrikel_available_indexes
      .map(v => Number(v))
      .filter(v => Number.isInteger(v) && v >= 1)
      .sort((a, b) => a - b)
    : [];

  const indexSet = new Set([...(primaryIndexes || []), ...(compareIndexes || [])]);
  if (!indexSet.size) indexSet.add(1);
  const indexes = Array.from(indexSet).sort((a, b) => a - b);
  const singleMatrikel = indexes.length === 1;

  const totals = data?.matrikel_totals || {};
  const compareTotals = compareData?.matrikel_totals || {};

  const rowsRaw = Array.isArray(data?.matrikel_year_rows) ? data.matrikel_year_rows : [];
  const rowsFallback = Array.isArray(data?.matrikel_years)
    ? data.matrikel_years.map(y => ({
      year: y.year,
      matrikler: { "1": { count: Number(y.count || 0), rank: y.rank ?? null } }
    }))
    : [];
  const rowsSource = rowsRaw.length ? rowsRaw : rowsFallback;

  const rows = rowsSource
    .map(row => ({ ...row, year: Number(row?.year) }))
    .filter(row => Number.isFinite(row.year))
    .sort((a, b) => b.year - a.year)
    .filter(row => indexes.some(idx => Number(row?.matrikler?.[String(idx)]?.count || 0) > 0));

  const hasTotal = indexes.some(idx => Number(totals?.[String(idx)]?.count || 0) > 0);
  if (!rows.length && !hasTotal) {
    target.innerHTML = '<div class="muted">Ingen årsdata fundet.</div>';
    return;
  }

  const headerCols = singleMatrikel
    ? '<th>X</th>'
    : indexes.map(idx => `<th>Matrikel ${idx}</th>`).join('');

  const formatMatrikelCell = (idx, cell) => {
    const count = Number(cell?.count || 0);
    const rank = cell?.rank;
    if (idx === 1 && rank) return `${formatNumber(count)} (#${rank})`;
    return formatNumber(count);
  };

  const buildPrimaryCountCells = (matriklerMap) => {
    if (singleMatrikel) {
      const idx = indexes[0];
      const cell = matriklerMap?.[String(idx)] || {};
      return `<td>${formatMatrikelCell(idx, cell)}</td>`;
    }
    return indexes.map(idx => {
      const cell = matriklerMap?.[String(idx)] || {};
      return `<td>${formatMatrikelCell(idx, cell)}</td>`;
    }).join('');
  };

  const totalParams = new URLSearchParams({
    scope: 'user_matrikel',
    obserkode: user.obserkode || '',
    navn: user.navn || user.obserkode || '',
    aar: 'global',
    matrikel: String(indexes[0] || 1)
  });
  const totalLink = `scoreboard.html?${totalParams.toString()}`;

  const totalPrimaryMap = {};
  indexes.forEach(idx => {
    totalPrimaryMap[String(idx)] = {
      count: Number(totals?.[String(idx)]?.count || 0),
      rank: totals?.[String(idx)]?.rank ?? null
    };
  });
  const totalRank = totals?.['1']?.rank ? `#${totals['1'].rank}` : '-';

  const totalCompareCell = compareUser
    ? (() => {
      const compareCount = Number(compareTotals?.['1']?.count || 0);
      const compareRank = compareTotals?.['1']?.rank ? `#${compareTotals['1'].rank}` : '-';
      return `<td>${compareCount > 0 ? `${formatNumber(compareCount)} (${compareRank})` : '-'}</td>`;
    })()
    : '';

  const totalRow = hasTotal
    ? `
      <tr style="font-weight:700;">
        <td><b>Total</b></td>
        <td><a href="${totalLink}">Se listen</a></td>
        ${buildPrimaryCountCells(totalPrimaryMap)}
        <td>${totalRank}</td>
        ${totalCompareCell}
      </tr>
    `
    : '';

  const compareRowsRaw = Array.isArray(compareData?.matrikel_year_rows) ? compareData.matrikel_year_rows : [];
  const compareRowsFallback = Array.isArray(compareData?.matrikel_years)
    ? compareData.matrikel_years.map(y => ({
      year: y.year,
      matrikler: { "1": { count: Number(y.count || 0), rank: y.rank ?? null } }
    }))
    : [];
  const compareRows = (compareRowsRaw.length ? compareRowsRaw : compareRowsFallback);
  const compareMap = new Map(compareRows.map(r => [String(r?.year), r]));

  const yearRows = rows.map(row => {
    const params = new URLSearchParams({
      scope: 'user_matrikel',
      obserkode: user.obserkode || '',
      navn: user.navn || user.obserkode || '',
      aar: String(row.year),
      matrikel: String(indexes[0] || 1)
    });
    const link = `scoreboard.html?${params.toString()}`;
    const rank = row?.matrikler?.['1']?.rank ? `#${row.matrikler['1'].rank}` : '-';
    const compareRow = compareMap.get(String(row.year));
    const compareCell = compareUser
      ? (() => {
        const c = Number(compareRow?.matrikler?.['1']?.count || 0);
        const r = compareRow?.matrikler?.['1']?.rank ? `#${compareRow.matrikler['1'].rank}` : '-';
        return `<td>${c > 0 ? `${formatNumber(c)} (${r})` : '-'}</td>`;
      })()
      : '';

    return `
      <tr>
        <td>${row.year}</td>
        <td><a href="${link}">Se listen</a></td>
        ${buildPrimaryCountCells(row.matrikler || {})}
        <td>${rank}</td>
        ${compareCell}
      </tr>
    `;
  }).join('');

  const compareHeader = compareUser ? `<th>${compareUser.obserkode || 'Sammenligning'}</th>` : '';

  target.innerHTML = `
    <table class="profile-table">
      <thead>
        <tr>
          <th>År</th>
          <th>Liste</th>
          ${headerCols}
          <th>Placering</th>
          ${compareHeader}
        </tr>
      </thead>
      <tbody>${totalRow}${yearRows}</tbody>
    </table>
  `;
}

function buildLineChart(canvasId, labels, datasets, yAxisTitle) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === 'undefined') return;
  if (chartInstances[canvasId]) {
    chartInstances[canvasId].destroy();
  }

  const numericYears = (labels || []).map(v => Number(v)).filter(Number.isFinite);
  const minYear = numericYears.length ? Math.min(...numericYears) : null;
  const maxYear = numericYears.length ? Math.max(...numericYears) : null;
  const overFiveYears = minYear !== null && maxYear !== null && (maxYear - minYear + 1) > 5;

  chartInstances[canvasId] = new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      labels,
      datasets
    },
    options: {
      elements: {
        point: {
          radius: 0,
          hoverRadius: 0
        }
      },
      plugins: { legend: { display: true } },
      scales: {
        x: {
          title: { display: true, text: 'År' },
          ticks: {
            callback: function(value) {
              const label = this.getLabelForValue(value);
              const year = Number(label);
              if (!Number.isFinite(year)) return '';
              if (!overFiveYears) return String(year);
              if (year === minYear || year === maxYear || year % 5 === 0) return String(year);
              return '';
            }
          },
          grid: {
            color: (ctx) => {
              const idx = ctx.tick?.value;
              const year = Number((typeof idx === 'number' ? labels[idx] : null));
              if (!Number.isFinite(year)) return 'rgba(0,0,0,0.08)';
              if (overFiveYears && year % 5 === 0) return 'rgba(0,0,0,0.3)';
              if (overFiveYears) return 'rgba(0,0,0,0.07)';
              return 'rgba(0,0,0,0.12)';
            }
          }
        },
        y: { title: { display: true, text: yAxisTitle }, beginAtZero: true }
      }
    }
  });
}

function mergeSeries(primary = [], compare = []) {
  const yearValues = [];
  (primary || []).forEach(d => yearValues.push(Number(d.year)));
  (compare || []).forEach(d => yearValues.push(Number(d.year)));
  const finiteYears = yearValues.filter(Number.isFinite);
  if (!finiteYears.length) return { labels: [], primaryValues: [], compareValues: [] };
  const minYear = Math.min(...finiteYears);
  const maxYear = Math.max(...finiteYears);
  const labels = [];
  for (let y = minYear; y <= maxYear; y++) labels.push(y);

  const primaryMap = new Map((primary || []).map(d => [Number(d.year), Number(d.count) || 0]));
  const compareMap = new Map((compare || []).map(d => [Number(d.year), Number(d.count) || 0]));
  const primaryValues = labels.map(y => primaryMap.get(y) || 0);
  const compareValues = labels.map(y => compareMap.get(y) || 0);
  return { labels, primaryValues, compareValues };
}

function buildDevelopmentSeriesFromFirsts(items = []) {
  const byYear = new Map();
  (items || []).forEach(item => {
    const dato = String(item?.dato || '');
    const parts = dato.split('-');
    if (parts.length !== 3) return;
    const year = Number(parts[2]);
    if (!Number.isFinite(year)) return;
    byYear.set(year, (byYear.get(year) || 0) + 1);
  });

  const years = Array.from(byYear.keys()).sort((a, b) => a - b);
  let running = 0;
  return years.map(year => {
    running += byYear.get(year) || 0;
    return { year, count: running };
  });
}

function mergeCumulativeSeries(primary = [], compare = []) {
  const yearValues = [];
  (primary || []).forEach(d => yearValues.push(Number(d.year)));
  (compare || []).forEach(d => yearValues.push(Number(d.year)));
  const finiteYears = yearValues.filter(Number.isFinite);
  if (!finiteYears.length) return { labels: [], primaryValues: [], compareValues: [] };
  const minYear = Math.min(...finiteYears);
  const maxYear = Math.max(...finiteYears);
  const labels = [];
  for (let y = minYear; y <= maxYear; y++) labels.push(y);

  const primaryMap = new Map((primary || []).map(d => [Number(d.year), Number(d.count) || 0]));
  const compareMap = new Map((compare || []).map(d => [Number(d.year), Number(d.count) || 0]));

  let lastPrimary = 0;
  let lastCompare = 0;
  const primaryValues = labels.map(y => {
    if (primaryMap.has(y)) lastPrimary = primaryMap.get(y) || 0;
    return lastPrimary;
  });
  const compareValues = labels.map(y => {
    if (compareMap.has(y)) lastCompare = compareMap.get(y) || 0;
    return lastCompare;
  });

  return { labels, primaryValues, compareValues };
}

function createDataset(label, data, color) {
  return {
    label,
    data,
    borderColor: color,
    backgroundColor: color,
    fill: false,
    tension: 0,
    pointRadius: 0,
    pointHoverRadius: 0
  };
}

function normalizeArtName(value) {
  return String(value || '').split('(')[0].split(',')[0].trim();
}

function uniqueArtsFromItems(items = []) {
  const set = new Set();
  (items || []).forEach(item => {
    const name = normalizeArtName(item?.artnavn);
    if (name) set.add(name);
  });
  return set;
}

function renderComparisonBlockers(primaryData, compareData) {
  const wrap = document.getElementById('comparison-blockers');
  if (!wrap) return;

  const primaryUser = primaryData?.user || {};
  const compareUser = compareData?.user || {};

  if (!compareData) {
    wrap.style.display = 'none';
    wrap.innerHTML = '';
    return;
  }

  const categories = [
    {
      key: 'danmark',
      title: 'Global (DK-arter)'
    },
    {
      key: 'vp',
      title: 'Matrikel'
    }
  ];

  const categoryHtml = categories.map(category => {
    const primarySet = uniqueArtsFromItems(primaryData?.lists?.[category.key]?.items || []);
    const compareSet = uniqueArtsFromItems(compareData?.lists?.[category.key]?.items || []);

    const primaryMissing = Array.from(compareSet).filter(name => !primarySet.has(name)).sort((a, b) => a.localeCompare(b, 'da'));
    const compareMissing = Array.from(primarySet).filter(name => !compareSet.has(name)).sort((a, b) => a.localeCompare(b, 'da'));

    const primaryMissingHtml = primaryMissing.length
      ? `<div style="margin-top:0.4em;max-height:180px;overflow:auto;">${primaryMissing.join('<br>')}</div>`
      : '<div class="muted" style="margin-top:0.4em;">Ingen</div>';
    const compareMissingHtml = compareMissing.length
      ? `<div style="margin-top:0.4em;max-height:180px;overflow:auto;">${compareMissing.join('<br>')}</div>`
      : '<div class="muted" style="margin-top:0.4em;">Ingen</div>';

    return `
      <div style="border:1px solid var(--border);border-radius:8px;padding:0.75em;margin-top:0.8em;">
        <div style="font-weight:700;margin-bottom:0.5em;">${category.title}</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.8em;">
          <div>
            <div><b>${primaryUser.obserkode || 'Primær'} mangler</b> (${primaryMissing.length})</div>
            ${primaryMissingHtml}
          </div>
          <div>
            <div><b>${compareUser.obserkode || 'Sammenligning'} mangler</b> (${compareMissing.length})</div>
            ${compareMissingHtml}
          </div>
        </div>
      </div>
    `;
  }).join('');

  wrap.style.display = 'block';
  wrap.innerHTML = `
    <h3 style="margin-bottom:0.2em;">Blockers i sammenligning</h3>
    <div class="muted" style="margin-bottom:0.5em;">Arter den ene har, som den anden endnu mangler.</div>
    ${categoryHtml}
  `;
}

async function fetchKommuneYearRow(kommuneId, yearValue) {
  const cacheKey = `${String(kommuneId)}|${String(yearValue)}`;
  if (kommuneYearRowCache.has(cacheKey)) return kommuneYearRowCache.get(cacheKey);

  try {
    const res = await fetch('/api/scoreboard', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scope: 'kommune_alle',
        kommune: String(kommuneId),
        aar: yearValue
      })
    });
    if (!res.ok) {
      kommuneYearRowCache.set(cacheKey, null);
      return null;
    }
    const payload = await res.json();
    const rows = Array.isArray(payload?.rows) ? payload.rows : [];
    const selfCode = normalizeCode(userScoreboardData?.self_obserkode || primaryStatData?.user?.obserkode);
    const row = rows.find(item => normalizeCode(item?.obserkode) === selfCode) || null;
    kommuneYearRowCache.set(cacheKey, row);
    return row;
  } catch (_) {
    kommuneYearRowCache.set(cacheKey, null);
    return null;
  }
}

async function fetchLokalafdelingYearRow(lokalafdelingNavn, yearValue) {
  const cacheKey = `${String(lokalafdelingNavn)}|${String(yearValue)}`;
  if (lokalafdelingYearRowCache.has(cacheKey)) return lokalafdelingYearRowCache.get(cacheKey);

  try {
    const res = await fetch('/api/scoreboard', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scope: 'lokal_alle',
        afdeling: String(lokalafdelingNavn),
        aar: yearValue
      })
    });
    if (!res.ok) {
      lokalafdelingYearRowCache.set(cacheKey, null);
      return null;
    }
    const payload = await res.json();
    const rows = Array.isArray(payload?.rows) ? payload.rows : [];
    const selfCode = normalizeCode(userScoreboardData?.self_obserkode || primaryStatData?.user?.obserkode);
    const row = rows.find(item => normalizeCode(item?.obserkode) === selfCode) || null;
    lokalafdelingYearRowCache.set(cacheKey, row);
    return row;
  } catch (_) {
    lokalafdelingYearRowCache.set(cacheKey, null);
    return null;
  }
}

async function renderLokalafdelingOverview() {
  const card = document.getElementById('lokalafdeling-overview-card');
  const select = document.getElementById('lokalafdeling-overview-select');
  const content = document.getElementById('lokalafdeling-overview-content');
  if (!card || !select || !content || !primaryStatData) return;

  const viewedCode = normalizeCode(primaryStatData?.user?.obserkode);
  const selfCode = normalizeCode(userScoreboardData?.self_obserkode);
  const rows = Array.isArray(userScoreboardData?.lokalafdelinger_overblik) ? userScoreboardData.lokalafdelinger_overblik : [];

  if (!viewedCode || !selfCode || viewedCode !== selfCode || !rows.length) {
    card.style.display = 'none';
    content.innerHTML = '';
    return;
  }

  const validRows = rows.filter(r => r && r.lokalafdeling_navn);
  if (!validRows.length) {
    card.style.display = 'none';
    content.innerHTML = '';
    return;
  }

  const primaryLokalafdelingNavn = String(userScoreboardData?.lokalafdeling_navn || '');

  const hasSelection = validRows.some(r => String(r.lokalafdeling_navn) === String(lokalafdelingOverviewSelection));
  if (!hasSelection) {
    const primaryRow = validRows.find(r => String(r.lokalafdeling_navn) === primaryLokalafdelingNavn);
    lokalafdelingOverviewSelection = String((primaryRow || validRows[0]).lokalafdeling_navn);
  }

  select.innerHTML = validRows
    .map((row) => `<option value="${String(row.lokalafdeling_navn)}">${row.lokalafdeling_navn}${String(row.lokalafdeling_navn) === primaryLokalafdelingNavn ? ' (primær)' : ''}</option>`)
    .join('');
  select.value = String(lokalafdelingOverviewSelection);
  select.onchange = () => {
    lokalafdelingOverviewSelection = String(select.value || '');
    void renderLokalafdelingOverview();
  };

  const selected = validRows.find(r => String(r.lokalafdeling_navn) === String(lokalafdelingOverviewSelection)) || validRows[0];
  const years = Array.isArray(primaryStatData?.years)
    ? primaryStatData.years
      .map(item => Number(item?.year))
      .filter(Number.isFinite)
      .sort((a, b) => b - a)
    : [];

  content.innerHTML = '<div class="muted">Henter lokalafdeling-data...</div>';

  const yearRows = await Promise.all(
    years.map(async year => ({
      year,
      row: await fetchLokalafdelingYearRow(selected.lokalafdeling_navn, year)
    }))
  );

  const totalRow = selected?.alle_total || selected?.alle || null;

  const rowHtml = (label, row, scope, yearValue = null) => {
    const count = Number(row?.antal_arter || 0);
    const rank = row?.placering ? `#${row.placering}` : '-';
    const rowStyle = String(label) === 'Total' ? ' style="font-weight:700;"' : '';
    const params = new URLSearchParams({
      scope,
      afdeling: String(selected.lokalafdeling_navn)
    });
    if (yearValue !== null && yearValue !== undefined) {
      params.set('aar', String(yearValue));
    }
    const link = `scoreboard.html?${params.toString()}`;
    return `
      <tr${rowStyle}>
        <td>${label}</td>
        <td>${count > 0 ? formatNumber(count) : '0'}</td>
        <td>${rank}</td>
        <td><a href="${link}">Se listen</a></td>
      </tr>
    `;
  };

  const yearRowsHtml = yearRows
    .map(item => rowHtml(String(item.year), item.row, 'lokal_alle', item.year))
    .join('');

  content.innerHTML = `
    <table class="profile-table">
      <thead>
        <tr>
          <th>År</th>
          <th>Arter</th>
          <th>Placering</th>
          <th>Liste</th>
        </tr>
      </thead>
      <tbody>
        ${rowHtml('Total', totalRow, 'lokal_alle', 'global')}
        ${yearRowsHtml}
      </tbody>
    </table>
  `;

  card.style.display = 'block';
}

async function renderKommuneOverview() {
  const card = document.getElementById('kommune-overview-card');
  const select = document.getElementById('kommune-overview-select');
  const content = document.getElementById('kommune-overview-content');
  if (!card || !select || !content || !primaryStatData) return;

  const viewedCode = normalizeCode(primaryStatData?.user?.obserkode);
  const selfCode = normalizeCode(userScoreboardData?.self_obserkode);
  const rows = Array.isArray(userScoreboardData?.kommuner_overblik) ? userScoreboardData.kommuner_overblik : [];

  if (!viewedCode || !selfCode || viewedCode !== selfCode || !rows.length) {
    card.style.display = 'none';
    content.innerHTML = '';
    return;
  }

  const validRows = rows.filter(r => r && r.kommune_id && r.kommune_navn);
  if (!validRows.length) {
    card.style.display = 'none';
    content.innerHTML = '';
    return;
  }

  const primaryKommuneId = String(userScoreboardData?.kommune_id || '');

  const hasSelection = validRows.some(r => String(r.kommune_id) === String(kommuneOverviewSelection));
  if (!hasSelection) {
    const primaryRow = validRows.find(r => String(r.kommune_id) === primaryKommuneId);
    kommuneOverviewSelection = String((primaryRow || validRows[0]).kommune_id);
  }

  select.innerHTML = validRows
    .map((row) => `<option value="${String(row.kommune_id)}">${row.kommune_navn}${String(row.kommune_id) === primaryKommuneId ? ' (primær)' : ''}</option>`)
    .join('');
  select.value = String(kommuneOverviewSelection);
  select.onchange = () => {
    kommuneOverviewSelection = String(select.value || '');
    void renderKommuneOverview();
  };

  const selected = validRows.find(r => String(r.kommune_id) === String(kommuneOverviewSelection)) || validRows[0];
  const years = Array.isArray(primaryStatData?.years)
    ? primaryStatData.years
      .map(item => Number(item?.year))
      .filter(Number.isFinite)
      .sort((a, b) => b - a)
    : [];

  content.innerHTML = '<div class="muted">Henter kommune-data...</div>';

  const yearRows = await Promise.all(
    years.map(async year => ({
      year,
      row: await fetchKommuneYearRow(selected.kommune_id, year)
    }))
  );

  const totalRow = selected?.alle_total || selected?.alle || null;

  const rowHtml = (label, row, scope, yearValue = null) => {
    const count = Number(row?.antal_arter || 0);
    const rank = row?.placering ? `#${row.placering}` : '-';
    const rowStyle = String(label) === 'Total' ? ' style="font-weight:700;"' : '';
    const params = new URLSearchParams({
      scope,
      kommune: String(selected.kommune_id),
      kommune_navn: String(selected.kommune_navn)
    });
    if (yearValue !== null && yearValue !== undefined) {
      params.set('aar', String(yearValue));
    }
    const link = `scoreboard.html?${params.toString()}`;
    return `
      <tr${rowStyle}>
        <td>${label}</td>
        <td>${count > 0 ? formatNumber(count) : '0'}</td>
        <td>${rank}</td>
        <td><a href="${link}">Se listen</a></td>
      </tr>
    `;
  };

  const yearRowsHtml = yearRows
    .map(item => rowHtml(String(item.year), item.row, 'kommune_alle', item.year))
    .join('');

  content.innerHTML = `
    <table class="profile-table">
      <thead>
        <tr>
          <th>År</th>
          <th>Arter</th>
          <th>Placering</th>
          <th>Liste</th>
        </tr>
      </thead>
      <tbody>
        ${rowHtml('Total', totalRow, 'kommune_alle', 'global')}
        ${yearRowsHtml}
      </tbody>
    </table>
  `;

  card.style.display = 'block';
}

function renderStatistik() {
  if (!primaryStatData) return;

  const data = primaryStatData;
  const user = data.user || {};
  const compareUser = compareStatData?.user || null;

  renderComparisonBlockers(data, compareStatData);

  const header = document.getElementById('profile-header');
  if (header) {
    const kommune = user.kommune_navn || user.kommune || '-';
    const compareText = compareUser
      ? `<div><b>Sammenligner med:</b> ${compareUser.navn || '-'} (${compareUser.obserkode || '-'})</div>`
      : '';
    header.innerHTML = `
      <div class="profile-title">${user.navn || 'Ukendt bruger'}</div>
      <div class="profile-meta">
        <div><b>Obserkode:</b> ${user.obserkode || '-'}</div>
        <div><b>Lokalafdeling:</b> ${user.lokalafdeling || '-'}</div>
        <div><b>Kommune:</b> ${kommune}</div>
        ${compareText}
      </div>
    `;
  }

  renderYearList(
    'year-list',
    data.years || [],
    user,
    'user_global',
    Number(data.lists?.danmark?.count || 0),
    data.lists?.danmark?.rank ?? null,
    compareStatData?.years || [],
    compareUser,
    compareStatData ? Number(compareStatData?.lists?.danmark?.count || 0) : null,
    compareStatData?.lists?.danmark?.rank ?? null
  );
  renderMatrikelYearList(
    'matrikel-year-list',
    data,
    user,
    compareStatData,
    compareUser
  );

  const globalSeries = mergeSeries(data.charts?.global_by_year || [], compareStatData?.charts?.global_by_year || []);
  const matrikelSeries = mergeSeries(data.charts?.matrikel_by_year || [], compareStatData?.charts?.matrikel_by_year || []);
  const obsSeries = mergeSeries(data.charts?.obs_by_year || [], compareStatData?.charts?.obs_by_year || []);
  const globalDevPrimary = buildDevelopmentSeriesFromFirsts(data.lists?.danmark?.items || []);
  const globalDevCompare = buildDevelopmentSeriesFromFirsts(compareStatData?.lists?.danmark?.items || []);
  const matrikelDevPrimary = buildDevelopmentSeriesFromFirsts(data.lists?.vp?.items || []);
  const matrikelDevCompare = buildDevelopmentSeriesFromFirsts(compareStatData?.lists?.vp?.items || []);
  const globalDevSeries = mergeCumulativeSeries(globalDevPrimary, globalDevCompare);
  const matrikelDevSeries = mergeCumulativeSeries(matrikelDevPrimary, matrikelDevCompare);

  buildLineChart(
    'chart-global',
    globalSeries.labels,
    [
      createDataset(user.obserkode || 'Primær', globalSeries.primaryValues, '#2b7a78'),
      ...(compareUser ? [createDataset(compareUser.obserkode || 'Sammenligning', globalSeries.compareValues, '#d32f2f')] : [])
    ],
    'Arter'
  );
  buildLineChart(
    'chart-matrikel',
    matrikelSeries.labels,
    [
      createDataset(user.obserkode || 'Primær', matrikelSeries.primaryValues, '#3aafa9'),
      ...(compareUser ? [createDataset(compareUser.obserkode || 'Sammenligning', matrikelSeries.compareValues, '#ef6c00')] : [])
    ],
    'Matrikelarter'
  );
  buildLineChart(
    'chart-global-dev',
    globalDevSeries.labels,
    [
      createDataset(user.obserkode || 'Primær', globalDevSeries.primaryValues, '#00695c'),
      ...(compareUser ? [createDataset(compareUser.obserkode || 'Sammenligning', globalDevSeries.compareValues, '#c62828')] : [])
    ],
    'Sete arter (kumulativ)'
  );
  buildLineChart(
    'chart-matrikel-dev',
    matrikelDevSeries.labels,
    [
      createDataset(user.obserkode || 'Primær', matrikelDevSeries.primaryValues, '#00838f'),
      ...(compareUser ? [createDataset(compareUser.obserkode || 'Sammenligning', matrikelDevSeries.compareValues, '#ef6c00')] : [])
    ],
    'Sete matrikelarter (kumulativ)'
  );
  buildLineChart(
    'chart-obs',
    obsSeries.labels,
    [
      createDataset(user.obserkode || 'Primær', obsSeries.primaryValues, '#1976d2'),
      ...(compareUser ? [createDataset(compareUser.obserkode || 'Sammenligning', obsSeries.compareValues, '#8e24aa')] : [])
    ],
    'Observationer'
  );

  void renderLokalafdelingOverview();
  void renderKommuneOverview();
}

function getQueryParam(name) {
  const params = new URLSearchParams(window.location.search);
  return params.get(name);
}

async function loadStatistik(obserkode) {
  try {
    const res = await fetch(`/api/statistik_data?obserkode=${encodeURIComponent(obserkode)}`);
    if (res.status === 404) {
      document.getElementById('profile-header').innerHTML = '<div class="muted">Observatør ikke fundet.</div>';
      return;
    }
    if (!res.ok) {
      document.getElementById('profile-header').innerHTML = '<div class="muted">Fejl ved indlæsning af data.</div>';
      return;
    }

    const data = await res.json();
    primaryStatData = data;
    compareStatData = null;
    const compareStatus = document.getElementById('compare-status');
    if (compareStatus) compareStatus.textContent = '';
    renderStatistik();
  } catch (e) {
    document.getElementById('profile-header').innerHTML = '<div class="muted">Fejl ved indlæsning af data.</div>';
  }
}

function initSearch() {
  const input = document.getElementById('obserkode-input');
  const btn = document.getElementById('search-btn');

  btn.addEventListener('click', () => {
    const value = input.value.trim();
    if (value) {
      window.location.href = `statistik.html?obserkode=${encodeURIComponent(value)}`;
    }
  });

  input.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
      btn.click();
    }
  });
}

function initComparison() {
  const input = document.getElementById('compare-obserkode-input');
  const btn = document.getElementById('compare-btn');
  const clearBtn = document.getElementById('clear-compare-btn');
  const status = document.getElementById('compare-status');

  const runCompare = async () => {
    if (!primaryStatData?.user?.obserkode) return;
    const value = (input?.value || '').trim().toUpperCase();
    if (!value) {
      if (status) status.textContent = 'Indtast en obserkode for at sammenligne.';
      return;
    }
    if (value === String(primaryStatData.user.obserkode || '').toUpperCase()) {
      if (status) status.textContent = 'Du sammenligner allerede med den viste observatør.';
      return;
    }
    if (status) status.textContent = `Henter data for ${value}...`;
    try {
      const res = await fetch(`/api/statistik_data?obserkode=${encodeURIComponent(value)}`);
      if (!res.ok) {
        if (status) status.textContent = 'Kunne ikke finde observatør til sammenligning.';
        return;
      }
      compareStatData = await res.json();
      renderStatistik();
      if (status) status.textContent = `Sammenligner nu med ${compareStatData?.user?.obserkode || value}.`;
    } catch (e) {
      if (status) status.textContent = 'Fejl ved hentning af sammenligningsdata.';
    }
  };

  btn?.addEventListener('click', runCompare);
  input?.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
      runCompare();
    }
  });
  clearBtn?.addEventListener('click', () => {
    compareStatData = null;
    renderStatistik();
    if (status) status.textContent = 'Sammenligning fjernet.';
    if (input) input.value = '';
  });
}

async function loadCurrentUser() {
  try {
    const res = await fetch('/api/profile_data');
    if (res.ok) {
      const data = await res.json();
      return data.user?.obserkode;
    }
  } catch (e) {
    // Fall through if not logged in
  }
  return null;
}

async function loadUserScoreboardData() {
  try {
    const res = await fetch('/api/user_scoreboard');
    if (!res.ok) {
      userScoreboardData = null;
      return;
    }
    userScoreboardData = await res.json();
  } catch (e) {
    userScoreboardData = null;
  }
}

// Main initialization
const queryObserkode = getQueryParam('obserkode');
const statisticsContent = document.getElementById('statistics-content');

statisticsContent.style.display = 'block';
initSearch();
initComparison();

loadUserScoreboardData().then(() => {
  void renderLokalafdelingOverview();
  void renderKommuneOverview();
});

// Load statistics
if (queryObserkode) {
  loadStatistik(queryObserkode);
} else {
  // Load current user's statistics
  loadCurrentUser().then(obserkode => {
    if (obserkode) {
      loadStatistik(obserkode);
    }
  });
}
