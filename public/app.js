(function () {
  "use strict";

  // Same origin by default (the server serves this page). When the frontend is
  // served from a different port/host, set window.API_BASE before this script,
  // e.g. <script>window.API_BASE = "http://localhost:5000";</script>.
  var API_BASE = window.API_BASE || "";

  var inr = new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
  });

  var els = {
    authView: document.getElementById("auth-view"),
    appView: document.getElementById("app-view"),
    authForm: document.getElementById("auth-form"),
    authError: document.getElementById("auth-error"),
    authSubmit: document.getElementById("auth-submit"),
    nameField: document.getElementById("name-field"),
    userBox: document.getElementById("user-box"),
    userEmail: document.getElementById("user-email"),
    logout: document.getElementById("logout"),
    txnForm: document.getElementById("txn-form"),
    txnError: document.getElementById("txn-error"),
    txnSubmit: document.getElementById("txn-submit"),
    categorySelect: document.getElementById("category-select"),
    list: document.getElementById("txn-list"),
    empty: document.getElementById("txn-empty"),
    count: document.getElementById("txn-count"),
  };

  var state = { user: null, categories: [], authMode: "login" };

  function api(path, options) {
    options = options || {};
    options.credentials = "include";
    options.headers = Object.assign({ Accept: "application/json" }, options.headers || {});
    if (options.body && !options.headers["Content-Type"]) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(options.body);
    }
    return fetch(API_BASE + path, options).then(function (res) {
      return res
        .json()
        .catch(function () {
          return null;
        })
        .then(function (data) {
          if (!res.ok) {
            var message =
              (data && (data.error || data.message)) || "Request failed (" + res.status + ")";
            var err = new Error(message);
            err.status = res.status;
            err.details = data && data.details;
            throw err;
          }
          return data;
        });
    });
  }

  function showError(el, err) {
    if (!el) return;
    var msg = err && err.message ? err.message : String(err || "Something went wrong");
    if (err && Array.isArray(err.details)) {
      msg += ": " + err.details.map(function (d) { return d.message || d; }).join(", ");
    }
    el.textContent = msg;
    el.hidden = false;
  }

  function clearError(el) {
    if (!el) return;
    el.hidden = true;
    el.textContent = "";
  }

  function showAuth() {
    els.authView.hidden = false;
    els.appView.hidden = true;
    els.userBox.hidden = true;
  }

  function showApp() {
    els.authView.hidden = true;
    els.appView.hidden = false;
    els.userBox.hidden = false;
    els.userEmail.textContent = state.user ? state.user.email : "";
  }

  function setAuthMode(mode) {
    state.authMode = mode;
    els.nameField.hidden = mode !== "register";
    els.authSubmit.textContent = mode === "register" ? "Create account" : "Sign in";
    document.querySelectorAll("[data-auth-tab]").forEach(function (tab) {
      tab.classList.toggle("is-active", tab.dataset.authTab === mode);
    });
    clearError(els.authError);
  }

  function selectedType() {
    var checked = els.txnForm.querySelector('input[name="type"]:checked');
    return checked ? checked.value : "expense";
  }

  function renderCategories() {
    var type = selectedType();
    var previous = els.categorySelect.value;
    els.categorySelect.innerHTML = "";
    var matching = state.categories.filter(function (c) { return c.type === type; });
    if (matching.length === 0) {
      var placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "No " + type + " categories";
      els.categorySelect.appendChild(placeholder);
      return;
    }
    matching.forEach(function (c) {
      var opt = document.createElement("option");
      opt.value = c.id;
      opt.textContent = c.name;
      els.categorySelect.appendChild(opt);
    });
    var keep = matching.some(function (c) { return c.id === previous; });
    els.categorySelect.value = keep ? previous : matching[0].id;
  }

  function updateSubmitLabel() {
    els.txnSubmit.textContent = selectedType() === "income" ? "Save income" : "Save expense";
  }

  function renderList(items) {
    els.list.innerHTML = "";
    els.empty.hidden = items.length !== 0;
    els.count.textContent = items.length + (items.length === 1 ? " entry" : " entries");

    items.forEach(function (t) {
      var li = document.createElement("li");
      li.className = "txn";
      li.innerHTML =
        '<div class="txn-main">' +
        '<span class="txn-title"></span>' +
        '<span class="txn-note"></span>' +
        '<span class="txn-date"></span>' +
        "</div>" +
        '<span class="txn-amount"></span>';
      li.querySelector(".txn-title").textContent = t.category || t.type;
      li.querySelector(".txn-note").textContent = t.description || "No note";
      li.querySelector(".txn-date").textContent = t.date;
      var amountEl = li.querySelector(".txn-amount");
      amountEl.classList.add(t.type);
      amountEl.textContent = (t.type === "income" ? "+" : "-") + inr.format(t.amount);
      els.list.appendChild(li);
    });
  }

  function loadTransactions() {
    return api("/api/transactions", { method: "GET" })
      .then(function (data) { renderList(data.items || []); })
      .catch(function (err) { showError(els.txnError, err); });
  }

  function loadCategories() {
    return api("/api/categories", { method: "GET" }).then(function (data) {
      state.categories = data.items || [];
      renderCategories();
    });
  }

  function loadAll() {
    return Promise.all([loadCategories(), loadTransactions()]);
  }

  els.authForm.addEventListener("submit", function (e) {
    e.preventDefault();
    clearError(els.authError);
    var data = new FormData(els.authForm);
    var payload = {
      email: String(data.get("email") || "").trim(),
      password: String(data.get("password") || ""),
    };
    if (state.authMode === "register") {
      payload.name = String(data.get("name") || "").trim();
    }
    els.authSubmit.disabled = true;
    api("/api/auth/" + state.authMode, { method: "POST", body: payload })
      .then(function (res) {
        state.user = res.user;
        showApp();
        return loadAll();
      })
      .catch(function (err) { showError(els.authError, err); })
      .finally(function () { els.authSubmit.disabled = false; });
  });

  document.querySelectorAll("[data-auth-tab]").forEach(function (tab) {
    tab.addEventListener("click", function () { setAuthMode(tab.dataset.authTab); });
  });

  els.logout.addEventListener("click", function () {
    api("/api/auth/logout", { method: "POST" })
      .catch(function () {})
      .finally(function () {
        state.user = null;
        state.categories = [];
        renderList([]);
        showAuth();
      });
  });

  els.txnForm.addEventListener("change", function (e) {
    if (e.target.name === "type") {
      renderCategories();
      updateSubmitLabel();
    }
  });

  els.txnForm.addEventListener("submit", function (e) {
    e.preventDefault();
    clearError(els.txnError);

    var data = new FormData(els.txnForm);
    var amount = parseFloat(String(data.get("amount") || ""));
    if (!isFinite(amount) || amount <= 0) {
      showError(els.txnError, new Error("Enter an amount greater than 0."));
      return;
    }
    if (!data.get("categoryId")) {
      showError(els.txnError, new Error("Pick a category."));
      return;
    }

    var payload = {
      type: selectedType(),
      amount: amount,
      categoryId: data.get("categoryId"),
      date: data.get("date"),
      description: String(data.get("description") || "").trim(),
    };

    els.txnSubmit.disabled = true;
    api("/api/transactions", { method: "POST", body: payload })
      .then(function () {
        els.txnForm.querySelector('[name="amount"]').value = "";
        els.txnForm.querySelector('[name="description"]').value = "";
        return loadTransactions();
      })
      .catch(function (err) { showError(els.txnError, err); })
      .finally(function () { els.txnSubmit.disabled = false; });
  });

  function init() {
    els.txnForm.querySelector('[name="date"]').value = new Date().toISOString().slice(0, 10);
    setAuthMode("login");
    updateSubmitLabel();

    api("/api/auth/me", { method: "GET" })
      .then(function (res) {
        state.user = res.user;
        showApp();
        return loadAll();
      })
      .catch(function () { showAuth(); });
  }

  init();
})();
