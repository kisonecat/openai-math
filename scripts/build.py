#!/usr/bin/env python3
"""Build the static index site for the openai/math manuscript collection.

Everything is parsed from files the upstream repository already ships:

  overview.tex             discipline of each result family (\\cataloguesection)
  CONTENTS.md              family summaries, paper titles, PDF paths, abstracts
  lean/formalization.yaml  which papers have a formalized main result
  README.md                which families have released reasoning summaries
  preprints/*/README.md    BibTeX for each paper

Usage: python3 scripts/build.py [--src math] [--out _site]
"""

import argparse
import datetime
import html
import json
import re
import shutil
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_URL = "https://github.com/openai/math"
BLOB = REPO_URL + "/blob/main/"
TREE = REPO_URL + "/tree/main/"

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}

# Short blurbs for the landing page; keyed by the overview.tex section name.
BLURBS = {
    "Number theory": "L-functions, elliptic curves, Diophantine problems, Galois theory",
    "Algebraic and complex geometry": "Birational geometry, Hodge theory, moduli, enumerative invariants",
    "Real and complex analysis": "Harmonic analysis, approximation, special functions, several complex variables",
    "Convex and metric geometry": "Convex bodies, isoperimetry, packings, metric embeddings",
    "Theoretical computer science": "Complexity, algorithms, hardness of approximation, information theory",
    "Dynamical systems and ergodic theory": "Ergodic theory, smooth dynamics, entropy, rigidity",
    "Combinatorics": "Extremal and additive combinatorics, Ramsey theory, graphs, matroids",
    "Algebra": "Commutative algebra, representation theory, rings and modules",
    "Probability and statistical mechanics": "Random structures, spin glasses, percolation, random matrices",
    "Mathematical logic": "Set theory, model theory, computability",
    "Group theory": "Geometric group theory, Artin groups, finite and profinite groups",
    "Mathematical physics": "Quantum spin systems, kinetic theory, integrable systems",
    "Operator algebras": "von Neumann algebras, C*-algebras, free probability",
    "Topology": "Low-dimensional topology, manifolds, homotopy theory",
    "Functional analysis": "Banach spaces, operator theory, spectral theory",
    "Differential geometry": "Curvature, Kähler geometry, minimal surfaces, symplectic geometry",
    "Partial differential equations": "Fluids, elliptic and dispersive equations, regularity",
}


# --------------------------------------------------------------------------
# Parsing


def slugify(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def md_inline(text):
    """Convert the small Markdown subset used in CONTENTS.md to HTML.

    Math $`...`$ becomes \\(...\\) for KaTeX; inline HTML (<i>, <sub>, ...)
    is passed through; relative links are resolved against the upstream repo.
    """
    maths = []

    def stash(m):
        maths.append(r"\(" + html.escape(m.group(1), quote=False) + r"\)")
        return f"\x00{len(maths) - 1}\x00"

    text = re.sub(r"\$`(.*?)`\$", stash, text, flags=re.S)
    text = re.sub(r"(?<=\s)<(?=[\s=])", "&lt;", text)  # bare "1 <= q"

    def link(m):
        label, url = m.group(1), m.group(2)
        if not re.match(r"https?://", url):
            url = BLOB + url
        return f'<a href="{html.escape(url)}">{label}</a>'

    text = re.sub(r"\[((?:\[[^\]]*\]|[^\]])*)\]\(([^)\s]+)\)", link, text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: maths[int(m.group(1))], text.strip())


def plain(text):
    """Plain-text version (for search indexing and <title>)."""
    text = re.sub(r"\$`(.*?)`\$", r"\1", text, flags=re.S)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(re.sub(r"\s+", " ", text.replace("**", ""))).strip()


def date_from_dir(d):
    m = re.search(r"-(\d{4})-(\d{2})-(\d{2})$", d)
    if m:
        return datetime.date(*map(int, m.groups()))
    m = re.search(r"-([A-Z][a-z]+)-(\d{1,2})-(\d{4})$", d)
    if not m or m.group(1) not in MONTHS:
        return None
    return datetime.date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))


