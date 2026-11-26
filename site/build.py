"""Render the mirrored pik5 data in data/ as a static, bilingual site in dist/.

    python site/build.py

Output: dist/index.html (language redirect), dist/en/..., dist/ja/...
"""

from __future__ import annotations

import html
import json
import re
import shutil
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from i18n import CAT_LABELS, COUNTRIES, GAMES, HEADERS, LINKS, STR
from icons import CAMERA, CONSOLES, DIFFICULTY, EGG, SPROUT, console_icon, difficulty_icon, video_icon
import score_meta

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DIST = ROOT / "dist"
SRC = Path(__file__).resolve().parent
PIK5 = "https://pik5.net"
NOW = datetime.now(timezone.utc)
LANGS = ("en", "ja")
FLAG_CDN = "https://cdn.jsdelivr.net/npm/flag-icons@7.2.3/flags/4x3/{}.svg"
SLUG = {1: "pikmin1", 2: "pikmin2", 3: "pikmin3", 4: "pikmin4"}
TOTAL_OF = {1: 10, 2: 20, 3: 30, 4: 40}

e = html.escape
JA_RE = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")

# pik5 stage time limits that disagree with what pik5 itself displays
# (Vehemoth Phosbat's Defeat Bosses! limit is 15:00; pik5 shows times on that basis).
LIMIT_FIX = {346: 900}


def load(name, default=None):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


meta = load("meta.json", {})
raw_categories = load("categories.json", [])
stages = {int(k): v for k, v in load("stages.json", {}).items()}
# pik5's English stage names with typos, corrected to the official names (checked on Pikipedia).
STAGE_NAME_FIX = {
    223: "Hidden Garden",
    313: "Beastly Caverns", 328: "Beastly Caverns",
    362: "Jigsaw Colosseum",
    402: "Hotshock Canyon",
    406: "Hefty Haulway",
    445: "Courtyard Commands",
}
for _sid, _name in STAGE_NAME_FIX.items():
    if _sid in stages:
        stages[_sid]["name"] = _name
users = load("users.json", {})
profiles = load("players.json", {})
overrides = {k: v for k, v in json.loads((SRC / "players_overrides.json").read_text(encoding="utf-8")).items()
             if not k.startswith("_")}


@lru_cache(maxsize=None)
def board(key):
    return load(f"boards/{key}.json", {"posts": [], "lower_is_better": False})


@lru_cache(maxsize=None)
def history(key):
    return load(f"history/{key}.json", {"records": []})


@lru_cache(maxsize=None)
def archive(key):
    return load(f"archive/{key}.json", {"posts": []})


# ---------------------------------------------------------------- categories
# Display categories. Pikmin 2's egg / no-egg boards are one challenge mode on
# pik5's side split by stage, so they're merged here with an egg marker.

def build_categories():
    out = []
    by_rule = {c["rule"]: c for c in raw_categories}
    for c in raw_categories:
        r = c["rule"]
        if r == 22 and 21 in by_rule:
            continue
        if r == 21 and 22 in by_rule:
            boards = [(21, s) for s in by_rule[21]["stages"]] + [(22, s) for s in by_rule[22]["stages"]]
            boards.sort(key=lambda b: b[1])
            out.append({"id": "p2", "series": 2, "kind": "main", "rules": [21, 22],
                        "boards": boards, "total": 20, "egg_rule": 21})
            continue
        out.append({"id": str(r), "series": c["series"], "kind": c["kind"], "rules": [r],
                    "boards": [(r, s) for s in c["stages"]], "total": r})
    return [c for c in out if c["id"] not in HIDDEN]


# Categories not shown anywhere on the site (their data is still synced).
# 27 is pik5's "Real Hardware Open Class", which in practice holds TASes; it
# feeds the TAS page instead.
HIDDEN = {
    "24",  # Pikmin 2 No Spray
    "25",  # Pikmin 2 Story Caves
    "26",  # Pikmin 2 2-Player Challenge
    "27",  # Pikmin 2 Real Hardware Open Class (TASes -> TAS page)
    "28",  # Pikmin 2 "TAS" (actually seed-picked real-console runs)
    "29",  # Pikmin 2 Solo Battle
    "34",  # Pikmin 3 2-Player Missions
    "35",  # Pikmin 3 Solo Bingo
    "44",  # Pikmin 4 Ultra-Spicy Challenge
    "45",  # Pikmin 4 Ultra-Spicy Battle
    "46",  # Pikmin 4 Ultra-Spicy Sage Leaf
}

CATS = build_categories()
TAS = json.loads((SRC / "tas.json").read_text(encoding="utf-8"))
# Pikmin 2 sublevel layout seeds read from record videos (see site/seeds.json)
SEEDS = json.loads((SRC / "seeds.json").read_text(encoding="utf-8")) if (SRC / "seeds.json").exists() else {}
SEED_BY_POST = {r["post"]: r for r in SEEDS.get("pikmin2", [])}
SEED_DOC = SEEDS.get("_doc", "")
SEED_DOC_LAYOUT = SEEDS.get("_doc_layout", "")


def seed_text(f):
    """GameCube/Wii seeds in 31 bits (bit 31 never reaches rand()); Switch (Pikmin 2 HD, musl
    64-bit LCG) seeds in all 16 hex digits."""
    if f.get("rng") == "switch":
        return f"{int(f['seed'], 16) & 0xFFFFFFFFFFFFFFFF:016X}"
    return f"{int(f['seed'], 16) & 0x7FFFFFFF:08X}"


def cavegen3_url(f):
    url = f"https://jhawk4.github.io/CaveGen3/?sublevel=pikmin2:{f['sublevel']}&seed={seed_text(f)}"
    return url + "&rng=switch" if f.get("rng") == "switch" else url


def seed_links(r):
    out = []
    for f in r.get("floors", []):
        out.append(f'<a href="{e(cavegen3_url(f))}"><span class="num">{e(f["sublevel"])} '
                   f'{seed_text(f)}</span></a>')
    return " · ".join(out)


def bkey(rule, sid):
    return f"{rule}-{sid}"


# ---------------------------------------------------------------- text helpers

class T:
    """Per-language helpers."""

    def __init__(self, lang):
        self.lang = lang

    def s(self, key, **kw):
        txt = STR[key][self.lang]
        return txt.format(**kw) if kw else txt

    def game(self, series):
        return GAMES[series][self.lang]

    def cat(self, c):
        return CAT_LABELS[c["id"]][self.lang]

    def stage(self, sid):
        st = stages.get(sid) or {}
        if self.lang == "ja":
            return st.get("name_ja") or st.get("name") or f"#{sid}"
        return st.get("name") or st.get("name_ja") or f"#{sid}"

    def date(self, s):
        d = parse_date(s)
        return d.strftime("%Y-%m-%d") if d else ""


def parse_date(s):
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def days_between(a, b):
    da, db = parse_date(a), parse_date(b)
    if not da or not db:
        return ""
    return str(max(0, (db - da).days))


# ---------------------------------------------------------------- players

def _norm(s):
    return re.sub(r"[^0-9a-z぀-ヿ一-鿿]", "", (s or "").lower())


def match_src_runners():
    """Match pik5 players to speedrun.com Pikmin runners by name (login id,
    display name, English nickname, or login id minus a trailing number /
    "_pik"). Only unambiguous matches are kept."""
    src = load("src_runners.json", {})
    idx = {}
    for r in src.values():
        for n in {_norm(r["name"]), _norm(r.get("name_ja"))} - {""}:
            idx.setdefault(n, []).append(r)
    out = {}
    for uid in users:
        ks = {_norm(uid), _norm(users.get(uid)), _norm(overrides.get(uid, {}).get("en")),
              _norm(re.sub(r"([_-]?(pik|pikmin)|[_-]?\d+)$", "", uid, flags=re.I))}
        found = {id(r): r for k in ks if len(k) >= 3 for r in idx.get(k, [])}
        if len(found) == 1:
            out[uid] = next(iter(found.values()))
    return out


SRC_MATCH = match_src_runners()


