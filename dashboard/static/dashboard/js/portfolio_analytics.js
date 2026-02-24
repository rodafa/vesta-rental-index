/**
 * Portfolio Analytics — portfolio-wide trends, charts, and segment comparison.
 * Fetches monthly-reports (market-level), monthly-segments (zip/bed detail),
 * rent-analysis (segments), and portfolio-summary.
 */
document.addEventListener('DOMContentLoaded', async () => {
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
    renderSegmentTable(segmentsData);
  } catch (err) {
    console.error('Portfolio Analytics load error:', err);
    VestaAPI.render('portfolio-stats', '<div class="loading">Error loading data</div>');
    VestaAPI.render('segment-body', '<tr><td colspan="7" class="loading">Error loading data</td></tr>');
  }

  document.getElementById('filter-apply').addEventListener('click', applyFilters);
});

// -- Portfolio Stats ----------------------------------------------------------

function renderPortfolioStats(portfolio, segments) {
  var vacancyRate = portfolio.total_units
    ? ((portfolio.vacant_units / portfolio.total_units) * 100)
    : 0;

  // Compute occupied vs vacant averages from rent-analysis segments
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
  // Monthly reports (market-level): sort ascending
  var sorted = months.slice().sort(function (a, b) {
    return a.report_month < b.report_month ? -1 : a.report_month > b.report_month ? 1 : 0;
  });
  var marketLabels = sorted.map(function (m) { return formatMonth(m.report_month); });

  // Chart 1: Avg List Price (from monthly-reports)
  VestaCharts.lineChart('chart-list-price', marketLabels, [
    { label: 'Avg List Price', data: sorted.map(function (m) { return Number(m.average_price) || 0; }) },
  ], 'Price ($)');

  // Chart 3: Avg DOM (from monthly-reports)
  VestaCharts.lineChart('chart-dom', marketLabels, [
    { label: 'Avg DOM', data: sorted.map(function (m) { return Number(m.average_dom) || 0; }) },
  ], 'Days');

  // Chart 5: Leads, Showings & Apps (from monthly-reports)
  VestaCharts.barChart('chart-leads', marketLabels, [
    { label: 'Leads', data: sorted.map(function (m) { return Number(m.total_leads) || 0; }) },
    { label: 'Showings', data: sorted.map(function (m) { return Number(m.total_showings) || 0; }) },
    { label: 'Apps', data: sorted.map(function (m) { return Number(m.total_applications) || 0; }) },
  ], 'Count');

  // Aggregate monthly-segments by month for portfolio-wide trends
  var segByMonth = aggregateSegmentsByMonth(monthlySeg);
  var segLabels = segByMonth.labels;

  // Chart 2: Avg Occupied Rent (from monthly-segments)
  VestaCharts.lineChart('chart-occ-rent', segLabels, [
    { label: 'Avg Occupied Rent', data: segByMonth.avgOccRent },
  ], 'Rent ($)');

  // Chart 4: Leases Written (from monthly-segments)
  VestaCharts.barChart('chart-leases', segLabels, [
    { label: 'Leases Written', data: segByMonth.leasesWritten },
  ], 'Count');

  // Chart 6: Avg Lease Length (from monthly-segments)
  VestaCharts.lineChart('chart-lease-len', segLabels, [
    { label: 'Avg Lease Length', data: segByMonth.avgLeaseLen },
  ], 'Months');
}

function aggregateSegmentsByMonth(monthlySeg) {
  // Group by month, weighted-average or sum across zip/bedroom combos
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

// -- Segment Table ------------------------------------------------------------

function renderSegmentTable(segments) {
  if (!segments || segments.length === 0) {
    VestaAPI.render(
      'segment-body',
      '<tr><td colspan="7" class="empty-state">No segment data available</td></tr>'
    );
    return;
  }

  var rows = '';
  for (var i = 0; i < segments.length; i++) {
    var s = segments[i];
    rows +=
      '<tr>' +
        '<td>' + (s.segment_label || '\u2014') + '</td>' +
        '<td class="num">' + VestaAPI.num(s.unit_count) + '</td>' +
        '<td class="num">' + VestaAPI.num(s.occupied_count) + '</td>' +
        '<td class="num">' + VestaAPI.num(s.vacant_count) + '</td>' +
        '<td class="num">' + VestaAPI.pct(s.vacancy_rate) + '</td>' +
        '<td class="num">' + VestaAPI.$(s.avg_target_rent) + '</td>' +
        '<td class="num">' + VestaAPI.$(s.avg_active_lease_rent) + '</td>' +
      '</tr>';
  }

  VestaAPI.render('segment-body', rows);
}

// -- Filters ------------------------------------------------------------------

async function applyFilters() {
  var zip = document.getElementById('filter-zip').value.trim();
  var beds = document.getElementById('filter-beds').value;

  var params = [];
  if (zip) params.push('postal_code=' + encodeURIComponent(zip));
  if (beds) params.push('bedrooms=' + encodeURIComponent(beds));

  var query = params.length ? '?' + params.join('&') : '';

  VestaAPI.render(
    'segment-body',
    '<tr><td colspan="7" class="loading">Loading segment data...</td></tr>'
  );

  try {
    var segments = await VestaAPI.get('/analytics/rent-analysis' + query);
    renderSegmentTable(segments);
  } catch (err) {
    console.error('Filter error:', err);
    VestaAPI.render(
      'segment-body',
      '<tr><td colspan="7" class="loading">Error loading data</td></tr>'
    );
  }
}
