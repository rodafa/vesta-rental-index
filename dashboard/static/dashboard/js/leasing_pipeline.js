/**
 * Leasing Pipeline — application funnel, screening, scorecards.
 * Auto-refreshes every 60 seconds (skips detail re-render while panel is open).
 */
var _funnelChart = null;
var _scorecardChart = null;
var _activeScorecard = null; // currently selected scorecard ID

async function loadAll() {
  try {
    var [stats, scorecards] = await Promise.all([
      VestaAPI.get('/analytics/leasing-pipeline'),
      VestaAPI.get('/analytics/leasing-pipeline/scorecards'),
    ]);

    renderStats(stats);
    renderFunnelChart(stats);
    renderScorecardChart(stats);
    renderScorecardsTable(scorecards);
  } catch (err) {
    console.error('Leasing Pipeline load error:', err);
    VestaAPI.render('pipeline-stats', '<div class="loading">Error loading data</div>');
  }
}

function renderStats(s) {
  var cards = [
    { label: 'Prospects 30d', value: VestaAPI.num(s.total_prospects_30d) },
    { label: 'Showings 30d', value: VestaAPI.num(s.total_showings_30d) },
    { label: 'Apps 30d', value: VestaAPI.num(s.total_applications_30d) },
    { label: 'Approved 30d', value: VestaAPI.num(s.total_approved_30d) },
    { label: 'Screening Pending', value: VestaAPI.num(s.screening_pending) },
    { label: 'Avg Score', value: s.avg_score != null ? VestaAPI.num(Math.round(s.avg_score)) : '\u2014' },
    { label: 'Auto-Denies', value: VestaAPI.num(s.auto_deny_count) },
    { label: 'Platinum', value: VestaAPI.num(s.platinum_count) },
  ];

  VestaAPI.render(
    'pipeline-stats',
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

function renderFunnelChart(stats) {
  var ctx = document.getElementById('funnel-chart');
  if (!ctx) return;

  var labels = ['Prospects', 'Showings', 'Applications', 'Approved'];
  var data = [
    stats.total_prospects_30d,
    stats.total_showings_30d,
    stats.total_applications_30d,
    stats.total_approved_30d,
  ];

  if (_funnelChart) _funnelChart.destroy();

  _funnelChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: '30-Day Funnel',
        data: data,
        backgroundColor: ['#4a90d9cc', '#50c878cc', '#f5a623cc', '#7b68eecc'],
        borderRadius: 3,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, title: { display: true, text: 'Count', font: { size: 11 } } },
      },
    },
  });
}

function renderScorecardChart(stats) {
  var ctx = document.getElementById('scorecard-chart');
  if (!ctx) return;

  var labels = ['Platinum', 'Strong', 'Borderline', 'High Risk', 'Reject', 'Auto-Deny'];
  var data = [
    stats.platinum_count,
    stats.strong_count,
    stats.borderline_count,
    stats.high_risk_count,
    stats.reject_count,
    stats.auto_deny_count,
  ];
  var colors = ['#7b68ee', '#50c878', '#f5a623', '#ff8c00', '#dc3545', '#6c757d'];

  if (_scorecardChart) _scorecardChart.destroy();

  _scorecardChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: labels,
      datasets: [{
        data: data,
        backgroundColor: colors,
      }],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { position: 'right', labels: { font: { size: 11 } } },
      },
    },
  });
}

function renderScorecardsTable(items) {
  if (!items || items.length === 0) {
    VestaAPI.render('scorecards-body', '<tr><td colspan="7" class="empty-state">No scorecards yet</td></tr>');
    return;
  }

  var recColors = {
    platinum: 'badge-purple',
    strong: 'badge-green',
    borderline: 'badge-yellow',
    high_risk: 'badge-orange',
    reject: 'badge-red',
    auto_deny: 'badge-red',
  };

  VestaAPI.render(
    'scorecards-body',
    items.map(function (item) {
      var badgeClass = recColors[item.recommendation] || '';
      var badge = '<span class="badge ' + badgeClass + '">' +
        item.recommendation.replace('_', ' ') + '</span>';
      var activeClass = _activeScorecard === item.id ? ' active-row' : '';

      return (
        '<tr class="clickable-row' + activeClass + '" data-id="' + item.id + '">' +
          '<td>' + (item.applicant_name || '\u2014') + '</td>' +
          '<td>' + (item.unit_address || '\u2014') + '</td>' +
          '<td class="num">' + VestaAPI.num(item.total_score) + '</td>' +
          '<td>' + badge + '</td>' +
          '<td>' + (item.screening_status || '\u2014') + '</td>' +
          '<td>' + (item.reviewed_by || '\u2014') + '</td>' +
          '<td>' + VestaAPI.dateStr(item.updated_at ? item.updated_at.split('T')[0] : null) + '</td>' +
        '</tr>'
      );
    }).join('')
  );

  // Bind click handlers
  var rows = document.querySelectorAll('#scorecards-body .clickable-row');
  for (var i = 0; i < rows.length; i++) {
    rows[i].addEventListener('click', onScorecardRowClick);
  }
}

