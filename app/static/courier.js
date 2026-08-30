/* pstore courier: opt-in capture + affiliate-click analytics (stdlib-only sniff). */
(function () {
  "use strict";
  var main = document.querySelector("main[data-niche]");
  var slug = main ? (main.getAttribute("data-niche") || "page") : "page";

  /* ---- email opt-in: <form class="courier">, POST /subscribe, JSON ---- */
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (!form.classList || !form.classList.contains("courier")) return;
    ev.preventDefault();
    var email = form.querySelector("[name=email]");
    var keyword = form.querySelector("[name=keyword]");
    var note = form.querySelector(".courier-msg");
    var val = (email && email.value || "").trim();
    if (!val) {
      if (note) note.textContent = "Please add an email address.";
      return;
    }
    form.setAttribute("disabled", "disabled");
    fetch("/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: val,
        keyword: (keyword && keyword.value) || slug,
        source: (main && main.dataset.source) || "niche"
      })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (!note) return;
        if (d && d.ok) note.textContent = d.message || "You're in — check your inbox.";
        else note.textContent = (d && d.error) || "That didn't work — please try again.";
      })
      .catch(function () { if (note) note.textContent = "Network error — please try again."; });
  });

  /* ---- click beacon: any on-page Amazon link reports (slug, source) ---- */
  document.addEventListener("click", function (ev) {
    var el = ev.target;
    var a = el && el.closest ? el.closest("a") : null;
    if (!a) return;
    var href = a.getAttribute("href") || "";
    if (href.indexOf("amazon.") === -1) return;
    var source = a.getAttribute("data-beacon") || "page";
    var asin = a.getAttribute("data-asin") || "";
    var payload = {
      slug: slug,
      source: source,
      referrer: (document.referrer || "").slice(0, 200),
      asin: asin
    };
    if (navigator.sendBeacon) {
      navigator.sendBeacon("/api/track",
        new Blob([JSON.stringify(payload)], { type: "application/json" }));
    } else {
      var img = new Image();
      img.src = "/api/track?slug=" + encodeURIComponent(payload.slug) +
                "&source=" + encodeURIComponent(payload.source) +
                "&referrer=" + encodeURIComponent(payload.referrer) +
                "&asin=" + encodeURIComponent(payload.asin);
    }
  });
})();