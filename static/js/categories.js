/* Categories page: usage-aware delete / reassign / archive with live updates. */
(function () {
  "use strict";

  var dialog = document.getElementById("category-delete-dialog");
  if (!dialog || !window.Modal || !window.Money) return;

  var form = document.getElementById("category-delete-form");
  var summary = document.getElementById("category-delete-summary");
  var choices = document.getElementById("category-delete-choices");
  var targetSelect = document.getElementById("category-delete-target");
  var budgetNote = document.getElementById("category-budget-note");
  var unusedHint = document.getElementById("category-delete-unused-hint");
  var errorEl = document.getElementById("category-delete-error");
  var submitBtn = document.getElementById("category-delete-submit");
  var current = null;

  var editDialog = document.getElementById("category-edit-dialog");
  var editForm = document.getElementById("category-edit-form");
  var editTitle = document.querySelector("[data-category-edit-title]");
  var editName = document.getElementById("category-edit-name");
  var editNameError = document.getElementById("category-edit-name-error");
  var editError = document.getElementById("category-edit-error");
  var editMeta = document.getElementById("category-edit-meta");
  var editSubmit = document.getElementById("category-edit-submit");
  var editCurrent = null;

  function api(url, body) {
    if (!window.Api) {
      return Promise.reject(new Error("The page needs to be refreshed before saving."));
    }
    return window.Api.post(url, body || {});
  }

  function clearError() {
    errorEl.textContent = "";
    errorEl.classList.remove("is-visible");
  }

  function showError(message) {
    errorEl.textContent = message || "That action could not be completed.";
    errorEl.classList.add("is-visible");
    errorEl.focus();
  }

  /* ---------- rename category ---------- */
  function setEditNameError(message) {
    if (!editNameError || !editName) return;
    editNameError.textContent = message || "";
    editNameError.classList.toggle("is-visible", Boolean(message));
    editName.setAttribute("aria-invalid", message ? "true" : "false");
    if (message) {
      editName.focus();
      editName.select();
    }
  }

  function setEditError(message) {
    if (!editError) return;
    editError.textContent = message || "";
    editError.classList.toggle("is-visible", Boolean(message));
    if (message) editError.focus();
  }

  function clearEditErrors() {
    setEditNameError("");
    setEditError("");
  }

  function setEditSubmitBusy(busy, label) {
    if (!editSubmit) return;
    editSubmit.disabled = Boolean(busy);
    if (busy) {
      editSubmit.textContent = label || "Saving…";
    } else {
      editSubmit.innerHTML = '<i data-lucide="check"></i> Save changes';
      if (window.RedrawIcons) window.RedrawIcons();
    }
  }

  function resetEditDialog() {
    editCurrent = null;
    if (!editForm) return;
    editForm.setAttribute("action", "");
    if (editTitle) editTitle.textContent = "Edit category";
    if (editName) editName.value = "";
    if (editMeta) editMeta.textContent = "";
    clearEditErrors();
    setEditSubmitBusy(false);
  }

  function sameNameExists(clean, id) {
    var wanted = clean.trim().toLowerCase();
    var found = false;
    document.querySelectorAll("[data-cat-row][data-category-name]").forEach(function (row) {
      if (found || row.dataset.catId === String(id)) return;
      if ((row.dataset.categoryName || "").trim().toLowerCase() === wanted) found = true;
    });
    return found;
  }

  function updateCategoryEverywhere(id, name) {
    document.querySelectorAll(
      '[data-cat-row][data-cat-id="' + id + '"]'
    ).forEach(function (row) {
      row.dataset.categoryName = name;

      var chip = row.querySelector(".cat-chip");
      if (chip) {
        Array.prototype.slice.call(chip.childNodes).forEach(function (node) {
          if (node.nodeType === 3) chip.removeChild(node);
        });
        chip.appendChild(document.createTextNode(" " + name));
      }

      row.querySelectorAll("[data-cat-edit], [data-cat-delete]").forEach(function (button) {
        button.dataset.categoryName = name;
        var action = button.hasAttribute("data-cat-edit") ? "Edit" : "Delete";
        button.setAttribute("aria-label", action + " " + name + " category");
      });
    });

    document.querySelectorAll('option[value="' + id + '"]').forEach(function (option) {
      option.textContent = name;
      if (option.dataset.name) option.dataset.name = name;
    });
  }

  function openEditDialog(button) {
    if (!editDialog || !editForm || !editName) return;
    editCurrent = {
      id: String(button.dataset.categoryId || ""),
      name: button.dataset.categoryName || "",
      type: button.dataset.categoryType || "",
      url: button.dataset.editUrl || ""
    };

    clearEditErrors();
    setEditSubmitBusy(false);
    editTitle.textContent = "Edit category";
    editName.value = editCurrent.name;
    editName.removeAttribute("aria-invalid");
    editMeta.textContent =
      (editCurrent.type === "income" ? "Income" : "Expense") +
      " category · only the name changes, so linked transactions and budgets stay as they are.";

    /* Always set the action on every open so a previous edit URL can never
       leak into the next save (the old transaction modal 405 bug). */
    editForm.setAttribute("action", editCurrent.url);
    window.Modal.open("category-edit-dialog", button);
    window.setTimeout(function () {
      editName.focus();
      editName.select();
    }, 80);
  }

  function plural(count, one, many) {
    return count + " " + (count === 1 ? one : many);
  }

  function selectedAction(usage) {
    if (!usage.transaction_count && !usage.budget_count) return "delete";
    var checked = form.querySelector('input[name="action"]:checked');
    return checked ? checked.value : "reassign";
  }

  function updateSubmitLabel() {
    if (!current) return;
    var action = selectedAction(current.usage);
    if (action === "reassign") submitBtn.textContent = "Reassign & delete";
    else if (action === "archive") submitBtn.textContent = "Archive category";
    else submitBtn.textContent = "Delete";
  }

  function updateBudgetNote() {
    if (!current) return;
    var usage = current.usage;
    if (!usage.budget_count) {
      budgetNote.hidden = true;
      budgetNote.textContent = "";
      return;
    }
    var option = targetSelect.selectedOptions[0];
    if (!option) {
      budgetNote.hidden = true;
      return;
    }
    var name = option.dataset.name || "the target category";
    if (option.dataset.hasBudget === "1") {
      var sourceLimit = parseFloat(usage.budget_limit || 0);
      var targetLimit = parseFloat(option.dataset.budgetLimit || 0);
      budgetNote.textContent =
        name + " already has a budget. The limits will be merged (" +
        window.Money.format(sourceLimit) + " + " +
        window.Money.format(targetLimit) + " = " +
        window.Money.format(sourceLimit + targetLimit) + ").";
    } else {
      budgetNote.textContent = "The budget will move to " + name + ".";
    }
    budgetNote.hidden = false;
  }

  function populate(usage, deleteUrl) {
    current = { usage: usage, deleteUrl: deleteUrl };
    summary.textContent =
      usage.category.name + " · " +
      plural(usage.transaction_count, "transaction", "transactions") + " · " +
      plural(usage.budget_count, "budget", "budgets");

    var used = usage.transaction_count > 0 || usage.budget_count > 0;
    choices.hidden = !used;
    unusedHint.hidden = used;
    targetSelect.innerHTML = "";
    clearError();

    if (used) {
      usage.candidates.forEach(function (candidate) {
        var option = document.createElement("option");
        option.value = candidate.id;
        option.textContent = candidate.name;
        option.dataset.name = candidate.name;
        option.dataset.hasBudget = candidate.has_budget ? "1" : "0";
        option.dataset.budgetLimit = candidate.budget_limit || "0";
        targetSelect.appendChild(option);
      });

      var reassign = form.querySelector('input[name="action"][value="reassign"]');
      var archive = form.querySelector('input[name="action"][value="archive"]');
      var hasTarget = usage.candidates.length > 0;
      reassign.disabled = !hasTarget;
      targetSelect.disabled = !hasTarget;
      if (hasTarget) {
        reassign.checked = true;
        targetSelect.selectedIndex = 0;
      } else {
        archive.checked = true;
      }
      updateBudgetNote();
    } else {
      var allActions = form.querySelectorAll('input[name="action"]');
      allActions.forEach(function (input) { input.checked = false; });
    }
    updateSubmitLabel();
    form.setAttribute("action", deleteUrl || "");
  }

  function openDeleteDialog(button) {
    clearError();
    button.disabled = true;
    window.Api.get(button.dataset.usageUrl).then(function (data) {
      populate(data.usage, data.usage.delete_url || button.dataset.deleteUrl);
      window.Modal.open("category-delete-dialog", button);
    }).catch(function (error) {
      window.showToast(error.message, "error");
    }).finally(function () {
      button.disabled = false;
    });
  }

  function removeCategoryFromPage(id) {
    document.querySelectorAll(
      '[data-cat-row][data-cat-id="' + id + '"]'
    ).forEach(function (row) { row.remove(); });
    document.querySelectorAll('option[value="' + id + '"]').forEach(function (option) {
      var select = option.closest("select");
      if (!select) return;
      if (select.id === "category-delete-target") return;
      option.remove();
    });
    updateCounts();
    window.dispatchEvent(new CustomEvent("categories:changed", { detail: { id: id } }));
    if (window.notifyDataChanged) window.notifyDataChanged();
  }

  function updateCounts() {
    ["income", "expense"].forEach(function (type) {
      var target = document.querySelector('[data-cat-count="' + type + '"]');
      if (!target) return;
      var count = 0;
      document.querySelectorAll(
        '[data-cat-row][data-cat-type="' + type + '"]'
      ).forEach(function (row) {
        if (row.closest("#archived-categories, #deleted-categories")) return;
        count += 1;
      });
      target.textContent = plural(count, "category", "categories");
    });
  }

  function historyRow(category, kind, actionUrl) {
    var row = document.createElement("li");
    row.className = "cat-row";
    row.dataset.catRow = "";
    row.dataset.catId = category.id;
    row.dataset.catType = category.type;

    var chip = document.createElement("span");
    chip.className = "cat-chip " + category.type;
    var icon = document.createElement("i");
    icon.setAttribute("data-lucide", "tag");
    chip.appendChild(icon);
    chip.appendChild(document.createTextNode(" " + category.name));
    row.appendChild(chip);

    var when = document.createElement("span");
    when.className = "cat-date";
    when.textContent = (kind === "archive" ? "archived " : "deleted ") +
      new Date().toISOString().slice(0, 10);
    row.appendChild(when);

    var actions = document.createElement("div");
    actions.className = "cat-actions";
    var button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-secondary btn-sm";
    button.innerHTML = '<i data-lucide="undo-2"></i> Undo';
    button.addEventListener("click", function () {
      runAction(actionUrl, {}, "Category restored.").catch(function () {});
    });
    actions.appendChild(button);
    row.appendChild(actions);
    return row;
  }

  function addHistoryRow(category, kind, actionUrl) {
    var section = document.getElementById(
      kind === "archive" ? "archived-categories" : "deleted-categories"
    );
    var list = document.getElementById(
      kind === "archive" ? "archived-category-list" : "deleted-category-list"
    );
    if (!section || !list) return;
    list.appendChild(historyRow(category, kind, actionUrl));
    section.hidden = false;
    if (window.RedrawIcons) window.RedrawIcons();
  }

  function historyRowFor(id) {
    return document.querySelector(
      '#archived-categories [data-cat-id="' + id + '"], ' +
      '#deleted-categories [data-cat-id="' + id + '"]'
    );
  }

  function removeHistoryRow(id) {
    document.querySelectorAll(
      '#archived-categories [data-cat-id="' + id + '"], ' +
      '#deleted-categories [data-cat-id="' + id + '"]'
    ).forEach(function (row) {
      var list = row.parentNode;
      row.remove();
      if (list && !list.children.length) {
        var section = list.closest("section");
        if (section) section.hidden = true;
      }
    });
  }

  function addCategoryOption(category) {
    document.querySelectorAll('select[name="category_id"]').forEach(function (select) {
      if (select.querySelector('option[value="' + category.id + '"]')) return;
      var option = document.createElement("option");
      option.value = category.id;
      option.textContent = category.name;
      option.dataset.type = category.type;
      option.dataset.name = category.name;
      select.appendChild(option);
    });
  }

  function activeRow(category, urls, iconName) {
    var row = document.createElement("div");
    row.className = "cat-row";
    row.dataset.catRow = "";
    row.dataset.catId = category.id;
    row.dataset.catType = category.type;
    row.dataset.categoryName = category.name;

    var chip = document.createElement("span");
    chip.className = "cat-chip " + category.type;
    var icon = document.createElement("i");
    icon.setAttribute("data-lucide", iconName || "tag");
    chip.appendChild(icon);
    chip.appendChild(document.createTextNode(" " + category.name));
    row.appendChild(chip);

    var actions = document.createElement("div");
    actions.className = "cat-actions";

    if (category.is_protected) {
      var locked = document.createElement("span");
      locked.className = "icon-btn cat-locked";
      locked.setAttribute("role", "img");
      locked.setAttribute("aria-label", category.name + " is a locked fallback category");
      locked.setAttribute("title", "Locked: this fallback category keeps uncategorised entries working, so it cannot be renamed.");
      locked.innerHTML = '<i data-lucide="lock"></i>';
      actions.appendChild(locked);
    } else {
      var edit = document.createElement("button");
      edit.type = "button";
      edit.className = "icon-btn";
      edit.setAttribute("aria-label", "Edit " + category.name + " category");
      edit.setAttribute("title", "Edit category");
      edit.setAttribute("data-cat-edit", "");
      edit.dataset.categoryId = category.id;
      edit.dataset.categoryName = category.name;
      edit.dataset.categoryType = category.type;
      edit.dataset.editUrl = urls.edit_url || "";
      edit.innerHTML = '<i data-lucide="pencil"></i>';
      actions.appendChild(edit);
    }

    var del = document.createElement("button");
    del.type = "button";
    del.className = "icon-btn danger";
    del.setAttribute("aria-label", "Delete " + category.name + " category");
    del.setAttribute("title", "Delete category");
    del.setAttribute("data-cat-delete", "");
    del.dataset.categoryId = category.id;
    del.dataset.categoryName = category.name;
    del.dataset.categoryType = category.type;
    del.dataset.usageUrl = urls.usage_url || "";
    del.dataset.deleteUrl = urls.delete_url || "";
    del.innerHTML = '<i data-lucide="trash-2"></i>';
    actions.appendChild(del);

    row.appendChild(actions);
    return row;
  }

  function addActiveRow(category, urls, iconName) {
    var type = category.type === "income" ? "income" : "expense";
    var count = document.querySelector('[data-cat-count="' + type + '"]');
    var section = count ? count.closest("section") : null;
    if (!section) return;
    var rows = section.querySelector(".cat-rows");
    if (!rows) {
      var empty = section.querySelector(".empty");
      if (empty) empty.remove();
      rows = document.createElement("div");
      rows.className = "cat-rows";
      section.appendChild(rows);
    }
    rows.appendChild(activeRow(category, urls, iconName));
    if (window.RedrawIcons) window.RedrawIcons();
  }

  function runAction(url, body, successMessage) {
    return api(url, body).then(function (data) {
      var category = data.category;
      if (category) {
        var previous = historyRowFor(category.id);
        var icon = previous ? previous.querySelector(".cat-chip i") : null;
        var iconName = icon ? icon.getAttribute("data-lucide") : "tag";

        removeHistoryRow(category.id);
        addActiveRow(category, data, iconName);
        addCategoryOption(category);
        updateCounts();
        window.dispatchEvent(new CustomEvent("categories:changed", {
          detail: { id: category.id, name: category.name }
        }));
        if (window.notifyDataChanged) window.notifyDataChanged();
      }
      window.showToast(data.message || successMessage, "success");
      return data;
    }).catch(function (error) {
      window.showToast(error.message, "error");
      throw error;
    });
  }

  if (editForm) {
    editForm.addEventListener("submit", function (event) {
      event.preventDefault();
      if (!editCurrent) return;
      clearEditErrors();

      var clean = editName.value.trim();
      editName.value = clean;
      if (!clean) {
        setEditNameError("Enter a category name.");
        return;
      }
      if (clean.length > 40) {
        setEditNameError("Category name must be 40 characters or fewer.");
        return;
      }

      /* Unchanged name: close without writing anything. */
      if (clean === editCurrent.name) {
        window.Modal.close(editDialog);
        return;
      }
      if (sameNameExists(clean, editCurrent.id)) {
        setEditNameError("A category named '" + clean + "' already exists.");
        return;
      }

      var id = editCurrent.id;
      var url = editCurrent.url || editForm.getAttribute("action");
      setEditSubmitBusy(true);
      api(url, { name: clean }).then(function (data) {
        window.Modal.close(editDialog);
        if (!data.changed) return;
        updateCategoryEverywhere(id, data.category.name);
        window.dispatchEvent(new CustomEvent("categories:changed", {
          detail: { id: id, name: data.category.name }
        }));
        if (window.notifyDataChanged) window.notifyDataChanged();
        window.showToast(data.message || "Category renamed.", "success");
      }).catch(function (error) {
        if (error.field === "name") setEditNameError(error.message);
        else setEditError(error.message);
        window.showToast(error.message, "error");
      }).finally(function () {
        setEditSubmitBusy(false);
      });
    });

    editDialog.addEventListener("close", resetEditDialog);
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!current) return;
    clearError();

    var usage = current.usage;
    var action = selectedAction(usage);
    var target = null;
    if (action === "reassign") {
      target = targetSelect.value;
      if (!target) {
        showError("Choose a category to reassign everything to.");
        targetSelect.focus();
        return;
      }
    }

    submitBtn.disabled = true;
    submitBtn.textContent = "Working…";
    api(current.deleteUrl, {
      action: action,
      target_category_id: target
    }).then(function (data) {
      var category = data.category;
      window.Modal.close(dialog);
      removeCategoryFromPage(category.id);

      var undoUrl = data.undo_url;
      var isArchive = data.action === "archive";
      if (isArchive) undoUrl = data.restore_url;
      addHistoryRow(category, isArchive ? "archive" : "delete", undoUrl);

      window.showToast(data.message, "success", {
        label: "Undo",
        onClick: function () {
          runAction(undoUrl, {}, "Category restored.").catch(function () {});
        }
      });
    }).catch(function (error) {
      showError(error.message);
      window.showToast(error.message, "error");
    }).finally(function () {
      submitBtn.disabled = false;
      updateSubmitLabel();
    });
  });

  form.addEventListener("change", function (event) {
    if (event.target.name === "action") updateSubmitLabel();
    if (event.target.name === "target_category_id") updateBudgetNote();
  });

  document.addEventListener("click", function (event) {
    var editButton = event.target.closest("[data-cat-edit]");
    if (editButton) {
      event.preventDefault();
      openEditDialog(editButton);
      return;
    }

    var deleteButton = event.target.closest("[data-cat-delete]");
    if (deleteButton) {
      event.preventDefault();
      openDeleteDialog(deleteButton);
      return;
    }

    var undoButton = event.target.closest("[data-cat-undo]");
    if (undoButton) {
      event.preventDefault();
      runAction(undoButton.dataset.undoUrl, {}, "Category restored.").catch(function () {});
      return;
    }

    var restoreButton = event.target.closest("[data-cat-restore]");
    if (restoreButton) {
      event.preventDefault();
      runAction(restoreButton.dataset.restoreUrl, {}, "Category restored.").catch(function () {});
    }
  });

  dialog.addEventListener("close", function () {
    current = null;
    clearError();
  });
})();
