/* Budgets page: month picker, filters, chart and JSON actions. */
(function () {
  "use strict";

  var monthPicker = document.querySelector("[data-month-picker]");
  var currentMonth = monthPicker ? monthPicker.value : "";

  if (monthPicker) {
    monthPicker.addEventListener("change", function () {
      if (!monthPicker.value) return;
      window.location.href = "/budgets?month=" + encodeURIComponent(monthPicker.value);
    });
  }

  /* ---------- budget dialog prefill ---------- */
  var form = document.getElementById("budget-form");
  var errorEl = form ? form.querySelector("[data-budget-error]") : null;

  function resetBudgetForm() {
    if (!form) return;
    form.reset();
    if (form.dataset.defaultAction) form.setAttribute("action", form.dataset.defaultAction);
    var title = form.querySelector("[data-budget-title]");
    if (title) title.textContent = "Add a budget";
    var threshold = form.querySelector('[name="alert_threshold"]');
    if (threshold) threshold.value = "80";
    if (errorEl) {
      errorEl.textContent = "";
      errorEl.classList.remove("is-visible");
    }
  }

  var previousDialogHook = window.beforeDialogOpen;
  window.beforeDialogOpen = function (id, opener) {
    if (typeof previousDialogHook === "function") previousDialogHook(id, opener);
    if (id !== "budget-dialog" || !form) return;
    resetBudgetForm();

    var month = form.querySelector('[name="month"]');
    if (month && opener.dataset.month) month.value = opener.dataset.month;

    if (opener.hasAttribute("data-budget-edit")) {
      form.setAttribute("action", opener.dataset.editUrl);
      var title = form.querySelector("[data-budget-title]");
      if (title) title.textContent = "Edit " + (opener.dataset.name || "budget");
      form.querySelector('[name="category_id"]').value = opener.dataset.categoryId || "";
      form.querySelector('[name="monthly_limit"]').value = opener.dataset.limit || "";
      form.querySelector('[name="alert_threshold"]').value = opener.dataset.threshold || "80";
      form.querySelector('[name="rollover"]').checked = opener.dataset.rollover === "1";
    } else if (opener.dataset.categoryId) {
      form.querySelector('[name="category_id"]').value = opener.dataset.categoryId;
    }
  };

  var dialog = document.getElementById("budget-dialog");
  if (dialog) {
    dialog.addEventListener("close", resetBudgetForm);
  }

  /* ---------- JSON forms (add/edit budget + overall budget) ---------- */
  document.addEventListener("submit", function (event) {
    var target = event.target;
    if (!target.matches || !target.matches("form[data-budget-json]")) return;
    event.preventDefault();

    var payload = {};
    new FormData(target).forEach(function (value, key) {
      payload[key] = value;
    });
    var submit = target.querySelector('[type="submit"]');
    if (submit) submit.disabled = true;
    if (errorEl) {
      errorEl.textContent = "";
      errorEl.classList.remove("is-visible");
    }

    window.Api.post(target.action, payload).then(function (data) {
      window.showToast(data.message || "Saved.", "success");
      if (target.closest("dialog") && window.Modal) window.Modal.close(target);
      setTimeout(function () { window.location.reload(); }, 350);
    }).catch(function (error) {
      if (errorEl && target.id === "budget-form") {
        errorEl.textContent = error.message;
        errorEl.classList.add("is-visible");
      } else {
        window.showToast(error.message, "error");
      }
      if (submit) submit.disabled = false;
    });
  });

  /* ---------- delete from the shared confirmation dialog ---------- */
  document.addEventListener("submit", function (event) {
    var target = event.target;
    if (!target || target.id !== "delete-confirm-form") return;
    if (!/\/budgets\//.test(target.action || "")) return;
    event.preventDefault();
    var submit = target.querySelector('[type="submit"]');
    if (submit) submit.disabled = true;
    window.Api.post(target.action, { month: currentMonth }).then(function (data) {
      window.showToast(data.message || "Removed.", "success");
      if (window.Modal) window.Modal.close(target);
      setTimeout(function () { window.location.reload(); }, 350);
    }).catch(function (error) {
      window.showToast(error.message, "error");
      if (submit) submit.disabled = false;
    });
  });

  /* ---------- copy last month ---------- */
  document.querySelectorAll("[data-copy-budgets]").forEach(function (button) {
    button.addEventListener("click", function () {
      button.disabled = true;
      window.Api.post(button.dataset.url, { month: button.dataset.month || currentMonth })
        .then(function (data) {
          window.showToast(data.message || "Budgets copied.", "success");
          setTimeout(function () { window.location.reload(); }, 350);
        })
        .catch(function (error) {
          window.showToast(error.message, "error");
          button.disabled = false;
        });
    });
  });

  /* ---------- search / filter / sort ---------- */
  var list = document.querySelector("[data-budget-list]");
  var rows = list ? Array.prototype.slice.call(list.querySelectorAll("[data-budget-row]")) : [];
  var search = document.querySelector("[data-budget-search]");
  var filter = document.querySelector("[data-budget-filter]");
  var sort = document.querySelector("[data-budget-sort]");
  var noResults = document.querySelector("[data-budget-no-results]");

  function number(row, key) {
    return parseFloat(row.dataset[key]) || 0;
  }

  function applyView() {
    if (!list) return;
    var term = search ? search.value.trim().toLowerCase() : "";
    var status = filter ? filter.value : "all";
    var order = sort ? sort.value : "name";

    rows.sort(function (a, b) {
      if (order === "percent") return number(b, "percent") - number(a, "percent");
      if (order === "spent") return number(b, "spent") - number(a, "spent");
      if (order === "remaining") return number(a, "remaining") - number(b, "remaining");
      return a.dataset.name.localeCompare(b.dataset.name);
    });

    var visible = 0;
    rows.forEach(function (row) {
      var matchesTerm = !term || row.dataset.name.indexOf(term) !== -1;
      var matchesStatus = status === "all" || row.dataset.status === status;
      var show = matchesTerm && matchesStatus;
      row.hidden = !show;
      if (show) visible += 1;
      list.appendChild(row);
    });
    if (noResults) noResults.hidden = visible !== 0;
  }

  [search, filter, sort].forEach(function (control) {
    if (!control) return;
    control.addEventListener("input", applyView);
    control.addEventListener("change", applyView);
  });
  applyView();

  /* ---------- chart ---------- */
  var chart = null;
  var budgetData = window.BUDGETS;

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function setChartState(state) {
    var el = document.getElementById("budgetChart-state");
    if (!el) return;
    if (state === "ok") {
      el.classList.remove("is-visible");
      el.innerHTML = "";
      return;
    }
    el.classList.add("is-visible");
    el.innerHTML = state === "error"
      ? '<span class="empty-icon"><i data-lucide="alert-triangle"></i></span><span>Chart couldn\'t load.</span>'
      : '<div class="skeleton skeleton-bar"></div><span>Loading chart…</span>';
    if (window.RedrawIcons) window.RedrawIcons();
  }

  function renderChart() {
    var canvas = document.getElementById("budgetChart");
    if (!canvas || !budgetData || !window.Chart) return;
    if (chart) chart.destroy();

    var muted = cssVar("--text-muted");
    var surface = cssVar("--surface");
    chart = new window.Chart(canvas, {
      type: "bar",
      data: {
        labels: budgetData.labels,
        datasets: [
          {
            label: "Spent",
            data: budgetData.spent,
            backgroundColor: cssVar("--expense"),
            borderRadius: 6,
            borderSkipped: false,
            maxBarThickness: 20
          },
          {
            label: "Budget",
            data: budgetData.limits,
            backgroundColor: cssVar("--accent-soft"),
            borderColor: cssVar("--accent"),
            borderWidth: 1,
            borderRadius: 6,
            borderSkipped: false,
            maxBarThickness: 20
          }
        ]
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 450, easing: "easeOutQuart" },
        scales: {
          x: {
            beginAtZero: true,
            border: { display: false },
            grid: { color: cssVar("--chart-grid") },
            ticks: {
              color: muted,
              font: { family: "Inter", size: 11 },
              callback: function (value) { return window.Money.format(value); }
            }
          },
          y: {
            grid: { display: false },
            border: { display: false },
            ticks: { color: muted, font: { family: "Inter", size: 11, weight: "500" } }
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
              padding: 14,
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
                return " " + ctx.dataset.label + ": " + window.Money.format(ctx.parsed.x);
              }
            }
          }
        }
      }
    });
    setChartState("ok");
  }

  if (document.getElementById("budgetChart")) {
    if (!window.Chart) {
      setChartState("error");
    } else if (document.readyState === "complete") {
      renderChart();
    } else {
      window.addEventListener("load", renderChart);
    }
    document.addEventListener("themechange", renderChart);
  }
})();
