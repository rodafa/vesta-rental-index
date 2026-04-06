/**
 * Renewal Pipeline — expirations, clustering, below-market flags.
 * Auto-refreshes every 60 seconds.
 */
var _clusteringChart = null;
var _leaseData = [];
var _sortCol = null;
var _sortDir = 1;
var _selectedMonths = {}; // keyed by "YYYY-MM", value true when selected

async function loadAll() {
  try {
    var data = await VestaAPI.get('/analytics/renewal-pipeline?months_ahead=6');
    renderStats(data);
    renderClusteringChart(data.clustering, data.total_active_leases);
    _leaseData = data.leases;
    _buildMonthChips();
    _applyFilters();
  } catch (err) {
    console.error('Renewal Pipeline load error:', err);
    VestaAPI.render('renewal-stats', '<div class="loading">Error loading data</div>');
  }
}

document.addEventListener('DOMContentLoaded', function () {
  loadAll();
  setInterval(loadAll, 60000);
});

function renderStats(d) {
  var cards = [
    { label: 'Expiring 30d', value: VestaAPI.num(d.expiring_30d) },
    { label: 'Expiring 60d', value: VestaAPI.num(d.expiring_60d) },
    { label: 'Expiring 90d', value: VestaAPI.num(d.expiring_90d) },
    { label: 'Below Market', value: VestaAPI.num(d.below_market_count) },
  ];

  VestaAPI.render(
    'renewal-stats',
    cards.map(function (c) {
      return (
        '<div class="stat-card">' +
          '<div class="label">' + c.label + '</div>' +
          '<div class="value">' + c.value + '</div>' +
        '</div>'
      );
    }).join('')
  );
}

function renderClusteringChart(clustering, totalActive) {
  if (!clustering || clustering.length === 0) return;

  var labels = clustering.map(function (c) { return c.month_label; });
  var counts = clustering.map(function (c) { return c.count; });
  var threshold = Math.round(totalActive * 0.15);
  var colors = clustering.map(function (c) {
    return c.is_concentrated ? '#dc3545cc' : '#6EA5CDcc';
  });

  var concentrated = clustering.filter(function (c) { return c.is_concentrated; });

  var ctx = document.getElementById('clustering-chart');
  if (!ctx) return;

  if (_clusteringChart) _clusteringChart.destroy();

  _clusteringChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: 'Expiring Leases',
        data: counts,
        backgroundColor: colors,
        borderRadius: 3,
      }],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
      },
      scales: {
        y: {
          beginAtZero: true,
          title: { display: true, text: 'Leases Expiring', font: { size: 11 } },
        },
      },
      onClick: function(event, elements) {
        if (!elements || elements.length === 0) return;
        var idx = elements[0].index;
        var monthKey = clustering[idx].month;
        window.location.href = '/dashboard/renewals/month/' + monthKey + '/';
      },
    },
    plugins: [{
      id: 'thresholdLine',
      afterDraw: function (chart) {
        if (threshold <= 0) return;
        var yScale = chart.scales.y;
        var yPixel = yScale.getPixelForValue(threshold);
        var ctx2 = chart.ctx;
        ctx2.save();
        ctx2.beginPath();
        ctx2.setLineDash([6, 4]);
        ctx2.strokeStyle = '#dc3545';
        ctx2.lineWidth = 1.5;
        ctx2.moveTo(chart.chartArea.left, yPixel);
        ctx2.lineTo(chart.chartArea.right, yPixel);
        ctx2.stroke();
        ctx2.fillStyle = '#dc3545';
        ctx2.font = '11px sans-serif';
        ctx2.fillText('15% threshold (' + threshold + ')', chart.chartArea.right - 140, yPixel - 5);
        ctx2.restore();
      },
    }],
  });

  ctx.style.cursor = 'pointer';

  var calloutEl = document.getElementById('clustering-callout');
  if (calloutEl) {
    if (concentrated.length > 0) {
      var names = concentrated.map(function (c) { return c.month_label; }).join(', ');
      calloutEl.textContent = 'Concentration risk: ' + names + ' exceed' + (concentrated.length === 1 ? 's' : '') + ' 15% of active leases. Click a bar to view details.';
      calloutEl.style.color = 'var(--red-accent)';
    } else {
      calloutEl.textContent = 'No concentration risk detected. Expirations are well-distributed. Click a bar to view details.';
    }
  }
}

