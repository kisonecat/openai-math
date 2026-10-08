"""Full-text word index over the .tex and .bib sources, for client-side search.

The index maps each word to the files containing it, with counts. It is
sharded by the word's first two letters, and a two-letter prefix whose shard
would be large is split by three letters (listed in meta.json "split"), so a
query fetches only a few small files and words of 3+ letters can be
prefix-matched within one shard.

Snippets are not stored: the browser fetches matching source files from
raw.githubusercontent.com at the pinned commit and finds lines itself.

Tokenization here must match tokenize() in site/search.js.
"""

import collections
import json
import re
import subprocess
import unicodedata

ACCENT = re.compile(r"\\[\"'`^~=.]\s*\{?([A-Za-z])\}?")  # K\"{a}hler -> Kahler
WORD = re.compile(r"[a-z0-9]+")
SHARD_LIMIT = 48_000  # bytes of JSON before a two-letter shard is split


def tokenize(text):
    text = ACCENT.sub(r"\1", text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return [w for w in WORD.findall(text)
            if 2 <= len(w) <= 32 and not (w.isdigit() and len(w) > 6)]


def build_index(src, out, papers):
    """Write out/meta.json and out/s/<prefix>.json; return (n_tex, n_bib)."""
    order = {p["dir"]: i for i, p in enumerate(papers)}
    files = sorted(
        (order[f.relative_to(src).parts[1]], f)
        for ext in ("tex", "bib")
        for f in src.glob(f"preprints/*/**/*.{ext}")
        if f.relative_to(src).parts[1] in order
    )
    if not files:
        print("warning: no .tex/.bib sources found; skipping full-text index")
        return None

    postings = collections.defaultdict(list)
    meta_files = []
    for fid, (pi, f) in enumerate(files):
        rel = f.relative_to(src / "preprints" / papers[pi]["dir"]).as_posix()
        meta_files.append([pi, rel])
        counts = collections.Counter(tokenize(f.read_text(errors="replace")))
        for w, n in counts.items():
            postings[w].append((fid, n))

    def encode(ps):  # [gap, count, gap, count, ...]
        flat, prev = [], 0
        for fid, n in ps:
            flat += [fid - prev, n]
            prev = fid
        return flat

    by2 = collections.defaultdict(dict)
    for w in sorted(postings):
        by2[w[:2]][w] = encode(postings[w])

    shard_dir = out / "s"
    shard_dir.mkdir(parents=True)
    split, n_shards = [], 0
    dump = lambda d: json.dumps(d, separators=(",", ":"))
    for p2, terms in by2.items():
        blob = dump(terms)
        if len(blob) <= SHARD_LIMIT:
            (shard_dir / f"{p2}.json").write_text(blob)
            n_shards += 1
            continue
        split.append(p2)
        by3 = collections.defaultdict(dict)
        for w, flat in terms.items():
            by3[w[:3]][w] = flat
        for p3, sub in by3.items():
            (shard_dir / f"{p3}.json").write_text(dump(sub))
            n_shards += 1

    try:
        sha = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        sha = "main"
    meta = {
        "sha": sha,
        "split": sorted(split),
        "papers": [[p["dir"], p["title_html"], p["slug"], p["hue"], p["pdf"]] for p in papers],
        "files": meta_files,
    }
    (out / "meta.json").write_text(dump(meta))
    n_bib = sum(rel.endswith(".bib") for _, rel in meta_files)
    print(f"indexed {len(files)} source files, {len(postings)} words, {n_shards} shards")
    return len(files) - n_bib, n_bib
