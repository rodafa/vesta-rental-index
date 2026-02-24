/**
 * Property Detail — single unit/listing deep dive.
 * Loads unit info, weekly/all-time performance, market context,
 * price history chart, showing feedback, and activity log.
 */
document.addEventListener('DOMContentLoaded', async () => {
  try {
    // Phase 1: parallel fetch of unit + all market/leasing data
    var [
      unit,
      snapshotData,
      weeklyData,
      dailyData,
      dailyStatsData,
      priceDropData,
      showingData,
      eventData,
    ] = await Promise.all([
      VestaAPI.get('/properties/units/' + UNIT_ID),
      VestaAPI.get('/market/snapshots?unit_id=' + UNIT_ID + '&limit=1&offset=0'),
      VestaAPI.get('/market/weekly-leasing?unit_id=' + UNIT_ID + '&limit=10&offset=0'),
      VestaAPI.get('/market/daily-leasing?unit_id=' + UNIT_ID + '&limit=500&offset=0'),
      VestaAPI.get('/market/daily-stats?limit=1&offset=0'),
      VestaAPI.get('/market/price-drops?unit_id=' + UNIT_ID + '&limit=100&offset=0'),
      VestaAPI.get('/leasing/showings?unit_id=' + UNIT_ID + '&limit=50&offset=0'),
      VestaAPI.get('/leasing/leasing-events?unit_id=' + UNIT_ID + '&limit=50&offset=0'),
    ]);

    var snapshot = snapshotData.items ? snapshotData.items[0] : null;
    var weeks = weeklyData.items || [];
    var dailyItems = dailyData.items || [];
    var portfolioStats = dailyStatsData.items ? dailyStatsData.items[0] : null;
    var priceDrops = priceDropData.items || [];
    var showings = showingData.items || [];
    var events = eventData.items || [];

    // Phase 2: fetch property detail (for portfolio name)
    var property = null;
    if (unit.property) {
      try {
        property = await VestaAPI.get('/properties/properties/' + unit.property);
      } catch (e) { /* property fetch optional */ }
    }

    // Render all sections
    renderUnitHeader(unit, snapshot, property);
    renderWeeklyPerformance(weeks);
    renderMarketContext(snapshot, portfolioStats);
    renderAllTimePerformance(dailyItems);
    renderPriceHistory(priceDrops);
    renderShowingFeedback(showings);
    renderActivityLog(events);
  } catch (err) {
    console.error('Property Detail load error:', err);
    VestaAPI.render('unit-header', '<div class="loading">Error loading unit data</div>');
  }
});

// ── Unit Header ──────────────────────────────────────────────────────────────

function renderUnitHeader(unit, snapshot, property) {
  var address = unit.address_line_1 || unit.property_address || 'Unknown Address';
  var meta = [];
  if (unit.bedrooms != null) meta.push('<span>' + unit.bedrooms + ' bd</span>');
  if (unit.full_bathrooms != null) meta.push('<span>' + unit.full_bathrooms + ' ba</span>');
  if (unit.square_feet != null) meta.push('<span>' + VestaAPI.num(unit.square_feet) + ' sqft</span>');
  if (unit.target_rental_rate != null) meta.push('<span>Target: ' + VestaAPI.$(unit.target_rental_rate) + '</span>');

  var snapshotHtml = '';
  if (snapshot) {
    var dom = snapshot.days_on_market || 0;
    var domClass = 'dom-ok';
    if (dom >= 30) domClass = 'dom-danger';
    else if (dom >= 15) domClass = 'dom-warn';
    snapshotHtml =
      '<span class="dom-badge ' + domClass + '">' + VestaAPI.num(dom) + ' DOM</span>' +
      '<span>' + VestaAPI.$(snapshot.listed_price) + ' listed</span>';
    if (snapshot.date_listed) {
      snapshotHtml += '<span>Listed ' + VestaAPI.dateStr(snapshot.date_listed) + '</span>';
    }
  }

  var ownerHtml = '';
  if (property && property.portfolio_name) {
    ownerHtml = '<span>Portfolio: ' + escapeHtml(property.portfolio_name) + '</span>';
  }

  VestaAPI.render(
    'unit-header',
    '<div>' +
      '<div class="address">' + escapeHtml(address) + '</div>' +
      '<div class="meta">' + meta.join('') + snapshotHtml + ownerHtml + '</div>' +
    '</div>'
  );
}

// ── Weekly Performance ───────────────────────────────────────────────────────