def english_name(uid):
    return (overrides.get(uid, {}).get("en") or profiles.get(uid, {}).get("src_name")
            or (SRC_MATCH.get(uid) or {}).get("name") or None)


def display_name(uid, lang):
    """Name to show: on English pages, Japanese names give way to the
    player's English nickname when one is known."""
    name = users.get(uid) or uid or "?"
    if lang == "en" and JA_RE.search(name):
        return english_name(uid) or name
    return name


def _flag_code(cc):
    """speedrun.com region codes like 'gb/sct' -> flag 'gb-sct'; 'ca/qc' -> 'ca'."""
    if not cc:
        return None
    if cc.startswith("gb/"):
        return cc.replace("/", "-")
    return cc.split("/")[0]


def country(uid):
    """(flag code, guessed?)"""
    o = overrides.get(uid, {}).get("country")
    if o:
        return o.lower(), False
    c = _flag_code(profiles.get(uid, {}).get("country")) or _flag_code((SRC_MATCH.get(uid) or {}).get("country"))
    if c:
        return c, False
    if JA_RE.search(users.get(uid, "")):
        return "jp", True
    return None, False


def flag(uid, t: T):
    code, guessed = country(uid)
    if not code:
        return ""
    name = COUNTRIES.get(code, {}).get(t.lang, code.upper())
    if guessed:
        name = t.s("inferred", name=name)
    return f'<img class="flag" src="{FLAG_CDN.format(code)}" alt="{code.upper()}" title="{e(name)}" loading="lazy">'


def player(uid, t: T, pre):
    if not uid:
        return "?"
    name = users.get(uid) or uid
    shown = display_name(uid, t.lang)
    title = f' title="{e(name)}"' if shown != name else ""
    return f'<a href="{pre}player/{quote(uid, safe="")}.html"{title}>{e(shown)}</a>'


# ---------------------------------------------------------------- scores

def mmss(sec):
    sec = int(sec)
    m, s = divmod(abs(sec), 60)
    h, m = divmod(m, 60)
    out = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    return ("-" if sec < 0 else "") + out


def score_views(score, rule, sid):
    """(points text|None, time used text|None, time left text|None).
    points is None when the score itself is a time."""
    st = stages.get(sid) or {}
    disp = st.get("display")
    limit = LIMIT_FIX.get(sid) or score_meta.time_limit(sid, st)
    if disp == "speedrun":
        return None, mmss(score), (mmss(limit - score) if limit else None)
    if disp == "time":
        return None, (mmss(limit - score) if limit else None), mmss(score)
    r = score_meta.times(score, rule, sid, st)
    if r and rule in score_meta.TIME_ATTACK_RULES:
        return None, mmss(r[0]), mmss(r[1])  # the score is just an encoded time
    if r:
        return f"{score:,}", mmss(r[0]), mmss(r[1])
    return f"{score:,}", None, None


def main_measure(rule, sid):
    """Which column a board is ranked by: 's' score, 'u' time used, 'l' time left."""
    if rule in score_meta.TIME_ATTACK_RULES:
        return "u"
    disp = (stages.get(sid) or {}).get("display")
    # time-ranked boards show time used as the headline figure
    return {"speedrun": "u", "time": "u"}.get(disp, "s")


def score_cell(p, rule, sid, t: T):
    """Three cells: score, time used, time left (a list; table() expands it).
    The board's ranking measure is bold; score is blank on pure-time boards."""
    if p.get("score") is None:
        return ["", "", ""]
    pts, used, left = score_views(p["score"], rule, sid)
    m = main_measure(rule, sid)
    if m == "u" and used is None:  # no time limit known: bold what we have
        m = "l"

    def num(val, key):
        if val is None:
            return ""
        return f'<span class="num">{f"<b>{val}</b>" if key == m else val}</span>'

    return [num(pts, "s"), num(used, "u"), num(left, "l")]


# pik5 fills empty comments with "コメントなし" ("no comment"), sometimes after a
# controller/console tag like "[Wii] "; show those as blank.
NO_COMMENT_RE = re.compile(r"^\s*(\[[^\]]*\]\s*)?(一行)?(コメントなし[。.]?)?\s*$")


def comment_text(p):
    c = (p.get("comment") or "").strip()
    return "" if NO_COMMENT_RE.match(c) else c


def H(t: T, key):
    """Compact table header with a tooltip (falls back to the plain label)."""
    if key not in HEADERS:
        return e(t.s(key))
    short, tip = HEADERS[key]
    return f'<span title="{e(tip[t.lang])}">{short[t.lang]}</span>'


def score_heads(t: T):
    """Headers for score_cell's three columns; time columns are dropped from a
    table when every row is empty."""
    return [H(t, "score"), ("drop", H(t, "mode_u")), ("drop", H(t, "mode_l"))]


# ---------------------------------------------------------------- video

def norm_video(v):
    v = (v or "").strip()
    if not v:
        return ""
    if not v.startswith("http"):
        v = "https://www.youtube.com/watch?v=" + v
    return v


def youtube_id(url):
    u = urlparse(url)
    host = u.netloc.lower()
    if host.endswith("youtu.be"):
        return u.path.strip("/").split("/")[0] or None
    if "youtube" in host:
        if u.path == "/watch":
            return (parse_qs(u.query).get("v") or [None])[0]
        m = re.match(r"/(shorts|live|embed)/([^/?]+)", u.path)
        if m:
            return m.group(2)
    return None


def _img_lookup():
    out = {}
    for f in (DATA / "boards").glob("*.json"):
        for q in json.loads(f.read_text(encoding="utf-8"))["posts"]:
            if q.get("img"):
                out[(q["user"], q["score"], (q.get("date") or "")[:10])] = q["img"]
    return out


IMG = _img_lookup()


def video_cell(p, t: T):
    """Video link icon, or a camera icon linking to pik5's proof screenshot."""
    v = norm_video(p.get("video"))
    if not v:
        img = p.get("img") or IMG.get((p.get("user"), p.get("score"), (p.get("date") or "")[:10]))
        if not img:
            return ""
        url = f"{PIK5}/api/file/{quote(img)}"
        return (f'<a class="ico" href="{e(url)}" target="_blank" rel="noopener" '
                f'title="{e(t.s("screenshot"))}">{CAMERA}</a>')
    icon, kind = video_icon(v)
    title = t.s("watch_on", site=kind)
    return f'<a class="ico" href="{e(v)}" target="_blank" rel="noopener" title="{e(title)}">{icon}</a>'


def best_with_video(key, low):
    posts = [p for p in board(key)["posts"] if norm_video(p.get("video"))]
    if not posts:
        return None
    return sorted(posts, key=lambda p: ((p["score"] if low else -p["score"]), p.get("date") or ""))[0]


def embed(p, t: T):
    v = norm_video(p.get("video"))
    if not v:
        return ""
    yid = youtube_id(v)
    if not yid:
        return ""
    thumb = f"https://i.ytimg.com/vi/{quote(yid)}/hqdefault.jpg"
    return (f'<div class="yt" data-id="{e(yid)}" style="background-image:url({thumb})">'
            f'<button type="button" aria-label="{e(t.s("play"))}"></button></div>')


# Games whose stages can be played on different difficulty settings.
DIFFICULTY_SERIES = {4}


def difficulty_cell(p, t: T, sid=None):
    if sid is None or (stages.get(sid) or {}).get("series") not in DIFFICULTY_SERIES:
        return ""
    return difficulty_icon(p.get("difficulty"), t.lang)


# Games where every run is on one console, so the column says nothing.
SINGLE_CONSOLE_SERIES = {4}


def console_cell(code, t: T, sid=None):
    if sid is not None and (stages.get(sid) or {}).get("series") in SINGLE_CONSOLE_SERIES:
        return ""
    return console_icon(code, t.lang)


# ---------------------------------------------------------------- layout

