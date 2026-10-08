// OpenAI Math manuscript index: math rendering, abstracts, and client-side filtering.
(function () {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const norm = (s) => s.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

  // ---- Math -------------------------------------------------------------
  function renderMath(root) {
    if (!window.renderMathInElement) return;
    renderMathInElement(root, {
      delimiters: [{ left: "\\(", right: "\\)", display: false }],
      throwOnError: false,
      strict: "ignore",
    });
  }

  // ---- Abstract expand / BibTeX copy -------------------------------------
  function updateMoreButtons() {
    for (const p of $$(".paper")) {
      if (p.offsetParent === null) continue; // hidden; checked again when shown
      const btn = $(".more", p);
      if (p.classList.contains("open")) continue;
      const a = $(".abstract", p);
      btn.hidden = a.scrollHeight <= a.clientHeight + 2;
    }
  }

  document.addEventListener("click", async (e) => {
    const more = e.target.closest(".more");
    if (more) {
      const p = more.closest(".paper");
      const open = p.classList.toggle("open");
      more.textContent = open ? "Show less" : "Show more";
      more.setAttribute("aria-expanded", String(open));
      return;
    }
    const bib = e.target.closest(".copy-bib");
    if (bib) {
      try {
        await navigator.clipboard.writeText(bib.dataset.bib);
        bib.textContent = "Copied ✓";
        bib.classList.add("done");
      } catch {
        bib.textContent = "Copy failed";
      }
      setTimeout(() => { bib.textContent = "BibTeX"; bib.classList.remove("done"); }, 1600);
    }
  });

  // ---- Filtering --------------------------------------------------------
  const list = $("#list");
  const input = $("#q");

  function setupFilters() {
    const papers = $$(".paper", list).map((el, i) => {
      const fam = el.closest(".family");
      const famText = fam ? $(".family-head", fam).textContent : "";
      const badge = $(".badge.fam", el);
      return {
        el, i,
        home: el.parentElement,
        date: el.dataset.date,
        pages: +el.dataset.pages || 0,
        title: norm($(".paper-title", el).textContent.trim()),
        lean: el.dataset.lean === "1",
        subject: el.closest(".subject-group")?.id || null,
        text: norm([el.textContent, famText, badge ? badge.title : ""].join(" ")),
      };
    });
    const total = papers.length;
    const status = $("#status");
    const leanBox = $("#lean-only");
    const sortSel = $("#sort");
    const empty = $(".empty");
    const chips = $$(".chip");
    const viewBtns = $$(".seg button");
    let view = "family";
    let flat = null;

    const params = new URLSearchParams(location.search);
    input.value = params.get("q") || "";
    leanBox.checked = params.get("lean") === "1";
    if (params.get("sort")) sortSel.value = params.get("sort");
    if (params.get("view") === "flat" && viewBtns.length) view = "flat";
    const picked = new Set((params.get("subject") || "").split(",").filter(Boolean));
    chips.forEach((c) => c.setAttribute("aria-pressed", String(picked.has(c.dataset.subject))));

    function terms(q) {
      const out = [];
      norm(q).replace(/"([^"]+)"|(\S+)/g, (_, a, b) => out.push(a || b));
      return out;
    }

    function compare(a, b) {
      switch (sortSel.value) {
        case "new": return b.date.localeCompare(a.date) || a.i - b.i;
        case "old": return a.date.localeCompare(b.date) || a.i - b.i;
        case "title": return a.title.localeCompare(b.title);
        case "long": return b.pages - a.pages || a.i - b.i;
        case "short": return (a.pages || Infinity) - (b.pages || Infinity) || a.i - b.i;
        default: return a.i - b.i;
      }
    }

    function apply() {
      const ts = terms(input.value);
      let shown = 0;
      for (const p of papers) {
        const ok = (!leanBox.checked || p.lean)
          && (!picked.size || picked.has(p.subject))
          && ts.every((t) => p.text.includes(t));
        p.el.hidden = !ok;
        if (ok) shown++;
      }

      // Order: within each home container (family/subject), or one flat list.
      const sorted = papers.slice().sort(compare);
      if (view === "flat") {
        if (!flat) {
          flat = document.createElement("div");
          flat.className = "papers flat";
          list.prepend(flat);
        }
        sorted.forEach((p) => flat.appendChild(p.el));
      } else {
        sorted.forEach((p) => p.home.appendChild(p.el));
        if (flat) { flat.remove(); flat = null; }
      }
      list.classList.toggle("view-flat", view === "flat");
      list.classList.toggle("view-family", view === "family" && viewBtns.length > 0);
      viewBtns.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === view)));

      for (const sec of $$(".family, .subject-group", list)) {
        sec.hidden = view === "flat" ? sec.classList.contains("family") : !$(".paper:not([hidden])", sec);
      }
      const liveFams = new Set(papers.filter((p) => !p.el.hidden).map((p) => p.el.dataset.fam));
      for (const a of $$(".toc a")) {
        a.parentElement.classList.toggle("dim", !liveFams.has(a.getAttribute("href").slice(2)));
      }

      const filtered = shown !== total;
      status.textContent = filtered ? `Showing ${shown} of ${total}` : `${total} papers`;
      empty.hidden = shown > 0;

      const qp = new URLSearchParams();
      if (input.value.trim()) qp.set("q", input.value.trim());
      if (leanBox.checked) qp.set("lean", "1");
      if (sortSel.value !== "catalog") qp.set("sort", sortSel.value);
      if (view === "flat") qp.set("view", "flat");
      if (picked.size) qp.set("subject", [...picked].join(","));
      const qs = qp.toString();
      history.replaceState(null, "", location.pathname + (qs ? "?" + qs : "") + location.hash);
      requestAnimationFrame(updateMoreButtons);
    }

    let timer;
    input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(apply, 80); });
    leanBox.addEventListener("change", apply);
    sortSel.addEventListener("change", apply);
    viewBtns.forEach((b) => b.addEventListener("click", () => { view = b.dataset.view; apply(); }));
    // Family links only make sense in the grouped view.
    $$(".toc a").forEach((a) => a.addEventListener("click", () => {
      if (view === "flat") { view = "family"; apply(); }
    }));
    chips.forEach((c) => c.addEventListener("click", () => {
      const s = c.dataset.subject;
      picked.has(s) ? picked.delete(s) : picked.add(s);
      c.setAttribute("aria-pressed", String(picked.has(s)));
      apply();
    }));
    $("#clear")?.addEventListener("click", () => {
      input.value = "";
      leanBox.checked = false;
      picked.clear();
      chips.forEach((c) => c.setAttribute("aria-pressed", "false"));
      apply();
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { input.value = ""; apply(); }
    });
    apply();
  }

  // "/" focuses search
  document.addEventListener("keydown", (e) => {
    if (e.key !== "/" || e.metaKey || e.ctrlKey) return;
    const t = e.target;
    if (t.matches && t.matches("input, textarea, select")) return;
    const box = $("#q") || $(".hero-search input");
    if (box) { e.preventDefault(); box.focus(); box.select(); }
  });

  // Highlight the current family in the sidebar table of contents.
  function setupToc() {
    const links = new Map($$(".toc a").map((a) => [a.getAttribute("href").slice(1), a]));
    if (!links.size || !("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver((entries) => {
      for (const en of entries) {
        if (!en.isIntersecting) continue;
        links.forEach((a) => a.classList.remove("active"));
        const a = links.get(en.target.id);
        if (a) {
          a.classList.add("active");
          a.scrollIntoView({ block: "nearest" });
        }
      }
    }, { rootMargin: "-25% 0px -65% 0px" });
    $$(".family").forEach((f) => io.observe(f));
  }

  function init() {
    renderMath(document.body);
    if (list && input) setupFilters();
    setupToc();
    updateMoreButtons();
    if (location.hash) $(location.hash)?.scrollIntoView();
  }

  if (document.readyState === "complete") init();
  else window.addEventListener("load", init);
  window.addEventListener("resize", () => requestAnimationFrame(updateMoreButtons));
})();
