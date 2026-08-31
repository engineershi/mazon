/* pstore table-flow: horizontal-overflow affordance for data tables.
   Detects tables whose columns overflow their container and pins a small
   direction pill in the corner — "more →" / "← more" / "↔" — so readers
   always know the table continues off-glass. No-op when nothing overflows,
   and re-checks on scroll + resize. */
(function () {
  "use strict";

  function fits(host) {
    return host.scrollWidth <= host.clientWidth + 1;
  }

  function paint(host, pill) {
    if (fits(host)) {
      pill.style.display = "none";
      return;
    }
    var atEnd = Math.abs(host.scrollWidth - host.clientWidth - host.scrollLeft) < 4;
    var text, dirn;
    if (atEnd) { text = "\u2190 more"; dirn = "left"; }
    else if (host.scrollLeft > 4) { text = "\u2194"; dirn = "both"; }
    else { text = "more \u2192"; dirn = "right"; }
    pill.style.display = "inline-flex";
    pill.textContent = text;
    pill.title = "Table continues " + (dirn === "left" ? "to the left" : "to the right");
  }

  function isScrollHost(node) {
    return node && node.classList &&
      (node.classList.contains("table-wrap") || node.classList.contains("tbl-flow"));
  }

  function setup(table) {
    // reuse an existing .table-wrap / .tbl-flow, else wrap the table
    var host = table;
    while (host && host.parentElement && !isScrollHost(host)) host = host.parentElement;
    if (host === table) {
      var own = document.createElement("div");
      own.className = "tbl-flow";
      table.parentNode.insertBefore(own, table);
      own.appendChild(table);
      host = own;
    }
    // outer positioned wrapper for the pill
    var outer = host.parentElement;
    if (!outer || !outer.classList || !outer.classList.contains("tbl-w")) {
      outer = document.createElement("div");
      outer.className = "tbl-w";
      host.parentNode.insertBefore(outer, host);
      outer.appendChild(host);
    }
    if (outer.querySelector(".tbl-pill")) return; // already instrumented
    var pill = document.createElement("span");
    pill.className = "tbl-pill";
    pill.setAttribute("aria-hidden", "true");
    outer.appendChild(pill);
    paint(host, pill);
    host.addEventListener("scroll", function () { paint(host, pill); }, { passive: true });
    window.addEventListener("resize", function () { paint(host, pill); }, { passive: true });
  }

  function scan() {
    var tables = document.querySelectorAll("table");
    for (var i = 0; i < tables.length; i++) setup(tables[i]);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", scan);
  else scan();
})();