function renderWeeklyPerformance(weeks) {
  if (!weeks.length) {
    VestaAPI.render(
      'weekly-perf',
      '<div class="card-title">Weekly Performance</div>' +
      '<div class="empty-state">No weekly data available</div>'
    );
    return;
  }

  var rows = '';
  for (var i = 0; i < weeks.length; i++) {
    var w = weeks[i];
    var prev = i + 1 < weeks.length ? weeks[i + 1] : null;
    rows +=
      '<tr>' +
        '<td>' + VestaAPI.dateStr(w.week_ending) + '</td>' +
        '<td class="num">' + VestaAPI.num(w.leads_count) + wowChange(w.leads_count, prev ? prev.leads_count : null) + '</td>' +
        '<td class="num">' + VestaAPI.num(w.showings_completed_count) + wowChange(w.showings_completed_count, prev ? prev.showings_completed_count : null) + '</td>' +
        '<td class="num">' + VestaAPI.num(w.showings_missed_count) + wowChange(w.showings_missed_count, prev ? prev.showings_missed_count : null) + '</td>' +
        '<td class="num">' + VestaAPI.num(w.applications_count) + wowChange(w.applications_count, prev ? prev.applications_count : null) + '</td>' +
      '</tr>';
  }

  VestaAPI.render(
    'weekly-perf',
    '<div class="card-title">Weekly Performance</div>' +
    '<table>' +
      '<thead><tr>' +
        '<th>Week Ending</th><th>Leads</th><th>Showings</th><th>Missed</th><th>Apps</th>' +
      '</tr></thead>' +
      '<tbody>' + rows + '</tbody>' +
    '</table>'
  );
}

function wowChange(current, previous) {
  if (previous == null || current == null) return '';
  var diff = current - previous;
  if (diff === 0) return '';
  if (diff > 0) return ' <span class="change positive">(+' + diff + ')</span>';
  return ' <span class="change negative">(' + diff + ')</span>';
}

// ── Market Context ───────────────────────────────────────────────────────────

function renderMarketContext(snapshot, portfolioStats) {
  if (!snapshot && !portfolioStats) {
    VestaAPI.render(
      'market-context',
      '<div class="card-title">Market Context</div>' +
      '<div class="empty-state">No market data available</div>'
    );
    return;
  }

  var unitDom = snapshot ? VestaAPI.num(snapshot.days_on_market) : '\u2014';
  var avgDom = portfolioStats ? VestaAPI.num(portfolioStats.average_dom) : '\u2014';
  var unitPrice = snapshot ? VestaAPI.$(snapshot.listed_price) : '\u2014';
  var avgPrice = portfolioStats ? VestaAPI.$(portfolioStats.average_price) : '\u2014';
  var avgRent = portfolioStats ? VestaAPI.$(portfolioStats.average_portfolio_rent) : '\u2014';

  VestaAPI.render(
    'market-context',
    '<div class="card-title">Market Context</div>' +
    '<div class="stats-grid">' +
      '<div class="stat-card">' +
        '<div class="label">This Unit DOM</div>' +
        '<div class="value">' + unitDom + '</div>' +
        '<div class="change">Portfolio Avg: ' + avgDom + '</div>' +
      '</div>' +
      '<div class="stat-card">' +
        '<div class="label">This Unit Price</div>' +
        '<div class="value">' + unitPrice + '</div>' +
        '<div class="change">Portfolio Avg: ' + avgPrice + '</div>' +
      '</div>' +
      '<div class="stat-card">' +
        '<div class="label">Avg Portfolio Rent</div>' +
        '<div class="value">' + avgRent + '</div>' +
      '</div>' +
    '</div>'
  );
}

// ── All-Time Performance ─────────────────────────────────────────────────────

function renderAllTimePerformance(dailyItems) {
  if (!dailyItems.length) {
    VestaAPI.render(
      'alltime-perf',
      '<div class="card-title">All-Time Performance</div>' +
      '<div class="empty-state">No daily leasing data available</div>'
    );
    return;
  }

  var totalLeads = 0;
  var totalShowings = 0;
  var totalMissed = 0;
  var totalApps = 0;

  for (var i = 0; i < dailyItems.length; i++) {
    totalLeads += Number(dailyItems[i].leads_count) || 0;
    totalShowings += Number(dailyItems[i].showings_completed_count) || 0;
    totalMissed += Number(dailyItems[i].showings_missed_count) || 0;
    totalApps += Number(dailyItems[i].applications_count) || 0;
  }

  var leadToShow = totalLeads > 0 ? ((totalShowings / totalLeads) * 100).toFixed(1) + '%' : '\u2014';
  var showToApp = totalShowings > 0 ? ((totalApps / totalShowings) * 100).toFixed(1) + '%' : '\u2014';

  VestaAPI.render(
    'alltime-perf',
    '<div class="card-title">All-Time Performance</div>' +
    '<div class="stats-grid">' +
      '<div class="stat-card"><div class="label">Total Leads</div><div class="value">' + VestaAPI.num(totalLeads) + '</div></div>' +
      '<div class="stat-card"><div class="label">Showings Completed</div><div class="value">' + VestaAPI.num(totalShowings) + '</div></div>' +
      '<div class="stat-card"><div class="label">Showings Missed</div><div class="value">' + VestaAPI.num(totalMissed) + '</div></div>' +
      '<div class="stat-card"><div class="label">Applications</div><div class="value">' + VestaAPI.num(totalApps) + '</div></div>' +
      '<div class="stat-card"><div class="label">Lead &rarr; Show</div><div class="value">' + leadToShow + '</div></div>' +
      '<div class="stat-card"><div class="label">Show &rarr; App</div><div class="value">' + showToApp + '</div></div>' +
    '</div>'
  );
}

