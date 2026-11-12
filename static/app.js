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
  function closeNav() {
    body.classList.remove("nav-open");
    if (menuButton) menuButton.setAttribute("aria-expanded", "false");
    if (scrim) scrim.hidden = true;
  }
  if (menuButton) {
    menuButton.addEventListener("click", function () {
      var open = body.classList.toggle("nav-open");
      menuButton.setAttribute("aria-expanded", String(open));
      if (scrim) scrim.hidden = !open;
    });
  }
  if (scrim) scrim.addEventListener("click", closeNav);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeNav();
  });

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

  setupBookFilters();
  setupSearchPage();
})();