function onScorecardRowClick(e) {
  var id = parseInt(e.currentTarget.getAttribute('data-id'), 10);
  if (!id) return;
  loadScorecardDetail(id);
}

async function loadScorecardDetail(id) {
  _activeScorecard = id;
  highlightActiveRow();

  var panel = document.getElementById('scorecard-detail');
  panel.style.display = 'block';
  panel.innerHTML = '<div class="loading">Loading scorecard...</div>';
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  try {
    var sc = await VestaAPI.get('/screening/scorecards/' + id + '/');
    renderScorecardDetail(sc);
  } catch (err) {
    console.error('Scorecard detail error:', err);
    panel.innerHTML = '<div class="loading">Error loading scorecard</div>';
  }
}

function highlightActiveRow() {
  var rows = document.querySelectorAll('#scorecards-body tr');
  for (var i = 0; i < rows.length; i++) {
    var rowId = parseInt(rows[i].getAttribute('data-id'), 10);
    if (rowId === _activeScorecard) {
      rows[i].classList.add('active-row');
    } else {
      rows[i].classList.remove('active-row');
    }
  }
}

function closeScorecardDetail() {
  _activeScorecard = null;
  var panel = document.getElementById('scorecard-detail');
  panel.style.display = 'none';
  panel.innerHTML = '';
  highlightActiveRow();
}