def parse_overview(src):
    """Map family number -> discipline name, preserving section order."""
    tex = (src / "overview.tex").read_text()
    secs = list(re.finditer(r"\\cataloguesection\{(.+?)\}\{(\d+)\}", tex))
    subjects, fam_subject = [], {}
    for i, s in enumerate(secs):
        end = secs[i + 1].start() if i + 1 < len(secs) else len(tex)
        subjects.append(s.group(1))
        for fam in re.findall(r"\\resultentry\{(\d+)\}", tex[s.end():end]):
            fam_subject[fam] = s.group(1)
    return subjects, fam_subject


def parse_contents(src):
    """Return (families, papers) parsed from the CONTENTS.md table cells."""
    md = (src / "CONTENTS.md").read_text()
    families, papers, fam = {}, [], None
    for cell in re.findall(r"<td>\s*(.*?)\s*</td>", md, flags=re.S):
        m = re.match(r"\*\*(\d{3})\. (.+?)\.\*\*\s*(.*)", cell, flags=re.S)
        if m:
            num, title, desc = m.groups()
            lean = re.search(r"\s*\(\[Lean\]\(([^)]+)\)\)\s*$", desc)
            if lean:
                desc = desc[:lean.start()]
            fam = num
            families[num] = {
                "num": num,
                "title": title,
                "desc": desc,
                "lean": BLOB + lean.group(1) if lean else None,
            }
            continue
        m = re.match(r"&emsp;\[((?:\[[^\]]*\]|[^\]])*)\]\((preprints/([^/]+)/[^)]+)\)\s*(.*)",
                     cell, flags=re.S)
        if m:
            title, path, d, abstract = m.groups()
            papers.append({"dir": d, "path": path, "title": title.strip(),
                           "abstract": abstract, "family": fam})
    return families, papers


def parse_lean(src):
    y = (src / "lean" / "formalization.yaml").read_text()
    return set(re.findall(r"id: \.\./preprints/([^/]+)/", y))


def parse_traces(src):
    rd = (src / "README.md").read_text()
    return {f: BLOB + p for f, p in
            re.findall(r"^\| (\d{3}) \| \[[^\]]*\]\((reasoning_traces/[^)]+)\)", rd, flags=re.M)}


def parse_bibtex(src, d):
    readme = src / "preprints" / d / "README.md"
    if not readme.exists():
        return None
    m = re.search(r"```bibtex\n(.*?)```", readme.read_text(), flags=re.S)
    return m.group(1).strip() if m else None


def load(src):
    subjects, fam_subject = parse_overview(src)
    families, papers = parse_contents(src)
    lean, traces = parse_lean(src), parse_traces(src)
    on_disk = {p.name for p in (src / "preprints").iterdir() if p.is_dir()}

    seen = {p["dir"] for p in papers}
    missing = on_disk - seen
    if missing:
        print(f"warning: {len(missing)} preprint dirs not in CONTENTS.md: {sorted(missing)[:3]}")
    unclassified = [f for f in families if f not in fam_subject]
    if unclassified:
        raise SystemExit(f"families missing from overview.tex: {unclassified}")

    for f in families.values():
        f["subject"] = fam_subject[f["num"]]
        f["trace"] = traces.get(f["num"])
        f["papers"] = []
    for p in papers:
        dt = date_from_dir(p["dir"])
        p.update({
            "date": dt.isoformat() if dt else "",
            "date_h": f"{dt:%b} {dt.day}, {dt.year}" if dt else "",
            "pdf": BLOB + p["path"],
            "source": TREE + "preprints/" + p["dir"],
            "lean": p["dir"] in lean,
            "bibtex": parse_bibtex(src, p["dir"]),
            "subject": fam_subject[p["family"]],
        })
        families[p["family"]]["papers"].append(p)
    return subjects, families, papers


# --------------------------------------------------------------------------
# Rendering

E = html.escape