// ---------------------------------------------------------------------------
// Month chips
// ---------------------------------------------------------------------------

function _buildMonthChips() {
  var container = document.getElementById('month-chips');
  if (!container) return;

  // Collect unique months in order from the data
  var seen = {};
  var months = []; // [{key: "2026-06", label: "Jun 2026"}, ...]
  for (var i = 0; i < _leaseData.length; i++) {
    var end = _leaseData[i].lease_end; // "YYYY-MM-DD"
    if (!end) continue;
    var key = end.slice(0, 7); // "YYYY-MM"
    if (!seen[key]) {
      seen[key] = true;
      var parts = key.split('-');
      var y = parseInt(parts[0]);
      var m = parseInt(parts[1]);
      var monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      months.push({ key: key, label: monthNames[m - 1] + ' ' + y });
    }
  }

  // Preserve any existing selections that are still valid
  var newSelected = {};
  for (var k in _selectedMonths) {
    if (_selectedMonths[k] && seen[k]) newSelected[k] = true;
  }
  _selectedMonths = newSelected;

  container.innerHTML = months.map(function(mo) {
    var active = _selectedMonths[mo.key] ? true : false;
    return (
      '<button onclick="_toggleMonth(\'' + mo.key + '\')" id="chip-' + mo.key + '" ' +
      'style="padding:.25rem .6rem;border-radius:20px;font-size:.78rem;cursor:pointer;border:1px solid var(--border-color,#e2e8f0);' +
      (active ? 'background:var(--blue-accent,#3b82f6);color:#fff;border-color:var(--blue-accent,#3b82f6);font-weight:600;' : 'background:var(--surface,#fff);color:var(--text-muted);') +
      '">' + mo.label + '</button>'
    );
  }).join('');
}

function _toggleMonth(key) {
  if (_selectedMonths[key]) {
    delete _selectedMonths[key];
  } else {
    _selectedMonths[key] = true;
  }

  // Update chip appearance in-place
  var chip = document.getElementById('chip-' + key);
  if (chip) {
    var active = _selectedMonths[key] ? true : false;
    chip.style.background = active ? 'var(--blue-accent,#3b82f6)' : 'var(--surface,#fff)';
    chip.style.color = active ? '#fff' : 'var(--text-muted)';
    chip.style.borderColor = active ? 'var(--blue-accent,#3b82f6)' : 'var(--border-color,#e2e8f0)';
    chip.style.fontWeight = active ? '600' : '';
  }

  _applyFilters();
}

// ---------------------------------------------------------------------------
// Filtering
// ---------------------------------------------------------------------------

function _getFilters() {
  return {
    city: (document.getElementById('f-city') || {}).value || '',
    zip: (document.getElementById('f-zip') || {}).value || '',
    beds: (document.getElementById('f-beds') || {}).value || '',
    rentMin: parseFloat((document.getElementById('f-rent-min') || {}).value) || null,
    rentMax: parseFloat((document.getElementById('f-rent-max') || {}).value) || null,
    sqftMin: parseInt((document.getElementById('f-sqft-min') || {}).value) || null,
    sqftMax: parseInt((document.getElementById('f-sqft-max') || {}).value) || null,
  };
}