function renderScorecardDetail(sc) {
  var recColors = {
    platinum: '#7b68ee',
    strong: '#50c878',
    borderline: '#f5a623',
    high_risk: '#ff8c00',
    reject: '#dc3545',
    auto_deny: '#6c757d',
  };
  var recColor = recColors[sc.recommendation] || '#888';
  var recLabel = (sc.recommendation || '').replace('_', ' ');

  // Helper: boolean display
  function boolIcon(val) {
    return val
      ? '<span style="color:#28a745;font-weight:700;">\u2713</span>'
      : '<span style="color:#dc3545;font-weight:700;">\u2717</span>';
  }

  // Helper: choice display (readable label from snake_case)
  function choiceLabel(val) {
    if (!val) return '\u2014';
    return val.replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  // Helper: build a category grid
  function catGrid(title, maxPts, rows) {
    var html = '<div class="sc-category">' +
      '<div class="sc-cat-header">' + esc(title) + ' <span style="color:var(--text-muted);font-weight:400;">(' + maxPts + ' max)</span></div>';
    for (var i = 0; i < rows.length; i++) {
      html += '<div class="sc-cat-row">' +
        '<span class="sc-cat-label">' + rows[i][0] + '</span>' +
        '<span class="sc-cat-value">' + rows[i][1] + '</span>' +
        '</div>';
    }
    html += '</div>';
    return html;
  }

  function esc(text) {
    if (text == null) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(String(text)));
    return div.innerHTML;
  }

  // ── Auto-deny flags ──
  var denyFlags = [
    ['False Information', sc.auto_deny_false_info],
    ['Recent Eviction', sc.auto_deny_recent_eviction],
    ['Violent Felony', sc.auto_deny_violent_felony],
    ['Pet Not Disclosed', sc.auto_deny_pet_not_disclosed],
    ['No Co-Signer (required)', sc.auto_deny_no_cosigner],
  ];
  var triggered = [];
  for (var i = 0; i < denyFlags.length; i++) {
    if (denyFlags[i][1]) triggered.push(denyFlags[i][0]);
  }
  var denyHtml;
  if (triggered.length === 0) {
    denyHtml = '<span style="color:#28a745;">None triggered</span>';
  } else {
    denyHtml = triggered.map(function (f) {
      return '<span class="badge badge-red" style="margin-right:.35rem;">' + esc(f) + '</span>';
    }).join('');
  }

  var html =
    // ── Header ──
    '<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.5rem;margin-bottom:1rem;">' +
      '<div>' +
        '<h2 style="font-size:1.15rem;font-weight:700;margin:0;">' + esc(sc.applicant_name) + '</h2>' +
        '<span style="font-size:.8rem;color:var(--text-muted);">' +
          'Screening: ' + esc(sc.auto_populated ? 'auto-populated' : 'manual') +
          (sc.reviewed_by ? ' &middot; Reviewed by: ' + esc(sc.reviewed_by) : '') +
        '</span>' +
      '</div>' +
      '<div style="display:flex;align-items:center;gap:.75rem;">' +
        '<span class="badge" style="background:' + recColor + ';color:#fff;font-size:.8rem;padding:.25rem .7rem;">' +
          esc(recLabel) + '</span>' +
        '<span style="font-size:1.5rem;font-weight:800;">' + sc.total_score + '<span style="font-size:.8rem;font-weight:400;color:var(--text-muted);"> pts</span></span>' +
      '</div>' +
    '</div>' +

    // ── Category Grid ──
    '<div class="sc-grid">' +

    catGrid('Income & Employment', 30, [
      ['Income Ratio', choiceLabel(sc.income_ratio) + (sc.income_ratio_numeric != null ? ' (' + sc.income_ratio_numeric + 'x)' : '')],
      ['Employment Verified', boolIcon(sc.income_employment_verified)],
      ['Savings Verified', boolIcon(sc.income_savings_verified)],
    ]) +

    catGrid('Credit & Financial', 25, [
      ['Credit Tier', choiceLabel(sc.credit_tier) + (sc.credit_score_raw != null ? ' (' + sc.credit_score_raw + ')' : '')],
      ['Bankruptcy', choiceLabel(sc.bankruptcy_status)],
      ['Active Chargeoffs', boolIcon(sc.credit_active_chargeoffs)],
      ['DTI', choiceLabel(sc.dti_tier) + (sc.dti_numeric != null ? ' (' + sc.dti_numeric + '%)' : '')],
    ]) +

    catGrid('Pets & ESA', 10, [
      ['Pet Status', choiceLabel(sc.pet_status)],
    ]) +

    catGrid('Rental History', 20, [
      ['Positive Reference', boolIcon(sc.rental_positive_ref)],
      ['Would Rent Again', boolIcon(sc.rental_would_rent_again)],
      ['No Complaints', boolIcon(sc.rental_no_complaints)],
      ['Eviction History', choiceLabel(sc.eviction_history)],
      ['Owes Landlord', boolIcon(sc.rental_owes_landlord)],
    ]) +

    catGrid('Legal History', 10, [
      ['No Felonies (5yr)', boolIcon(sc.legal_no_felonies_5yr)],
      ['Nonviolent Felony (5yr+)', boolIcon(sc.legal_nonviolent_felony_over_5yr)],
      ['Drug Misdemeanor (3yr)', boolIcon(sc.legal_drug_misdemeanor_3yr)],
      ['Other Misdemeanors (3yr)', String(sc.legal_other_misdemeanor_3yr)],
      ['Settled Small Claims (3yr)', boolIcon(sc.legal_settled_small_claims_3yr)],
      ['Open Small Claims', boolIcon(sc.legal_open_small_claims)],
      ['Settled Landlord/Tenant', boolIcon(sc.legal_settled_landlord_tenant)],
      ['Unpaid Landlord Judgment', boolIcon(sc.legal_unpaid_landlord_judgment)],
    ]) +

    catGrid('Application', 17, [
      ['Completed', boolIcon(sc.app_completed)],
      ['Docs Verified', boolIcon(sc.app_docs_verified)],
      ['Good Communication', boolIcon(sc.app_good_communication)],
      ['On-Time Appointment', boolIcon(sc.app_on_time_appointment)],
    ]) +

    catGrid('Co-Signer', 15, [
      ['Strength', choiceLabel(sc.cosigner_strength)],
    ]) +

    // Auto-deny section in the grid
    '<div class="sc-category">' +
      '<div class="sc-cat-header">Auto-Deny Flags</div>' +
      '<div class="sc-cat-row">' + denyHtml + '</div>' +
    '</div>' +

    '</div>' +

    // ── Notes ──
    (sc.notes ? '<div style="margin-top:1rem;padding:.75rem;background:#f9f9ff;border-radius:6px;font-size:.85rem;">' +
      '<strong>Notes:</strong> ' + esc(sc.notes) + '</div>' : '') +

    // ── Close button ──
    '<div style="text-align:right;margin-top:1rem;">' +
      '<button class="btn btn-secondary" id="btn-close-scorecard">Close</button>' +
    '</div>';

  var panel = document.getElementById('scorecard-detail');
  panel.innerHTML = html;

  document.getElementById('btn-close-scorecard').addEventListener('click', closeScorecardDetail);
}

document.addEventListener('DOMContentLoaded', function () {
  // Inject scorecard detail styles
  var style = document.createElement('style');
  style.textContent =
    '.active-row td { background: #eef2ff !important; }' +
    '.sc-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }' +
    '.sc-category { background: #fafafa; border-radius: 6px; padding: .75rem; border: 1px solid var(--border); }' +
    '.sc-cat-header { font-size: .85rem; font-weight: 700; margin-bottom: .5rem; color: var(--accent); }' +
    '.sc-cat-row { display: flex; justify-content: space-between; padding: .2rem 0; font-size: .82rem; border-bottom: 1px solid #eee; }' +
    '.sc-cat-row:last-child { border-bottom: none; }' +
    '.sc-cat-label { color: var(--text-muted); }' +
    '.sc-cat-value { font-weight: 600; }' +
    '@media (max-width: 700px) { .sc-grid { grid-template-columns: 1fr; } }';
  document.head.appendChild(style);

  loadAll();
  setInterval(loadAll, 60000);
});
