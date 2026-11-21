/* Small vanilla-JS helpers. No dependencies. */
(function () {
  "use strict";

  // --- Countdown timers: <span data-countdown="2026-12-05T23:59:00+05:30"> -------------------
  function fmtLeft(ms) {
    if (ms <= 0) return "deadline passed";
    var s = Math.floor(ms / 1000), d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600),
        m = Math.floor((s % 3600) / 60);
    if (d > 0) return d + "d " + h + "h left";
    if (h > 0) return h + "h " + m + "m left";
    return m + "m " + (s % 60) + "s left";
  }
  var timers = document.querySelectorAll("[data-countdown]");
  function tick() {
    timers.forEach(function (el) {
      el.textContent = fmtLeft(new Date(el.dataset.countdown).getTime() - Date.now());
    });
  }
  if (timers.length) { tick(); setInterval(tick, 1000); }

  // --- Copy buttons -------------------------------------------------------------------------
  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var text = btn.dataset.copy, label = btn.textContent;
      var done = function () { btn.textContent = "Copied ✓"; setTimeout(function () { btn.textContent = label; }, 1600); };
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done);
      else {
        var ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta);
        ta.select(); document.execCommand("copy"); ta.remove(); done();
      }
    });
  });

  // --- Prevent double submits ---------------------------------------------------------------
  // Runs after other submit handlers so a validation handler can still cancel the submit.
  document.querySelectorAll("form[data-once]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      var btn = form.querySelector("button[type=submit]");
      if (!btn) return;
      setTimeout(function () {
        if (e.defaultPrevented || form.dataset.busy) return;
        form.dataset.busy = "1";
        btn.dataset.label = btn.textContent;
        if (btn.dataset.busyText) btn.textContent = btn.dataset.busyText;
        btn.disabled = true;
      }, 0);
    });
  });
  // Coming back with the browser's Back button must not leave the form stuck in "busy".
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    document.querySelectorAll("form[data-once]").forEach(function (form) {
      delete form.dataset.busy;
      var btn = form.querySelector("button[type=submit]");
      if (btn) { btn.disabled = false; if (btn.dataset.label) btn.textContent = btn.dataset.label; }
    });
  });

  // --- File drop zone -----------------------------------------------------------------------
  document.querySelectorAll(".dropzone").forEach(function (zone) {
    var input = zone.querySelector("input[type=file]"), label = zone.querySelector(".fname"),
        maxMb = parseFloat(zone.dataset.maxMb || "10"), note = zone.parentElement.querySelector(".client-err");
    ["dragenter", "dragover"].forEach(function (e) { zone.addEventListener(e, function () { zone.classList.add("drag"); }); });
    ["dragleave", "drop", "change"].forEach(function (e) { zone.addEventListener(e, function () { zone.classList.remove("drag"); }); });
    input.addEventListener("change", function () {
      var f = input.files[0], msg = "";
      if (!f) { label.textContent = ""; return; }
      if (!/\.(pdf|docx?)$/i.test(f.name)) msg = "Only PDF or Word files (.pdf, .doc, .docx) are accepted.";
      else if (f.size > maxMb * 1024 * 1024) msg = "That file is larger than " + maxMb + " MB.";
      label.textContent = f.name + " · " + (f.size / 1048576).toFixed(2) + " MB";
      if (note) note.textContent = msg;
      if (msg) input.value = "";
    });
  });

  // --- Team form: build member rows from the chosen team size -------------------------------
  var box = document.getElementById("members");
  if (!box) return;
  var tpl = document.getElementById("row-template"),
      total = document.getElementById("id_members-TOTAL_FORMS"),
      empty = document.getElementById("members-empty"),
      limits = JSON.parse(document.getElementById("batch-limits").textContent),
      warn = document.getElementById("size-warning"),
      sizes = document.querySelectorAll("input[name=size]");

  function rows() { return box.querySelectorAll(".member"); }

  function renumber() {
    rows().forEach(function (row, i) {
      row.querySelector(".num").textContent = i + 1;
      row.querySelector(".title").textContent = "Member " + (i + 1);
    });
    var any = rows().length > 0;
    empty.hidden = any;
    total.value = rows().length;
  }

  function checkLimits() {
    var min = Infinity;
    box.querySelectorAll("select[name$=-batch]").forEach(function (sel) {
      if (sel.value && limits[sel.value]) min = Math.min(min, limits[sel.value]);
    });
    var n = rows().length;
    if (warn) {
      warn.hidden = !(isFinite(min) && n > min);
      warn.textContent = "Students from the selected batches can form teams of at most " + min + ".";
    }
  }

  function addRow() {
    var i = rows().length;
    var html = tpl.innerHTML.replace(/__prefix__/g, i);
    var holder = document.createElement("div");
    holder.innerHTML = html.trim();
    box.appendChild(holder.firstElementChild);
  }

  function setSize(n) {
    while (rows().length < n) addRow();
    while (rows().length > n) box.removeChild(box.lastElementChild);
    var checked = box.querySelector("input[name=leader]:checked");
    if (!checked && n === 1) { var r = box.querySelector("input[name=leader]"); if (r) r.checked = true; }
    renumber(); checkLimits();
  }

  sizes.forEach(function (r) { r.addEventListener("change", function () { setSize(parseInt(r.value, 10)); }); });
  box.addEventListener("change", function (e) { if (e.target.matches("select[name$=-batch]")) checkLimits(); });
  box.addEventListener("input", function (e) {
    if (e.target.classList.contains("upper")) {
      var s = e.target.selectionStart; e.target.value = e.target.value.toUpperCase().replace(/\s+/g, "");
      try { e.target.setSelectionRange(s, s); } catch (_) {}
    }
  });

  // Initial state: rows may already exist (validation errors, resume, edit).
  var existing = rows().length;
  if (existing) {
    sizes.forEach(function (r) { r.checked = parseInt(r.value, 10) === existing; });
  }
  renumber(); checkLimits();

  // The team form shouldn't submit without a leader chosen: nudge instead of a server round-trip.
  box.closest("form").addEventListener("submit", function (e) {
    if (rows().length && !box.querySelector("input[name=leader]:checked")) {
      e.preventDefault();
      var el = document.getElementById("leader-error"); if (el) { el.hidden = false; el.scrollIntoView({ block: "center", behavior: "smooth" }); }
    }
  });
  box.addEventListener("change", function (e) {
    if (e.target.name === "leader") { var el = document.getElementById("leader-error"); if (el) el.hidden = true; }
  });
})();

// Uppercase Team ID / enrolment inputs everywhere else on the site.
document.addEventListener("input", function (e) {
  var t = e.target;
  if (t.matches && t.matches("input.upper") && !t.closest("#members")) t.value = t.value.toUpperCase();
});
