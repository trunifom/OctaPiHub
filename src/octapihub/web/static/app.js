/* OctaPiHub interface script: preferences, live updates (SSE), history chart, token helper.
   No polling loops: the server pushes changes; charts reload only when pushed data arrives. */
(function () {
  "use strict";

  function $(selector, root) { return (root || document).querySelector(selector); }
  function $$(selector, root) { return Array.prototype.slice.call((root || document).querySelectorAll(selector)); }
  function safeStorage(kind) {
    try { return window[kind]; } catch (e) { return null; }
  }

  /* ---------- preferences ---------- */
  var THEME_TEXT = { auto: "Automatisch", light: "Hell", dark: "Dunkel" };
  var SIZE_TEXT = { normal: "Normal", large: "Groß" };
  function syncPrefs() {
    var root = document.documentElement;
    var themeLabel = $('[data-pref-label="theme"]');
    var sizeLabel = $('[data-pref-label="size"]');
    var theme = root.getAttribute("data-theme") || "auto";
    var size = root.getAttribute("data-size") || "normal";
    if (themeLabel) { themeLabel.textContent = THEME_TEXT[theme]; }
    if (sizeLabel) { sizeLabel.textContent = SIZE_TEXT[size]; }
    var sizeButton = $('[data-pref="size"]');
    if (sizeButton) { sizeButton.setAttribute("aria-pressed", size === "large" ? "true" : "false"); }
    var themeButton = $('[data-pref="theme"]');
    if (themeButton) { themeButton.setAttribute("aria-label", "Darstellung: " + THEME_TEXT[theme] + ". Zum Wechseln drücken."); }
  }
  document.addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest("[data-pref]") : null;
    if (!button || !window.octaPrefs) { return; }
    if (button.getAttribute("data-pref") === "theme") { window.octaPrefs.cycleTheme(); }
    else { window.octaPrefs.toggleSize(); }
    syncPrefs();
  });
  syncPrefs();

  /* ---------- live updates ---------- */
  var lastPush = 0;
  function applyFragments(fragments) {
    Object.keys(fragments).forEach(function (id) {
      var target = document.getElementById(id);
      if (target && target.innerHTML !== fragments[id]) { target.innerHTML = fragments[id]; }
    });
  }
  if (document.body.getAttribute("data-live") && "EventSource" in window) {
    var stateEl = $("#live-state");
    var source = new EventSource("/events");
    source.addEventListener("fragments", function (event) {
      try { applyFragments(JSON.parse(event.data)); } catch (e) { return; }
      if (stateEl) { stateEl.hidden = true; }
      document.dispatchEvent(new CustomEvent("octapihub:live"));
    });
    source.onopen = function () { if (stateEl) { stateEl.hidden = true; } };
    source.onerror = function () { if (stateEl) { stateEl.hidden = false; } };
  }

  /* ---------- history chart ---------- */
  var NS = "http://www.w3.org/2000/svg";
  function svgEl(name, attrs) {
    var node = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    return node;
  }
  function niceStep(raw) {
    var pow = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var f = raw / pow;
    return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * pow;
  }
  var zone = "Europe/Zurich";
  function fmtTime(ms, range) {
    var opts = range === "1h" || range === "24h"
      ? { hour: "2-digit", minute: "2-digit", timeZone: zone }
      : { day: "2-digit", month: "2-digit", timeZone: zone };
    return new Intl.DateTimeFormat("de-CH", opts).format(new Date(ms));
  }
  function fmtFull(ms) {
    return new Intl.DateTimeFormat("de-CH", {
      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: zone
    }).format(new Date(ms));
  }

  function initChart() {
    var panel = $("#verlauf");
    var chart = $("#chart");
    if (!panel || !chart) { return; }
    var tip = $("#chart-tip");
    var emptyEl = $("#chart-empty");
    var statsEl = $("#chart-stats");
    var rawToggle = $("#raw-toggle");
    var groupButtons = $$("#chart-groups [data-group]");
    var rangeButtons = $$("#chart-ranges [data-range]");
    var state = { group: groupButtons[0].getAttribute("data-group"), range: "24h", raw: false, data: null, loadedAt: 0 };

    function setPressed(buttons, attr, value) {
      buttons.forEach(function (b) { b.setAttribute("aria-pressed", b.getAttribute(attr) === value ? "true" : "false"); });
    }
    function showEmpty(text) {
      chart.hidden = true; statsEl.hidden = true; tip.hidden = true;
      emptyEl.hidden = false; emptyEl.textContent = text;
    }
    function load() {
      var url = "/api/sense-hat/series/" + encodeURIComponent(state.group) + "?range=" + state.range + "&basis=" + (state.raw ? "raw" : "corrected");
      fetch(url, { headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
        .then(function (data) {
          state.data = data; state.loadedAt = Date.now(); draw();
        })
        .catch(function () {
          showEmpty("Der Verlauf konnte nicht geladen werden. Bitte laden Sie die Seite neu.");
        });
    }

    function draw() {
      var data = state.data;
      if (!data) { return; }
      if (rawToggle) { rawToggle.parentNode.hidden = !data.correction_active; }
      if (!data.available) {
        showEmpty(data.reason === "history_disabled"
          ? "Der Verlauf ist für diesen Sensor ausgeschaltet. Schalten Sie ihn unter Einstellungen ein."
          : "Der Verlauf ist gerade nicht verfügbar, weil das Sense HAT oder der Speicher nicht erreichbar ist.");
        return;
      }
      if (!data.points.length) {
        showEmpty("Für diesen Zeitraum gibt es noch keine Verlaufsdaten. Der Verlauf wird gebündelt gespeichert; die ersten Punkte erscheinen nach wenigen Minuten.");
        return;
      }
      chart.hidden = false; emptyEl.hidden = true; statsEl.hidden = false;
      var W = Math.max(280, chart.clientWidth), H = Math.max(200, chart.clientHeight);
      var m = { l: 52, r: 14, t: 14, b: 28 };
      var pw = W - m.l - m.r, ph = H - m.t - m.b;
      var vals = data.points.map(function (p) { return p.v; });
      var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
      if (data.fixed) { lo = data.fixed[0]; hi = data.fixed[1]; }
      else {
        var span = Math.max(hi - lo, data.min_span), mid = (hi + lo) / 2;
        lo = mid - span / 2; hi = mid + span / 2;
      }
      var step = niceStep((hi - lo) / 4);
      var y0 = Math.floor(lo / step) * step, y1 = Math.ceil(hi / step) * step;
      var decimals = Math.max(0, -Math.floor(Math.log(step) / Math.LN10 + 1e-9));
      function X(t) { return m.l + (t - data.since) / (data.until - data.since) * pw; }
      function Y(v) { return m.t + (1 - (v - y0) / (y1 - y0)) * ph; }

      var svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none", "aria-hidden": "true" });
      for (var v = y0; v <= y1 + step / 2; v += step) {
        var gy = Y(v);
        svg.appendChild(svgEl("line", { x1: m.l, x2: W - m.r, y1: gy, y2: gy, "class": "grid" }));
        var label = svgEl("text", { x: m.l - 8, y: gy + 4, "text-anchor": "end", "class": "axis" });
        label.textContent = v.toFixed(decimals);
        svg.appendChild(label);
      }
      for (var i = 0; i <= 4; i++) {
        var t = data.since + (data.until - data.since) * i / 4;
        var tx = svgEl("text", { x: X(t), y: H - 8, "text-anchor": i === 0 ? "start" : i === 4 ? "end" : "middle", "class": "axis" });
        tx.textContent = fmtTime(t, data.range);
        svg.appendChild(tx);
      }
      var gap = data.bucket_seconds * 1000 * 4, line = "", area = "", seg = [];
      function flush() {
        if (!seg.length) { return; }
        var d = "M" + seg.map(function (p) { return X(p.t).toFixed(1) + " " + Y(p.v).toFixed(1); }).join("L");
        line += d;
        area += d + "L" + X(seg[seg.length - 1].t).toFixed(1) + " " + (m.t + ph) + "L" + X(seg[0].t).toFixed(1) + " " + (m.t + ph) + "Z";
        seg = [];
      }
      data.points.forEach(function (p, idx) {
        if (idx && p.t - data.points[idx - 1].t > gap) { flush(); }
        seg.push(p);
      });
      flush();
      svg.appendChild(svgEl("path", { d: area, "class": "area" }));
      svg.appendChild(svgEl("path", { d: line, "class": "line" }));
      var cursor = svgEl("line", { y1: m.t, y2: m.t + ph, "class": "cursor", visibility: "hidden" });
      var dot = svgEl("circle", { r: 5, "class": "dot", visibility: "hidden" });
      svg.appendChild(cursor); svg.appendChild(dot);
      chart.textContent = "";
      chart.appendChild(svg);

      function show(clientX) {
        var rect = svg.getBoundingClientRect();
        var px = (clientX - rect.left) / rect.width * W;
        var best = null, bd = Infinity;
        data.points.forEach(function (p) { var d = Math.abs(X(p.t) - px); if (d < bd) { bd = d; best = p; } });
        if (!best) { return; }
        var cx = X(best.t), cy = Y(best.v);
        cursor.setAttribute("x1", cx); cursor.setAttribute("x2", cx); cursor.setAttribute("visibility", "visible");
        dot.setAttribute("cx", cx); dot.setAttribute("cy", cy); dot.setAttribute("visibility", "visible");
        tip.textContent = "";
        var strong = document.createElement("strong");
        strong.textContent = best.v.toFixed(data.precision) + " " + data.unit;
        tip.appendChild(strong);
        tip.appendChild(document.createElement("br"));
        tip.appendChild(document.createTextNode(fmtFull(best.t)));
        tip.hidden = false;
        tip.style.left = (cx / W * rect.width) + "px";
        tip.style.top = (cy / H * rect.height) + "px";
      }
      function hide() { cursor.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tip.hidden = true; }
      svg.addEventListener("pointermove", function (e) { show(e.clientX); });
      svg.addEventListener("pointerleave", hide);

      var sum = vals.reduce(function (a, b) { return a + b; }, 0);
      var stats = { min: Math.min.apply(null, vals), avg: sum / vals.length, max: Math.max.apply(null, vals) };
      Object.keys(stats).forEach(function (key) {
        var cell = $('[data-stat="' + key + '"]', statsEl);
        if (cell) { cell.textContent = stats[key].toFixed(data.precision) + " " + data.unit; }
      });
      chart.setAttribute("aria-label", data.label + " im Zeitraum " + data.range_label + ": " + vals.length +
        " Messpunkte von " + stats.min.toFixed(data.precision) + " bis " + stats.max.toFixed(data.precision) + " " + data.unit + ".");
    }

    groupButtons.forEach(function (b) {
      b.addEventListener("click", function () {
        state.group = b.getAttribute("data-group"); setPressed(groupButtons, "data-group", state.group); load();
      });
    });
    rangeButtons.forEach(function (b) {
      b.addEventListener("click", function () {
        state.range = b.getAttribute("data-range"); setPressed(rangeButtons, "data-range", state.range); load();
      });
    });
    if (rawToggle) { rawToggle.addEventListener("change", function () { state.raw = rawToggle.checked; load(); }); }
    document.addEventListener("click", function (event) {
      var link = event.target.closest ? event.target.closest("[data-chart-link]") : null;
      if (!link) { return; }
      var group = link.getAttribute("data-chart-link");
      if (groupButtons.some(function (b) { return b.getAttribute("data-group") === group; })) {
        event.preventDefault();
        state.group = group; setPressed(groupButtons, "data-group", group); load();
        panel.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
    /* New data arrives via SSE; reload the chart at most once per minute. */
    document.addEventListener("octapihub:live", function () {
      if (Date.now() - state.loadedAt > 60000) { load(); }
    });
    if (window.ResizeObserver) {
      var frame = 0;
      new ResizeObserver(function () {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(function () { if (state.data && !chart.hidden) { draw(); } });
      }).observe(chart);
    }
    load();
  }
  initChart();

  /* ---------- LED matrix: live preview and controls ---------- */
  function initDisplay() {
    var root = $("#display-page");
    if (!root) { return; }
    var grid = $("#led-live");
    var viewLabel = $("#display-view");
    var stateBadge = $("#display-state");
    var message = $("#display-msg");
    var tokenField = $("#f-token");
    var brightness = $("#display-brightness");
    var circles = grid ? $$("circle.led", grid) : [];

    function paint(hex) {
      if (!grid || hex.length !== 384) { return; }
      circles.forEach(function (circle, index) {
        var color = hex.substr(index * 6, 6);
        if (color === "000000") {
          circle.setAttribute("class", "led off");
          circle.removeAttribute("fill");
        } else {
          circle.setAttribute("class", "led");
          circle.setAttribute("fill", "#" + color);
        }
      });
    }
    function say(text, isError) {
      if (!message) { return; }
      message.textContent = text || "";
      message.classList.toggle("is-error", !!isError);
    }
    function markActive(view) {
      $$("[data-view-card]").forEach(function (card) {
        var active = card.getAttribute("data-view-card") === view;
        card.classList.toggle("is-active", active);
        if (active) { card.setAttribute("aria-current", "true"); } else { card.removeAttribute("aria-current"); }
      });
    }
    function send(action, value) {
      var body = new URLSearchParams();
      body.set("action", action);
      body.set("value", value);
      var headers = { "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json" };
      if (tokenField && tokenField.value) { headers["X-OctaPiHub-Token"] = tokenField.value; }
      say("");
      fetch("/display/control", { method: "POST", headers: headers, body: body.toString() })
        .then(function (response) { return response.json().then(function (data) { return { ok: response.ok, data: data }; }); })
        .then(function (result) {
          if (!result.ok) {
            say((result.data.title || "Fehler") + (result.data.detail ? ": " + result.data.detail : "") +
              (result.data.code ? " (" + result.data.code + ")" : ""), true);
            return;
          }
          if (result.data.label && viewLabel) { viewLabel.textContent = result.data.label; }
          markActive(result.data.view);
          if (brightness && result.data.brightness) { brightness.value = String(result.data.brightness); }
        })
        .catch(function () { say("Die Verbindung zum Gerät ist gerade nicht möglich.", true); });
    }

    root.addEventListener("click", function (event) {
      var target = event.target.closest ? event.target.closest("[data-joy], [data-view]") : null;
      if (!target) { return; }
      if (target.hasAttribute("data-joy")) { send("joystick", target.getAttribute("data-joy")); }
      else { send("view", target.getAttribute("data-view")); }
    });
    if (brightness) {
      brightness.addEventListener("change", function () { send("brightness", brightness.value); });
    }
    if (!("EventSource" in window)) { return; }
    var source = new EventSource("/events/display");
    source.addEventListener("display", function (event) {
      var data;
      try { data = JSON.parse(event.data); } catch (e) { return; }
      paint(data.frame);
      if (viewLabel) { viewLabel.textContent = data.label; }
      markActive(data.view);
    });
  }
  initDisplay();

  /* ---------- settings: remember the token for this browser tab only ---------- */
  var token = $("#f-token");
  var store = safeStorage("sessionStorage");
  if (token && store) {
    try { token.value = store.getItem("octapihub.token") || ""; } catch (e) { /* ignore */ }
    token.addEventListener("input", function () {
      try { store.setItem("octapihub.token", token.value); } catch (e) { /* ignore */ }
    });
  }
  /* ---------- import: put the chosen file's text into the text field ---------- */
  $$("input[type=file][data-fill-target]").forEach(function (input) {
    input.addEventListener("change", function () {
      var target = document.getElementById(input.getAttribute("data-fill-target"));
      var file = input.files && input.files[0];
      if (!target || !file) { return; }
      if (file.size > 524288) { target.value = ""; target.placeholder = "Die Datei ist zu groß (höchstens 512 KB)."; return; }
      var reader = new FileReader();
      reader.onload = function () { target.value = String(reader.result || ""); };
      reader.onerror = function () { target.value = ""; target.placeholder = "Die Datei konnte nicht gelesen werden."; };
      reader.readAsText(file);
    });
  });
  /* ---------- cube faces: highlight the face that is up right now ---------- */
  function markActiveFaces() {
    var active = {};
    $$("[data-cube-name]").forEach(function (card) {
      var face = card.getAttribute("data-last-face");
      if (face !== null && /^[0-9]$/.test(face)) { active[card.getAttribute("data-cube-name")] = face; }
    });
    $$(".face-tile[data-cube]").forEach(function (tile) {
      var on = active[tile.getAttribute("data-cube")] === tile.getAttribute("data-face");
      tile.classList.toggle("is-active", on);
    });
  }
  markActiveFaces();
  document.addEventListener("octapihub:live", markActiveFaces);
  /* ---------- help buttons at input fields ---------- */
  document.addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest(".info-btn") : null;
    if (!button) { return; }
    var pop = document.getElementById(button.getAttribute("data-info"));
    if (!pop) { return; }
    var open = pop.hasAttribute("hidden");
    if (open) { pop.removeAttribute("hidden"); } else { pop.setAttribute("hidden", ""); }
    button.setAttribute("aria-expanded", open ? "true" : "false");
  });
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") { return; }
    $$(".info-pop:not([hidden])").forEach(function (pop) {
      pop.setAttribute("hidden", "");
      var button = document.querySelector('[data-info="' + pop.id + '"]');
      if (button) { button.setAttribute("aria-expanded", "false"); }
    });
  });
})();
