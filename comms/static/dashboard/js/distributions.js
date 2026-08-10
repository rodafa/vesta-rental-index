/**
 * Vesta Dashboard — Owner Distributions (Stage 1: Generate + Review)
 *
 * Drives the distribution snapshots page.
 * Endpoints used:
 *   GET  /api/reports/distribution-snapshots?month=YYYY-MM
 *   POST /api/reports/distribution-snapshots/generate?month=YYYY-MM
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  function esc(s) { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

  function toast(msg, kind) {
    var t = $('toast');
    t.textContent = msg;
    t.className = 'toast show' + (kind ? (' ' + kind) : '');
    clearTimeout(t._t);
    t._t = setTimeout(function () { t.className = 'toast'; }, 3500);
  }

  function setStatus(kind, msg) {
    $('statusbar').className = 'statusbar is-' + kind;
    $('status-text').textContent = msg;
  }

  // --- Money-safe helpers ---

  /** Parse a decimal string like "1234.56" to integer cents. */
  function parseCents(s) {
    if (s == null || s === '' || s === 'None') return 0;
    var str = String(s);
    var neg = str.charAt(0) === '-';
    if (neg) str = str.substring(1);
    var parts = str.split('.');
    var dollars = parseInt(parts[0], 10) || 0;
    var cents = 0;
    if (parts[1] != null) {
      var frac = (parts[1] + '00').substring(0, 2);
      cents = parseInt(frac, 10) || 0;
    }
    var total = dollars * 100 + cents;
    return neg ? -total : total;
  }

  /** Convert integer cents to formatted display string "$1,234.56". */
  function centsToDisplay(cents) {
    var neg = cents < 0;
    cents = Math.abs(cents);
    var d = Math.floor(cents / 100);
    var c = cents % 100;
    var dStr = d.toLocaleString('en-US');
    return (neg ? '-$' : '$') + dStr + '.' + String(c).padStart(2, '0');
  }

  /** Format a decimal string from the API for display. */
  function formatMoney(s) {
    if (s == null || s === '' || s === 'None') return '\u2014';
    return centsToDisplay(parseCents(s));
  }

  /** Format a date string like "2025-07-15" for display. */
  function formatDate(s) {
    if (!s || s === 'None') return '\u2014';
    var d = new Date(s + 'T00:00:00');
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }

  // --- Month helpers ---

  function monthsList(count) {
    var months = [];
    var d = new Date();
    d.setDate(1);
    d.setMonth(d.getMonth() - 1); // start from last month
    for (var i = 0; i < count; i++) {
      var y = d.getFullYear();
      var m = d.getMonth() + 1;
      months.push({
        value: y + '-' + String(m).padStart(2, '0'),
        label: d.toLocaleDateString('en-US', { month: 'long', year: 'numeric' }),
      });
      d.setMonth(d.getMonth() - 1);
    }
    return months;
  }

  function formatMonth(ym) {
    var d = new Date(ym + '-01T00:00:00');
    return d.toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
  }

  // --- State ---
  var selectedMonth = monthsList(1)[0].value; // default to last month

  // --- Init month selector ---
  function initMonthSelector() {
    var sel = $('month-select');
    var months = monthsList(6);
    sel.innerHTML = months.map(function (m) {
      return '<option value="' + m.value + '">' + m.label + '</option>';
    }).join('');
    sel.value = selectedMonth;
    $('month-label').textContent = 'Distribution Snapshots';

    sel.addEventListener('change', function () {
      selectedMonth = sel.value;
      loadSnapshots();
    });
  }

  // --- Show/hide states ---
  function showPlaceholder() {
    $('placeholder').style.display = 'flex';
    $('spinner').classList.remove('show');
    $('results').classList.remove('show');
  }
  function showSpinner() {
    $('placeholder').style.display = 'none';
    $('spinner').classList.add('show');
    $('results').classList.remove('show');
  }
  function showResults() {
    $('placeholder').style.display = 'none';
    $('spinner').classList.remove('show');
    $('results').classList.add('show');
  }

  // --- Load existing snapshots ---
  function loadSnapshots() {
    VestaAPI.get('/reports/distribution-snapshots?month=' + selectedMonth)
      .then(function (data) {
        if (data && data.length > 0) {
          renderSnapshotTable(data);
          showResults();
          setStatus('done', data.length + ' snapshot' + (data.length === 1 ? '' : 's') + ' loaded.');
        } else {
          showPlaceholder();
          setStatus('idle', 'No snapshots for ' + formatMonth(selectedMonth) + '. Click Generate to create them.');
        }
      })
      .catch(function (e) {
        showPlaceholder();
        setStatus('warn', 'Failed to load snapshots: ' + e.message);
      });
  }

  // --- Render snapshot table (from GET list_snapshots) ---
  function renderSnapshotTable(snapshots) {
    var totalCents = 0;
    var html = snapshots.map(function (s) {
      totalCents += parseCents(s.distribution_amount);
      return '<tr>' +
        '<td>' + esc(s.portfolio_name) + '</td>' +
        '<td><span class="badge badge-snapshot">snapshot</span></td>' +
        '<td class="money">' + formatMoney(s.distribution_amount) + '</td>' +
        '<td>' + formatDate(s.distribution_date) + '</td>' +
        '<td class="num">' + (s.line_items_count || 0) + ' items</td>' +
        '</tr>';
    }).join('');

    // Simplified table header for snapshot view (fewer columns)
    var thead = '<tr>' +
      '<th>Portfolio</th>' +
      '<th>Status</th>' +
      '<th class="num">Distribution</th>' +
      '<th>Date</th>' +
      '<th class="num">Line Items</th>' +
      '</tr>';
    $('results-body').closest('table').querySelector('thead').innerHTML = thead;

    $('results-body').innerHTML = html;
    $('results-foot').innerHTML = '<tr><td colspan="2"><strong>Grand Total</strong></td>' +
      '<td class="money is-green">' + centsToDisplay(totalCents) + '</td>' +
      '<td colspan="2"></td></tr>';
    $('results-meta').textContent = formatMonth(selectedMonth) + ' \u00b7 ' + snapshots.length + ' portfolio' + (snapshots.length === 1 ? '' : 's');
    $('errors-box').innerHTML = '';
  }

  // --- Render generate results (from POST generate) ---
  function renderGenerateResults(data) {
    var outcomes = data.outcomes || [];
    var totalCents = 0;
    var errorOutcomes = [];

    // Restore full table header for generate view
    var thead = '<tr>' +
      '<th>Portfolio</th>' +
      '<th>Status</th>' +
      '<th class="num">Expected</th>' +
      '<th class="num">Collected</th>' +
      '<th class="num">Distribution</th>' +
      '<th>Date</th>' +
      '<th class="num">Undeposited</th>' +
      '<th>Source</th>' +
      '</tr>';
    $('results-body').closest('table').querySelector('thead').innerHTML = thead;

    var html = outcomes.map(function (o) {
      var isError = o.status === 'error' || o.error_message;
      if (isError) errorOutcomes.push(o);

      var distCents = parseCents(o.distribution_amount);
      totalCents += distCents;

      var statusClass = o.status;
      if (isError) statusClass = 'error';

      var undepositedHtml = '';
      if (parseCents(o.undeposited_amount) !== 0) {
        undepositedHtml = formatMoney(o.undeposited_amount);
      } else {
        undepositedHtml = '\u2014';
      }

      return '<tr class="' + (isError ? 'error-row' : '') + '">' +
        '<td>' + esc(o.portfolio_name) +
          (isError && o.error_message ? '<div class="error-msg">' + esc(o.error_message) + '</div>' : '') +
        '</td>' +
        '<td><span class="badge badge-' + esc(statusClass) + '">' + esc(o.status) + '</span></td>' +
        '<td class="money">' + formatMoney(o.expected) + '</td>' +
        '<td class="money">' + formatMoney(o.collected) + '</td>' +
        '<td class="money is-green">' + formatMoney(o.distribution_amount) + '</td>' +
        '<td>' + formatDate(o.distribution_date) + '</td>' +
        '<td class="money">' + undepositedHtml + '</td>' +
        '<td>' + esc(o.undeposited_source || '') + '</td>' +
        '</tr>';
    }).join('');

    $('results-body').innerHTML = html;
    $('results-foot').innerHTML = '<tr>' +
      '<td colspan="4"><strong>Grand Total</strong></td>' +
      '<td class="money is-green">' + centsToDisplay(totalCents) + '</td>' +
      '<td colspan="3"></td></tr>';

    $('results-meta').textContent = formatMonth(selectedMonth) + ' \u00b7 ' +
      (data.created || 0) + ' created, ' + (data.updated || 0) + ' updated' +
      (data.errors ? ', ' + data.errors + ' error(s)' : '');

    // Errors summary box
    if (errorOutcomes.length > 0) {
      $('errors-box').innerHTML = '<div class="errors-summary"><h3>' + errorOutcomes.length +
        ' portfolio(s) had errors</h3><ul>' +
        errorOutcomes.map(function (o) {
          return '<li><strong>' + esc(o.portfolio_name) + ':</strong> ' + esc(o.error_message || 'Unknown error') + '</li>';
        }).join('') +
        '</ul></div>';
    } else {
      $('errors-box').innerHTML = '';
    }
  }

  // --- Generate ---
  function openGenerateConfirm() {
    $('gen-modal-title').textContent = 'Generate distribution snapshots for ' + formatMonth(selectedMonth) + '?';
    $('gen-modal').classList.add('show');
  }
  function closeGenerateConfirm() { $('gen-modal').classList.remove('show'); }

  function doGenerate() {
    closeGenerateConfirm();
    showSpinner();
    setStatus('running', 'Generating distribution snapshots... this takes ~90 seconds.');
    $('generate').disabled = true;

    VestaAPI.post('/reports/distribution-snapshots/generate?month=' + selectedMonth, {})
      .then(function (data) {
        $('generate').disabled = false;
        if (!data.ok) {
          showPlaceholder();
          setStatus('warn', 'Generation returned error: ' + (data.error || 'Unknown'));
          toast('Generation failed: ' + (data.error || 'Unknown'), 'warn');
          return;
        }
        renderGenerateResults(data);
        showResults();
        var msg = (data.created || 0) + ' created, ' + (data.updated || 0) + ' updated';
        if (data.errors) msg += ', ' + data.errors + ' error(s)';
        setStatus(data.errors ? 'warn' : 'done', 'Generation complete \u2014 ' + msg + '.');
        toast('Generation complete.', data.errors ? 'warn' : 'ok');
      })
      .catch(function (e) {
        $('generate').disabled = false;
        showPlaceholder();
        setStatus('warn', 'Generation failed: ' + e.message);
        toast('Generation failed: ' + e.message, 'warn');
      });
  }

  $('generate').addEventListener('click', openGenerateConfirm);
  $('gen-cancel').addEventListener('click', closeGenerateConfirm);
  $('gen-confirm').addEventListener('click', doGenerate);
  $('gen-modal').addEventListener('click', function (e) { if (e.target === this) closeGenerateConfirm(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeGenerateConfirm(); });

  // --- Init ---
  initMonthSelector();
  loadSnapshots();

})();
