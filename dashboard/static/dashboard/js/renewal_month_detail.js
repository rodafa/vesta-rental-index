/**
 * Renewal Month Detail — leases expiring in a specific month.
 * Auto-refreshes every 60 seconds.
 */
var SORT_COL = null;
var SORT_DIR = 1;
var _data = [];
var _currentPage = 1;
var PAGE_SIZE = 25;

function loadAll() {
  VestaAPI.get('/analytics/renewal-leases?month=' + MONTH)
    .then(function(items) {
      _data = items;

      var heading = document.getElementById('month-heading');
      if (heading && items.length > 0) {
        var d = new Date(items[0].lease_end);
        var label = d.toLocaleDateString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
        heading.textContent = label + ' \u2014 Lease Expirations';
      } else if (heading) {
        heading.textContent = MONTH + ' \u2014 Lease Expirations';
      }

      renderStats(items);
      _currentPage = 1;
      applyFilters();
    })
    .catch(function(err) {
      console.error('Renewal detail load error:', err);
      VestaAPI.render('detail-body', '<tr><td colspan="13" class="loading">Error loading data</td></tr>');
    });
}

function renderStats(items) {
  var total = items.length;
  var belowMarket = items.filter(function(i) { return i.is_below_market; }).length;
  var rentItems = items.filter(function(i) { return i.current_rent != null; });
  var avgRent = 0;
  if (rentItems.length > 0) {
    avgRent = rentItems.reduce(function(sum, i) { return sum + parseFloat(i.current_rent); }, 0) / rentItems.length;
  }

  VestaAPI.render(
    'detail-stats',
    [
      { label: 'Leases This Month', value: VestaAPI.num(total) },
      { label: 'Below Market', value: VestaAPI.num(belowMarket) },
      { label: 'Avg Current Rent', value: avgRent > 0 ? VestaAPI.$(avgRent) : '\u2014' },
    ].map(function(c) {
      return (
        '<div class="stat-card">' +
          '<div class="label">' + c.label + '</div>' +
          '<div class="value">' + c.value + '</div>' +
        '</div>'
      );
    }).join('')
  );
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

function applyFilters() {
  _currentPage = 1;
  _renderFiltered();
}

function _renderFiltered() {
  var f = _getFilters();
  var filtered = _data.filter(function(item) {
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

  if (SORT_COL) {
    filtered = filtered.slice().sort(function(a, b) {
      var av = a[SORT_COL], bv = b[SORT_COL];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === 'string') return av.localeCompare(bv) * SORT_DIR;
      return (av - bv) * SORT_DIR;
    });
  }

  var total = filtered.length;
  var totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (_currentPage > totalPages) _currentPage = totalPages;

  var start = (_currentPage - 1) * PAGE_SIZE;
  var page = filtered.slice(start, start + PAGE_SIZE);

  var countEl = document.getElementById('filter-count');
  if (countEl) {
    countEl.textContent = total + ' of ' + _data.length + ' leases';
  }

  renderTable(page);
  renderPagination('detail-pagination', total, _currentPage, totalPages);
}

function clearFilters() {
  var ids = ['f-city', 'f-zip', 'f-beds', 'f-rent-min', 'f-rent-max', 'f-sqft-min', 'f-sqft-max'];
  for (var i = 0; i < ids.length; i++) {
    var el = document.getElementById(ids[i]);
    if (el) el.value = '';
  }
  _currentPage = 1;
  _renderFiltered();
}

// ---------------------------------------------------------------------------
// Sorting
// ---------------------------------------------------------------------------

function sortBy(col) {
  if (SORT_COL === col) {
    SORT_DIR *= -1;
  } else {
    SORT_COL = col;
    SORT_DIR = 1;
  }

  var cols = ['address', 'city', 'postal_code', 'bedrooms', 'bathrooms', 'square_feet', 'current_rent', 'target_rent', 'gap_pct', 'tenant_names', 'lease_end', 'days_until_expiry'];
  for (var i = 0; i < cols.length; i++) {
    var el = document.getElementById('sort-' + cols[i]);
    if (el) el.textContent = (cols[i] === SORT_COL) ? (SORT_DIR === 1 ? ' \u25b2' : ' \u25bc') : '';
  }

  _currentPage = 1;
  _renderFiltered();
}

// ---------------------------------------------------------------------------
// Pagination
// ---------------------------------------------------------------------------

function renderPagination(containerId, total, current, totalPages) {
  var el = document.getElementById(containerId);
  if (!el) return;

  if (totalPages <= 1) {
    el.innerHTML = '';
    return;
  }

  var start = (current - 1) * PAGE_SIZE + 1;
  var end = Math.min(current * PAGE_SIZE, total);

  var btnStyle = 'padding:.3rem .65rem;border:1px solid var(--border-color,#e2e8f0);border-radius:4px;background:var(--surface,#fff);cursor:pointer;font-size:.82rem;';
  var disabledStyle = btnStyle + 'opacity:.4;cursor:default;';

  el.innerHTML =
    '<span>Showing ' + start + '\u2013' + end + ' of ' + total + ' leases</span>' +
    '<div style="display:flex;gap:.35rem;align-items:center">' +
      '<button onclick="_goPage(1)" style="' + (current === 1 ? disabledStyle : btnStyle) + '" ' + (current === 1 ? 'disabled' : '') + '>&laquo;</button>' +
      '<button onclick="_goPage(' + (current - 1) + ')" style="' + (current === 1 ? disabledStyle : btnStyle) + '" ' + (current === 1 ? 'disabled' : '') + '>&lsaquo; Prev</button>' +
      '<span style="font-size:.82rem;padding:0 .4rem">Page ' + current + ' of ' + totalPages + '</span>' +
      '<button onclick="_goPage(' + (current + 1) + ')" style="' + (current === totalPages ? disabledStyle : btnStyle) + '" ' + (current === totalPages ? 'disabled' : '') + '>Next &rsaquo;</button>' +
      '<button onclick="_goPage(' + totalPages + ')" style="' + (current === totalPages ? disabledStyle : btnStyle) + '" ' + (current === totalPages ? 'disabled' : '') + '>&raquo;</button>' +
    '</div>';
}

function _goPage(p) {
  _currentPage = p;
  _renderFiltered();
  var tbl = document.getElementById('detail-table');
  if (tbl) tbl.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ---------------------------------------------------------------------------
// Table rendering
// ---------------------------------------------------------------------------

function renderTable(items) {
  if (!items || items.length === 0) {
    VestaAPI.render('detail-body', '<tr><td colspan="13" class="empty-state">No leases match your filters</td></tr>');
    return;
  }

  VestaAPI.render(
    'detail-body',
    items.map(function(item) {
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
          '<td class="num">' + VestaAPI.$(item.current_rent) + '</td>' +
          '<td class="num">' + VestaAPI.$(item.target_rent) + '</td>' +
          '<td class="num">' + gapStr + '</td>' +
          '<td>' + (item.tenant_names || '\u2014') + '</td>' +
          '<td>' + VestaAPI.dateStr(item.lease_end) + '</td>' +
          '<td class="num ' + daysClass + '">' + item.days_until_expiry + 'd</td>' +
          '<td>' + badge + '</td>' +
        '</tr>'
      );
    }).join('')
  );
}

document.addEventListener('DOMContentLoaded', function() {
  loadAll();
  setInterval(loadAll, 60000);
});
