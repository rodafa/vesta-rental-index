/**
 * Revenue Intelligence — rent gap, turn time, concessions.
 * Auto-refreshes every 60 seconds.
 */

async function loadAll() {
  try {
    var [summary, gaps, turns, concessions] = await Promise.all([
      VestaAPI.get('/analytics/revenue-leakage-summary'),
      VestaAPI.get('/analytics/rent-gap'),
      VestaAPI.get('/analytics/turn-cycles'),
      VestaAPI.get('/analytics/concession-tracker'),
    ]);

    renderStats(summary);
    renderGapTable(gaps);
    renderTurnTable(turns);
    renderConcessionTable(concessions);
  } catch (err) {
    console.error('Revenue Intelligence load error:', err);
    VestaAPI.render('revenue-stats', '<div class="loading">Error loading data</div>');
  }
}

document.addEventListener('DOMContentLoaded', function () {
  loadAll();
  setInterval(loadAll, 60000);
});

function renderStats(s) {
  var cards = [
    { label: 'Annual Rent Gap', value: VestaAPI.$(s.total_annual_rent_gap) },
    { label: 'Avg Gap %', value: VestaAPI.pct(s.avg_rent_gap_pct) },
    { label: 'Avg Turn Days', value: s.avg_turn_days != null ? VestaAPI.num(Math.round(s.avg_turn_days)) : '\u2014' },
    { label: 'Avg Concession Rate', value: s.avg_concession_rate != null ? VestaAPI.pct(s.avg_concession_rate) : '\u2014' },
    { label: 'Units Below Target', value: VestaAPI.num(s.units_below_target) + ' / ' + VestaAPI.num(s.total_occupied_revenue_units) },
  ];

  VestaAPI.render(
    'revenue-stats',
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

function renderGapTable(items) {
  if (!items || items.length === 0) {
    VestaAPI.render('gap-body', '<tr><td colspan="8" class="empty-state">No rent gaps detected</td></tr>');
    VestaAPI.render('gap-foot', '');
    return;
  }

  var totalGap = 0;
  var rows = items.map(function (item) {
    totalGap += Number(item.gap_amount);
    var rowClass = item.is_critical ? 'flagged' : '';
    var badge = item.is_critical
      ? '<span class="badge badge-red">Critical</span>'
      : '<span class="badge badge-yellow">Gap</span>';

    return (
      '<tr class="' + rowClass + '">' +
        '<td>' + (item.address || '\u2014') + '</td>' +
        '<td>' + (item.city || '\u2014') + '</td>' +
        '<td class="num">' + (item.bedrooms != null ? item.bedrooms : '\u2014') + '</td>' +
        '<td class="num">' + VestaAPI.$(item.target_rent) + '</td>' +
        '<td class="num">' + VestaAPI.$(item.lease_rent) + '</td>' +
        '<td class="num flag-value">' + VestaAPI.$(item.gap_amount) + '</td>' +
        '<td class="num">' + VestaAPI.pct(item.gap_pct) + '</td>' +
        '<td>' + badge + '</td>' +
      '</tr>'
    );
  });

  VestaAPI.render('gap-body', rows.join(''));
  VestaAPI.render(
    'gap-foot',
    '<tr style="font-weight:700;background:#f0f0f0">' +
      '<td colspan="5">Total Monthly Gap</td>' +
      '<td class="num">' + VestaAPI.$(totalGap) + '</td>' +
      '<td colspan="2"></td>' +
    '</tr>'
  );
}

function renderTurnTable(items) {
  if (!items || items.length === 0) {
    VestaAPI.render('turn-body', '<tr><td colspan="10" class="empty-state">No turn cycles found</td></tr>');
    return;
  }

  VestaAPI.render(
    'turn-body',
    items.map(function (item) {
      var rowClass = item.total_turn_days != null && item.total_turn_days > 45 ? 'flagged' : '';
      return (
        '<tr class="' + rowClass + '">' +
          '<td>' + (item.address || '\u2014') + '</td>' +
          '<td class="num">' + (item.bedrooms != null ? item.bedrooms : '\u2014') + '</td>' +
          '<td>' + VestaAPI.dateStr(item.move_out_date) + '</td>' +
          '<td>' + VestaAPI.dateStr(item.listed_date) + '</td>' +
          '<td>' + VestaAPI.dateStr(item.leased_date) + '</td>' +
          '<td>' + VestaAPI.dateStr(item.move_in_date) + '</td>' +
          '<td class="num">' + (item.make_ready_days != null ? item.make_ready_days + 'd' : '\u2014') + '</td>' +
          '<td class="num">' + (item.dom != null ? VestaAPI.num(item.dom) : '\u2014') + '</td>' +
          '<td class="num">' + (item.total_turn_days != null ? item.total_turn_days + 'd' : '\u2014') + '</td>' +
          '<td class="num">' + VestaAPI.$(item.vacancy_cost) + '</td>' +
        '</tr>'
      );
    }).join('')
  );
}

function renderConcessionTable(items) {
  if (!items || items.length === 0) {
    VestaAPI.render('concession-body', '<tr><td colspan="9" class="empty-state">No concession data</td></tr>');
    return;
  }

  VestaAPI.render(
    'concession-body',
    items.map(function (item) {
      var rowClass = item.is_heavy_concession ? 'flagged' : '';
      var badge = item.is_heavy_concession
        ? '<span class="badge badge-red">Heavy</span>'
        : '<span class="badge badge-green">Normal</span>';
      var ratio = item.list_to_lease_ratio != null
        ? (item.list_to_lease_ratio * 100).toFixed(1) + '%'
        : '\u2014';

      return (
        '<tr class="' + rowClass + '">' +
          '<td>' + (item.address || '\u2014') + '</td>' +
          '<td class="num">' + (item.bedrooms != null ? item.bedrooms : '\u2014') + '</td>' +
          '<td class="num">' + VestaAPI.$(item.original_price) + '</td>' +
          '<td class="num">' + VestaAPI.$(item.final_price) + '</td>' +
          '<td class="num">' + VestaAPI.$(item.signed_amount) + '</td>' +
          '<td class="num">' + ratio + '</td>' +
          '<td class="num">' + VestaAPI.num(item.total_drops) + '</td>' +
          '<td class="num">' + VestaAPI.$(item.total_drop_amount) + '</td>' +
          '<td>' + badge + '</td>' +
        '</tr>'
      );
    }).join('')
  );
}
