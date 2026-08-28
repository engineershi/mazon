// Mazon frontend — talks to the stdlib Python HTTP API.
"use strict";

let markets = null;

function $(id) { return document.getElementById(id); }

function fillMarkets(select, current) {
  if (!markets) return;
  select.innerHTML = "";
  for (const [id, m] of Object.entries(markets)) {
    const o = document.createElement("option");
    o.value = id; o.textContent = m.name;
    o.selected = (id === current);
    select.appendChild(o);
  }
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function productCard(p) {
  const price = p.price != null ? "$" + Number(p.price).toFixed(2) : "—";
  const stars = p.stars != null ? "★ " + p.stars : "";
  const revs = p.reviews != null ? " (" + p.reviews.toLocaleString() + ")" : "";
  return `<div class="product">
    <h4 title="${esc(p.title)}">${esc((p.title||"").slice(0,90))}</h4>
    <div class="price">${esc(price)}</div>
    <div class="meta">${esc(stars + revs)} ${esc(p.asin||"")}</div>
    <a href="${esc(p.url)}" target="_blank" rel="nofollow noopener">View on Amazon ↗</a>
  </div>`;
}

async function loadSettings() {
  const r = await fetch("/api/settings");
  const s = await r.json();
  markets = s.markets;
  fillMarkets($("market"), s.market);
  fillMarkets($("mine-market"), s.market);
  fillMarkets($("search-market"), s.market);
  $("tag").value = s.affiliate_tag || "";
  const box = $("scraper-keys");
  box.innerHTML = "";
  for (const [pid, p] of Object.entries(s.scraper.providers)) {
    const row = document.createElement("div");
    row.className = "prow";
    row.innerHTML = `<span class="tag">${esc(p.name)}</span>
      <input data-pid="${esc(pid)}" placeholder="${esc(p.kind)} provider key" value="">
      <button data-pid="${esc(pid)}" class="set-scraper">Save</button>
      <span class="tag">${p.has_key ? "keyed ✓" : "no key"}</span>`;
    box.appendChild(row);
  }
}

async function doMine() {
  const seed = $("mine-seed").value.trim();
  const market = $("mine-market").value;
  if (!seed) { $("mine-msg").textContent = "Enter a seed topic first."; return; }
  $("mine-msg").textContent = "Mining… (this hits live Amazon + autosuggest)";
  $("mine-results").innerHTML = "";
  try {
    const r = await fetch(`/api/mine?seed=${encodeURIComponent(seed)}&market=${market}`);
    const data = await r.json();
    $("mine-msg").textContent =
      `Found ${data.niches.filter(n=>n.products&&n.products.length).length} niche(s) with products. ` +
      `Signals: ${(data.meta.signals||[]).join(", ")}; ${data.meta.autosuggest_count} autosuggest keywords.`;
    $("mine-results").innerHTML = data.niches.map(nicheBlock).join("");
  } catch (e) {
    $("mine-msg").textContent = "Error: " + e;
  }
}

function nicheBlock(n) {
  const demand = n.score != null ? `<span class="badge demand">demand ${n.score}/10</span>` : "";
  const sat = n.saturation != null ? `<span class="badge saturation">saturation ${n.saturation}/10</span>` :
              `<span class="badge saturation">saturation —</span>`;
  const src = n.source ? `<span class="badge source">${esc(n.source)}</span>` : "";
  const products = (n.products||[]).map(productCard).join("");
  return `<div class="niche">
    <div class="kw">${esc(n.keyword)}</div>
    <div class="badges">${demand}${sat}${src}
      <button class="save-btn" data-kw="${esc(n.keyword)}" data-score="${n.score||""}"
        data-sat="${n.saturation||""}" data-products="${esc(JSON.stringify(n.products||[]))}">Save</button>
    </div>
    <div class="grid">${products}</div>
  </div>`;
}

async function doSearch() {
  const q = $("search-q").value.trim();
  const market = $("search-market").value;
  if (!q) { $("search-msg").textContent = "Enter a search term."; return; }
  $("search-msg").textContent = "Searching…";
  try {
    const r = await fetch(`/api/search?q=${encodeURIComponent(q)}&market=${market}`);
    const data = await r.json();
    $("search-msg").textContent = `${data.count} results via ${data.source||"none"}.`;
    $("search-results").innerHTML = (data.items||[]).map(productCard).join("");
  } catch (e) {
    $("search-msg").textContent = "Error: " + e;
  }
}

async function saveNiche(kw, score, sat, products) {
  await fetch("/api/niches", { method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify({keyword: kw, score: score, saturation: sat, products: products}) });
  loadSaved();
}

async function loadSaved() {
  const r = await fetch("/api/niches");
  const data = await r.json();
  $("saved-list").innerHTML = (data.niches||[]).map(s => `
    <div class="saved-niche">
      <b>${esc(s.keyword)}</b>
      <span class="badge demand">demand ${s.score!=null?s.score:"—"}</span>
      <span class="badge saturation">sat ${s.saturation!=null?s.saturation:"—"}</span>
      <span class="tag" style="color:var(--muted);font-size:12px">${esc(s.market)} · ${esc(s.created_at)}</span>
      ${(s.products||[]).map(productCard).join("")}
    </div>`).join("");
}

document.addEventListener("DOMContentLoaded", () => {
  loadSettings().then(loadSaved);
  $("mine-btn").onclick = doMine;
  $("search-btn").onclick = doSearch;
  $("save-settings").onclick = async () => {
    await fetch("/api/settings", { method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({market: $("market").value, affiliate_tag: $("tag").value}) });
    $("settings-msg").textContent = "Saved.";
    loadSettings();
  };
  $("mine-seed").addEventListener("keydown", e => { if (e.key === "Enter") doMine(); });
  $("search-q").addEventListener("keydown", e => { if (e.key === "Enter") doSearch(); });
  document.addEventListener("click", async (e) => {
    const b = e.target.closest(".save-btn");
    if (b) saveNiche(b.dataset.kw, b.dataset.score, b.dataset.sat, JSON.parse(b.dataset.products));
    const s = e.target.closest(".set-scraper");
    if (s) {
      const input = document.querySelector(`input[data-pid="${s.dataset.pid}"]`);
      await fetch("/api/settings", { method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({scraper: {[s.dataset.pid]: input.value}}) });
      loadSettings();
    }
  });
});
