/**
 * Vesta Dashboard — Chart.js helpers.
 */
const VestaCharts = (() => {
  // Shared defaults
  Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = '#666';
  Chart.defaults.plugins.legend.position = 'bottom';
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.plugins.legend.labels.padding = 16;

  const COLORS = [
    '#4a90d9', '#e67e22', '#2ecc71', '#e74c3c', '#9b59b6',
    '#1abc9c', '#f39c12', '#3498db', '#e91e63', '#00bcd4',
  ];

  function lineChart(canvasId, labels, datasets, yLabel) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;
    return new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: datasets.map((ds, i) => ({
          borderColor: COLORS[i % COLORS.length],
          backgroundColor: COLORS[i % COLORS.length] + '20',
          borderWidth: 2,
          pointRadius: 3,
          tension: 0.3,
          fill: datasets.length === 1,
          ...ds,
        })),
      },
      options: {
        responsive: true,
        interaction: { mode: 'index', intersect: false },
        scales: {
          y: {
            beginAtZero: false,
            title: yLabel ? { display: true, text: yLabel, font: { size: 11 } } : undefined,
          },
        },
      },
    });
  }

  function barChart(canvasId, labels, datasets, yLabel) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;
    return new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: datasets.map((ds, i) => ({
          backgroundColor: COLORS[i % COLORS.length] + 'cc',
          borderRadius: 3,
          ...ds,
        })),
      },
      options: {
        responsive: true,
        interaction: { mode: 'index', intersect: false },
        scales: {
          y: {
            beginAtZero: true,
            title: yLabel ? { display: true, text: yLabel, font: { size: 11 } } : undefined,
          },
        },
      },
    });
  }

  return { lineChart, barChart, COLORS };
})();
