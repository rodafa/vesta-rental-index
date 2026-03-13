/**
 * Portfolio Analytics — portfolio-wide trends, charts, and segment comparison.
 * Fetches monthly-reports (market-level), monthly-segments (zip/bed detail),
 * rent-analysis (segments), and portfolio-summary.
 * Auto-refreshes every 60 seconds.
 */

var _segmentsCache = null;

async function loadAll() {
  try {
    var [monthlyData, segmentsData, portfolio, monthlySegData] = await Promise.all([
      VestaAPI.get('/market/monthly-reports?limit=24&offset=0'),
      VestaAPI.get('/analytics/rent-analysis'),
      VestaAPI.get('/analytics/portfolio-summary'),
      VestaAPI.get('/market/monthly-segments?limit=500&offset=0'),
    ]);

    var months = monthlyData.items || [];
    var monthlySeg = monthlySegData.items || [];

    renderPortfolioStats(portfolio, segmentsData);
    renderCharts(months, monthlySeg);
    renderPivotTable(segmentsData);
  } catch (err) {
    console.error('Portfolio Analytics load error:', err);
    VestaAPI.render('portfolio-stats', '<div class="loading">Error loading data</div>');
    VestaAPI.render('segment-pivot', '<div class="loading">Error loading data</div>');
  }
}

document.addEventListener('DOMContentLoaded', function () {
  loadAll();
  setInterval(loadAll, 60000);
  document.getElementById('filter-apply').addEventListener('click', applyFilters);
  document.getElementById('pivot-metric').addEventListener('change', function () {
    if (_segmentsCache) renderPivotTable(_segmentsCache);
  });
});

// -- Portfolio Stats ----------------------------------------------------------

function renderPortfolioStats(portfolio, segments) {
  var vacancyRate = portfolio.total_units
    ? ((portfolio.vacant_units / portfolio.total_units) * 100)
    : 0;

  var totalOccupied = 0, totalVacant = 0;
  var occRentSum = 0, occRentCount = 0;
  var vacRentSum = 0, vacRentCount = 0;
  if (segments && segments.length) {
    for (var i = 0; i < segments.length; i++) {
      var seg = segments[i];
      totalOccupied += seg.occupied_count || 0;
      totalVacant += seg.vacant_count || 0;
      if (seg.avg_active_lease_rent != null && seg.occupied_count > 0) {
        occRentSum += Number(seg.avg_active_lease_rent) * seg.occupied_count;
        occRentCount += seg.occupied_count;
      }
      if (seg.avg_target_rent != null && seg.vacant_count > 0) {
        vacRentSum += Number(seg.avg_target_rent) * seg.vacant_count;
        vacRentCount += seg.vacant_count;
      }
    }
  }
  var avgOccRent = occRentCount > 0 ? occRentSum / occRentCount : null;
  var avgVacRent = vacRentCount > 0 ? vacRentSum / vacRentCount : null;

  var cards = [
    { label: 'Total Properties', value: VestaAPI.num(portfolio.total_properties) },
    { label: 'Total Units', value: VestaAPI.num(portfolio.total_units) },
    { label: 'Occupied', value: VestaAPI.num(portfolio.occupied_units) },
    { label: 'Vacant', value: VestaAPI.num(portfolio.vacant_units) },
    { label: 'Vacancy Rate', value: VestaAPI.pct(vacancyRate) },
    { label: 'Avg Occupied Rent', value: VestaAPI.$(avgOccRent) },
    { label: 'Avg Vacant Target', value: VestaAPI.$(avgVacRent) },
  ];

  VestaAPI.render(
    'portfolio-stats',
    cards
      .map(function (c) {
        return (
          '<div class="stat-card">' +
            '<div class="label">' + c.label + '</div>' +
            '<div class="value">' + c.value + '</div>' +
          '</div>'
        );
      })
      .join('')
  );
}

// -- Charts -------------------------------------------------------------------

