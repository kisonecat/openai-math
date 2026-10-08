// Full-text search over the LaTeX sources (index built by scripts/fulltext.py).
(function () {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const E = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const BATCH = 15;          // papers rendered per "show more"
  const FILES_SHOWN = 3;     // files with snippets per paper
  const LINES_SHOWN = 4;     // snippet lines per file
  const PREFIX_MIN = 4;      // words this long also match longer words

  // ---- Tokenization: must match tokenize() in scripts/fulltext.py ---------
  const ACCENT = /\\["'`^~=.]\s*\{?([A-Za-z])\}?/g;
  const fold = (s) => s.normalize("NFKD").replace(/[^\x00-\x7f]/g, "").toLowerCase();
  const keep = (w) => w.length >= 2 && w.length <= 32 && !(/^\d+$/.test(w) && w.length > 6);
  function tokenize(text) {
    return (fold(text.replace(ACCENT, "$1")).match(/[a-z0-9]+/g) || []).filter(keep);
  }

  function parseQuery(q) {
    const words = [], phrases = [];
    q.replace(/"([^"]*)"|(\S+)/g, (_, ph, w) => {
      const t = tokenize(ph ?? w);
      if (ph !== undefined && t.length > 1) phrases.push(t);
      t.forEach((x) => words.includes(x) || words.push(x));
    });
    return { words, phrases };
  }

  // A query word matches a term exactly, or as a prefix if long enough.
  const matches = (term, w) => term === w || (w.length >= PREFIX_MIN && term.startsWith(w));

  // ---- Index access ------------------------------------------------------
  let meta, split;
  const shards = new Map();
  const srcCache = new Map();

  function shard(w) {
    const key = split.has(w.slice(0, 2)) ? w.slice(0, 3) : w.slice(0, 2);
    if (!shards.has(key)) {
      shards.set(key, fetch(`fts/s/${key}.json`).then((r) => (r.ok ? r.json() : {})).catch(() => ({})));
    }
    return shards.get(key);
  }

  // Map fileId -> count, summed over every term the word matches.
  async function postings(w) {
    const sh = await shard(w);
    const out = new Map();
    for (const term in sh) {
      if (!matches(term, w)) continue;
      const flat = sh[term];
      for (let i = 0, id = 0; i < flat.length; i += 2) {
        id += flat[i];
        out.set(id, (out.get(id) || 0) + flat[i + 1]);
      }
    }
    return out;
  }

  const rawUrl = (f) => `https://raw.githubusercontent.com/openai/math/${meta.sha}/` + path(f);
  const blobUrl = (f, line) =>
    `https://github.com/openai/math/blob/${meta.sha}/` + path(f) + (line ? `#L${line}` : "");
  function path(f) {
    const [pi, rel] = meta.files[f];
    return ["preprints", meta.papers[pi][0], ...rel.split("/")].map(encodeURIComponent).join("/");
  }

  function source(f) {
    if (!srcCache.has(f)) {
      srcCache.set(f, fetch(rawUrl(f)).then((r) => (r.ok ? r.text() : null)).catch(() => null));
    }
    return srcCache.get(f);
  }

  // ---- Matching lines ----------------------------------------------------
  function hasSeq(tokens, seq) {
    outer: for (let i = 0; i + seq.length <= tokens.length; i++) {
      for (let j = 0; j < seq.length; j++) if (!matches(tokens[i + j], seq[j])) continue outer;
      return true;
    }
    return false;
  }

  function findLines(text, q) {
    const lines = text.split(/\r?\n/);
    const hits = [];
    lines.forEach((line, i) => {
      const toks = tokenize(line);
      if (!toks.length) return;
      let score = 0;
      for (const w of q.words) if (toks.some((t) => matches(t, w))) score += 1;
      for (const ph of q.phrases) if (hasSeq(toks, ph)) score += 3;
      if (score) hits.push({ n: i + 1, line, score });
    });
    return hits;
  }

  // Highlight matched words in one line, trimmed to a window around the first hit.
  function highlight(line, q) {
    let norm = "";
    const map = [];
    for (let i = 0; i < line.length; i++) {
      const c = fold(line[i]);
      for (let k = 0; k < c.length; k++) { norm += c[k]; map.push(i); }
    }
    const marks = [];
    for (const m of norm.matchAll(/[a-z0-9]+/g)) {
      if (keep(m[0]) && q.words.some((w) => matches(m[0], w))) {
        marks.push([map[m.index], map[m.index + m[0].length - 1] + 1]);
      }
    }
    const WIN = 240;
    let a = 0, b = line.length;
    if (line.length > WIN) {
      a = Math.max(0, (marks[0] ? marks[0][0] : 0) - 70);
      b = Math.min(line.length, a + WIN);
    }
    let html = a > 0 ? "…" : "", pos = a;
    for (const [s, e] of marks) {
      if (s < pos || e > b) continue;
      html += E(line.slice(pos, s)) + "<mark>" + E(line.slice(s, e)) + "</mark>";
      pos = e;
    }
    return html + E(line.slice(pos, b)) + (b < line.length ? "…" : "");
  }

  // ---- Search ------------------------------------------------------------
  const input = $("#q"), subjectSel = $("#subject"), status = $("#status");
  const results = $("#results"), moreBtn = $("#more"), examples = $("#examples");
  let run = 0, queue = [], query = null;

  async function search() {
    const id = ++run;
    const raw = input.value.trim();
    syncUrl(raw);
    results.innerHTML = "";
    moreBtn.hidden = true;
    examples.hidden = !!raw;
    query = parseQuery(raw);
    if (!query.words.length) { status.textContent = ""; return; }
    status.textContent = "Searching…";

    const lists = await Promise.all(query.words.map((w) => postings(w)));
    if (id !== run) return;
    const N = meta.files.length;
    lists.sort((a, b) => a.size - b.size);
    const scores = new Map();
    for (const [f] of lists[0]) {
      if (!lists.every((l) => l.has(f))) continue;
      let s = 0;
      for (const l of lists) s += (1 + Math.log(l.get(f))) * Math.log(1 + N / l.size);
      scores.set(f, s);
    }

    // Group by paper; rank papers by best file plus a little for breadth.
    const subj = subjectSel.value;
    const byPaper = new Map();
    for (const [f, s] of scores) {
      const pi = meta.files[f][0];
      if (subj && meta.papers[pi][2] !== subj) continue;
      if (!byPaper.has(pi)) byPaper.set(pi, []);
      byPaper.get(pi).push({ f, s });
    }
    queue = [...byPaper].map(([pi, files]) => {
      files.sort((a, b) => b.s - a.s);
      return { pi, files, score: files[0].s + 0.1 * files.slice(1).reduce((t, x) => t + x.s, 0) };
    }).sort((a, b) => b.score - a.score);

    const nFiles = queue.reduce((t, p) => t + p.files.length, 0);
    const approx = query.phrases.length ? "up to " : "";
    status.textContent = queue.length
      ? `${approx}${nFiles.toLocaleString()} file${nFiles === 1 ? "" : "s"} in ${approx}${queue.length} paper${queue.length === 1 ? "" : "s"}`
      : "No matches";
    if (!queue.length) {
      results.innerHTML = `<p class="empty">No source files contain ${query.words.map((w) => `<b>${E(w)}</b>`).join(" and ")}.</p>`;
      return;
    }
    await renderMore(id);
  }

  // Render the next batch of papers. Phrase queries are verified against the
  // fetched source, so papers whose files lack the phrase are skipped.
  async function renderMore(id) {
    moreBtn.hidden = true;
    let shown = 0;
    while (shown < BATCH && queue.length) {
      const batch = queue.splice(0, BATCH - shown);
      const blocks = await Promise.all(batch.map((p) => paperBlock(p)));
      if (id !== run) return;
      for (const b of blocks) if (b) { results.appendChild(b); shown++; }
      if (window.renderMathInElement) {
        for (const b of blocks) if (b) renderMathInElement($(".hit-title", b), {
          delimiters: [{ left: "\\(", right: "\\)", display: false }], throwOnError: false, strict: "ignore",
        });
      }
    }
    if (!results.children.length) {
      results.innerHTML = `<p class="empty">The words appear, but never in that order.</p>`;
      status.textContent = "No phrase matches";
    }
    moreBtn.hidden = !queue.length;
  }

  async function paperBlock({ pi, files }) {
    const [dir, titleHtml, slug, hue, pdf] = meta.papers[pi];
    const q = query;
    const checkAll = q.phrases.length > 0;
    const candidates = checkAll ? files.slice(0, 12) : files.slice(0, FILES_SHOWN);
    const texts = await Promise.all(candidates.map(({ f }) => source(f)));

    let shownFiles = [];
    candidates.forEach(({ f }, i) => {
      const text = texts[i];
      if (text === null) { if (!checkAll) shownFiles.push({ f, lines: null }); return; }
      if (checkAll) {
        const toks = tokenize(text);
        if (!q.phrases.every((ph) => hasSeq(toks, ph))) return;
      }
      const hits = findLines(text, q);
      const best = hits.slice().sort((a, b) => b.score - a.score || a.n - b.n)
        .slice(0, LINES_SHOWN).sort((a, b) => a.n - b.n);
      shownFiles.push({ f, lines: best, total: hits.length });
    });
    if (!shownFiles.length) return null;
    const restCount = checkAll ? 0 : files.length - shownFiles.length;
    shownFiles = shownFiles.slice(0, FILES_SHOWN);

    const el = document.createElement("article");
    el.className = "hit";
    el.setAttribute("style", `--hue:${hue}`);
    const subjName = subjectSel.querySelector(`option[value="${slug}"]`)?.textContent || "";
    el.innerHTML = `
      <h3 class="hit-title"><a href="${E(pdf)}">${titleHtml}</a></h3>
      <div class="meta"><a class="badge subj" href="${slug}.html">${E(subjName)}</a>
        <a class="badge" href="https://github.com/openai/math/tree/main/preprints/${encodeURIComponent(dir)}">Source folder</a>
        <a class="badge" href="${E(pdf)}">PDF</a></div>
      ${shownFiles.map(({ f, lines, total }) => `
        <div class="file">
          <div class="file-head"><a href="${blobUrl(f)}">${E(meta.files[f][1])}</a>
            ${total ? `<span class="n">${total} matching line${total === 1 ? "" : "s"}</span>` : ""}</div>
          ${lines === null
            ? `<p class="file-err">Couldn’t load a preview — <a href="${blobUrl(f)}">open on GitHub</a>.</p>`
            : `<ol class="lines">${lines.map((h) => `
              <li><a class="ln" href="${blobUrl(f, h.n)}">${h.n}</a><code>${highlight(h.line, q)}</code></li>`).join("")}</ol>`}
        </div>`).join("")}
      ${restCount > 0 ? `<details class="rest"><summary>${restCount} more matching file${restCount === 1 ? "" : "s"}</summary>
        <ul>${files.slice(FILES_SHOWN).map(({ f }) => `<li><a href="${blobUrl(f)}">${E(meta.files[f][1])}</a></li>`).join("")}</ul></details>` : ""}`;
    return el;
  }

  function syncUrl(q) {
    const p = new URLSearchParams();
    if (q) p.set("q", q);
    if (subjectSel.value) p.set("subject", subjectSel.value);
    const qs = p.toString();
    history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
  }

  async function init() {
    status.textContent = "Loading index…";
    try {
      meta = await fetch("fts/meta.json").then((r) => r.json());
    } catch {
      status.textContent = "Couldn’t load the search index.";
      return;
    }
    split = new Set(meta.split);
    status.textContent = "";
    const params = new URLSearchParams(location.search);
    input.value = params.get("q") || "";
    subjectSel.value = params.get("subject") || "";

    let timer;
    input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(search, 300); });
    subjectSel.addEventListener("change", search);
    $("#fts-form").addEventListener("submit", (e) => { e.preventDefault(); clearTimeout(timer); search(); });
    moreBtn.addEventListener("click", () => renderMore(run));
    examples.addEventListener("click", (e) => {
      const b = e.target.closest("[data-q]");
      if (b) { input.value = b.dataset.q; search(); }
    });
    if (input.value) search();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
