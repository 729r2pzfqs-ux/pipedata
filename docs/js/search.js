/* PipeData client-side search over the static page index */
(function () {
  "use strict";

  var input = document.getElementById("pd-search");
  var box = document.getElementById("pd-results");
  if (!input || !box) return;

  var base = input.getAttribute("data-base") || "/";
  var pages = null;
  var loading = false;
  var active = -1;

  function load(cb) {
    if (pages) { cb(); return; }
    if (loading) return;
    loading = true;
    fetch(base + "search-index.json")
      .then(function (r) { return r.json(); })
      .then(function (d) { pages = d; loading = false; cb(); })
      .catch(function () { loading = false; });
  }

  /* Normalise so "nps 1 1/2", "1-1/2" and "1.5" all reach the same page, and
     so "sch40" matches "Schedule 40" — both are how people actually type. */
  function norm(s) {
    return s.toLowerCase()
      .replace(/[–—]/g, "-")
      .replace(/\bsch\b\.?\s*/g, "schedule ")
      .replace(/\bclass\s*/g, "class ")
      .replace(/["']/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function score(p, q) {
    var hay = norm(p.t + " " + p.m);
    var i = hay.indexOf(q);
    if (i === 0) return 0;
    if (i > 0) return 1;
    var words = q.split(/\s+/).filter(Boolean);
    for (var w = 0; w < words.length; w++) {
      if (hay.indexOf(words[w]) === -1) return -1;
    }
    return 2;
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function render(list, q) {
    active = -1;
    if (!q) { box.innerHTML = ""; return; }
    if (!list.length) {
      box.innerHTML = '<div class="sr-none">Nothing matches &ldquo;' +
        esc(q) + '&rdquo;.</div>';
      return;
    }
    box.innerHTML = list.map(function (p) {
      return '<a href="' + base + esc(p.u) + '"><strong>' + esc(p.t) +
        "</strong>" + '<span class="sr-meta">' + esc(p.m) + "</span></a>";
    }).join("");
  }

  function search() {
    var q = norm(input.value);
    if (!q) { render([], q); return; }
    load(function () {
      if (!pages) return;
      var hits = [];
      for (var i = 0; i < pages.length; i++) {
        var s = score(pages[i], q);
        if (s >= 0) hits.push([s, i, pages[i]]);
      }
      hits.sort(function (a, b) { return a[0] - b[0] || a[1] - b[1]; });
      render(hits.slice(0, 12).map(function (h) { return h[2]; }), q);
    });
  }

  function move(delta) {
    var links = box.querySelectorAll("a");
    if (!links.length) return;
    if (active >= 0) links[active].classList.remove("active");
    active = (active + delta + links.length) % links.length;
    links[active].classList.add("active");
    links[active].scrollIntoView({ block: "nearest" });
  }

  input.addEventListener("input", search);
  input.addEventListener("focus", function () { load(function () {}); });

  input.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Enter") {
      var links = box.querySelectorAll("a");
      if (active >= 0 && links[active]) {
        e.preventDefault();
        window.location.href = links[active].href;
      } else if (links.length === 1) {
        e.preventDefault();
        window.location.href = links[0].href;
      }
    } else if (e.key === "Escape") {
      input.value = "";
      render([], "");
      input.blur();
    }
  });

  document.addEventListener("click", function (e) {
    if (!box.contains(e.target) && e.target !== input) box.innerHTML = "";
  });

  /* Deep link from the WebSite SearchAction target: /?q=nps+6 */
  var q = new URLSearchParams(window.location.search).get("q");
  if (q) { input.value = q; search(); }
})();