def page(title, body, *, desc="", depth="", script=True):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{E(title)}</title>
<meta name="description" content="{E(desc)}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400&display=swap">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<link rel="stylesheet" href="{depth}style.css">
<link rel="icon" href="{depth}favicon.svg" type="image/svg+xml">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"></script>
{f'<script defer src="{depth}app.js"></script>' if script else ''}
</head>
<body>
<header class="topbar">
  <a class="brand" href="{depth}index.html"><span class="brand-mark">∮</span> OpenAI Math <span class="brand-sub">manuscript index</span></a>
  <nav class="topnav">
    <a href="{depth}all.html">All papers</a>
    <a href="{REPO_URL}">GitHub</a>
  </nav>
</header>
{body}
<footer class="foot">
  <p>An unofficial index of the <a href="{REPO_URL}">openai/math</a> manuscript collection.
  Subjects and family groupings follow the upstream <a href="{BLOB}overview.pdf">overview</a>;
  abstracts come from <a href="{BLOB}CONTENTS.md">CONTENTS.md</a>.
  Built {datetime.date.today():%B %-d, %Y}.</p>
</footer>
</body>
</html>
"""


def paper_card(p, families, *, show_subject=False, show_family=True):
    fam = families[p["family"]]
    badges = []
    if show_subject:
        badges.append(f'<a class="badge subj" style="--hue:{p["hue"]}" '
                      f'href="{p["slug"]}.html">{E(p["subject"])}</a>')
    if show_family:
        badges.append(f'<a class="badge fam" href="{p["slug"]}.html#f{fam["num"]}" '
                      f'title="{E(plain(fam["title"]))}">No. {fam["num"]}</a>')
    if p["lean"]:
        badges.append('<span class="badge lean" title="Main result formalized in Lean">Lean ✓</span>')
    bib = (f'<button class="act copy-bib" type="button" data-bib="{E(p["bibtex"])}">BibTeX</button>'
           if p["bibtex"] else "")
    return f"""<article class="paper" data-date="{p['date']}" data-lean="{int(p['lean'])}" data-fam="{fam['num']}">
  <h3 class="paper-title"><a href="{E(p['pdf'])}">{md_inline(p['title'])}</a></h3>
  <div class="meta"><time datetime="{p['date']}">{p['date_h']}</time>{''.join(badges)}</div>
  <div class="abstract">{md_inline(p['abstract'])}</div>
  <div class="actions">
    <a class="act primary" href="{E(p['pdf'])}">PDF</a>
    <a class="act" href="{E(p['source'])}">Source</a>
    {bib}
    <button class="act more" type="button" aria-expanded="false">Show more</button>
  </div>
</article>"""


def family_block(f, families):
    links = []
    if f["lean"]:
        links.append(f'<a class="badge lean" href="{E(f["lean"])}">Lean notes</a>')
    if f["trace"]:
        links.append(f'<a class="badge trace" href="{E(f["trace"])}">Reasoning summary</a>')
    n = len(f["papers"])
    cards = "\n".join(paper_card(p, families, show_family=False)
                      for p in sorted(f["papers"], key=lambda p: (p["date"], p["title"]), reverse=True))
    return f"""<section class="family" id="f{f['num']}" data-fam="{f['num']}">
  <header class="family-head">
    <div class="family-num">No. {f['num']}</div>
    <h2 class="family-title">{md_inline(f['title'])}</h2>
    <p class="family-desc">{md_inline(f['desc'])}</p>
    <div class="family-links"><span class="count">{n} paper{'s' if n != 1 else ''}</span>{''.join(links)}</div>
  </header>
  <div class="papers">
{cards}
  </div>
