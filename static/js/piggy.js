/* Piggy bank page: month picker, entry/settings dialogs, filters and chart. */
(function () {
  "use strict";

  var config = window.PIGGY || {};

  /* ---------- print ---------- */
  var printButton = document.querySelector("[data-piggy-print]");
  if (printButton) {
    printButton.addEventListener("click", function () { window.print(); });
  }

  /* ---------- month picker ---------- */
  var monthPicker = document.querySelector("[data-piggy-month]");
  if (monthPicker) {
    monthPicker.addEventListener("change", function () {
      if (!monthPicker.value) return;
      window.location.href = "/piggy?month=" + encodeURIComponent(monthPicker.value);
    });
  }

  /* ---------- entry dialog prefill / reset ---------- */
  var entryForm = document.getElementById("piggy-entry-form");
  var entryDialog = document.getElementById("piggy-entry-dialog");
  var entryError = entryForm ? entryForm.querySelector("[data-piggy-error]") : null;
  var defaultDate = "";
  var defaultMonth = "";

  if (entryForm) {
    var dateInput = entryForm.querySelector('[name="entry_date"]');
    var monthInput = entryForm.querySelector('[name="month"]');
    defaultDate = dateInput ? dateInput.value : "";
    defaultMonth = monthInput ? monthInput.value : "";
  }

  function setEntryError(message) {
    if (!entryError) return;
    entryError.textContent = message || "";
    entryError.classList.toggle("is-visible", Boolean(message));
  }

  function setEntryTitle(text) {
    if (!entryForm) return;
    var title = entryForm.querySelector("[data-piggy-entry-title]");
    if (title) title.textContent = text;
  }

  function resetEntryForm() {
    if (!entryForm) return;
    entryForm.reset();
    if (entryForm.dataset.defaultAction) {
      entryForm.setAttribute("action", entryForm.dataset.defaultAction);
    }
    var month = entryForm.querySelector('[name="month"]');
    if (month) month.value = defaultMonth;
    var date = entryForm.querySelector('[name="entry_date"]');
    if (date) date.value = defaultDate;
    var allowed = entryForm.querySelector('[name="allow_duplicate"]');
    if (allowed) allowed.value = "0";
    setEntryTitle("Add piggy-bank entry");
    setEntryError("");
  }

  var previousDialogHook = window.beforeDialogOpen;
  window.beforeDialogOpen = function (id, opener) {
    if (typeof previousDialogHook === "function") previousDialogHook(id, opener);
    if (id !== "piggy-entry-dialog" || !entryForm) return;

    resetEntryForm();

    var month = entryForm.querySelector('[name="month"]');
    var date = entryForm.querySelector('[name="entry_date"]');

    if (opener) {
      if (opener.dataset.month && month) month.value = opener.dataset.month;
      if (opener.dataset.entryDate && date) date.value = opener.dataset.entryDate;
    }

    if (!opener || !opener.hasAttribute("data-edit-piggy")) return;

    var row = opener.closest("[data-piggy-row]");
    if (!row) return;

    var data = row.dataset;
    var url = opener.dataset.editUrl || row.dataset.editUrl;
    if (url) entryForm.setAttribute("action", url);

    setEntryTitle("Edit piggy-bank entry");

    var contributor = entryForm.querySelector('input[name="contributor"][value="' + data.contributor + '"]');
    if (contributor) contributor.checked = true;

    var type = entryForm.querySelector('input[name="entry_type"][value="' + data.type + '"]');
    if (type) type.checked = true;

    var amount = entryForm.querySelector('[name="amount"]');
    if (amount) amount.value = data.amount || "";

    if (date) date.value = data.date || defaultDate;
    if (month && data.month) month.value = data.month;

    var note = entryForm.querySelector('[name="note"]');
    if (note) note.value = data.note || "";
  };

  if (entryDialog) {
    entryDialog.addEventListener("close", resetEntryForm);
  }

  /* ---------- JSON forms (entry + settings) ---------- */
  var duplicateDialog = document.getElementById("piggy-duplicate-dialog");
  var pendingDuplicate = null;

  function clearErrors(form) {
    form.querySelectorAll("[data-piggy-error]").forEach(function (el) {
      el.textContent = "";
      el.classList.remove("is-visible");
    });
    form.querySelectorAll('[aria-invalid="true"]').forEach(function (el) {
      el.setAttribute("aria-invalid", "false");
    });
  }

  function submitPiggyForm(form, payload) {
    var submit = form.querySelector('[type="submit"]');
    if (submit) submit.disabled = true;
    clearErrors(form);

    window.Api.post(form.action, payload).then(function (data) {
      window.showToast(data.message || "Saved.", "success");
      if (window.Modal) {
        if (pendingDuplicate && duplicateDialog) window.Modal.close(duplicateDialog);
        pendingDuplicate = null;
        window.Modal.close(form);
      }
      setTimeout(function () { window.location.reload(); }, 350);
    }).catch(function (error) {
      if (submit) submit.disabled = false;

      if (error.code === "duplicate_deposit" && form === entryForm && duplicateDialog) {
        pendingDuplicate = { form: form, payload: payload };
        var text = duplicateDialog.querySelector("#piggy-duplicate-text");
        if (text && error.message) text.textContent = error.message;
        window.Modal.open("piggy-duplicate-dialog");
        return;
      }

      if (entryError && form === entryForm) {
        setEntryError(error.message);
        var amount = entryForm.querySelector('[name="amount"]');
        if (amount && error.field === "amount") amount.setAttribute("aria-invalid", "true");
        if (amount && error.field === "amount") amount.focus();
      } else {
        window.showToast(error.message, "error");
      }
    });
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.matches || !form.matches("form[data-piggy-json]")) return;
    event.preventDefault();

    var payload = {};
    new FormData(form).forEach(function (value, key) {
      payload[key] = value;
    });
    if (form === entryForm) payload.allow_duplicate = "0";
    submitPiggyForm(form, payload);
  });

  document.addEventListener("input", function (event) {
    var form = event.target.closest && event.target.closest("form[data-piggy-json]");
    if (!form) return;
    clearErrors(form);
  });

  var duplicateConfirm = document.querySelector("[data-piggy-duplicate-confirm]");
  if (duplicateConfirm) {
    duplicateConfirm.addEventListener("click", function () {
      if (!pendingDuplicate) return;
      var form = pendingDuplicate.form;
      var payload = Object.assign({}, pendingDuplicate.payload, { allow_duplicate: "1" });
      pendingDuplicate = null;
      submitPiggyForm(form, payload);
    });
  }

  var duplicateCancel = document.querySelector("[data-piggy-duplicate-cancel]");
  if (duplicateCancel) {
    duplicateCancel.addEventListener("click", function () {
      pendingDuplicate = null;
      if (duplicateDialog && window.Modal) window.Modal.close(duplicateDialog);
    });
  }

  /* ---------- delete via the shared confirmation dialog ---------- */
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || form.id !== "delete-confirm-form") return;
    if (!/\/piggy\/entries\/[^/]+\/delete/.test(form.action || "")) return;

    event.preventDefault();
    var submit = form.querySelector('[type="submit"]');
    if (submit) submit.disabled = true;

    window.Api.post(form.action, { month: config.month || "" }).then(function (data) {
      window.showToast(data.message || "Entry moved to Recently deleted.", "success");
      if (window.Modal) window.Modal.close(form);
      setTimeout(function () { window.location.reload(); }, 350);
    }).catch(function (error) {
      window.showToast(error.message, "error");
      if (submit) submit.disabled = false;
    });
  });

  /* ---------- verify a saved PDF statement ---------- */
  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("[data-piggy-verify]");
    if (!button) return;
    event.preventDefault();
    button.disabled = true;

    window.Api.get(button.dataset.verifyUrl).then(function (data) {
      button.disabled = false;
      if (data.matches) {
        window.showToast("Statement verified: the PDF matches its saved SHA-256 hash.", "success");
      } else {
        window.showToast("Verification failed: the PDF file has changed.", "error");
      }
    }, function (error) {
      button.disabled = false;
      window.showToast(error.message || "Could not verify the statement.", "error");
    });
  });

  /* ---------- search / contributor / type filters ---------- */
  var list = document.querySelector("[data-piggy-list]");
  var rows = list ? Array.prototype.slice.call(list.querySelectorAll("[data-piggy-row]")) : [];
  var search = document.querySelector("[data-piggy-search]");
  var contributor = document.querySelector("[data-piggy-contributor]");
  var type = document.querySelector("[data-piggy-type]");
  var filteredEmpty = document.getElementById("piggy-filtered-empty");
  var clearFilters = document.querySelector("[data-piggy-clear]");

  function applyFilters() {
    if (!list) return;
    var term = search ? search.value.trim().toLowerCase() : "";
    var who = contributor ? contributor.value : "all";
    var kind = type ? type.value : "all";
    var visible = 0;

    rows.forEach(function (row) {
      var show = true;
      if (term && (row.dataset.text || "").indexOf(term) === -1) show = false;
      if (who !== "all" && row.dataset.contributor !== who) show = false;
      if (kind !== "all" && row.dataset.type !== kind) show = false;
      row.hidden = !show;
      if (show) visible += 1;
    });

    if (filteredEmpty) filteredEmpty.hidden = visible !== 0;
  }

  [search, contributor, type].forEach(function (control) {
    if (!control) return;
    control.addEventListener("input", applyFilters);
    control.addEventListener("change", applyFilters);
  });

  if (clearFilters) {
    clearFilters.addEventListener("click", function () {
      if (search) search.value = "";
      if (contributor) contributor.value = "all";
      if (type) type.value = "all";
      applyFilters();
      if (search) search.focus();
    });
  }

  applyFilters();

  /* ---------- chart: deposits/withdrawals bars + balance line ---------- */
  var chart = null;

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function setChartState(state) {
    var el = document.getElementById("piggyChart-state");
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
    var canvas = document.getElementById("piggyChart");
    var series = config.series;
    if (!canvas || !series || !window.Chart) return;
    if (chart) chart.destroy();

    var muted = cssVar("--text-muted");
    var grid = cssVar("--chart-grid");
    var accent = cssVar("--accent");

    chart = new window.Chart(canvas, {
      type: "bar",
      data: {
        labels: series.map(function (point) { return point.label; }),
        datasets: [
          {
            label: "Deposits",
            data: series.map(function (point) { return point.deposits; }),
            backgroundColor: cssVar("--income"),
            borderRadius: 6,
            borderSkipped: false,
            maxBarThickness: 20,
            order: 2
          },
          {
            label: "Withdrawals",
            data: series.map(function (point) { return point.withdrawals; }),
            backgroundColor: cssVar("--expense"),
            borderRadius: 6,
            borderSkipped: false,
            maxBarThickness: 20,
            order: 2
          },
          {
            type: "line",
            label: "Balance",
            data: series.map(function (point) { return point.balance; }),
            borderColor: accent,
            backgroundColor: accent,
            borderWidth: 2,
            pointRadius: 3,
            pointHoverRadius: 4,
            tension: 0.35,
            fill: false,
            yAxisID: "y1",
            order: 1
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        animation: { duration: 450, easing: "easeOutQuart" },
        scales: {
          x: {
            grid: { display: false },
            border: { display: false },
            ticks: { color: muted, font: { family: "Inter", size: 11, weight: "500" } }
          },
          y: {
            beginAtZero: true,
            border: { display: false },
            grid: { color: grid },
            ticks: {
              color: muted,
              font: { family: "Inter", size: 11 },
              callback: function (value) { return window.Money.format(value); }
            }
          },
          y1: {
            beginAtZero: true,
            position: "right",
            border: { display: false },
            grid: { display: false },
            ticks: {
              color: muted,
              font: { family: "Inter", size: 11 },
              callback: function (value) { return window.Money.format(value); }
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
              padding: 14,
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
                var point = series[items[0].dataIndex];
                return point ? point.label_long : "";
              },
              label: function (ctx) {
                return " " + ctx.dataset.label + ": " + window.Money.format(ctx.parsed.y);
              }
            }
          }
        }
      }
    });
    setChartState("ok");
  }

  if (document.getElementById("piggyChart")) {
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