def page(t: T, rel, title, body, *, series=None, current_key=None, body_extra=""):
    depth = rel.count("/")
    pre = "../" * depth
    games = " | ".join(f'<a class="g{s}{" on" if series == s else ""}" href="{pre}{SLUG[s]}.html">{e(t.game(s))}</a>'
                       for s in GAMES)
    topnav = (f'[ <a href="{pre}index.html">{e(t.s("home"))}</a> | {games} | '
              f'<a href="{pre}players.html">{e(t.s("players"))}</a> ]')
    langnav = (f'[ <a data-lang="en" href="{pre}../en/{rel}"{" class=on" if t.lang == "en" else ""}>English</a> | '
               f'<a data-lang="ja" href="{pre}../ja/{rel}"{" class=on" if t.lang == "ja" else ""}>日本語</a> ]')

    side = [f'<div class="navbox"><a class="head" href="{pre}index.html">{e(t.s("games"))}</a>']
    for s in GAMES:
        side.append(f'<a class="g{s}{" on" if series == s else ""}" href="{pre}{SLUG[s]}.html">{e(t.game(s))}</a>')
    side.append("</div>")
    side.append(f'<div class="navbox"><span class="head">{e(t.s("more"))}</span>'
                f'<a href="{pre}recent.html">{e(t.s("recent_wrs"))}</a>'
                f'<a href="{pre}longest.html">{e(t.s("longest"))}</a>'
                f'<a href="{pre}snapshot/{SLUG[series or 1]}.html">{e(t.s("snapshots"))}</a>'
                f'<a href="{pre}activity.html">{e(t.s("activity"))}</a>'
                f'<a href="{pre}tas.html">{e(t.s("tas_title"))}</a>'
                f'<a href="{pre}seeds.html">{e(t.s("seeds_title"))}</a>'
                f'<a href="{pre}download.html">{e(t.s("download"))}</a>'
                f'<a href="{pre}players.html">{e(t.s("players"))}</a>'
                f'<a href="{pre}suggest.html?from={quote(rel)}">{e(t.s("suggest_title"))}</a></div>')
    if series:
        for c in [c for c in CATS if c["series"] == series]:
            side.append(f'<div class="navbox"><a class="head" href="{pre}{SLUG[series]}.html#c{c["id"]}">{e(t.cat(c))}</a>')
            for r, sid in c["boards"]:
                k = bkey(r, sid)
                on = ' class="on"' if k == current_key else ""
                egg = EGG if c.get("egg_rule") == r else ""
                side.append(f'<a{on} href="{pre}board/{k}.html">{e(t.stage(sid))}{egg}</a>')
            side.append("</div>")
    side.append(f'<div class="navbox links"><span class="head">{e(t.s("links"))}</span>')
    for en_label, ja_label, url in LINKS:
        label = ja_label if t.lang == "ja" else en_label
        side.append(f'<a href="{e(url.format(lang=t.lang))}" target="_blank" rel="noopener">{e(label)}</a>')
    side.append("</div>")

    synced = parse_date(meta.get("synced_at"))
    synced_txt = synced.strftime("%Y-%m-%d %H:%M UTC") if synced else "?"
    return f"""<!doctype html>
<html lang="{t.lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} - {e(t.s("site"))}</title>
<link rel="stylesheet" href="{pre}../style.css">
</head>
<body>
<div id="outer">
<div id="header">
  <div class="logo"><a href="{pre}index.html">{SPROUT}{e(t.s("site"))}</a></div>
  <div class="bars">
    <div>{topnav}</div>
    <div>{langnav}</div>
  </div>
</div>
<button id="menubtn" type="button" aria-expanded="false">&#9776; {e(t.s("menu"))}</button>
<div id="cols">
<nav id="nav">{''.join(side)}</nav>
<div id="main">
{body}
<div id="footer">{t.s("footer", pik5=f'<a href="{PIK5}/{t.lang}/">pik5.net</a>', synced=synced_txt)}
<br><a href="{pre}suggest.html?from={quote(rel)}">{e(t.s("suggest_link"))}</a></div>
</div>
</div>
</div>
{body_extra}
<script src="{pre}../site.js"></script>
</body>
</html>
"""


def _flat(cells):
    """Expand list cells (score_cell's three columns) into (html, rowspan) pairs.
    A cell may be a string, a list of strings, or ("rs", n, string|list)."""
    out = []
    for c in cells:
        span = 1
        if isinstance(c, tuple) and c[0] == "rs":
            span, c = c[1], c[2]
        for x in (c if isinstance(c, list) else [c]):
            out.append((x, span))
    return out


def table(headers, rows, cls="wr", dropped=None):
    """Render a table. Headers given as ("drop", label) are removed when every
    cell in that column is empty. If `dropped` is a list, the removed column
    indices are appended to it."""
    heads = []
    for h in headers:
        for x in (h if isinstance(h, list) else [h]):
            heads.append((x[1], True) if isinstance(x, tuple) else (x, False))
    ncol = len(heads)
    # place cells on a grid, honouring rowspans
    placed = []  # (row class, [(col, html, rowspan)])
    busy = [0] * ncol
    for r in rows:
        rcls, cells = (r[0], r[1]) if isinstance(r, tuple) else (None, r)
        col, out = 0, []
        for html_, span in _flat(cells):
            while col < ncol and busy[col] > 0:
                col += 1
            out.append((col, html_, span))
            if col < ncol:
                busy[col] = span
            col += 1
        busy = [max(0, b - 1) for b in busy]
        placed.append((rcls, out))
    has = [False] * ncol
    for _, out in placed:
        for col, html_, _ in out:
            if col < ncol and str(html_).strip():
                has[col] = True
    drop = {i for i, (_, droppable) in enumerate(heads) if droppable and not has[i]}
    if dropped is not None:
        dropped.extend(sorted(drop))
    th = "".join(f"<th>{h}</th>" for i, (h, _) in enumerate(heads) if i not in drop)
    trs = []
    for rcls, out in placed:
        tds = "".join((f'<td rowspan="{span}">' if span > 1 else "<td>") + f"{html_}</td>"
                      for col, html_, span in out if col not in drop)
        trs.append((f'<tr class="{rcls}">' if rcls else "<tr>") + tds + "</tr>")
    return (f'<div class="tw"><table class="{cls}"><thead><tr>{th}</tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table></div>')


def write(lang, rel, content):
    p = DIST / lang / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def pik5_url(key, t: T):
    r, s = key.split("-")
    return f"{PIK5}/{t.lang}/stage/{s}" if r == "48" else f"{PIK5}/{t.lang}/stage/{s}/0/{r}"


def board_tabs(t: T, key, active, pre):
    def a(k, href, label):
        return f'<a href="{href}"{" class=on" if active == k else ""}>{e(label)}</a>'
    return (f'<p class="tabs">[ {a("lb", f"{pre}board/{key}.html", t.s("leaderboard"))} | '
            f'{a("hist", f"{pre}history/{key}.html", t.s("wr_history"))} | '
            f'<a href="{pik5_url(key, t)}" target="_blank" rel="noopener">{e(t.s("on_pik5"))}</a> ]</p>')


def stage_heading(t: T, c, rule, sid):
    st = stages.get(sid) or {}
    facts = [f'<a href="../{SLUG[c["series"]]}.html#c{c["id"]}">{e(t.game(c["series"]))} · {e(t.cat(c))}</a>']
    if st.get("time"):
        facts.append(e(t.s("time_limit", t=mmss(st["time"]))))
    if st.get("pikmin"):
        facts.append(e(t.s("start_pikmin", n=st["pikmin"])))
    egg = ""
    if c.get("egg_rule") == rule:
        egg = f' <span class="eggnote">{EGG} {e(t.s("egg_stage"))}</span>'
    return f'<h1>{e(t.stage(sid))}{egg}</h1><p class="facts">{" · ".join(facts)}</p>'


# ---------------------------------------------------------------- pages

def proof_level(p):
    """0 video, 1 screenshot, 2 no proof."""
    if norm_video(p.get("video")):
        return 0
    if p.get("img") or IMG.get((p.get("user"), p.get("score"), (p.get("date") or "")[:10])):
        return 1
    return 2


def current_wrs(key):
    """Current #1s; ties ordered by proof (video, screenshot, none), then date."""
    wrs = [p for p in board(key)["posts"] if p.get("rank") == 1]
    return sorted(wrs, key=lambda p: (proof_level(p), p.get("date") or ""))