</section>"""


def controls(placeholder, *, grouping=True):
    group = ("""<div class="seg" role="group" aria-label="Layout">
      <button type="button" data-view="family" aria-pressed="true">By family</button>
      <button type="button" data-view="flat" aria-pressed="false">Flat list</button>
    </div>""" if grouping else "")
    return f"""<div class="controls" role="search">
  <div class="search">
    <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
    <input id="q" type="search" placeholder="{E(placeholder)}" autocomplete="off" spellcheck="false">
    <kbd>/</kbd>
  </div>
  <div class="control-row">
    {group}
    <label class="chk"><input type="checkbox" id="lean-only"> Lean-formalized only</label>
    <label class="sel">Sort
      <select id="sort">
        <option value="catalog">Catalog order</option>
        <option value="new">Newest first</option>
        <option value="old">Oldest first</option>
        <option value="title">Title A–Z</option>
      </select>
    </label>
    <span class="status" id="status" aria-live="polite"></span>
  </div>
</div>"""


def subject_nav(subjects, current, info):
    items = []
    for s in subjects:
        cur = ' aria-current="page"' if s == current else ""
        items.append(f'<li><a href="{info[s]["slug"]}.html" style="--hue:{info[s]["hue"]}"{cur}>'
                     f'<span class="dot"></span>{E(s)}<span class="n">{info[s]["papers"]}</span></a></li>')
    return f'<nav class="subjects-nav" aria-label="Subjects"><h2>Subjects</h2><ul>{"".join(items)}</ul></nav>'


def build(src, out):
    subjects, families, papers = load(src)

    # Spread hues around the wheel, stepping by ~7/17 of a turn so neighbours differ.
    info = {}
    for i, s in enumerate(subjects):
        fams = [f for f in families.values() if f["subject"] == s]
        info[s] = {"slug": slugify(s), "hue": round((i * 7 * 360 / len(subjects) + 160) % 360),
                   "families": fams, "papers": sum(len(f["papers"]) for f in fams),
                   "lean": sum(p["lean"] for f in fams for p in f["papers"])}
    for p in papers:
        p["slug"], p["hue"] = info[p["subject"]]["slug"], info[p["subject"]]["hue"]

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for f in (ROOT / "site").iterdir():
        shutil.copy(f, out / f.name)

    # Landing page
    recent = sorted(papers, key=lambda p: (p["date"], p["title"]), reverse=True)[:6]
    tiles = "\n".join(f"""<a class="tile" href="{info[s]['slug']}.html" style="--hue:{info[s]['hue']}">
  <h3>{E(s)}</h3>
  <p>{E(BLURBS.get(s, ''))}</p>
  <div class="tile-stats"><span><b>{info[s]['papers']}</b> papers</span><span><b>{len(info[s]['families'])}</b> families</span>{f"<span><b>{info[s]['lean']}</b> in Lean</span>" if info[s]['lean'] else ''}</div>
</a>""" for s in subjects)
    n_lean = sum(p["lean"] for p in papers)
    dates = sorted(p["date"] for p in papers if p["date"])
    body = f"""<main class="home">
<section class="hero">
  <h1>{len(papers)} research manuscripts, sorted by subject</h1>
  <p class="lede">An index of the <a href="{REPO_URL}">openai/math</a> collection:
  {len(families)} result families across {len(subjects)} areas of mathematics,
  {n_lean} with Lean-formalized main results.</p>
  <form class="hero-search" action="all.html" method="get" role="search">
    <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
    <input name="q" type="search" placeholder="Search titles and abstracts — e.g. “Riemann”, “spin glass”, “matroid”" autocomplete="off">
    <button type="submit">Search</button>
  </form>
  <dl class="stats">
    <div><dt>Manuscripts</dt><dd>{len(papers)}</dd></div>
    <div><dt>Families</dt><dd>{len(families)}</dd></div>
    <div><dt>Lean-formalized</dt><dd>{n_lean}</dd></div>
    <div><dt>Dated</dt><dd>{datetime.date.fromisoformat(dates[0]):%b %-d}–{datetime.date.fromisoformat(dates[-1]):%b %-d, %Y}</dd></div>
  </dl>
</section>
<section class="tiles-wrap">
  <h2 class="section-h">Browse by subject</h2>
  <div class="tiles">
{tiles}
  </div>