function renderCharts(months, monthlySeg) {
  var sorted = months.slice().sort(function (a, b) {
    return a.report_month < b.report_month ? -1 : a.report_month > b.report_month ? 1 : 0;
  });
  var marketLabels = sorted.map(function (m) { return formatMonth(m.report_month); });

  VestaCharts.lineChart('chart-list-price', marketLabels, [
    { label: 'Avg List Price', data: sorted.map(function (m) { return Number(m.average_price) || 0; }) },
  ], 'Price ($)');

  VestaCharts.lineChart('chart-dom', marketLabels, [
    { label: 'Avg DOM', data: sorted.map(function (m) { return Number(m.average_dom) || 0; }) },
  ], 'Days');

  VestaCharts.barChart('chart-leads', marketLabels, [
    { label: 'Leads', data: sorted.map(function (m) { return Number(m.total_leads) || 0; }) },
    { label: 'Showings', data: sorted.map(function (m) { return Number(m.total_showings) || 0; }) },
    { label: 'Apps', data: sorted.map(function (m) { return Number(m.total_applications) || 0; }) },
  ], 'Count');

  var segByMonth = aggregateSegmentsByMonth(monthlySeg);
  var segLabels = segByMonth.labels;

  VestaCharts.lineChart('chart-occ-rent', segLabels, [
    { label: 'Avg Occupied Rent', data: segByMonth.avgOccRent },
  ], 'Rent ($)');

  VestaCharts.barChart('chart-leases', segLabels, [
    { label: 'Leases Written', data: segByMonth.leasesWritten },
  ], 'Count');

  VestaCharts.lineChart('chart-lease-len', segLabels, [
    { label: 'Avg Lease Length', data: segByMonth.avgLeaseLen },
  ], 'Months');
}

function aggregateSegmentsByMonth(monthlySeg) {
  var monthMap = {};
  for (var i = 0; i < monthlySeg.length; i++) {
    var s = monthlySeg[i];
    var key = s.month;
    if (!monthMap[key]) {
      monthMap[key] = {
        occRentWeightedSum: 0, occCount: 0,
        leasesWritten: 0,
        leaseLenWeightedSum: 0, leaseLenCount: 0,
      };
    }
    var m = monthMap[key];
    var occ = s.occupied_unit_count || 0;
    if (s.avg_occupied_rent != null && occ > 0) {
      m.occRentWeightedSum += Number(s.avg_occupied_rent) * occ;
      m.occCount += occ;
    }
    m.leasesWritten += s.leases_written_count || 0;
    var lw = s.leases_written_count || 0;
    if (s.avg_lease_length_months != null && lw > 0) {
      m.leaseLenWeightedSum += Number(s.avg_lease_length_months) * lw;
      m.leaseLenCount += lw;
    }
  }

  var keys = Object.keys(monthMap).sort();
  var labels = keys.map(function (k) { return formatMonth(k); });
  var avgOccRent = keys.map(function (k) {
    var m = monthMap[k];
    return m.occCount > 0 ? Math.round(m.occRentWeightedSum / m.occCount) : 0;
  });
  var leasesWritten = keys.map(function (k) { return monthMap[k].leasesWritten; });
  var avgLeaseLen = keys.map(function (k) {
    var m = monthMap[k];
    return m.leaseLenCount > 0 ? Number((m.leaseLenWeightedSum / m.leaseLenCount).toFixed(1)) : 0;
  });

  return { labels: labels, avgOccRent: avgOccRent, leasesWritten: leasesWritten, avgLeaseLen: avgLeaseLen };
}

function formatMonth(reportMonth) {
  if (!reportMonth) return '\u2014';
  var parts = reportMonth.split('-');
  var monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var monthIdx = parseInt(parts[1], 10) - 1;
  return monthNames[monthIdx] + ' ' + parts[0];
}

// -- Pivot Table --------------------------------------------------------------