def first_set(wrs):
    """Date the record score was first reached (earliest of the tied holders)."""
    return min((p.get("date") or "" for p in wrs), default="")


def game_page(t: T, series):
    pre = ""
    cats = [c for c in CATS if c["series"] == series]
    parts = [f"<h1>{e(t.s('game_title', game=t.game(series)))}</h1>"]
    parts.append('<p class="tabs">[ ' + " | ".join(f'<a href="#c{c["id"]}">{e(t.cat(c))}</a>' for c in cats)
                 + f' | <a href="#overall">{e(t.s("overall"))}</a> ]</p>')
    for kind in ("main", "special"):
        group = [c for c in cats if c["kind"] == kind]
        if not group:
            continue
        parts.append(f"<h2>{e(t.s('cur_wrs' if kind == 'main' else 'cur_wrs_special'))}</h2>")
        for c in group:
            rows = []
            for r, sid in c["boards"]:
                k = bkey(r, sid)
                b = board(k)
                wrs = current_wrs(k)
                egg = EGG if c.get("egg_rule") == r else ""
                link = f'<a href="board/{k}.html">{e(t.stage(sid))}</a>{egg}'
                if not wrs:
                    rows.append([link, "—", "", "", "", "", "", ""])
                    continue
                w = wrs[0]
                if len(wrs) <= 2:
                    # one sub-row per holder so proof / flag / date / console line up
                    shown = wrs
                    n = len(wrs)
                    for i, p in enumerate(wrs):
                        cells = [video_cell(p, t), player(p["user"], t, pre), flag(p["user"], t),
                                 t.date(p.get("date"))]
                        if i == 0:
                            rows.append([("rs", n, link), ("rs", n, score_cell(w, r, sid, t)), *cells,
                                         ("rs", n, days_between(first_set(wrs), NOW)),
                                         console_cell(p.get("console"), t, sid), difficulty_cell(p, t, sid)])
                        else:
                            rows.append(("tied", [*cells, console_cell(p.get("console"), t, sid), difficulty_cell(p, t, sid)]))
                else:
                    shown = [w]
                    others = ", ".join(display_name(p["user"], t.lang) for p in wrs[1:])
                    who = (f'{player(w["user"], t, pre)} <a class="tie" title="{e(others)}" href="board/{k}.html">'
                           f'{e(t.s("plus_tied", n=len(wrs) - 1))}</a>')
                    rows.append([link, score_cell(w, r, sid, t), video_cell(w, t), who, flag(w["user"], t),
                                 t.date(w.get("date")), days_between(first_set(wrs), NOW),
                                 console_cell(w.get("console"), t, sid), difficulty_cell(w, t, sid)])
                if not any(norm_video(p.get("video")) for p in shown):
                    bv = best_with_video(k, b.get("lower_is_better"))
                    if bv:
                        rows.append(("proof", [
                            f'<span class="small">↳ {e(t.s("best_video"))}</span>', score_cell(bv, r, sid, t),
                            video_cell(bv, t), player(bv["user"], t, pre), flag(bv["user"], t),
                            t.date(bv.get("date")), "", console_cell(bv.get("console"), t, sid), difficulty_cell(bv, t, sid)]))
            parts.append(
                f'<h3 id="c{c["id"]}">{e(t.cat(c))}</h3>'
                + table([t.s("stage"), score_heads(t), H(t, "video"), t.s("player"), H(t, "nation"), t.s("date"),
                         H(t, "days"), ("drop", H(t, "console")), ("drop", H(t, "difficulty"))], rows))
    parts.append(f'<h2 id="overall">{e(t.s("overall_title", game=t.game(series)))}</h2>')
    parts.append(ranking_table(t, TOTAL_OF[series], pre))
    write(t.lang, f"{SLUG[series]}.html", page(t, f"{SLUG[series]}.html", t.game(series), "\n".join(parts),
                                               series=series))


def ranking_table(t: T, total_id, pre, limit=None):
    tot = load(f"totals/{total_id}.json")
    if not tot:
        return ""
    rows = [[str(r.get("rank") or ""), player(r["user"], t, pre), flag(r["user"], t),
             f'<span class="num"><b>{r["score"]:,}</b></span>' if r.get("score") is not None else "",
             f'<span class="num">{r["rps"]:,}</span>' if r.get("rps") is not None else "", str(r.get("boards") or "")]
            for r in tot["rows"][:limit]]
    return table(["#", t.s("player"), H(t, "nation"), H(t, "total_score"), H(t, "points"), H(t, "stages")], rows)


def ranking_page(t: T, total_id, title, series):
    rel = f"ranking/{total_id}.html"
    body = (f"<h1>{e(title)}</h1><p class='facts'>{e(t.s('ranked_by_points'))} "
            f'[ <a href="{PIK5}/{t.lang}/total/{total_id}" target="_blank" rel="noopener">{e(t.s("on_pik5"))}</a> ]</p>'
            + ranking_table(t, total_id, "../"))
    write(t.lang, rel, page(t, rel, title, body, series=series))


def history_page(t: T, c, rule, sid):
    pre = "../"
    k = bkey(rule, sid)
    rel = f"history/{k}.html"
    h = history(k)
    b = board(k)
    low = b.get("lower_is_better")
    parts = [stage_heading(t, c, rule, sid), board_tabs(t, k, "hist", pre), video_block(t, k, rule, sid, low, pre)]

    wrs = current_wrs(k)
    if wrs:
        rows = [[t.date(p.get("date")), score_cell(p, rule, sid, t), video_cell(p, t), player(p["user"], t, pre),
                 flag(p["user"], t), console_cell(p.get("console"), t, sid), difficulty_cell(p, t, sid), days_between(p.get("date"), NOW)]
                for p in wrs[:5]]
        more = f'<p class="small">{e(t.s("and_more_tied", n=len(wrs) - 5))}</p>' if len(wrs) > 5 else ""
        parts.append(f"<h2>{e(t.s('current_wr'))}</h2>"
                     + table([t.s("date"), score_heads(t), H(t, "video"), t.s("player"), H(t, "nation"), ("drop", H(t, "console")), ("drop", H(t, "difficulty")),
                              H(t, "days")], rows) + more)

    recs = h.get("records") or []
    groups = []
    for r in recs:
        if r.get("tie") and groups:
            groups[-1][1].append(r)
        else:
            groups.append((r, []))
    rows = []
    for i, (r, ties) in enumerate(groups):
        nxt = groups[i + 1][0] if i + 1 < len(groups) else None
        lasted = days_between(r["date"], nxt["date"] if nxt else NOW) + ("" if nxt else "+")
        tie = ""
        if ties:
            names = ", ".join(f'{display_name(x["user"], t.lang)} ({t.date(x["date"])})' for x in ties)
            tie = f'<span class="tie" title="{e(names)}">+{len(ties)}</span>'
        rows.append([t.date(r["date"]), score_cell(r, rule, sid, t), video_cell(r, t), player(r["user"], t, pre),
                     flag(r["user"], t), tie, console_cell(r.get("console"), t, sid), difficulty_cell(r, t, sid), lasted])
    rows.reverse()
    note = "" if h.get("backfilled") else f'<p class="note">{e(t.s("not_backfilled"))}</p>'
    parts.append(f"<h2>{e(t.s('history'))}</h2>" + note
                 + table([t.s("date"), score_heads(t), H(t, "video"), t.s("player"), H(t, "nation"), H(t, "tied_by"),
                          ("drop", H(t, "console")), ("drop", H(t, "difficulty")), H(t, "days")], rows))
    write(t.lang, rel, page(t, rel, f"{t.stage(sid)} - {t.cat(c)}", "\n".join(parts), series=c["series"],
                            current_key=k))


def seed_block(t: T, k, pre):
    """Layout seeds read from the current record's video, when known."""
    for p in current_wrs(k):
        r = SEED_BY_POST.get(p.get("id"))
        if r and r.get("floors"):
            return (f'<p class="vidcap">{e(t.s("layout_seeds"))}: {seed_links(r)} '
                    f'<span class="small">({e((r.get("how_short") or {}).get(t.lang) or t.s("seed_how_short"))} <a href="{pre}seeds.html">{e(t.s("more_info"))}</a>)</span></p>')
    return ""