</section>
<section class="recent">
  <h2 class="section-h">Most recent</h2>
  <div class="papers">
{chr(10).join(paper_card(p, families, show_subject=True) for p in recent)}
  </div>
  <p class="see-all"><a href="all.html?sort=new">See all {len(papers)} papers →</a></p>
</section>
</main>"""
    (out / "index.html").write_text(page("OpenAI Math — manuscript index", body,
                                         desc=f"{len(papers)} manuscripts from openai/math, sorted by subject."))

    # One page per subject
    for s in subjects:
        si = info[s]
        fams = "\n".join(family_block(f, families) for f in si["families"])
        toc = "".join(f'<li><a href="#f{f["num"]}"><span class="toc-num">{f["num"]}</span>{md_inline(f["title"])}</a></li>'
                      for f in si["families"])
        body = f"""<div class="layout" style="--hue:{si['hue']}">
<aside class="sidebar">
  {subject_nav(subjects, s, info)}
  <nav class="toc" aria-label="Families"><h2>Families</h2><ol>{toc}</ol></nav>
</aside>
<main class="content">
  <header class="subject-head">
    <p class="crumbs"><a href="index.html">Subjects</a> /</p>
    <h1>{E(s)}</h1>
    <p class="lede">{si['papers']} papers in {len(si['families'])} result families{f", {si['lean']} with Lean-formalized main results" if si['lean'] else ''}.</p>
  </header>
  {controls(f"Filter {si['papers']} papers in {s.lower()}…")}
  <div id="list" class="list view-family">
{fams}
  </div>
  <p class="empty" hidden>No papers match. <button type="button" class="linkish" id="clear">Clear filters</button></p>
</main>
</div>"""
        (out / f"{si['slug']}.html").write_text(
            page(f"{s} — OpenAI Math index", body, desc=f"{si['papers']} manuscripts in {s.lower()}."))

    # All papers, grouped by subject
    groups = "\n".join(f"""<section class="subject-group" id="{info[s]['slug']}" style="--hue:{info[s]['hue']}">
  <h2 class="group-h"><a href="{info[s]['slug']}.html">{E(s)}</a> <span class="n">{info[s]['papers']}</span></h2>
  <div class="papers">
{chr(10).join(paper_card(p, families) for f in info[s]['families'] for p in sorted(f['papers'], key=lambda p: (p['date'], p['title']), reverse=True))}
  </div>
</section>""" for s in subjects)
    chips = "".join(f'<button type="button" class="chip" data-subject="{info[s]["slug"]}" '
                    f'style="--hue:{info[s]["hue"]}" aria-pressed="false">{E(s)}</button>' for s in subjects)
    body = f"""<main class="content wide">
  <header class="subject-head">
    <h1>All papers</h1>
    <p class="lede">{len(papers)} manuscripts across {len(subjects)} subjects.</p>
  </header>
  {controls("Search titles, abstracts, and family names…", grouping=False)}
  <div class="chips" role="group" aria-label="Filter by subject">{chips}</div>
  <div id="list" class="list view-subject">
{groups}
  </div>
  <p class="empty" hidden>No papers match. <button type="button" class="linkish" id="clear">Clear filters</button></p>
</main>"""
    (out / "all.html").write_text(page("All papers — OpenAI Math index", body,
                                       desc=f"Search all {len(papers)} openai/math manuscripts."))

    # Machine-readable data
    data = [{k: p[k] for k in ("title", "date", "subject", "family", "pdf", "source", "lean")}
            | {"title": plain(p["title"]), "abstract": plain(p["abstract"]),
               "family_title": plain(families[p["family"]]["title"])} for p in papers]
    (out / "papers.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    (out / ".nojekyll").write_text("")
    print(f"built {len(papers)} papers, {len(families)} families, {len(subjects)} subjects -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", type=Path, default=ROOT / "math")
    ap.add_argument("--out", type=Path, default=ROOT / "_site")
    a = ap.parse_args()
    build(a.src, a.out)
