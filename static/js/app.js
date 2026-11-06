/* Shared UI behaviour: icons, dialogs, toasts, validation, money formatting. */
(function () {
  "use strict";

  var inr = new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2
  });

  window.Money = {
    format: function (n) { return inr.format(Number(n) || 0); },
    parse: function (s) {
      var n = parseFloat(String(s).replace(/[^0-9.\-]/g, ""));
      return isFinite(n) ? n : NaN;
    }
  };

  /* ---------- API: CSRF + expired-session recovery ---------- */
  var csrfMeta = document.querySelector('meta[name="csrf-token"]');

  function csrfToken() {
    return csrfMeta ? csrfMeta.getAttribute("content") || "" : "";
  }

  function setCsrfToken(token) {
    if (!token) return;
    if (!csrfMeta) {
      csrfMeta = document.createElement("meta");
      csrfMeta.name = "csrf-token";
      document.head.appendChild(csrfMeta);
    }
    csrfMeta.setAttribute("content", token);
    document.querySelectorAll('input[name="csrf_token"]').forEach(function (input) {
      input.value = token;
    });
  }

  function syncCsrfInputs() {
    var token = csrfToken();
    if (!token) return;
    document.querySelectorAll('input[name="csrf_token"]').forEach(function (input) {
      input.value = token;
    });
  }

  function refreshCsrfToken() {
    return fetch("/csrf-token", {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Accept": "application/json" }
    }).then(function (response) {
      return response.json();
    }).then(function (data) {
      if (!data || !data.ok || !data.csrf_token) {
        throw new Error("Could not refresh the session token.");
      }
      setCsrfToken(data.csrf_token);
      return data.csrf_token;
    });
  }

  function sendRequest(url, fetchOptions, canRetry) {
    return fetch(url, fetchOptions).then(function (response) {
      return response.json().catch(function () {
        throw new Error("The server returned an unexpected response.");
      }).then(function (data) {
        var expired = response.status === 401 || response.status === 403 || data.code === "csrf_expired";
        if (expired && canRetry) {
          showToast("Session expired, refreshing…", "error");
          return refreshCsrfToken().then(function (token) {
            var headers = Object.assign({}, fetchOptions.headers, {
              "X-CSRF-Token": token,
              "Accept": "application/json"
            });
            return sendRequest(url, Object.assign({}, fetchOptions, { headers: headers }), false);
          }).catch(function (error) {
            if (error && error.status) throw error;
            showToast("Session expired. Reloading the page…", "error");
            setTimeout(function () { window.location.reload(); }, 900);
            throw new Error("Session expired. Please try again.");
          });
        }
        if (!response.ok || data.ok === false) {
          var error = new Error(data.error || "That action could not be completed.");
          error.field = data.field;
          error.status = response.status;
          error.code = data.code;
          throw error;
        }
        return data;
      });
    }).catch(function (error) {
      if (error && error.status === 403 && canRetry) {
        showToast("Session expired. Reloading the page…", "error");
        setTimeout(function () { window.location.reload(); }, 900);
      }
      throw error;
    });
  }

  function apiRequest(url, options) {
    options = options || {};
    var method = options.method || "POST";
    var body = options.body;
    var headers = Object.assign({ "Accept": "application/json" }, options.headers || {});
    var token = csrfToken();
    if (token) headers["X-CSRF-Token"] = token;
    if (body && typeof body === "object" && !(body instanceof FormData) && !(body instanceof Blob)) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }
    var fetchOptions = {
      method: method,
      credentials: "same-origin",
      cache: "no-store",
      headers: headers
    };
    if (body !== undefined && body !== null) fetchOptions.body = body;
    return sendRequest(url, fetchOptions, true);
  }

  window.Api = {
    csrfToken: csrfToken,
    syncToken: syncCsrfInputs,
    refreshToken: refreshCsrfToken,
    request: apiRequest,
    get: function (url, options) {
      return apiRequest(url, Object.assign({}, options || {}, { method: "GET" }));
    },
    post: function (url, body, options) {
      return apiRequest(url, Object.assign({}, options || {}, { method: "POST", body: body }));
    }
  };

  window.addEventListener("unhandledrejection", function (event) {
    var reason = event.reason;
    if (reason && reason.message) {
      showToast(reason.message, "error");
      event.preventDefault();
    }
  });

  /* ---------- icons ---------- */
  function drawIcons() {
    if (window.lucide) window.lucide.createIcons({ attrs: { "stroke-width": 2 } });
  }

  /* ---------- dialogs ---------- */
  var dialogOpeners = new WeakMap();

  function lockScroll() {
    document.documentElement.classList.add("modal-open");
    document.body.classList.add("modal-open");
  }

  function unlockScroll() {
    if (document.querySelector("dialog[open]")) return;
    document.documentElement.classList.remove("modal-open");
    document.body.classList.remove("modal-open");
  }

  function focusable(d) {
    return Array.prototype.filter.call(
      d.querySelectorAll(
        'a[href], button:not([disabled]), input:not([type="hidden"]):not([disabled]), ' +
        'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      ),
      function (el) { return !el.hidden && el.getClientRects().length > 0; }
    );
  }

  function handleDialogClosed(d) {
    unlockScroll();
    var opener = dialogOpeners.get(d);
    dialogOpeners.delete(d);
    if (opener && document.contains(opener) && typeof opener.focus === "function") {
      setTimeout(function () { opener.focus(); }, 0);
    }
  }

  function openDialog(id, opener) {
    var d = document.getElementById(id);
    if (!d) return;
    syncCsrfInputs();
    dialogOpeners.set(d, opener || document.activeElement);
    if (typeof d.showModal === "function") {
      if (!d.open) d.showModal();
    } else {
      d.setAttribute("open", "");
    }
    lockScroll();
    var first = d.querySelector("[autofocus]") || focusable(d)[0];
    if (first) setTimeout(function () { first.focus(); }, 60);
  }

  function closeDialog(el) {
    var d = el && el.closest ? el.closest("dialog") : null;
    if (!d) return;
    if (typeof d.close === "function" && d.open) {
      d.close();
    } else {
      d.removeAttribute("open");
      handleDialogClosed(d);
    }
  }

  window.Modal = { open: openDialog, close: closeDialog };

  /* JSON forms must never do a native POST navigation (raw JSON page). */
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (form && form.matches && form.matches("form[data-json-form]")) {
      e.preventDefault();
    }
  }, true);

  document.addEventListener("click", function (e) {
    var opener = e.target.closest("[data-dialog]");
    if (opener) {
      var target = document.getElementById(opener.dataset.dialog);
      if (target) {
        e.preventDefault();
        if (typeof window.beforeDialogOpen === "function") window.beforeDialogOpen(opener.dataset.dialog, opener);
        openDialog(opener.dataset.dialog, opener);
      }
      return;
    }
    var closer = e.target.closest("[data-dialog-close]");
    if (closer) { e.preventDefault(); closeDialog(closer); }
  });

  /* Escape closes the top dialog. Native <dialog> handles this itself; the
     explicit path covers browsers without showModal(). */
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var open = document.querySelectorAll("dialog[open]");
    if (!open.length) return;
    var d = open[open.length - 1];
    if (typeof d.close !== "function") closeDialog(d);
  });

  /* keep Tab inside the open dialog */
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Tab") return;
    var open = document.querySelectorAll("dialog[open]");
    if (!open.length) return;
    var d = open[open.length - 1];
    var items = focusable(d);
    if (!items.length) { e.preventDefault(); d.focus(); return; }
    var first = items[0];
    var last = items[items.length - 1];
    var active = document.activeElement;
    if (e.shiftKey && (active === first || !d.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && (active === last || !d.contains(active))) {
      e.preventDefault();
      first.focus();
    }
  });

  /* clicking outside the card (the full-screen dialog area) closes */
  document.addEventListener("click", function (e) {
    var d = e.target.closest && e.target.closest("dialog");
    if (d && e.target === d) closeDialog(d);
  });

  /* ---------- toasts ---------- */
  function dismissToast(el) {
    if (!el || !el.parentNode) return;
    el.style.transition = "opacity 200ms, transform 200ms";
    el.style.opacity = "0";
    el.style.transform = "translateY(-6px)";
    setTimeout(function () { el.remove(); }, 220);
  }

  function showToast(message, type, action) {
    var stack = document.querySelector(".flash-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "flash-stack";
      document.body.appendChild(stack);
    }
    var kind = type === "error" ? "error" : "success";
    var el = document.createElement("div");
    el.className = "flash " + kind;
    el.setAttribute("role", kind === "error" ? "alert" : "status");

    var icon = document.createElement("i");
    icon.setAttribute("data-lucide", kind === "error" ? "alert-circle" : "check-circle-2");
    el.appendChild(icon);

    var text = document.createElement("span");
    text.className = "flash-text";
    text.textContent = message;
    el.appendChild(text);

    var timer = null;
    if (action) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "flash-action";
      button.textContent = action.label || "Undo";
      button.addEventListener("click", function () {
        if (timer) clearTimeout(timer);
        dismissToast(el);
        action.onClick();
      });
      el.appendChild(button);
    }
    stack.appendChild(el);
    drawIcons();
    timer = setTimeout(function () { dismissToast(el); }, action ? 10000 : 5000);
    return el;
  }

  window.showToast = showToast;

  document.addEventListener("DOMContentLoaded", function () {
    syncCsrfInputs();
    document.querySelectorAll(".flash").forEach(function (t) {
      setTimeout(function () {
        if (!t.dataset.sticky) dismissToast(t);
      }, 4000);
    });
  });

  /* ---------- delete confirmation ---------- */
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-delete-url]");
    if (!btn) return;
    var form = document.getElementById("delete-confirm-form");
    if (!form) return;
    form.action = btn.dataset.deleteUrl;
    var label = btn.dataset.deleteLabel;
    var desc = document.getElementById("delete-confirm-desc");
    if (desc && label) desc.textContent = label;
    openDialog("delete-dialog", btn);
  });

  /* ---------- budget alerts ---------- */
  function alertRequest(url, body) {
    window.Api.post(url, body || {}).then(function (data) {
      if (data && data.message) showToast(data.message, "success");
      setTimeout(function () { window.location.reload(); }, 250);
    }).catch(function (error) {
      showToast(error.message, "error");
    });
  }

  document.addEventListener("click", function (e) {
    var toggle = e.target.closest("[data-alerts-toggle]");
    if (toggle) {
      e.preventDefault();
      var panel = document.getElementById("alerts-panel");
      if (!panel) return;
      panel.hidden = !panel.hidden;
      toggle.setAttribute("aria-expanded", panel.hidden ? "false" : "true");
      return;
    }
    var dismiss = e.target.closest("[data-alert-dismiss]");
    if (dismiss) {
      e.preventDefault();
      alertRequest(dismiss.dataset.url);
      return;
    }
    var dismissAll = e.target.closest("[data-alert-dismiss-all]");
    if (dismissAll) {
      e.preventDefault();
      alertRequest("/budgets/alerts/dismiss-all");
      return;
    }
    var panel = document.getElementById("alerts-panel");
    if (panel && !panel.hidden && !e.target.closest(".notification-wrap")) {
      panel.hidden = true;
      var toggleButton = document.querySelector("[data-alerts-toggle]");
      if (toggleButton) toggleButton.setAttribute("aria-expanded", "false");
    }
  });

  /* ---------- transaction form (modal + full page) ---------- */
  function syncCategories(form) {
    var typeInput = form.querySelector('input[name="type"]:checked');
    var type = typeInput ? typeInput.value : "expense";
    var select = form.querySelector('select[name="category_id"]');
    if (!select) return;
    var firstVisible = null;
    Array.prototype.forEach.call(select.options, function (opt) {
      var match = !opt.dataset.type || opt.dataset.type === type;
      opt.hidden = !match;
      opt.disabled = !match;
      if (match && !firstVisible) firstVisible = opt;
    });
    var current = select.selectedOptions[0];
    if (!current || current.disabled || current.hidden) {
      select.value = firstVisible ? firstVisible.value : "";
    }
  }

  function setError(form, name, message) {
    var input = form.querySelector('[name="' + name + '"]');
    var err = form.querySelector('[data-error-for="' + name + '"]');
    if (input) input.setAttribute("aria-invalid", message ? "true" : "false");
    if (err) {
      err.textContent = message || "";
      err.classList.toggle("is-visible", Boolean(message));
    }
    var wrap = input && input.closest(".amount-wrap");
    if (wrap) wrap.classList.toggle("is-invalid", Boolean(message));
    return !message;
  }

  function validate(form) {
    var ok = true;
    var amount = form.querySelector('[name="amount"]');
    var category = form.querySelector('[name="category_id"]');
    var date = form.querySelector('[name="txn_date"]');

    var val = amount ? window.Money.parse(amount.value) : NaN;
    ok = setError(form, "amount", !amount.value.trim()
      ? "Enter an amount."
      : (isNaN(val) || val <= 0 ? "Amount must be greater than 0." : "")) && ok;

    ok = setError(form, "category_id", category && !category.value ? "Pick a category." : "") && ok;

    ok = setError(form, "txn_date", date && !date.value ? "Choose a date." : "") && ok;

    return ok;
  }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches(".txn-form")) return;
    if (!validate(form)) {
      e.preventDefault();
      var invalid = form.querySelector('[aria-invalid="true"]');
      if (invalid) invalid.focus();
    }
  });

  document.addEventListener("input", function (e) {
    var form = e.target.closest && e.target.closest(".txn-form");
    if (form && e.target.name && form.querySelector('[data-error-for="' + e.target.name + '"]')) {
      setError(form, e.target.name, "");
    }
  });

  document.addEventListener("change", function (e) {
    var form = e.target.closest && e.target.closest(".txn-form");
    if (!form) return;
    if (e.target.name === "type") syncCategories(form);
    if (e.target.name === "category_id") setError(form, "category_id", "");
  });

  window.TxnForm = {
    sync: syncCategories,
    validate: validate,
    reset: function (form) {
      form.reset();
      form.querySelectorAll('option[data-archived="true"]').forEach(function (opt) {
        opt.remove();
      });
      var archivedNote = form.querySelector("[data-archived-note]");
      if (archivedNote) archivedNote.hidden = true;
      if (form.dataset.defaultAction) form.setAttribute("action", form.dataset.defaultAction);
      form.querySelectorAll("[data-error-for]").forEach(function (el) {
        el.classList.remove("is-visible");
        el.textContent = "";
      });
      form.querySelectorAll('[aria-invalid="true"]').forEach(function (el) {
        el.setAttribute("aria-invalid", "false");
      });
      var title = form.querySelector("[data-form-title]");
      if (title) title.textContent = "Add transaction";
      var today = form.querySelector('[name="txn_date"]');
      if (today && !today.dataset.keepDate) today.value = new Date().toISOString().slice(0, 10);
      syncCategories(form);
    }
  };

  /* ---------- edit: prefill the shared modal from the row's data attributes ---------- */
  window.beforeDialogOpen = function (id, opener) {
    if (id !== "txn-dialog") return;
    var form = document.getElementById("txn-form");
    if (!form) return;

    var row = opener && opener.closest ? opener.closest(".txn-row") : null;
    var isEdit = opener && opener.hasAttribute("data-edit") && row;
    window.TxnForm.reset(form);
    if (!isEdit) return;

    var data = row.dataset;
    var url = opener.getAttribute("data-edit-url") || row.getAttribute("data-edit-url");
    if (url) form.action = url;

    var title = form.querySelector("[data-form-title]");
    if (title) title.textContent = "Edit transaction";

    var amount = form.querySelector('[name="amount"]');
    if (amount) amount.value = data.amount || "";
    var dateInput = form.querySelector('[name="txn_date"]');
    if (dateInput) dateInput.value = data.date || "";
    var note = form.querySelector('[name="description"]');
    if (note) note.value = data.note || "";
    var typeRadio = form.querySelector('input[name="type"][value="' + data.type + '"]');
    if (typeRadio) typeRadio.checked = true;
    syncCategories(form);
    var category = form.querySelector('[name="category_id"]');
    if (category && data.categoryId) {
      var hasOption = Array.prototype.some.call(category.options, function (opt) {
        return opt.value === data.categoryId;
      });
      if (!hasOption) {
        /* The category may have been archived since this transaction was
           created. Keep it as the current value so editing the note cannot
           silently move the transaction to a different category. */
        var archivedOption = document.createElement("option");
        archivedOption.value = data.categoryId;
        archivedOption.dataset.type = data.type;
        archivedOption.dataset.archived = "true";
        archivedOption.textContent = (data.category || "Archived") + " (archived)";
        category.appendChild(archivedOption);
        archivedOption.hidden = true;
        archivedOption.disabled = false;
        var archivedNote = form.querySelector("[data-archived-note]");
        if (archivedNote) archivedNote.hidden = false;
      }
      category.value = data.categoryId;
    }
  };

  document.addEventListener("DOMContentLoaded", function () {
    drawIcons();
    document.querySelectorAll(".txn-form").forEach(function (form) {
      form.noValidate = true;
      if (form.getAttribute("action")) form.dataset.defaultAction = form.getAttribute("action");
      syncCategories(form);
    });

    document.querySelectorAll("dialog").forEach(function (d) {
      d.addEventListener("close", function () { handleDialogClosed(d); });
    });

    /* Whenever the shared transaction dialog closes - for any reason:
       Cancel, Escape, backdrop click, or after a submit - immediately snap
       the form back to the "Add transaction" state and the default action.
       Without this, an edit URL (/transactions/<id>/edit, POST-only) could
       survive into the next open and a plain "Add" submit would 405. */
    var txnDialog = document.getElementById("txn-dialog");
    if (txnDialog) {
      txnDialog.addEventListener("close", function () {
        var form = document.getElementById("txn-form");
        if (form) window.TxnForm.reset(form);
      });
    }
  });

  window.RedrawIcons = drawIcons;

  /* Keep other open tabs (dashboard/transactions) from showing stale totals. */
  var dataChannel = null;
  try {
    if ("BroadcastChannel" in window) {
      dataChannel = new BroadcastChannel("moneywise-data");
      dataChannel.addEventListener("message", function (event) {
        if (!event.data || event.data.type !== "categories-changed") return;
        if (document.querySelector("dialog[open]")) return;
        window.location.reload();
      });
    }
  } catch (e) { dataChannel = null; }

  window.notifyDataChanged = function () {
    try {
      if (dataChannel) dataChannel.postMessage({ type: "categories-changed" });
    } catch (e) {}
  };
})();