def video_block(t: T, k, rule, sid, low, pre):
    """The best run with a video (the record if it has one), embedded, with a caption."""
    vid = next((p for p in current_wrs(k) if norm_video(p.get("video"))), None) or best_with_video(k, low)
    if not vid or not embed(vid, t):
        return ""
    shown = " / ".join(x for x in score_views(vid["score"], rule, sid) if x)
    return (f'<p class="vidcap">{e(t.s("fastest_video"))}: {player(vid["user"], t, pre)} {flag(vid["user"], t)} '
            f'<span class="num">{e(shown)}</span> <span class="small">({t.date(vid.get("date"))})</span></p>'
            + embed(vid, t))


def board_page(t: T, c, rule, sid):
    pre = "../"
    k = bkey(rule, sid)
    rel = f"board/{k}.html"
    b = board(k)
    low = bool(b.get("lower_is_better"))
    head = ["#", score_heads(t), H(t, "video"), t.s("player"), H(t, "nation"), H(t, "points"), t.s("date"),
            ("drop", H(t, "console")), ("drop", H(t, "difficulty")), t.s("comment")]
    rows = [[str(p.get("rank") or ""), score_cell(p, rule, sid, t), video_cell(p, t), player(p["user"], t, pre),
             flag(p["user"], t), f'<span class="num">{p["rps"]:,}</span>' if p.get("rps") is not None else "", t.date(p.get("date")),
             console_cell(p.get("console"), t, sid), difficulty_cell(p, t, sid), e(comment_text(p)[:80])]
            for p in b["posts"]]

    # snapshot data: every archived submission, with pre-rendered cells
    arc = archive(k)["posts"]
    snap = {
        "low": low,
        "rows": [{"u": a["user"], "s": a["score"], "d": t.date(a.get("date")),
                  "c": [*score_cell(a, rule, sid, t), video_cell(a, t), player(a["user"], t, pre),
                        flag(a["user"], t), "", t.date(a.get("date")), console_cell(a.get("console"), t, sid), difficulty_cell(a, t, sid),
                        e(comment_text(a)[:80])]}
                 for a in arc if a.get("score") is not None],
    }
    earliest = min((r["d"] for r in snap["rows"] if r["d"]), default="")
    picker = (f'<form class="snap" onsubmit="return false">{e(t.s("as_of"))} '
              f'<input type="date" id="asof" min="{earliest}" max="{NOW:%Y-%m-%d}"> '
              f'<button type="button" id="asof-reset">{e(t.s("today"))}</button> '
              f'<span class="small" id="asof-note"></span></form>'
              f'<p class="note">{e(t.s("snapshot_note", n=len(snap["rows"])))}</p>')
    dropped = []
    lb_table = table(head, rows, "wr lb", dropped=dropped)
    # keep snapshot cells in step with the columns the table kept (cells start after "#")
    for row in snap["rows"]:
        row["c"] = [c for i, c in enumerate(row["c"], start=1) if i not in dropped]
    data = json.dumps(snap, ensure_ascii=False).replace("</", "<\\/")
    texts = json.dumps({"players": t.s("n_players", n="{n}"), "asof": t.s("snapshot_shown")}, ensure_ascii=False)
    video = video_block(t, k, rule, sid, low, pre) + seed_block(t, k, pre)
    body = (stage_heading(t, c, rule, sid) + board_tabs(t, k, "lb", pre) + video
            + f"<h2>{e(t.s('leaderboard'))} <span class='small' id='lb-count'>{e(t.s('n_players', n=len(rows)))}</span></h2>"
            + picker + lb_table)
    extra = (f'<script type="application/json" id="snapdata">{data}</script>'
             f'<script>window.SNAP_TEXT={texts}</script>')
    write(t.lang, rel, page(t, rel, f"{t.stage(sid)} - {t.cat(c)}", body, series=c["series"], current_key=k,
                            body_extra=extra))


def collect_player_entries():
    per = {}
    for c in CATS:
        for r, sid in c["boards"]:
            for p in board(bkey(r, sid))["posts"]:
                per.setdefault(p["user"], []).append((c, r, sid, p))
    return per


def player_page(t: T, uid, entries):
    rel = f"player/{quote(uid, safe='')}.html"
    order = {c["id"]: i for i, c in enumerate(CATS)}
    entries = sorted(entries, key=lambda x: (x[0]["series"], order[x[0]["id"]], x[2]))
    wrs = sum(1 for *_, p in entries if p.get("rank") == 1)
    rows = []
    for c, r, sid, p in entries:
        rank = p.get("rank")
        rows.append([e(t.game(c["series"])), e(t.cat(c)),
                     f'<a href="../board/{bkey(r, sid)}.html">{e(t.stage(sid))}</a>',
                     score_cell(p, r, sid, t), video_cell(p, t),
                     '<b class="gold">1</b>' if rank == 1 else str(rank or ""),
                     t.date(p.get("date")), console_cell(p.get("console"), t, sid), difficulty_cell(p, t, sid)])
    name = users.get(uid) or uid
    shown = display_name(uid, t.lang)
    title = e(shown) + (f' <span class="romaji">({e(name)})</span>' if shown != name else "")
    src = profiles.get(uid, {}).get("src")
    links = [f'<a href="{PIK5}/{t.lang}/user/{quote(uid, safe="")}" target="_blank" rel="noopener">pik5.net</a>']
    if src:
        links.append(f'<a href="https://www.speedrun.com/users/{quote(src)}" target="_blank" rel="noopener">speedrun.com</a>')
    body = (f"<h1>{flag(uid, t)} {title}</h1>"
            f'<p class="facts">{e(t.s("player_facts", n=len(entries), w=wrs))} [ {" | ".join(links)} ]</p>'
            + table([t.s("game"), t.s("category"), t.s("stage"), score_heads(t), H(t, "video"), H(t, "rank"),
                     t.s("date"), ("drop", H(t, "console")), ("drop", H(t, "difficulty"))], rows))
    write(t.lang, rel, page(t, rel, shown, body))
    return wrs


def players_index(t: T, per, wr_counts):
    rows = [[player(uid, t, ""), flag(uid, t), str(wr_counts.get(uid, 0)), str(len(per[uid]))]
            for uid in sorted(per, key=lambda u: (-wr_counts.get(u, 0), -len(per[u]), display_name(u, t.lang).lower()))]
    body = f"<h1>{e(t.s('players'))}</h1>" + table([t.s("player"), H(t, "nation"), H(t, "cur_firsts"), H(t, "records")], rows)
    write(t.lang, "players.html", page(t, "players.html", t.s("players"), body))


def index_page(t: T):
    pre = ""
    head, rows = recent_rows(t, pre, 25)
    games = "".join(
        f'<li><a href="{SLUG[s]}.html">{e(t.game(s))}</a> <span class="small">'
        f'({e(t.s("n_boards", n=sum(len(c["boards"]) for c in CATS if c["series"] == s)))})</span></li>'
        for s in GAMES)
    pik5_link = f'<a href="{PIK5}/{t.lang}/">pik5.net</a>'
    body = (f"<h1>{e(t.s('site'))}</h1><p>{t.s('intro', pik5=pik5_link)}</p>"
            f"<ul class='games'>{games}</ul>"
            f"<h2>{e(t.s('recent'))}</h2>"
            + table(head, rows) + f'<p><a href="recent.html">{e(t.s("all_recent"))} →</a></p>')
    write(t.lang, "index.html", page(t, "index.html", t.s("home"), body))


ROOT_INDEX = """<!doctype html>
<html><head><meta charset="utf-8"><title>Pikmin Score Records</title>
<script>
(function(){var l;try{l=localStorage.getItem("lang")}catch(_){}
if(l!=="en"&&l!=="ja"){var ls=navigator.languages||[navigator.language||"en"];
l=ls.some(function(x){return /^ja\\b/i.test(x)})?"ja":"en"}
location.replace(l+"/index.html")})();
</script></head>
<body><p><a href="en/index.html">English</a> · <a href="ja/index.html">日本語</a></p></body></html>
"""


