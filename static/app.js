(function () {
  "use strict";

  var root = document.documentElement;
  var body = document.body;
  var base = body.getAttribute("data-base") || "";

  function withBase(path) {
    if (!path) return base || "/";
    if (/^(https?:|#|mailto:)/.test(path)) return path;
    return base + "/" + path.replace(/^\/+/, "");
  }

  function setTheme(theme) {
    if (theme === "light" || theme === "dark") {
      root.dataset.theme = theme;
    } else {
      delete root.dataset.theme;
    }
    try { localStorage.setItem("weread.theme", theme); } catch (e) {}
  }

  var themeButton = document.getElementById("theme-button");
  if (themeButton) {
    themeButton.addEventListener("click", function () {
      var current = root.dataset.theme;
      if (!current) {
        current = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
      }
      setTheme(current === "dark" ? "light" : "dark");
    });
  }

  var menuButton = document.getElementById("menu-button");
  var scrim = document.getElementById("nav-scrim");
  var desktopQuery = window.matchMedia("(min-width: 901px)");

  function isDesktop() {
    return desktopQuery.matches;
  }

  function updateMenuButton() {
    if (!menuButton) return;
    var expanded;
    var label;
    if (isDesktop()) {
      expanded = !root.classList.contains("sidebar-collapsed");
      label = expanded ? "收起侧边栏" : "展开侧边栏";
    } else {
      expanded = body.classList.contains("nav-open");
      label = expanded ? "关闭导航" : "打开导航";
    }
    menuButton.setAttribute("aria-expanded", String(expanded));
    menuButton.setAttribute("aria-label", label);
    menuButton.setAttribute("title", label);
  }

  function setSidebarCollapsed(collapsed, persist) {
    root.classList.toggle("sidebar-collapsed", collapsed);
    if (persist) {
      try {
        localStorage.setItem("weread.sidebar", collapsed ? "collapsed" : "expanded");
      } catch (e) {}
    }
    updateMenuButton();
  }

  function closeNav() {
    body.classList.remove("nav-open");
    if (scrim) scrim.hidden = true;
    updateMenuButton();
  }
  if (menuButton) {
    menuButton.addEventListener("click", function () {
      if (isDesktop()) {
        setSidebarCollapsed(!root.classList.contains("sidebar-collapsed"), true);
      } else {
        var open = body.classList.toggle("nav-open");
        if (scrim) scrim.hidden = !open;
        updateMenuButton();
      }
    });
  }
  if (scrim) scrim.addEventListener("click", closeNav);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeNav();
  });
  function syncResponsiveNav() {
    if (isDesktop()) closeNav();
    else updateMenuButton();
  }
  if (desktopQuery.addEventListener) {
    desktopQuery.addEventListener("change", syncResponsiveNav);
  } else if (desktopQuery.addListener) {
    desktopQuery.addListener(syncResponsiveNav);
  }
  updateMenuButton();

  function setupHeatmap() {
    var heatmap = document.querySelector("[data-heatmap]");
    if (!heatmap) return;
    var yearSelect = document.getElementById("heatmap-year");
    var tooltip = document.getElementById("heatmap-tooltip");
    var panels = Array.prototype.slice.call(
      heatmap.querySelectorAll("[data-heatmap-panel]")
    );
    var scroller = heatmap.querySelector(".heatmap-scroll");
    var hideTimer = null;
    var activeCell = null;
    if (!panels.length || !tooltip) return;

    function panelCells(panel) {
      return Array.prototype.slice.call(
        panel.querySelectorAll("[data-heatmap-cell]")
      );
    }

    function setRovingTabIndex(panel, target) {
      panelCells(panel).forEach(function (cell) {
        cell.tabIndex = cell === target ? 0 : -1;
      });
    }

    function firstCell(panel) {
      var cells = panelCells(panel);
      return cells.find(function (cell) {
        return !cell.classList.contains("is-future");
      }) || cells[0];
    }

    function clearTooltip() {
      if (hideTimer) {
        clearTimeout(hideTimer);
        hideTimer = null;
      }
      activeCell = null;
      tooltip.hidden = true;
      tooltip.textContent = "";
      tooltip.classList.remove("is-below");
    }

    function positionTooltip(cell) {
      if (!cell || tooltip.hidden) return;
      var anchor = cell.getBoundingClientRect();
      var box = tooltip.getBoundingClientRect();
      var gap = 10;
      var spaceAbove = anchor.top;
      var spaceBelow = window.innerHeight - anchor.bottom;
      var below = spaceAbove < box.height + gap + 8 && spaceBelow > spaceAbove;
      var top = below
        ? anchor.bottom + gap
        : anchor.top - box.height - gap;
      var left = anchor.left + anchor.width / 2 - box.width / 2;
      left = Math.max(12, Math.min(left, window.innerWidth - box.width - 12));
      top = Math.max(8, Math.min(top, window.innerHeight - box.height - 8));
      tooltip.style.left = left + "px";
      tooltip.style.top = top + "px";
      tooltip.classList.toggle("is-below", below);
      var arrowLeft = anchor.left + anchor.width / 2 - left;
      arrowLeft = Math.max(12, Math.min(arrowLeft, box.width - 12));
      tooltip.style.setProperty("--arrow-left", arrowLeft + "px");
    }

    function showTooltip(cell) {
      if (!cell) return;
      if (hideTimer) {
        clearTimeout(hideTimer);
        hideTimer = null;
      }
      activeCell = cell;
      tooltip.textContent = cell.getAttribute("aria-label") || "";
      tooltip.hidden = false;
      positionTooltip(cell);
    }

    function scheduleTooltipHide() {
      if (hideTimer) clearTimeout(hideTimer);
      hideTimer = window.setTimeout(clearTooltip, 160);
    }

    function activateYear(year, persist) {
      var panel = panels.find(function (item) {
        return item.getAttribute("data-heatmap-panel") === String(year);
      });
      if (!panel) return;
      panels.forEach(function (item) {
        item.hidden = item !== panel;
      });
      if (yearSelect) yearSelect.value = String(year);
      var target = panel.querySelector('[data-heatmap-cell][tabindex="0"]') ||
        firstCell(panel);
      if (target) setRovingTabIndex(panel, target);
      if (scroller) scroller.scrollLeft = 0;
      clearTooltip();
      if (persist) {
        try {
          localStorage.setItem("weread.heatmap-year", String(year));
        } catch (e) {}
      }
    }

    if (yearSelect) {
      yearSelect.addEventListener("change", function () {
        activateYear(yearSelect.value, true);
      });
      try {
        var savedYear = localStorage.getItem("weread.heatmap-year");
        if (
          savedYear &&
          yearSelect.querySelector('option[value="' + savedYear + '"]')
        ) {
          activateYear(savedYear, false);
        }
      } catch (e) {}
    }

    heatmap.addEventListener("pointerover", function (event) {
      if (event.pointerType === "touch") return;
      var cell = event.target.closest("[data-heatmap-cell]");
      if (cell) showTooltip(cell);
    });
    heatmap.addEventListener("pointerout", function (event) {
      var cell = event.target.closest("[data-heatmap-cell]");
      if (!cell) return;
      var next = event.relatedTarget;
      if (next && (cell.contains(next) || tooltip.contains(next))) return;
      scheduleTooltipHide();
    });
    tooltip.addEventListener("pointerenter", function () {
      if (hideTimer) {
        clearTimeout(hideTimer);
        hideTimer = null;
      }
    });
    tooltip.addEventListener("pointerleave", scheduleTooltipHide);

    heatmap.addEventListener("focusin", function (event) {
      var cell = event.target.closest("[data-heatmap-cell]");
      if (!cell) return;
      var panel = cell.closest("[data-heatmap-panel]");
      if (panel) setRovingTabIndex(panel, cell);
      showTooltip(cell);
    });
    heatmap.addEventListener("focusout", function (event) {
      if (event.relatedTarget && tooltip.contains(event.relatedTarget)) return;
      scheduleTooltipHide();
    });
    heatmap.addEventListener("click", function (event) {
      var cell = event.target.closest("[data-heatmap-cell]");
      if (cell) showTooltip(cell);
    });
    document.addEventListener("pointerdown", function (event) {
      if (!heatmap.contains(event.target)) clearTooltip();
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !tooltip.hidden) {
        clearTooltip();
      }
    });

    heatmap.addEventListener("keydown", function (event) {
      var cell = event.target.closest("[data-heatmap-cell]");
      if (!cell) return;
      var panel = cell.closest("[data-heatmap-panel]");
      var weeks = Array.prototype.slice.call(
        panel.querySelectorAll(".heatmap-week")
      );
      var weekIndex = weeks.indexOf(cell.parentElement);
      var dayIndex = Array.prototype.indexOf.call(
        cell.parentElement.children,
        cell
      );
      var nextWeek = weekIndex;
      var nextDay = dayIndex;
      if (event.key === "ArrowUp") nextDay -= 1;
      else if (event.key === "ArrowDown") nextDay += 1;
      else if (event.key === "ArrowLeft") nextWeek -= 1;
      else if (event.key === "ArrowRight") nextWeek += 1;
      else if (event.key === "Home") {
        nextWeek = 0;
        nextDay = 0;
      } else if (event.key === "End") {
        nextWeek = weeks.length - 1;
        nextDay = 6;
      } else {
        return;
      }
      event.preventDefault();
      while (
        nextWeek >= 0 && nextWeek < weeks.length &&
        nextDay >= 0 && nextDay < 7
      ) {
        var target = weeks[nextWeek].children[nextDay];
        if (target && target.matches("[data-heatmap-cell]")) {
          target.focus();
          target.scrollIntoView({ block: "nearest", inline: "nearest" });
          return;
        }
        if (event.key === "ArrowUp") nextDay -= 1;
        else if (event.key === "ArrowDown") nextDay += 1;
        else if (event.key === "ArrowLeft") nextWeek -= 1;
        else if (event.key === "ArrowRight") nextWeek += 1;
        else if (event.key === "Home") nextWeek += 1;
        else if (event.key === "End") nextWeek -= 1;
        else return;
      }
    });

    function repositionTooltip() {
      if (activeCell) positionTooltip(activeCell);
    }
    if (scroller) {
      scroller.addEventListener("scroll", repositionTooltip, { passive: true });
    }
    window.addEventListener("scroll", repositionTooltip, { passive: true });
    window.addEventListener("resize", repositionTooltip);
  }

  function normalize(value) {
    return String(value || "").toLowerCase().trim();
  }

  function setupBookFilters() {
    var grid = document.getElementById("book-grid");
    if (!grid) return;
    var cards = Array.prototype.slice.call(grid.querySelectorAll(".book-card"));
    var search = document.getElementById("book-search");
    var category = document.getElementById("category-filter");
    var result = document.getElementById("filter-result");
    var empty = document.getElementById("filter-empty");
    var chips = Array.prototype.slice.call(document.querySelectorAll(".filter-chips .chip"));
    var activeStatus = "all";
    try {
      var params = new URLSearchParams(window.location.search);
      var initialStatus = params.get("status");
      if (initialStatus && ["reading", "finished", "want"].indexOf(initialStatus) >= 0) {
        activeStatus = initialStatus;
      }
      var query = params.get("q");
      if (query && search) search.value = query;
    } catch (e) {}
    chips.forEach(function (chip) {
      chip.classList.toggle("active", chip.getAttribute("data-status") === activeStatus);
    });

    function apply() {
      var query = normalize(search ? search.value : "");
      var selectedCategory = normalize(category ? category.value : "");
      var visible = 0;
      cards.forEach(function (card) {
        var status = card.getAttribute("data-status") || "";
        var haystack = [
          card.getAttribute("data-title"),
          card.getAttribute("data-author"),
          card.getAttribute("data-category")
        ].join(" ");
        var ok = (activeStatus === "all" || status === activeStatus)
          && (!query || haystack.indexOf(query) >= 0)
          && (!selectedCategory || haystack.indexOf(selectedCategory) >= 0);
        card.hidden = !ok;
        if (ok) visible += 1;
      });
      if (result) {
        result.textContent = "显示 " + visible + " / " + cards.length + " 本";
      }
      if (empty) empty.hidden = visible !== 0;
    }

    chips.forEach(function (chip) {
      chip.addEventListener("click", function () {
        activeStatus = chip.getAttribute("data-status") || "all";
        chips.forEach(function (item) {
          item.classList.toggle("active", item === chip);
        });
        apply();
      });
    });
    if (search) search.addEventListener("input", apply);
    if (category) category.addEventListener("change", apply);
    apply();
  }

  function setupSearchPage() {
    var input = document.getElementById("search-page-input");
    var results = document.getElementById("search-results");
    if (!input || !results) return;
    var resultLabel = document.getElementById("search-result");
    var empty = document.getElementById("search-empty");
    var index = [];
    var loaded = false;
    var url = input.getAttribute("data-search-url") || withBase("/data/search.json");

    function render() {
      var query = normalize(input.value);
      results.innerHTML = "";
      if (!query) {
        if (resultLabel) resultLabel.textContent = "输入关键词开始搜索";
        if (empty) empty.hidden = true;
        return;
      }
      var matches = index.filter(function (book) {
        var text = [
          book.title,
          (book.authors || []).join(" "),
          (book.categories || []).join(" "),
          book.status_label
        ].join(" ").toLowerCase();
        return text.indexOf(query) >= 0;
      }).slice(0, 40);
      if (resultLabel) resultLabel.textContent = "找到 " + matches.length + " 本";
      if (empty) empty.hidden = matches.length !== 0;
      matches.forEach(function (book) {
        var link = document.createElement("a");
        link.className = "search-result";
        link.href = withBase("/book/" + encodeURIComponent(book.slug) + "/");
        var cover = document.createElement("span");
        cover.className = "search-result-cover";
        var img = document.createElement("img");
        img.loading = "lazy";
        img.alt = "";
        img.src = book.cover_url ? withBase(book.cover_url) : withBase("/assets/covers/" + encodeURIComponent(book.slug) + ".svg");
        cover.appendChild(img);
        var main = document.createElement("span");
        var title = document.createElement("strong");
        title.textContent = book.title;
        var meta = document.createElement("small");
        var bits = [];
        if (book.authors && book.authors.length) bits.push(book.authors.join(" / "));
        if (book.categories && book.categories.length) bits.push(book.categories.join(" · "));
        meta.textContent = bits.join(" · ");
        main.appendChild(title);
        main.appendChild(meta);
        var status = document.createElement("span");
        status.className = "status status-" + (book.status || "want");
        status.textContent = book.status_label || "";
        link.appendChild(cover);
        link.appendChild(main);
        link.appendChild(status);
        results.appendChild(link);
      });
    }

    function load() {
      if (loaded) return;
      loaded = true;
      fetch(url)
        .then(function (response) { return response.json(); })
        .then(function (data) {
          index = Array.isArray(data) ? data : [];
          render();
        })
        .catch(function () {
          if (resultLabel) resultLabel.textContent = "搜索索引加载失败，请刷新重试。";
        });
    }

    var params = new URLSearchParams(window.location.search);
    if (params.get("q")) input.value = params.get("q");
    input.addEventListener("input", function () {
      load();
      render();
    });
    load();
  }

  setupHeatmap();
  setupBookFilters();
  setupSearchPage();
})();
