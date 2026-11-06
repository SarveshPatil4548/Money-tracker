/* Theme: follows system preference until the user picks a manual mode. */
(function () {
  var STORAGE_KEY = "money-tracker-theme";
  var root = document.documentElement;

  function stored() {
    var v = localStorage.getItem(STORAGE_KEY);
    return v === "light" || v === "dark" || v === "system" ? v : "system";
  }

  function effective(mode) {
    if (mode === "light" || mode === "dark") return mode;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function apply(mode, persist) {
    if (mode === "system") {
      root.removeAttribute("data-theme");
      if (persist) localStorage.setItem(STORAGE_KEY, "system");
    } else {
      root.setAttribute("data-theme", mode);
      if (persist) localStorage.setItem(STORAGE_KEY, mode);
    }
    syncButtons();
    document.dispatchEvent(new CustomEvent("themechange", { detail: { mode: mode } }));
  }

  function syncButtons() {
    var mode = stored();
    document.querySelectorAll("[data-theme-set]").forEach(function (btn) {
      btn.setAttribute("aria-pressed", String(btn.dataset.themeSet === mode));
    });
    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
      var next = effective(mode) === "dark" ? "light" : "dark";
      btn.setAttribute("aria-label", "Switch to " + next + " mode");
      btn.setAttribute("title", "Switch to " + next + " mode");
    });
  }

  window.Theme = {
    init: function () {
      apply(stored(), false);

      document.addEventListener("click", function (e) {
        var set = e.target.closest("[data-theme-set]");
        if (set) { apply(set.dataset.themeSet, true); return; }
        var toggle = e.target.closest("[data-theme-toggle]");
        if (toggle) {
          var next = effective(stored()) === "dark" ? "light" : "dark";
          apply(next, true);
        }
      });

      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
        if (stored() === "system") apply("system", false);
      });
    }
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", window.Theme.init);
  } else {
    window.Theme.init();
  }
})();