function renderPivotTable(segments) {
  _segmentsCache = segments;
  var metric = document.getElementById('pivot-metric').value || 'vacancy_rate';

  if (!segments || segments.length === 0) {
    VestaAPI.render('segment-pivot', '<div class="empty-state">No segment data available</div>');
    return;
  }

  // Build zip → bedroom → segment map
  var zipMap = {};
  var allBeds = {};
  for (var i = 0; i < segments.length; i++) {
    var s = segments[i];
    var parts = s.segment_label.split(' / ');
    var zip = parts[0] || 'Unknown';
    var brStr = (parts[1] || '').replace('BR', '').trim();
    var br = parseInt(brStr, 10);
    if (!zipMap[zip]) zipMap[zip] = {};
    zipMap[zip][br] = s;
    if (!isNaN(br)) allBeds[br] = true;
  }

  var zips = Object.keys(zipMap).sort();
  var beds = Object.keys(allBeds).map(Number).sort(function (a, b) { return a - b; });

  var metricLabel = {
    vacancy_rate: 'Vacancy Rate',
    avg_target_rent: 'Avg Target Rent',
    avg_active_lease_rent: 'Avg Lease Rent',
    unit_count: 'Total Units',
    occupied_count: 'Occupied',
    vacant_count: 'Vacant',
  }[metric] || metric;

  var html = '<table class="pivot-table">';
  html += '<thead><tr><th>' + metricLabel + '</th>';
  for (var bi = 0; bi < beds.length; bi++) {
    html += '<th>' + beds[bi] + 'BR</th>';
  }
  html += '</tr></thead><tbody>';

  for (var z = 0; z < zips.length; z++) {
    var zip = zips[z];
    html += '<tr><td class="pivot-zip">' + zip + '</td>';
    for (var bi2 = 0; bi2 < beds.length; bi2++) {
      var br2 = beds[bi2];
      var seg = zipMap[zip] && zipMap[zip][br2];
      if (!seg || seg.unit_count === 0) {
        html += '<td class="num pivot-empty">\u2014</td>';
      } else {
        var val = seg[metric];
        var display = formatPivotValue(metric, val, seg);
        var cls = getPivotCellClass(metric, val);
        html += '<td class="num' + (cls ? ' ' + cls : '') + '">' + display + '</td>';
      }
    }
    html += '</tr>';
  }

  html += '</tbody></table>';
  VestaAPI.render('segment-pivot', html);
}

function formatPivotValue(metric, val, seg) {
  if (metric === 'vacancy_rate') {
    if (val == null) return '\u2014';
    return VestaAPI.pct(val);
  }
  if (metric === 'avg_target_rent' || metric === 'avg_active_lease_rent') {
    if (!val || Number(val) === 0) return '\u2014';
    return VestaAPI.$(val);
  }
  if (val == null || val === 0) return '\u2014';
  return VestaAPI.num(val);
}

function getPivotCellClass(metric, val) {
  if (metric !== 'vacancy_rate' || val == null) return '';
  if (val <= 0) return 'pivot-cell-green';
  if (val < 8) return 'pivot-cell-green';
  if (val < 20) return 'pivot-cell-yellow';
  return 'pivot-cell-red';
}

// -- Filters ------------------------------------------------------------------

function getFilterParams() {
  var zip = document.getElementById('filter-zip').value.trim();
  var beds = document.getElementById('filter-beds').value;

  var params = [];
  if (zip) params.push('postal_code=' + encodeURIComponent(zip));
  if (beds) params.push('bedrooms=' + encodeURIComponent(beds));

  return params.length ? '?' + params.join('&') : '';
}

async function applyFilters() {
  var query = getFilterParams();

  VestaAPI.render('portfolio-stats', '<div class="loading">Loading...</div>');
  VestaAPI.render('segment-pivot', '<div class="loading">Loading segment data...</div>');

  try {
    var [portfolio, segments, monthlySegData] = await Promise.all([
      VestaAPI.get('/analytics/portfolio-summary' + query),
      VestaAPI.get('/analytics/rent-analysis' + query),
      VestaAPI.get('/market/monthly-segments?limit=500&offset=0'),
    ]);

    renderPortfolioStats(portfolio, segments);
    renderPivotTable(segments);

    var monthlySeg = monthlySegData.items || [];
    if (query) {
      var zip = document.getElementById('filter-zip').value.trim();
      var beds = document.getElementById('filter-beds').value;
      monthlySeg = monthlySeg.filter(function (s) {
        if (zip && s.zip_code !== zip) return false;
        if (beds && String(s.bedroom_count) !== beds) return false;
        return true;
      });
    }

    var segByMonth = aggregateSegmentsByMonth(monthlySeg);
    var segLabels = segByMonth.labels;
    VestaCharts.lineChart('chart-occ-rent', segLabels, [
      { label: 'Avg Occupied Rent', data: segByMonth.avgOccRent },
    ], 'Rent ($)');
    VestaCharts.barChart('chart-leases', segLabels, [
      { label: 'Leases Written', data: segByMonth.leasesWritten },
    ], 'Count');
    VestaCharts.lineChart('chart-lease-len', segLabels, [
      { label: 'Avg Lease Length', data: segByMonth.avgLeaseLen },
    ], 'Months');
  } catch (err) {
    console.error('Filter error:', err);
    VestaAPI.render('portfolio-stats', '<div class="loading">Error loading data</div>');
    VestaAPI.render('segment-pivot', '<div class="loading">Error loading data</div>');
  }
}
