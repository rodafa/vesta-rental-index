/**
 * Daily Pulse — Monday morning overview.
 * Loads headline stats and active listings alert table.
 */
var _allListings = [];
var _domFilterActive = false;

document.addEventListener('DOMContentLoaded', async () => {
  try {
    var [portfolio, dailyStats, funnel, listings] = await Promise.all([
      VestaAPI.get('/analytics/portfolio-summary'),
      VestaAPI.get('/market/daily-stats?limit=1&offset=0'),
      VestaAPI.get('/analytics/leasing-funnel?date_from=' + VestaAPI.daysAgo(7)),
      VestaAPI.get('/analytics/active-listings'),
    ]);

    _allListings = listings || [];

    var stats = dailyStats.items ? dailyStats.items[0] : null;
    var occupancy = portfolio.total_units
      ? (portfolio.occupied_units / portfolio.total_units) * 100
      : 0;

    // Render headline stat cards
    var cards = [
      { label: 'Active Units', value: VestaAPI.num(stats ? stats.active_unit_count : null) },
      { label: 'Avg DOM', value: stats ? VestaAPI.num(stats.average_dom) : '\u2014' },
      { label: 'Median DOM', value: stats ? VestaAPI.num(stats.median_dom) : '\u2014' },
      { label: 'Avg List Price', value: VestaAPI.$(stats ? stats.average_price : null) },
      { label: 'Median List Price', value: VestaAPI.$(stats ? stats.median_price : null) },
      { label: '30+ DOM', value: VestaAPI.num(stats ? stats.count_30_plus_dom : null), id: 'stat-30-dom', clickable: true },
      { label: 'Avg Portfolio Rent', value: VestaAPI.$(stats ? stats.average_portfolio_rent : null) },
      { label: 'Occupancy Rate', value: VestaAPI.pct(occupancy) },
      { label: 'Leads 7d', value: VestaAPI.num(funnel.total_prospects) },
      { label: 'Showings 7d', value: VestaAPI.num(funnel.total_showings_completed) },
      { label: 'Missed 7d', value: VestaAPI.num(funnel.total_showings_missed) },
      { label: 'Apps 7d', value: VestaAPI.num(funnel.total_applications) },
    ];

    VestaAPI.render(
      'headline-stats',
      cards
        .map(function (c) {
          var extraClass = c.clickable ? ' clickable' : '';
          var idAttr = c.id ? ' id="' + c.id + '"' : '';
          return (
            '<div class="stat-card' + extraClass + '"' + idAttr + '>' +
              '<div class="label">' + c.label + '</div>' +
              '<div class="value">' + c.value + '</div>' +
            '</div>'
          );
        })
        .join('')
    );

    // Bind 30+ DOM click handler
    var domCard = document.getElementById('stat-30-dom');
    if (domCard) {
      domCard.addEventListener('click', function () {
        _domFilterActive = !_domFilterActive;
        if (_domFilterActive) {
          domCard.classList.add('active-filter');
          var filtered = _allListings.filter(function (item) {
            return item.days_on_market >= 30;
          });
          renderListingsTable(filtered);
        } else {
          domCard.classList.remove('active-filter');
          renderListingsTable(_allListings);
        }
        // Scroll to table
        var table = document.getElementById('alert-table');
        if (table) table.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    }

    // Render active listings alert table
    renderListingsTable(_allListings);
  } catch (err) {
    console.error('Daily Pulse load error:', err);
    VestaAPI.render('headline-stats', '<div class="loading">Error loading data</div>');
    VestaAPI.render(
      'alert-body',
      '<tr><td colspan="10" class="loading">Error loading data</td></tr>'
    );
  }
});

function renderListingsTable(items) {
  if (!items || items.length === 0) {
    VestaAPI.render(
      'alert-body',
      '<tr><td colspan="10" class="empty-state">No active listings</td></tr>'
    );
    return;
  }

  VestaAPI.render(
    'alert-body',
    items
      .map(function (item) {
        // Red flag: < 0.5 leads/day. Yellow flag: 30+ DOM.
        var rowClass = '';
        var lpdClass = '';
        if (item.is_flagged) {
          rowClass = 'flagged';
          lpdClass = 'flag-value';
        }
        var domClass = '';
        if (item.days_on_market >= 30) {
          domClass = 'flag-value';
          if (!rowClass) rowClass = 'flagged';
        }

        return (
          '<tr class="clickable-row ' + rowClass + '" onclick="location=\'/dashboard/property/' + item.unit_id + '/\'">' +
          '<td>' + (item.address || '\u2014') + '</td>' +
          '<td class="num">' + (item.bedrooms != null ? item.bedrooms : '\u2014') + '</td>' +
          '<td class="num">' + VestaAPI.$(item.listed_price) + '</td>' +
          '<td class="num ' + domClass + '">' + VestaAPI.num(item.days_on_market) + '</td>' +
          '<td>' + VestaAPI.dateStr(item.date_listed) + '</td>' +
          '<td class="num">' + VestaAPI.num(item.total_leads) + '</td>' +
          '<td class="num">' + VestaAPI.num(item.total_showings) + '</td>' +
          '<td class="num">' + VestaAPI.num(item.total_missed) + '</td>' +
          '<td class="num">' + VestaAPI.num(item.total_apps) + '</td>' +
          '<td class="num ' + lpdClass + '">' + item.leads_per_active_day.toFixed(2) + '</td>' +
          '</tr>'
        );
      })
      .join('')
  );
}
