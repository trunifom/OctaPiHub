/* Applies the saved theme and font size before first paint (loaded in <head>, no flash). */
(function () {
  "use strict";
  var KEYS = { theme: "octapihub.theme", size: "octapihub.size" };
  function read(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function write(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* private mode: keep for this page only */ }
  }
  var root = document.documentElement;
  var theme = read(KEYS.theme);
  var size = read(KEYS.size);
  if (theme === "light" || theme === "dark") { root.setAttribute("data-theme", theme); }
  if (size === "large") { root.setAttribute("data-size", "large"); }

  window.octaPrefs = {
    cycleTheme: function () {
      var order = ["auto", "light", "dark"];
      var current = root.getAttribute("data-theme") || "auto";
      var next = order[(order.indexOf(current) + 1) % order.length];
      root.setAttribute("data-theme", next);
      write(KEYS.theme, next);
      return next;
    },
    toggleSize: function () {
      var next = root.getAttribute("data-size") === "large" ? "normal" : "large";
      root.setAttribute("data-size", next);
      write(KEYS.size, next);
      return next;
    }
  };
})();
