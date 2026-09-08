(() => {
  "use strict";

  const CATEGORY_ORDER = [
    "chemical",
    "biological",
    "radiological_nuclear",
    "explosives",
    "autonomous_weapons",
    "directed_energy",
  ];
  const CATEGORY_FALLBACK_LABELS = {
    chemical: "Chemical",
    biological: "Biological",
    radiological_nuclear: "Radiological/Nuclear",
    explosives: "Explosives",
    autonomous_weapons: "Autonomous Weapons",
    directed_energy: "Directed Energy Weapons",
  };

  const state = {
    data: null,
    activeCategories: new Set(), // empty = all
    rangeDays: 14,
    searchTerm: "",
    chart: null,
  };

  const el = (id) => document.getElementById(id);

  function categoryColor(cat) {
    return getComputedStyle(document.documentElement).getPropertyValue(`--cat-${cat}`).trim() || "#888";
  }

  async function loadData() {
    const res = await fetch("data/latest.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`Failed to load data/latest.json: ${res.status}`);
    return res.json();
  }

  function renderHeader(data) {
    const generated = new Date(data.generated_at);
    el("last-updated").textContent = `Updated ${generated.toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    })}`;
    el("total-items").textContent = `${data.total_items} items in archive`;
    el("stat-window").textContent = `${data.retention_days}d`;
  }

  function buildCategoryChips(labels) {
    const container = el("category-chips");
    container.innerHTML = "";
    CATEGORY_ORDER.forEach((cat) => {
      if (!(cat in labels)) return;
      const btn = document.createElement("button");
      btn.className = "chip";
      btn.dataset.category = cat;
      btn.textContent = labels[cat] || CATEGORY_FALLBACK_LABELS[cat] || cat;
      btn.style.borderColor = categoryColor(cat);
      btn.addEventListener("click", () => {
        if (state.activeCategories.has(cat)) {
          state.activeCategories.delete(cat);
        } else {
          state.activeCategories.add(cat);
        }
        syncCategoryChipStyles();
        render();
      });
      container.appendChild(btn);
    });
  }

  function syncCategoryChipStyles() {
    document.querySelectorAll("#category-chips .chip").forEach((btn) => {
      const cat = btn.dataset.category;
      const active = state.activeCategories.has(cat);
      btn.classList.toggle("is-active", active);
      btn.style.background = active ? categoryColor(cat) : "";
      btn.style.color = active ? "#fff" : "";
    });
  }

  function filteredItems() {
    const now = Date.now();
    const cutoffMs = state.rangeDays === "all" ? null : now - state.rangeDays * 86400000;
    const term = state.searchTerm.trim().toLowerCase();

    return state.data.items.filter((item) => {
      if (cutoffMs !== null) {
        const t = new Date(item.published_date).getTime();
        if (!Number.isNaN(t) && t < cutoffMs) return false;
      }
      if (state.activeCategories.size > 0) {
        const cats = item.categories || [];
        if (!cats.some((c) => state.activeCategories.has(c))) return false;
      }
      if (term) {
        const haystack = `${item.title} ${item.source_domain || ""} ${item.source_country || ""}`.toLowerCase();
        if (!haystack.includes(term)) return false;
      }
      return true;
    });
  }

  function renderStats(items) {
    el("stat-total").textContent = items.length;

    const countries = new Set(items.map((i) => i.source_country).filter(Boolean));
    el("stat-countries").textContent = countries.size;

    const counts = {};
    items.forEach((i) => (i.categories || []).forEach((c) => (counts[c] = (counts[c] || 0) + 1)));
    const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
    const labels = state.data.category_labels || CATEGORY_FALLBACK_LABELS;
    el("stat-top-category").textContent = top ? labels[top[0]] || top[0] : "—";
  }

  function renderChart(items) {
    const labels = state.data.category_labels || CATEGORY_FALLBACK_LABELS;
    const counts = CATEGORY_ORDER.map(
      (cat) => items.filter((i) => (i.categories || []).includes(cat)).length
    );
    const colors = CATEGORY_ORDER.map(categoryColor);
    const displayLabels = CATEGORY_ORDER.map((c) => labels[c] || CATEGORY_FALLBACK_LABELS[c]);

    const ctx = el("category-chart").getContext("2d");
    if (state.chart) state.chart.destroy();
    state.chart = new Chart(ctx, {
      type: "bar",
      data: {
        labels: displayLabels,
        datasets: [
          {
            data: counts,
            backgroundColor: colors,
            borderRadius: 4,
            maxBarThickness: 46,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: {
            grid: { display: false },
            ticks: { color: getComputedStyle(document.documentElement).getPropertyValue("--text-secondary") },
          },
          y: {
            beginAtZero: true,
            ticks: {
              precision: 0,
              color: getComputedStyle(document.documentElement).getPropertyValue("--text-muted"),
            },
            grid: { color: getComputedStyle(document.documentElement).getPropertyValue("--gridline") },
          },
        },
      },
    });
  }

  function renderCountries(items) {
    const counts = {};
    items.forEach((i) => {
      const c = i.source_country || "Unknown";
      counts[c] = (counts[c] || 0) + 1;
    });
    const sorted = Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8);
    const max = sorted.length ? sorted[0][1] : 1;

    const container = el("country-list");
    container.innerHTML = "";
    if (sorted.length === 0) {
      container.innerHTML = '<p class="feed-empty">No data.</p>';
      return;
    }
    sorted.forEach(([country, count]) => {
      const row = document.createElement("div");
      row.className = "country-row";
      row.innerHTML = `
        <span class="country-name">${escapeHtml(country)}</span>
        <span class="country-bar-track"><span class="country-bar-fill" style="width:${(count / max) * 100}%"></span></span>
        <span class="country-count">${count}</span>
      `;
      container.appendChild(row);
    });
  }

  function renderFeed(items) {
    const labels = state.data.category_labels || CATEGORY_FALLBACK_LABELS;
    const container = el("feed-list");
    const empty = el("feed-empty");
    container.innerHTML = "";
    el("feed-count").textContent = `(${items.length})`;

    if (items.length === 0) {
      empty.hidden = false;
      return;
    }
    empty.hidden = true;

    const sorted = [...items].sort(
      (a, b) => new Date(b.published_date).getTime() - new Date(a.published_date).getTime()
    );

    const frag = document.createDocumentFragment();
    sorted.slice(0, 300).forEach((item) => {
      const row = document.createElement("div");
      row.className = "feed-item";

      const date = new Date(item.published_date);
      const dateStr = Number.isNaN(date.getTime())
        ? ""
        : date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });

      const badges = (item.categories || [])
        .map(
          (c) =>
            `<span class="badge" style="background:${categoryColor(c)}">${escapeHtml(
              labels[c] || CATEGORY_FALLBACK_LABELS[c] || c
            )}</span>`
        )
        .join("");

      const sourceLabel = item.source === "rss" ? item.rss_feed_name : item.source_domain;

      row.innerHTML = `
        <div class="feed-main">
          <p class="feed-title"><a href="${escapeAttr(item.url)}" target="_blank" rel="noopener">${escapeHtml(item.title)}</a></p>
          <div class="feed-sub">
            <span>${escapeHtml(sourceLabel || "Unknown source")}</span>
            ${item.source_country ? `<span>· ${escapeHtml(item.source_country)}</span>` : ""}
            ${dateStr ? `<span>· ${dateStr}</span>` : ""}
          </div>
        </div>
        <div class="feed-badges">${badges}</div>
      `;
      frag.appendChild(row);
    });
    container.appendChild(frag);
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str ?? "";
    return div.innerHTML;
  }
  function escapeAttr(str) {
    return (str ?? "").replace(/"/g, "&quot;");
  }

  function render() {
    const items = filteredItems();
    renderStats(items);
    renderChart(items);
    renderCountries(items);
    renderFeed(items);
  }

  function wireStaticControls() {
    document.querySelectorAll("#range-chips .chip").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("#range-chips .chip").forEach((b) => b.classList.remove("is-active"));
        btn.classList.add("is-active");
        const r = btn.dataset.range;
        state.rangeDays = r === "all" ? "all" : Number(r);
        render();
      });
    });

    let searchTimer = null;
    el("search-input").addEventListener("input", (e) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        state.searchTerm = e.target.value;
        render();
      }, 150);
    });
  }

  async function init() {
    wireStaticControls();
    try {
      const data = await loadData();
      state.data = data;
      renderHeader(data);
      buildCategoryChips(data.category_labels || CATEGORY_FALLBACK_LABELS);
      render();
    } catch (err) {
      el("last-updated").textContent = "Failed to load data";
      el("feed-empty").hidden = false;
      el("feed-empty").textContent = `Could not load data/latest.json: ${err.message}`;
      console.error(err);
    }
  }

  init();
})();
