/* Transactions page: search, filters, sort, day totals — pure DOM enhancement
   over the server-rendered list, so the page still works without JS. */
(function () {
  "use strict";

  var list = document.getElementById("txn-results");
  if (!list || !window.Money) return;

  var rows = Array.prototype.slice.call(list.querySelectorAll(".txn-row"));
  var headers = {};
  Array.prototype.forEach.call(list.querySelectorAll("[data-group-header]"), function (h) {
    headers[h.dataset.date] = h;
  });

  var search = document.getElementById("filter-search");
  var typeSel = document.getElementById("filter-type");
  var categorySel = document.getElementById("filter-category");
  var sortSel = document.getElementById("filter-sort");
  var fromInput = document.getElementById("filter-from");
  var toInput = document.getElementById("filter-to");
  var summary = document.getElementById("filter-summary");
  var filteredEmpty = document.getElementById("filtered-empty");
  var clearBtn = document.getElementById("clear-filters");

  function syncCategoryOptions() {
    var t = typeSel.value;
    Array.prototype.forEach.call(categorySel.options, function (opt) {
      if (!opt.value) return;
      var match = !t || opt.dataset.type === t;
      opt.hidden = !match;
      opt.disabled = !match;
    });
    var selected = categorySel.selectedOptions[0];
    if (selected && selected.disabled) categorySel.value = "";
  }

  function matches(row) {
    var t = typeSel.value;
    var c = categorySel.value;
    var q = search.value.trim().toLowerCase();
    if (t && row.dataset.type !== t) return false;
    if (c && row.dataset.category !== c) return false;
    if (q && row.dataset.text.indexOf(q) === -1) return false;
    if (fromInput.value && row.dataset.date < fromInput.value) return false;
    if (toInput.value && row.dataset.date > toInput.value) return false;
    return true;
  }

  function compare(mode, a, b) {
    var da = a.dataset;
    var db = b.dataset;
    if (mode === "high") {
      return (parseFloat(db.amount) - parseFloat(da.amount)) || (Number(db.id) - Number(da.id));
    }
    if (mode === "low") {
      return (parseFloat(da.amount) - parseFloat(db.amount)) || (Number(db.id) - Number(da.id));
    }
    var c = da.date < db.date ? 1 : da.date > db.date ? -1 : 0;
    if (mode === "oldest") c = -c;
    return c || (Number(db.id) - Number(da.id));
  }

  function render() {
    syncCategoryOptions();

    var mode = sortSel.value;
    var byAmount = mode === "high" || mode === "low";
    var visibleCount = 0;
    var income = 0;
    var expense = 0;
    var dayNet = {};

    rows.forEach(function (row) {
      var ok = matches(row);
      row.hidden = !ok;
      if (ok) {
        visibleCount += 1;
        var amount = parseFloat(row.dataset.amount) || 0;
        var d = row.dataset.date;
        dayNet[d] = (dayNet[d] || 0) + (row.dataset.type === "expense" ? amount : -amount);
        if (row.dataset.type === "income") income += amount;
        else expense += amount;
      }
    });

    var sorted = rows.slice().sort(function (a, b) { return compare(mode, a, b); });
    var frag = document.createDocumentFragment();

    if (byAmount) {
      Object.keys(headers).forEach(function (d) { headers[d].hidden = true; });
      sorted.forEach(function (r) { frag.appendChild(r); });
    } else {
      var order = [];
      sorted.forEach(function (r) {
        if (order.indexOf(r.dataset.date) === -1) order.push(r.dataset.date);
      });
      order.forEach(function (d) {
        var header = headers[d];
        var net = dayNet[d] || 0;
        var totalEl = header.querySelector("[data-day-total]");
        if (totalEl) {
          totalEl.textContent = net > 0
            ? "spent " + window.Money.format(net)
            : net < 0
              ? "received " + window.Money.format(-net)
              : "—";
        }
        header.hidden = !dayNet.hasOwnProperty(d);
        frag.appendChild(header);
        sorted.forEach(function (r) {
          if (r.dataset.date === d) frag.appendChild(r);
        });
      });
    }
    list.appendChild(frag);

    var net = income - expense;
    if (summary) {
      summary.innerHTML =
        "Showing <strong>" + visibleCount + "</strong> of " + rows.length +
        ' · <span class="pos">In ' + window.Money.format(income) + "</span>" +
        ' · <span class="neg">Out ' + window.Money.format(expense) + "</span>" +
        " · Net " + window.Money.format(net);
    }
    if (filteredEmpty) filteredEmpty.hidden = visibleCount !== 0;
  }

  [search, typeSel, categorySel, sortSel, fromInput, toInput].forEach(function (el) {
    if (!el) return;
    el.addEventListener("input", render);
    el.addEventListener("change", render);
  });

  if (clearBtn) {
    clearBtn.addEventListener("click", function () {
      search.value = "";
      typeSel.value = "";
      categorySel.value = "";
      sortSel.value = "newest";
      fromInput.value = "";
      toInput.value = "";
      render();
      search.focus();
    });
  }

  render();
})();