def seeds_page(t: T):
    """Pikmin 2 challenge-mode records with the sublevel layout seeds read from their videos."""
    pre = ""
    cat = next((c for c in CATS if c["id"] == "p2"), None)
    intro = t.s("seeds_intro")
    parts = [f"<h1>{e(t.s('seeds_title'))}</h1><p>{e(intro)}</p>"]
    if SEED_DOC:
        links = [f'<a href="{e(SEED_DOC)}">{e(t.s("seeds_method"))}</a>']
        if SEED_DOC_LAYOUT:
            links.append(f'<a href="{e(SEED_DOC_LAYOUT)}">{e(t.s("seeds_method_layout"))}</a>')
        parts.append(f'<p class="small">{" · ".join(links)}</p>')
    rows = []
    for rule, sid in (cat["boards"] if cat else []):
        k = bkey(rule, sid)
        egg = EGG if cat.get("egg_rule") == rule else ""
        link = f'<a href="board/{k}.html">{e(t.stage(sid))}</a>{egg}'
        wr = current_wrs(k)
        p = wr[0] if wr else None
        r = SEED_BY_POST.get(p.get("id")) if p else None
        if not p:
            continue
        seeds = seed_links(r) if r and r.get("floors") else ""
        why = ""
        if not seeds:
            note = (r or {}).get("note") or ((SEEDS.get("unread") or {}).get(str(sid)) if SEEDS else None)
            why = f'<span class="small">{e(note[t.lang] if isinstance(note, dict) else (note or t.s("seed_unknown")))}</span>'
        rows.append([link, f'<span class="num"><b>{p["score"]:,}</b></span>', video_cell(p, t),
                     player(p["user"], t, pre), seeds or why,
                     e((r or {}).get("how", {}).get(t.lang, "")) if seeds else ""])
    parts.append(table([t.s("stage"), H(t, "score"), H(t, "video"), t.s("player"), t.s("layout_seeds"), t.s("seed_how")],
                       rows))
    write(t.lang, "seeds.html", page(t, "seeds.html", t.s("seeds_title"), "\n".join(parts)))


def tas_page(t: T):
    """Best known challenge-mode TAS per stage (site/tas.json), next to the
    current real-time record."""
    pre = ""
    parts = [f"<h1>{e(t.s('tas_title'))}</h1><p>{e(t.s('tas_intro'))}</p>"]
    groups = [(1, "pikmin1", "10"), (2, "pikmin2", "p2")]
    for series, key, cat_id in groups:
        cat = next((c for c in CATS if c["id"] == cat_id), None)
        if not cat:
            continue
        runs = {r["stage"]: r for r in TAS.get(key, [])}
        rows = []
        for rule, sid in cat["boards"]:
            k = bkey(rule, sid)
            egg = EGG if cat.get("egg_rule") == rule else ""
            link = f'<a href="board/{k}.html">{e(t.stage(sid))}</a>{egg}'
            wr = current_wrs(k)
            wr_score = wr[0]["score"] if wr else None
            r = runs.get(sid)
            # a Pikmin 2 TAS only counts if it beats the real-time record
            if r and series == 2 and wr_score is not None and r["score"] <= wr_score:
                r = None
            if not r:
                rows.append([link, f'<span class="small">{e(t.s("no_tas"))}</span>', "", "", "", "", "",
                             f"{wr_score:,}" if wr_score is not None else ""])
                continue
            uid = r.get("pik5_user") or (TAS.get(f"{key}_default_player") if series == 2 else None)
            who = player(uid, t, pre) if uid and uid in users else e(r.get("player") or uid or "?")
            diff = ""
            if wr_score is not None:
                d = r["score"] - wr_score
                diff = f'<span class="{"gold" if d > 0 else "small"}">{d:+,}</span>' if d else "±0"
            tl = r.get("time_left")
            rows.append([
                link, f'<span class="num"><b>{r["score"]:,}</b></span>', video_cell({"video": r["video"]}, t), who,
                r.get("date", ""), str(tl) if tl is not None else "",
                (EGG + f' {r["mitites"]}') if r.get("mitites") else "",
                (f"{wr_score:,} " if wr_score is not None else "") + diff
                + (f' <span class="small">{e(r["note"][t.lang] if isinstance(r["note"], dict) else r["note"])}</span>'
                   if r.get("note") else ""),
            ])
        parts.append(f"<h2>{e(t.game(series))}</h2>" + table(
            [t.s("stage"), H(t, "score"), H(t, "video"), t.s("tasser"), t.s("date"), H(t, "time_left_col"),
             H(t, "mitites"), H(t, "vs_record")], rows))
    write(t.lang, "tas.html", page(t, "tas.html", t.s("tas_title"), "\n".join(parts)))


# ---------------------------------------------------------------- record stats pages

def wr_groups(key):
    """Record history as [(record, [ties])], oldest first."""
    groups = []
    for r in history(key).get("records") or []:
        if r.get("tie") and groups:
            groups[-1][1].append(r)
        else:
            groups.append((r, []))
    return groups


GAME_FILTERED = ("recent", "longest", "activity")


def filter_rel(base, series):
    return f"{base}.html" if not series else f"{base}-{SLUG[series]}.html"


def game_filter(t: T, base, series):
    links = [f'<a href="{filter_rel(base, None)}"{" class=on" if not series else ""}>{e(t.s("all_games"))}</a>']
    links += [f'<a href="{filter_rel(base, s)}"{" class=on" if s == series else ""}>{e(t.game(s))}</a>' for s in GAMES]
    return '<p class="tabs">[ ' + " | ".join(links) + " ]</p>"


def all_boards(series=None):
    for c in CATS:
        if series and c["series"] != series:
            continue
        for r, sid in c["boards"]:
            yield c, r, sid, bkey(r, sid)


def stage_link(t: T, c, r, sid, pre, with_cat=True):
    egg = EGG if c.get("egg_rule") == r else ""
    cat = f' <span class="small">({e(t.cat(c))})</span>' if with_cat else ""
    return f'<a href="{pre}board/{bkey(r, sid)}.html">{e(t.stage(sid))}</a>{egg}{cat}'


def recent_rows(t: T, pre, limit, series=None):
    items = []
    for c, r, sid, k in all_boards(series):
        groups = wr_groups(k)
        for i, (rec, ties) in enumerate(groups):
            prev = groups[i - 1][0] if i else None
            items.append((rec.get("date") or "", c, r, sid, rec, prev, False))
            for x in ties:
                items.append((x.get("date") or "", c, r, sid, x, rec, True))
    items.sort(key=lambda x: x[0], reverse=True)
    rows = []
    for d, c, r, sid, rec, prev, is_tie in items[:limit]:
        if is_tie:
            prev_cell, lasted = f'<span class="small">{e(t.s("tie_label"))}</span>', ""
        elif prev:
            prev_cell, lasted = player(prev["user"], t, pre), days_between(prev.get("date"), d)
        else:
            prev_cell, lasted = f'<span class="small">{e(t.s("first_record"))}</span>', ""
        rows.append([t.date(d), e(t.game(c["series"])), stage_link(t, c, r, sid, pre),
                     score_cell(rec, r, sid, t), video_cell(rec, t), player(rec["user"], t, pre),
                     flag(rec["user"], t), prev_cell, lasted])
    head = [t.s("date"), t.s("game"), t.s("stage"), score_heads(t), H(t, "video"), t.s("player"),
            H(t, "nation"), t.s("prev_holder"), H(t, "prev_lasted")]
    return head, rows


def recent_page(t: T, series=None):
    rel = filter_rel("recent", series)
    head, rows = recent_rows(t, "", 300, series)
    body = (f"<h1>{e(t.s('recent_wrs'))}</h1>" + game_filter(t, "recent", series)
            + f"<p>{e(t.s('recent_intro'))}</p>" + table(head, rows))
    write(t.lang, rel, page(t, rel, t.s("recent_wrs"), body, series=series))


