/* Dashboard charts. Colours are read from CSS theme tokens so they follow dark mode. */
(function () {
  "use strict";

  var data = window.DASHBOARD;
  var charts = {};

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function setState(canvasId, state, message) {
    var el = document.getElementById(canvasId + "-state");
    if (!el) return;
    if (state === "ok") {
      el.classList.remove("is-visible");
      el.innerHTML = "";
      return;
    }
    el.classList.add("is-visible");
    el.innerHTML = state === "error"
      ? '<span class="empty-icon"><i data-lucide="alert-triangle"></i></span><span>' + (message || "Chart couldn't load.") + "</span>"
      : '<div class="skeleton skeleton-bar"></div><span>Loading chart…</span>';
    if (window.RedrawIcons) window.RedrawIcons();
  }

  var centerTextPlugin = {
    id: "centerText",
    afterDraw: function (chart) {
      if (chart.config.type !== "doughnut") return;
      var meta = chart.getDatasetMeta(0);
      if (!meta.data.length) return;
      var total = (chart.data.datasets[0].data || []).reduce(function (a, b) { return a + b; }, 0);
      var ctx = chart.ctx;
      var x = meta.data[0].x;
      var y = meta.data[0].y;
      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = cssVar("--text-muted");
      ctx.font = "500 11px Inter, sans-serif";
      ctx.fillText("TOTAL SPENT", x, y - 11);
      ctx.fillStyle = cssVar("--text");
      ctx.font = "700 16px Inter, sans-serif";
      ctx.fillText(window.Money.format(total), x, y + 10);
      ctx.restore();
    }
  };

  function baseOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 450, easing: "easeOutQuart" }
    };
  }

  function renderTrend() {
    var canvas = document.getElementById("trendChart");
    if (!canvas || !window.Chart) return;
    if (charts.trend) charts.trend.destroy();

    var accent = cssVar("--accent");
    var border = cssVar("--border");
    var muted = cssVar("--text-muted");

    charts.trend = new Chart(canvas, {
      type: "bar",
      data: {
        labels: data.labels,
        datasets: [
          {
            label: "Income",
            data: data.income,
            backgroundColor: cssVar("--income"),
            borderRadius: 6,
            borderSkipped: false,
            maxBarThickness: 18
          },
          {
            label: "Expenses",
            data: data.expenses,
            backgroundColor: cssVar("--expense"),
            borderRadius: 6,
            borderSkipped: false,
            maxBarThickness: 18
          }
        ]
      },
      options: Object.assign(baseOptions(), {
        interaction: { mode: "index", intersect: false },
        scales: {
          x: {
            grid: { display: false },
            border: { display: false },
            ticks: { color: muted, font: { family: "Inter", size: 11, weight: "500" } }
          },
          y: {
            beginAtZero: true,
            border: { display: false },
            grid: { color: cssVar("--chart-grid") },
            ticks: {
              color: muted,
              font: { family: "Inter", size: 11 },
              callback: function (v) { return window.Money.format(v); }
            }
          }
        },
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              usePointStyle: true,
              pointStyle: "circle",
              boxWidth: 7,
              boxHeight: 7,
              padding: 16,
              color: muted,
              font: { family: "Inter", size: 12, weight: "500" }
            }
          },
          tooltip: {
            backgroundColor: accent,
            padding: 10,
            cornerRadius: 8,
            titleFont: { family: "Inter", size: 12, weight: "600" },
            bodyFont: { family: "Inter", size: 12, weight: "500" },
            callbacks: {
              title: function (items) {
                var i = items[0].dataIndex;
                return data.periods[i];
              },
              label: function (ctx) {
                return " " + ctx.dataset.label + ": " + window.Money.format(ctx.parsed.y);
              }
            }
          }
        }
      })
    });
    setState("trendChart", "ok");
  }

  function renderDonut() {
    var canvas = document.getElementById("spendingChart");
    if (!canvas || !window.Chart) return;
    if (charts.donut) charts.donut.destroy();

    var muted = cssVar("--text-muted");
    var surface = cssVar("--surface");
    var palette = [1, 2, 3, 4, 5, 6, 7, 8].map(function (i) { return cssVar("--chart-" + i); });

    charts.donut = new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: data.spendingLabels,
        datasets: [{
          data: data.spendingValues,
          backgroundColor: palette,
          borderColor: surface,
          borderWidth: 3,
          hoverOffset: 6
        }]
      },
      options: Object.assign(baseOptions(), {
        cutout: "68%",
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              usePointStyle: true,
              pointStyle: "circle",
              boxWidth: 7,
              boxHeight: 7,
              padding: 12,
              color: muted,
              font: { family: "Inter", size: 12, weight: "500" }
            }
          },
          tooltip: {
            backgroundColor: cssVar("--accent"),
            padding: 10,
            cornerRadius: 8,
            titleFont: { family: "Inter", size: 12, weight: "600" },
            bodyFont: { family: "Inter", size: 12, weight: "500" },
            callbacks: {
              label: function (ctx) {
                var total = ctx.dataset.data.reduce(function (a, b) { return a + b; }, 0);
                var pct = total ? Math.round((ctx.parsed / total) * 100) : 0;
                return " " + window.Money.format(ctx.parsed) + " · " + pct + "%";
              }
            }
          }
        }
      }),
      plugins: [centerTextPlugin]
    });
    setState("spendingChart", "ok");
  }

  function renderAll() {
    if (!window.Chart) {
      setState("trendChart", "error");
      setState("spendingChart", "error");
      return;
    }
    try { renderTrend(); } catch (e) { setState("trendChart", "error"); }
    try { renderDonut(); } catch (e) { setState("spendingChart", "error"); }
  }

  if (!data) return;

  if (document.readyState === "complete") renderAll();
  else window.addEventListener("load", renderAll);

  document.addEventListener("themechange", renderAll);
})();
