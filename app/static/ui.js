/* pstore ui: shared page-level behaviours — show the back-to-top pill only
   once the reader has scrolled, and keep it hidden otherwise. Tiny, dependency-
   free, and no-op when the pill element isn't present. */
(function () {
  "use strict";
  var pill = document.querySelector(".totop");
  if (!pill) return;
  function paint() {
    if (window.scrollY > 360) pill.classList.add("show");
    else pill.classList.remove("show");
  }
  window.addEventListener("scroll", paint, { passive: true });
  window.addEventListener("resize", paint, { passive: true });
  paint();
})();