def longest_page(t: T, series=None):
    pre = ""
    rel = filter_rel("longest", series)
    cur, ever = [], []
    for c, r, sid, k in all_boards(series):
        groups = wr_groups(k)
        for i, (rec, ties) in enumerate(groups):
            end = groups[i + 1][0]["date"] if i + 1 < len(groups) else None
            d1 = parse_date(rec.get("date"))
            d2 = parse_date(end) if end else NOW
            if not d1:
                continue
            item = (max(0, (d2 - d1).days), c, r, sid, rec, ties, end is None)
            ever.append(item)
            if end is None:
                cur.append(item)

    def rows_of(items, limit):
        rows = []
        for n, (days, c, r, sid, rec, ties, standing) in enumerate(sorted(items, key=lambda x: -x[0])[:limit], 1):
            tie = ""
            if ties:
                names = ", ".join(display_name(x["user"], t.lang) for x in ties)
                tie = f' <span class="tie" title="{e(names)}">+{len(ties)}</span>'
            rows.append([str(n), f'<span class="num"><b>{days:,}{"+" if standing else ""}</b></span>',
                         e(t.game(c["series"])), stage_link(t, c, r, sid, pre), score_cell(rec, r, sid, t),
                         video_cell(rec, t), player(rec["user"], t, pre) + tie, flag(rec["user"], t),
                         t.date(rec.get("date"))])
        return rows

    head = ["#", H(t, "days"), t.s("game"), t.s("stage"), score_heads(t), H(t, "video"), t.s("player"),
            H(t, "nation"), t.s("date")]
    body = (f"<h1>{e(t.s('longest'))}</h1>" + game_filter(t, "longest", series)
            + f"<p>{e(t.s('longest_intro'))}</p>"
            f"<h2>{e(t.s('longest_cur'))}</h2>" + table(head, rows_of(cur, 100))
            + f"<h2>{e(t.s('longest_all'))}</h2>" + table(head, rows_of(ever, 100)))
    write(t.lang, rel, page(t, rel, t.s("longest"), body, series=series))


def activity_page(t: T, series=None):
    pre = ""
    rel = filter_rel("activity", series)
    gs = [series] if series else list(GAMES)
    subs, wrs_set, runners = {}, {}, {}
    first_seen, held, set_count = {}, {}, {}
    for c, r, sid, k in all_boards(series):
        s = c["series"]
        for a in archive(k)["posts"]:
            d = parse_date(a.get("date"))
            if not d:
                continue
            y = d.year
            subs[(y, s)] = subs.get((y, s), 0) + 1
            runners.setdefault((y, s), set()).add(a["user"])
            runners.setdefault((y, 0), set()).add(a["user"])
            if a["user"] not in first_seen or d < first_seen[a["user"]]:
                first_seen[a["user"]] = d
        groups = wr_groups(k)
        for i, (rec, ties) in enumerate(groups):
            end = parse_date(groups[i + 1][0]["date"]) if i + 1 < len(groups) else NOW
            for x in [rec, *ties]:
                d = parse_date(x.get("date"))
                if not d:
                    continue
                wrs_set[(d.year, s)] = wrs_set.get((d.year, s), 0) + 1
                set_count[x["user"]] = set_count.get(x["user"], 0) + 1
                held[x["user"]] = held.get(x["user"], 0) + max(0, (end - d).days)
    years = sorted({y for y, _ in subs} | {y for y, _ in wrs_set}, reverse=True)
    new_by_year = {}
    for d in first_seen.values():
        new_by_year[d.year] = new_by_year.get(d.year, 0) + 1

    def num(n, bold=False):
        if not n:
            return ""
        return f'<span class="num">{"<b>" if bold else ""}{n:,}{"</b>" if bold else ""}</span>'

    game_heads = [e(t.game(s)) for s in gs]

    def year_table(counts):
        rows = []
        for y in years:
            vals = [counts.get((y, s), 0) for s in gs]
            rows.append([str(y), *[num(v, len(gs) == 1) for v in vals]] + ([num(sum(vals), True)] if len(gs) > 1 else []))
        return table([t.s("year"), *game_heads] + ([t.s("all_games")] if len(gs) > 1 else []), rows)

    run_rows = []
    for y in years:
        vals = [len(runners.get((y, s), ())) for s in gs]
        run_rows.append([str(y), *[num(v, len(gs) == 1) for v in vals]]
                        + ([num(len(runners.get((y, 0), ())), True)] if len(gs) > 1 else [])
                        + [num(new_by_year.get(y, 0))])
    cur = {}
    for c, r, sid, k in all_boards(series):
        for p in current_wrs(k):
            cur[p["user"]] = cur.get(p["user"], 0) + 1
    top = sorted(set_count, key=lambda u: (-set_count[u], -held.get(u, 0)))[:50]
    top_rows = [[str(i), player(u, t, pre), flag(u, t), num(set_count[u], True), num(cur.get(u, 0)),
                 num(held.get(u, 0))] for i, u in enumerate(top, 1)]
    body = (f"<h1>{e(t.s('activity'))}</h1>" + game_filter(t, "activity", series)
            + f"<p>{e(t.s('activity_intro'))}</p>"
            f"<h2>{e(t.s('act_subs'))}</h2>" + year_table(subs)
            + f"<h2>{e(t.s('act_wrs'))}</h2>" + year_table(wrs_set)
            + f"<h2>{e(t.s('act_runners'))}</h2>"
            + table([t.s("year"), *game_heads] + ([t.s("all_games")] if len(gs) > 1 else []) + [H(t, "runners_new")], run_rows)
            + f"<h2>{e(t.s('act_top'))}</h2>"
            + table(["#", t.s("player"), H(t, "nation"), H(t, "wrs_set"), H(t, "cur_firsts"), H(t, "days_held")],
                    top_rows))
    write(t.lang, rel, page(t, rel, t.s("activity"), body, series=series))


def snapshot_page(t: T, series):
    """Every record in a game as of a chosen date, rebuilt in the browser from record history."""
    pre = "../"
    rel = f"snapshot/{SLUG[series]}.html"
    tabs = '<p class="tabs">[ ' + " | ".join(
        f'<a href="{SLUG[s]}.html"{" class=on" if s == series else ""}>{e(t.game(s))}</a>' for s in GAMES) + " ]</p>"
    data, sections = [], []
    earliest = ""
    for c in [c for c in CATS if c["series"] == series]:
        boards, all_cells = [], []
        for r, sid in c["boards"]:
            k = bkey(r, sid)
            recs = []
            for rec, ties in wr_groups(k):
                grp = []
                for x in [rec, *ties]:
                    d = t.date(x.get("date"))
                    if not d:
                        continue
                    cells = [*score_cell(x, r, sid, t), video_cell(x, t), player(x["user"], t, pre),
                             flag(x["user"], t), d]
                    grp.append({"d": d, "c": cells})
                    all_cells.append(cells)
                    earliest = min(earliest or d, d)
                if grp:
                    recs.append(grp)
            boards.append({"name": stage_link(t, c, r, sid, pre, with_cat=False), "g": recs})
        # drop the time columns no record in this category uses (cell indices 1 and 2)
        keep = [0] + [i for i in (1, 2) if any(str(cells[i]).strip() for cells in all_cells)] + [3, 4, 5, 6]
        for b in boards:
            for grp in b["g"]:
                for x in grp:
                    x["c"] = [x["c"][i] for i in keep]
        score_h = {0: H(t, "score"), 1: H(t, "mode_u"), 2: H(t, "mode_l")}
        heads = ([t.s("stage")] + [score_h[i] for i in keep if i in score_h]
                 + [H(t, "video"), t.s("player"), H(t, "nation"), t.s("date"), H(t, "days")])
        th = "".join(f"<th>{h}</th>" for h in heads)
        sections.append(f'<h2 id="c{c["id"]}">{e(t.cat(c))}</h2><div class="tw"><table class="wr snapt" '
                        f'data-cat="{len(data)}"><thead><tr>{th}</tr></thead><tbody></tbody></table></div>')
        data.append(boards)
    picker = (f'<form class="snap" onsubmit="return false">{e(t.s("wrs_as_of"))} '
              f'<input type="date" id="wr-asof" min="{earliest}" max="{NOW:%Y-%m-%d}" value="{NOW:%Y-%m-%d}"> '
              f'<button type="button" id="wr-asof-today">{e(t.s("today"))}</button></form>')
    body = (f"<h1>{e(t.s('snapshots'))}: {e(t.game(series))}</h1>{tabs}"
            f"<p>{e(t.s('snapshot_intro', game=t.game(series)))}</p>" + picker + "".join(sections)
            + "<noscript><p class='note'>JavaScript is needed for this page.</p></noscript>")
    js = json.dumps({"cats": data, "none": t.s("no_record_yet"), "today": f"{NOW:%Y-%m-%d}"},
                    ensure_ascii=False).replace("</", "<\\/")
    extra = f'<script type="application/json" id="wrsnap">{js}</script>'
    write(t.lang, rel, page(t, rel, f"{t.s('snapshots')}: {t.game(series)}", body, series=series, body_extra=extra))