function _applyFilters() {
  var f = _getFilters();
  var hasMonthFilter = Object.keys(_selectedMonths).length > 0;

  var filtered = _leaseData.filter(function(item) {
    if (hasMonthFilter) {
      var key = (item.lease_end || '').slice(0, 7);
      if (!_selectedMonths[key]) return false;
    }
    if (f.city && item.city.toLowerCase().indexOf(f.city.toLowerCase()) === -1) return false;
    if (f.zip && (item.postal_code || '').indexOf(f.zip) === -1) return false;
    if (f.beds) {
      var beds = item.bedrooms;
      if (f.beds === '4') { if (beds == null || beds < 4) return false; }
      else { if (beds == null || beds !== parseInt(f.beds)) return false; }
    }
    if (f.rentMin != null && (item.current_rent == null || parseFloat(item.current_rent) < f.rentMin)) return false;
    if (f.rentMax != null && (item.current_rent == null || parseFloat(item.current_rent) > f.rentMax)) return false;
    if (f.sqftMin != null && (item.square_feet == null || item.square_feet < f.sqftMin)) return false;
    if (f.sqftMax != null && (item.square_feet == null || item.square_feet > f.sqftMax)) return false;
    return true;
  });

  var countEl = document.getElementById('filter-count');
  if (countEl) {
    countEl.textContent = filtered.length + ' of ' + _leaseData.length + ' leases';
  }

  if (_sortCol) {
    filtered = filtered.sort(function(a, b) {
      var av = a[_sortCol], bv = b[_sortCol];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === 'string') return av.localeCompare(bv) * _sortDir;
      return (av - bv) * _sortDir;
    });
  }

  renderRenewalTable(filtered);
}

function _clearFilters() {
  var ids = ['f-city', 'f-zip', 'f-beds', 'f-rent-min', 'f-rent-max', 'f-sqft-min', 'f-sqft-max'];
  for (var i = 0; i < ids.length; i++) {
    var el = document.getElementById(ids[i]);
    if (el) el.value = '';
  }
  _selectedMonths = {};
  _buildMonthChips();
  _applyFilters();
}

// ---------------------------------------------------------------------------
// Sorting
// ---------------------------------------------------------------------------

function _sortLeases(col) {
  if (_sortCol === col) {
    _sortDir *= -1;
  } else {
    _sortCol = col;
    _sortDir = 1;
  }
  _applyFilters();
}

// ---------------------------------------------------------------------------
// Table rendering
// ---------------------------------------------------------------------------

function renderRenewalTable(items) {
  if (!items || items.length === 0) {
    VestaAPI.render('renewal-body', '<tr><td colspan="13" class="empty-state">No expiring leases match your filters</td></tr>');
    return;
  }

  var allCols = ['address', 'city', 'postal_code', 'bedrooms', 'bathrooms', 'square_feet', 'tenant_names', 'lease_end', 'days_until_expiry', 'current_rent', 'target_rent', 'gap_pct'];
  for (var i = 0; i < allCols.length; i++) {
    var el = document.getElementById('rth-' + allCols[i]);
    if (!el) continue;
    el.textContent = (_sortCol === allCols[i]) ? (_sortDir === 1 ? ' \u25b2' : ' \u25bc') : '';
  }

  VestaAPI.render(
    'renewal-body',
    items.map(function (item) {
      var rowClass = item.is_below_market ? 'flagged' : '';
      var badge = item.is_below_market
        ? '<span class="badge badge-red">Below Market</span>'
        : '<span class="badge badge-green">On Target</span>';
      var gapStr = item.gap_pct != null ? VestaAPI.pct(item.gap_pct) : '\u2014';
      var daysClass = item.days_until_expiry <= 30 ? 'flag-value' : '';
      var bathStr = item.bathrooms != null ? item.bathrooms : '\u2014';
      var sqftStr = item.square_feet != null ? item.square_feet.toLocaleString() : '\u2014';

      return (
        '<tr class="' + rowClass + '">' +
          '<td>' + (item.address || '\u2014') + '</td>' +
          '<td>' + (item.city || '\u2014') + '</td>' +
          '<td>' + (item.postal_code || '\u2014') + '</td>' +
          '<td class="num">' + (item.bedrooms != null ? item.bedrooms : '\u2014') + '</td>' +
          '<td class="num">' + bathStr + '</td>' +
          '<td class="num">' + sqftStr + '</td>' +
          '<td>' + (item.tenant_names || '\u2014') + '</td>' +
          '<td>' + VestaAPI.dateStr(item.lease_end) + '</td>' +
          '<td class="num ' + daysClass + '">' + item.days_until_expiry + 'd</td>' +
          '<td class="num">' + VestaAPI.$(item.current_rent) + '</td>' +
          '<td class="num">' + VestaAPI.$(item.target_rent) + '</td>' +
          '<td class="num">' + gapStr + '</td>' +
          '<td>' + badge + '</td>' +
        '</tr>'
      );
    }).join('')
  );
}