// ── Price History Chart ──────────────────────────────────────────────────────

function renderPriceHistory(priceDrops) {
  if (!priceDrops.length) {
    VestaAPI.render(
      'price-history',
      '<div class="card-title">Price History</div>' +
      '<div class="empty-state">No price changes recorded</div>'
    );
    return;
  }

  var sorted = priceDrops.slice().sort(function (a, b) {
    return a.detected_date < b.detected_date ? -1 : a.detected_date > b.detected_date ? 1 : 0;
  });

  var labels = sorted.map(function (d) { return VestaAPI.dateStr(d.detected_date); });
  var prices = sorted.map(function (d) { return Number(d.new_price); });

  VestaCharts.lineChart('price-chart', labels, [
    { label: 'Listed Price', data: prices },
  ], 'Price ($)');
}

// ── Showing Feedback ─────────────────────────────────────────────────────────

function renderShowingFeedback(showings) {
  if (!showings.length) {
    VestaAPI.render('feedback-cards', '<div class="empty-state">No showings recorded</div>');
    return;
  }

  var html = '';
  for (var i = 0; i < showings.length; i++) {
    var s = showings[i];
    var headerParts = [];
    // Use scheduled_at for date, showing_method for method
    if (s.scheduled_at) {
      headerParts.push(VestaAPI.dateStr(s.scheduled_at.split('T')[0]));
    }
    if (s.showing_method) headerParts.push(s.showing_method);
    if (s.status) headerParts.push(s.status);

    var bodyHtml = '';
    var feedback = s.feedback;
    if (feedback && typeof feedback === 'string') {
      try { feedback = JSON.parse(feedback); } catch (e) { feedback = null; }
    }
    if (feedback && typeof feedback === 'object' && Object.keys(feedback).length > 0) {
      var pairs = [];
      var keys = Object.keys(feedback);
      for (var k = 0; k < keys.length; k++) {
        pairs.push('<strong>' + escapeHtml(keys[k]) + ':</strong> ' + escapeHtml(String(feedback[keys[k]])));
      }
      bodyHtml = pairs.join('<br>');
    } else {
      bodyHtml = '<span style="color:var(--text-muted)">No feedback</span>';
    }

    html +=
      '<div class="feedback-card">' +
        '<div class="fc-header">' + headerParts.join(' &middot; ') + '</div>' +
        '<div class="fc-body">' + bodyHtml + '</div>' +
      '</div>';
  }

  VestaAPI.render('feedback-cards', html);
}

// ── Activity Log ─────────────────────────────────────────────────────────────

function renderActivityLog(events) {
  if (!events.length) {
    VestaAPI.render(
      'activity-log-body',
      '<tr><td colspan="3" class="empty-state">No activity recorded</td></tr>'
    );
    return;
  }

  // Sort by event_date desc (most recent first)
  var sorted = events.slice().sort(function (a, b) {
    return a.event_date > b.event_date ? -1 : a.event_date < b.event_date ? 1 : 0;
  });

  var rows = '';
  for (var i = 0; i < sorted.length; i++) {
    var ev = sorted[i];
    // Build detail from available fields
    var details = '';
    if (ev.event_timestamp) {
      var ts = new Date(ev.event_timestamp);
      details = ts.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    }
    if (ev.prospect) {
      details += (details ? ' \u2014 ' : '') + 'Prospect #' + ev.prospect;
    }

    rows +=
      '<tr>' +
        '<td>' + VestaAPI.dateStr(ev.event_date) + '</td>' +
        '<td>' + escapeHtml(ev.event_type_display || ev.event_type || '\u2014') + '</td>' +
        '<td>' + (details || '\u2014') + '</td>' +
      '</tr>';
  }

  VestaAPI.render('activity-log-body', rows);
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function escapeHtml(text) {
  if (text == null) return '';
  var div = document.createElement('div');
  div.appendChild(document.createTextNode(String(text)));
  return div.innerHTML;
}
