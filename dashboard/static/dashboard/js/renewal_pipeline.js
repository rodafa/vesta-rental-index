/**
 * Renewal Pipeline — expirations, clustering, below-market flags.
 * Auto-refreshes every 60 seconds.
 */
var _clusteringChart = null;

async function loadAll() {
  try {
    var data = await VestaAPI.get('/analytics/renewal-pipeline?months_ahead=6');
    renderStats(data);
    renderClusteringChart(data.clustering, data.total_active_leases);
    renderRenewalTable(data.leases);
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
    return c.is_concentrated ? '#dc3545cc' : '#4a90d9cc';
  });

  // Concentrated months for callout
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

  // Callout text
  var calloutEl = document.getElementById('clustering-callout');
  if (calloutEl) {
    if (concentrated.length > 0) {
      var names = concentrated.map(function (c) { return c.month_label; }).join(', ');
      calloutEl.textContent = 'Concentration risk: ' + names + ' exceed' + (concentrated.length === 1 ? 's' : '') + ' 15% of active leases.';
      calloutEl.style.color = 'var(--red-accent)';
    } else {
      calloutEl.textContent = 'No concentration risk detected. Expirations are well-distributed.';
    }
  }
}

function renderRenewalTable(items) {
  if (!items || items.length === 0) {
    VestaAPI.render('renewal-body', '<tr><td colspan="10" class="empty-state">No expiring leases found</td></tr>');
    return;
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

      return (
        '<tr class="' + rowClass + '">' +
          '<td>' + (item.address || '\u2014') + '</td>' +
          '<td>' + (item.city || '\u2014') + '</td>' +
          '<td class="num">' + (item.bedrooms != null ? item.bedrooms : '\u2014') + '</td>' +
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