# ---------------------------------------------------------------- data download

CSV_FILES = ("submissions", "records", "leaderboards")


def _csv_common(c, r, sid, p):
    st = stages.get(sid) or {}
    pts, used, left = score_views(p["score"], r, sid) if p.get("score") is not None else (None, None, None)
    cons = CONSOLES.get(p.get("console"))
    diff = DIFFICULTY.get(p.get("difficulty")) if (st.get("series") in DIFFICULTY_SERIES) else None
    uid = p.get("user")
    cc, _ = country(uid) if uid else (None, None)
    return {
        "game": GAMES[c["series"]]["en"], "category": CAT_LABELS[c["id"]]["en"], "rule": r, "stage_id": sid,
        "stage": st.get("name") or "", "stage_ja": st.get("name_ja") or "",
        "egg_stage": "yes" if c.get("egg_rule") == r else "",
        "player_id": uid or "", "player": users.get(uid) or uid or "", "player_en": english_name(uid) or "",
        "country": (cc or "").upper(),
        "score": p.get("score") if p.get("score") is not None else "",
        "time_used": used or "", "time_left": left or "",
        "date": (p.get("date") or "")[:10], "video": norm_video(p.get("video")),
        "screenshot": f"{PIK5}/api/file/{quote(p['img'])}" if p.get("img") else "",
        "console": cons[3]["en"] if cons else "", "difficulty": diff[1]["en"] if diff else "",
        "comment": comment_text(p),
    }


def write_csvs():
    """dist/data/*.csv: every known submission, the record history, and current leaderboards.
    UTF-8 with a BOM so Excel shows Japanese names correctly."""
    import csv
    out = DIST / "data"
    out.mkdir(exist_ok=True)
    tables = {k: [] for k in CSV_FILES}
    for c, r, sid, k in all_boards():
        ranks = {(p["user"], p["score"]): p for p in board(k)["posts"]}
        for a in archive(k)["posts"]:
            if a.get("score") is None:
                continue
            cur = ranks.get((a["user"], a["score"]))
            tables["submissions"].append({**_csv_common(c, r, sid, a),
                                          "current_rank": (cur or {}).get("rank") or "",
                                          "source": "old sheet scrape" if a.get("source") else "pik5.net"})
        groups = wr_groups(k)
        for i, (rec, ties) in enumerate(groups):
            end = groups[i + 1][0].get("date") if i + 1 < len(groups) else None
            for x in [rec, *ties]:
                tables["records"].append({**_csv_common(c, r, sid, x), "tie": "yes" if x is not rec else "",
                                          "beaten_on": (end or "")[:10],
                                          "days_lasted": days_between(rec.get("date"), end or NOW)})
        for p in board(k)["posts"]:
            if p.get("score") is None:
                continue
            tables["leaderboards"].append({"rank": p.get("rank") or "", **_csv_common(c, r, sid, p),
                                           "points": p.get("rps") if p.get("rps") is not None else ""})
    sizes = {}
    for name, rows in tables.items():
        path = out / f"{name}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            if rows:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        sizes[name] = (len(rows), path.stat().st_size)
    return sizes


def download_page(t: T, sizes):
    rows = []
    for name in CSV_FILES:
        n, size = sizes.get(name, (0, 0))
        rows.append([f'<a href="../data/{name}.csv" download>{name}.csv</a>', e(t.s(f"dl_{name}")),
                     f'<span class="num">{n:,}</span>', f'<span class="num">{size / 1024:,.0f} KB</span>'])
    body = (f"<h1>{e(t.s('download'))}</h1><p>{e(t.s('download_intro'))}</p>"
            + table([t.s("file"), t.s("contents"), t.s("rows"), t.s("size")], rows)
            + f'<p class="note">{e(t.s("download_note"))}</p>')
    write(t.lang, "download.html", page(t, "download.html", t.s("download"), body))


CONFIG = json.loads((SRC / "config.json").read_text(encoding="utf-8"))


def suggest_page(t: T):
    """Suggestion form. Entries go to the form service set in site/config.json."""
    cfg = CONFIG.get("suggestions") or {}
    f = cfg.get("fields") or {}
    action = cfg.get("action") or ""
    setup = "" if action else f'<p class="note">{e(t.s("suggest_off"))}</p>'
    body = f"""<h1>{e(t.s("suggest_title"))}</h1>
<p>{e(t.s("suggest_intro"))}</p>{setup}
<form id="suggest" class="suggest" action="{e(action)}" method="POST"
      data-no-cors="{'1' if cfg.get('no_cors') else ''}">
  <label>{e(t.s("suggest_name"))}<br><input name="{e(f.get('name', 'name'))}" maxlength="80"
         placeholder="{e(t.s('suggest_name_ph'))}"></label>
  <label>{e(t.s("suggest_text"))}<br><textarea name="{e(f.get('message', 'message'))}" rows="7" maxlength="4000" required></textarea></label>
  <label>{e(t.s("suggest_contact"))}<br><input name="{e(f.get('contact', 'contact'))}" maxlength="120"
         placeholder="{e(t.s('suggest_contact_ph'))}"></label>
  <input type="hidden" name="{e(f.get('page', 'page'))}" id="suggest-page">
  <button type="submit"{'' if action else ' disabled'}>{e(t.s("suggest_send"))}</button>
  <span id="suggest-status" class="small"></span>
</form>
<script>window.SUGGEST_TEXT={json.dumps({"sent": t.s("suggest_sent"), "fail": t.s("suggest_fail"), "sending": t.s("suggest_sending")}, ensure_ascii=False)}</script>"""
    write(t.lang, "suggest.html", page(t, "suggest.html", t.s("suggest_title"), body))


def main():
    if not raw_categories:
        raise SystemExit("no data — run sync/pik5_sync.py first")
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    shutil.copy(SRC / "style.css", DIST / "style.css")
    shutil.copy(SRC / "site.js", DIST / "site.js")
    (DIST / "index.html").write_text(ROOT_INDEX, encoding="utf-8")
    per = collect_player_entries()
    csv_sizes = write_csvs()
    for lang in LANGS:
        t = T(lang)
        for s in GAMES:
            game_page(t, s)
        for c in CATS:
            for r, sid in c["boards"]:
                history_page(t, c, r, sid)
                board_page(t, c, r, sid)
        for s, tid in TOTAL_OF.items():
            ranking_page(t, tid, t.s("overall_title", game=t.game(s)), s)
        wr_counts = {uid: player_page(t, uid, ent) for uid, ent in per.items()}
        players_index(t, per, wr_counts)
        index_page(t)
        tas_page(t)
        seeds_page(t)
        suggest_page(t)
        download_page(t, csv_sizes)
        for s_ in (None, *GAMES):
            recent_page(t, s_)
            longest_page(t, s_)
            activity_page(t, s_)
        for s_ in GAMES:
            snapshot_page(t, s_)
    n = sum(1 for _ in DIST.rglob("*.html"))
    print(f"built {n} pages into {DIST}")


if __name__ == "__main__":
    main()
