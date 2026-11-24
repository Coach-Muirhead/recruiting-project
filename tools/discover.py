#!/usr/bin/env python3
"""
Recruiting Project - program discovery.

Builds the master list of every college baseball program (NCAA D1/D2/D3, NAIA,
NJCAA, CCCAA, NWAC) and works out, for each one, where its roster lives.

  1. Membership: active-program lists on The Baseball Cube (college, nickname,
     conference, city/state, full name, latest season played).
  2. Location, enrollment, federal ID, school website: College Scorecard, pulled in
     bulk (about 70 requests) and matched by name and state.
  3. Athletics website: Wikidata "official website" of the school's athletics
     article on Wikipedia, else the "Athletics" link on the school's home page.
  4. Platform: probes the athletics site for a Sidearm or PrestoSports roster page.

Writes data/discovered.csv (every program and how far it got) and adds programs
whose roster page was found to scraper/schools.csv. Existing rows are never changed.

  SCORECARD_API_KEY=... python tools/discover.py [--levels ncaa-1,naia] [--limit 50]
"""
from __future__ import annotations

import argparse, csv, json, os, re, sys, time, urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
SCHOOLS = ROOT / "scraper" / "schools.csv"
OUT = ROOT / "data" / "discovered.csv"
UA = "RecruitingProjectBot/1.0 (college baseball recruiting tool; github.com/Coach-Muirhead/recruiting-project)"
S = requests.Session(); S.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"})

LEVELS = {  # source -> division
    "d1": "D1", "d2": "D2", "d3": "D3", "naia": "NAIA", "njcaa1": "JUCO", "njcaa2": "JUCO", "njcaa3": "JUCO", "nwac": "JUCO", "cccaa": "JUCO",
}
WIKI_PAGES = {"d1": "List_of_NCAA_Division_I_baseball_programs", "d2": "List_of_NCAA_Division_II_baseball_programs", "d3": "List_of_NCAA_Division_III_institutions",
              "naia": "List_of_NAIA_institutions", "njcaa1": "List_of_NJCAA_Division_I_schools", "njcaa2": "List_of_NJCAA_Division_II_schools",
              "njcaa3": "List_of_NJCAA_Division_III_schools", "nwac": "Northwest_Athletic_Conference"}
STATES = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY", "Puerto Rico": "PR"}
SEASONS = ["2026-27", "2025-26"]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def get(url, **kw):
    try:
        return S.get(url, timeout=kw.pop("timeout", 12), **kw)
    except requests.RequestException as e:
        return None


def wget(url, params):
    """Wikipedia/Wikidata request: one at a time, honours 429 Retry-After, up to 5 tries."""
    for attempt in range(6):
        q = {**params, "maxlag": 5} if attempt < 4 else params  # busy servers: ask politely first, then plainly
        r = get(url, params=q, timeout=40)
        if r is not None and r.status_code == 200 and '"error"' not in r.text[:300]:
            time.sleep(0.5)
            return r
        if r is not None and r.status_code == 200:
            log(f"  {urlparse(url).netloc} said: {r.text[:160]}")
        wait = int(r.headers.get("Retry-After", "0") or 0) if r is not None else 0
        time.sleep(max(wait, 5 * (attempt + 1)))
    return None


def norm(s: str) -> str:
    s = s.lower().replace("&", " and ").replace("saint ", "st ").replace("st. ", "st ")
    s = re.sub(r"\bthe\b|\bcommunity college\b|\bcc\b|\bcollege\b|\buniversity\b|\bof\b|[^a-z0-9 ]", " ", s)
    return " ".join(s.split())


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())[:28]


# ------------------------------------------------------------ 1. membership (Wikipedia member lists)
def strip(h: str) -> str:
    h = re.sub(r"<sup.*?</sup>", "", h, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)).replace("&amp;", "&").replace("&#39;", "'").strip()


def state_code(t: str) -> str:
    t = re.sub(r"\(.*?\)", "", t).strip()
    return t if t in STATES.values() else STATES.get(t, t)


def wiki_html(page: str) -> str:
    r = wget("https://en.wikipedia.org/w/api.php", {"action": "parse", "page": page, "prop": "text", "format": "json", "formatversion": 2})
    return r.json()["parse"]["text"] if r and r.ok and "parse" in r.json() else ""


def wiki_tables(html: str) -> list[dict]:
    """Rows of every wikitable, keyed by normalised header, with the first link title of each cell."""
    out = []
    for t in re.findall(r'<table class="[^"]*wikitable[^"]*".*?</table>', html, re.S):
        trs = re.findall(r"<tr.*?</tr>", t, re.S)
        if not trs:
            continue
        heads = [strip(c).lower() for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", trs[0], re.S)]
        for tr in trs[1:]:
            cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)
            if len(cells) != len(heads):
                continue
            row = {}
            for h, c in zip(heads, cells):
                link = re.search(r'<a href="/wiki/[^"]+" [^>]*title="([^"]+)"', c)
                row[h] = strip(c); row[h + "@link"] = link.group(1).replace("&amp;", "&").replace("&#39;", "'") if link else ""
            out.append(row)
    return out


def col(row: dict, *names: str, link=False) -> str:
    for k in row:
        if k.endswith("@link"):
            continue
        if any(k.startswith(n) for n in names):
            return row.get(k + "@link", "") if link else row[k]
    return ""


def membership(src: str) -> list[dict]:
    if src == "cccaa":
        return []  # filled from College Scorecard in main()
    html = wiki_html(WIKI_PAGES[src]); time.sleep(1)
    out = []
    if src.startswith("njcaa"):
        for state_html in re.split(r'<h3 id="', html)[1:]:
            state = state_code(state_html.split('"', 1)[0].replace("_", " "))
            for li in re.findall(r"<li>(.*?)</li>", state_html, re.S):
                a = re.search(r'<a href="/wiki/[^"]+"[^>]*title="([^"]+)"[^>]*>(.*?)</a>(.*)', li, re.S)
                if not a:
                    continue
                rest = strip(a.group(3))
                nick, _, city = rest.partition(" in ")
                out.append({"full": strip(a.group(2)), "wiki_school": a.group(1), "nickname": nick.strip(), "city": city.strip(), "state": state, "conf": f"NJCAA Division {'I' * int(src[-1])}"})
        log(f"list {src}: {len(out)} member schools"); return out
    for r in wiki_tables(html):
        school = col(r, "school", "team", "institution")
        if not school or school.lower().startswith(("school", "team")):
            continue
        st = state_code(col(r, "state"))
        city = col(r, "city")
        loc = col(r, "location")
        if loc and not city:
            city, _, st2 = loc.partition(",")
            st = state_code(st2.strip()) or st
        if src == "nwac" and st not in ("WA", "OR", "ID"):
            continue  # skip the BC (Canada) members
        out.append({"full": re.sub(r"\s*\*$", "", re.sub(r"\s*\(.*?\)\s*$", "", school)).strip(), "short": (re.search(r"\((.*?)\)", school) or [None, ""])[1],
                    "wiki_school": col(r, "school", "team", "institution", link=True), "nickname": col(r, "nickname"),
                    "wiki_team": col(r, "nickname", link=True), "city": city.strip(), "state": st,
                    "conf": "NWAC – " + col(r, "division") if src == "nwac" else col(r, "conference")})
    log(f"list {src}: {len(out)} rows")
    return out


# ------------------------------------------------------------ 2. College Scorecard (bulk)
def scorecard_all(key: str) -> list[dict]:
    fields = "id,school.name,school.alias,school.city,school.state,school.school_url,school.ownership,school.degrees_awarded.predominant,location.lat,location.lon,latest.student.size"
    rows, page = [], 0
    while True:
        r = get(f"https://api.data.gov/ed/collegescorecard/v1/schools?school.operating=1&fields={fields}&per_page=100&page={page}&api_key={key}", timeout=60)
        if not r or r.status_code != 200:
            log(f"scorecard page {page}: {r.status_code if r else 'error'}"); break
        j = r.json(); rows += j["results"]
        if (page + 1) * 100 >= j["metadata"]["total"]:
            break
        page += 1
    log(f"scorecard: {len(rows)} institutions")
    return rows


def match(p: dict, sc_by_state: dict) -> dict | None:
    cands = sc_by_state.get(p["state"], [])
    want = {norm(p["full"]), norm(p["short"])}
    for c in cands:
        names = [c["school.name"]] + [a.strip() for a in (c.get("school.alias") or "").split(",") if a.strip()]
        if any(norm(n) in want for n in names):
            return c
    # same city, strong word overlap
    w = set(norm(p["full"]).split())
    best, score = None, 0.0
    for c in cands:
        if (c.get("school.city") or "").lower() != p["city"].lower():
            continue
        cw = set(norm(c["school.name"]).split())
        if w and cw:
            j = len(w & cw) / len(w | cw)
            if j > score:
                best, score = c, j
    return best if score >= 0.5 else None


# ------------------------------------------------------------ 3. athletics website
def _wd_items(titles: list[str]) -> dict[str, str]:
    """Wikipedia title -> Wikidata id (follows redirects and title normalisation)."""
    out = {}
    for i in range(0, len(titles), 50):
        r = wget("https://en.wikipedia.org/w/api.php", {"action": "query", "prop": "pageprops", "ppprop": "wikibase_item", "titles": "|".join(titles[i:i + 50]), "redirects": 1, "format": "json", "formatversion": 2})
        if r is None or not r.ok:
            log(f"  pageprops HTTP {r.status_code if r is not None else 'error'}"); continue
        j = r.json().get("query", {})
        alias = {}
        for x in j.get("normalized", []) + j.get("redirects", []):
            alias[x["to"]] = alias.get(x["from"], x["from"])
        for pg in j.get("pages", []):
            qid = pg.get("pageprops", {}).get("wikibase_item")
            if qid:
                out[alias.get(pg["title"], pg["title"])] = qid
                out[pg["title"]] = qid
        time.sleep(0.2)
    return out


def _wd_claims(qids: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(qids), 50):
        r = wget("https://www.wikidata.org/w/api.php", {"action": "wbgetentities", "ids": "|".join(qids[i:i + 50]), "props": "claims", "format": "json"})
        if r is None or not r.ok:
            log(f"  wikidata HTTP {r.status_code if r is not None else 'error'}"); continue
        for qid, e in r.json().get("entities", {}).items():
            def vals(p):
                return [c["mainsnak"].get("datavalue", {}).get("value") for c in e.get("claims", {}).get(p, [])]
            out[qid] = {"site": [v for v in vals("P856") if isinstance(v, str)], "ipeds": [v for v in vals("P1771") if isinstance(v, str)]}
        time.sleep(0.2)
    return out


def wiki_sites(programs: list[dict]) -> None:
    """From Wikidata: the athletics article's official website (p['wiki_site']) and the
    school article's federal IPEDS id (p['ipeds']) and website (p['wiki_school_site'])."""
    team_title = {}
    searched = 0
    for p in programs:
        team = p.get("wiki_team") or ""
        if team and p["nickname"] and p["nickname"].split()[-1].lower() in team.lower():
            team_title[id(p)] = re.sub(r" baseball$", "", team)
            continue
        if not p["nickname"]:
            continue
        short = p.get("short") if p.get("short") != p["full"] else ""
        short = short or re.sub(r"\b(university|college|community|of|the|state university)\b", " ", p["full"], flags=re.I)
        r = wget("https://en.wikipedia.org/w/api.php", {"action": "query", "list": "search", "srsearch": f'{" ".join(short.split())} {p["nickname"]}', "srlimit": 5, "format": "json"})
        searched += 1
        hits = [h["title"] for h in (r.json().get("query", {}).get("search", []) if r is not None and r.ok else [])]
        nick = p["nickname"].lower()
        t = next((h for h in hits if nick in h.lower() and not re.search(r"baseball|season|football|basketball|stadium|field|arena", h.lower())), None) \
            or next((h[: -len(" baseball")] for h in hits if nick in h.lower() and h.lower().endswith(" baseball")), None)
        if t:
            team_title[id(p)] = t
    school_title = {id(p): p["wiki_school"] for p in programs if p.get("wiki_school")}
    qid = _wd_items(sorted(set(team_title.values()) | set(school_title.values())))
    claims = _wd_claims(sorted(set(qid.values())))
    for p in programs:
        t = claims.get(qid.get(team_title.get(id(p), ""), ""), {})
        sc = claims.get(qid.get(school_title.get(id(p), ""), ""), {})
        if t.get("site") and t is not sc:
            p["wiki_site"] = t["site"][-1]
        if sc.get("ipeds"):
            p["ipeds"] = sc["ipeds"][0]
        if sc.get("site"):
            p["wiki_school_site"] = sc["site"][-1]
    log(f"wikipedia: searched {searched}, team articles {len(team_title)}, school articles {len(school_title)}, wikidata items {len(set(qid.values()))}, "
        f"athletics sites {sum(1 for p in programs if p.get('wiki_site'))}, IPEDS ids {sum(1 for p in programs if p.get('ipeds'))} of {len(programs)}")


ATHLETIC_HOSTS = re.compile(r"prestosports\.com|sidearmsports\.com|athletics|sports\.com|/athletics", re.I)


HOME_DIAG: dict[str, str] = {}


def home_athletics_link(school_url: str) -> str | None:
    """Finds the athletics site from the school's home page, following one /athletics hop."""
    if not school_url:
        return None
    base = school_url if school_url.startswith("http") else "https://" + school_url
    r = get(base)
    HOME_DIAG[school_url] = f"home:{r.status_code if r is not None else 0}:{len(r.text) if r is not None else 0}:links={len(re.findall('<a ', r.text)) if r is not None else 0}"
    if r is None or r.status_code != 200:
        return None
    school = domain(r.url).split(".")[-2] if "." in domain(r.url) else domain(r.url)
    outside, best = [], None
    for href, text in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', r.text, re.S | re.I):
        t = re.sub(r"<[^>]+>|\s+", " ", text).strip().lower()
        u = urljoin(r.url, href)
        if re.search(r"\.(js|css|png|jpe?g|svg|gif|pdf|ico)(\?|$)", u, re.I) or not u.startswith("http"):
            continue
        if re.search(r"/(news|taxonomy|tag|tags|category|stories|blog|events?)/", urlparse(u).path, re.I):
            continue
        if not (("athletic" in t and len(t) < 40) or t in ("sports", "go") or ATHLETIC_HOSTS.search(u)):
            continue
        d = domain(u)
        if d == domain(r.url) or d.endswith("." + domain(r.url)) or school in d.split(".")[:-1] and "athletic" not in d:
            best = best or u  # a page on the school's own site
        elif not re.search(r"facebook|twitter|x\.com|instagram|youtube|ncaa\.org|njcaa\.org|naia\.org|tiktok|linkedin", d):
            outside.append(u)
    if outside:
        return outside[0]  # an outside athletics site: the usual case
    if best:  # an /athletics page on the school site: look there for the outside site
        r2 = get(best)
        if r2 is not None and r2.status_code == 200:
            for href in re.findall(r'href="(https?://[^"#]+)"', r2.text):
                d = domain(href)
                if d != domain(r.url) and ATHLETIC_HOSTS.search(href) and not re.search(r"facebook|twitter|x\.com|instagram|youtube|ncaa\.org|njcaa\.org|naia\.org|tiktok", d):
                    return href
        return best
    return None


def domain(u: str) -> str:
    return urlparse(u if "//" in u else "https://" + u).netloc.lower().removeprefix("www.")


# ------------------------------------------------------------ 4. platform probe
ROSTER_PATHS = [("/sports/baseball/roster", "sidearm"), ("/sports/mens-baseball/roster", "sidearm"), ("/sports/m-basebl/roster", "sidearm")] + \
               [(f"/sports/bsb/{s}/roster", "presto") for s in SEASONS]


def probe(site: str) -> dict:
    base = site if "//" in site else "https://" + site
    base = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
    seen = []
    for path, plat in ROSTER_PATHS:
        r = get(base + path)
        code = r.status_code if r is not None else 0
        seen.append(f"{path}:{code}")
        if r is not None and code == 200 and re.search(r"roster", r.text[:200000], re.I) and len(r.text) > 3000:
            fb = f"{urlparse(r.url).scheme}://{urlparse(r.url).netloc}"
            return {"platform": plat, "athletics_base": fb, "roster_path": urlparse(r.url).path, "probe": " ".join(seen)}
        if code in (401, 403, 429):
            return {"platform": "blocked", "athletics_base": base, "roster_path": "", "probe": " ".join(seen)}
    return {"platform": "", "athletics_base": base, "roster_path": "", "probe": " ".join(seen)}


def resolve(p: dict) -> dict:
    sc_dom = domain(p.get("school_url") or "x")
    cands = []
    if p.get("wiki_site"):
        cands.append(p["wiki_site"])
    link = home_athletics_link(p.get("school_url") or p.get("wiki_school_site") or "")
    if link and domain(link) not in [domain(c) for c in cands]:
        cands.append(link)
    if p.get("school_url"):
        cands.append(p["school_url"])  # some schools host rosters on the main site
    blocked = None
    last = None
    for c in cands:
        res = probe(c)
        if res["platform"] in ("sidearm", "presto"):
            return {**p, **res, "site_source": "wikipedia" if c == p.get("wiki_site") else "homepage" if c == link else "school site", "home_link": link or ""}
        if res["platform"] == "blocked" and not blocked:
            blocked = res
        last = res
    res = blocked or last or {"platform": "", "athletics_base": "", "roster_path": "", "probe": "no candidate site"}
    return {**p, **res, "site_source": "", "home_link": link or "", "home_diag": HOME_DIAG.get(p.get("school_url") or p.get("wiki_school_site") or "", "")}


# ------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default=",".join(LEVELS))  # d1,d2,d3,naia,njcaa1,njcaa2,njcaa3,nwac,cccaa
    ap.add_argument("--limit", type=int, default=0, help="programs per level, for test runs")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-merge", action="store_true")
    ap.add_argument("--merge-only", action="store_true")
    a = ap.parse_args()
    if a.merge_only:
        return merge()
    key = os.environ.get("SCORECARD_API_KEY")
    if not key:
        sys.exit("SCORECARD_API_KEY is required")

    programs = []
    for src in a.levels.split(","):
        rows = membership(src)
        if a.limit:
            rows = rows[: a.limit]
        for r in rows:
            r["div"] = LEVELS[src]
            r.setdefault("short", ""); r["short"] = r["short"] or r["full"]
            programs.append(r)

    sc = scorecard_all(key)
    if "cccaa" in a.levels.split(","):
        cc = [c for c in sc if c.get("school.state") == "CA" and c.get("school.ownership") == 1 and c.get("school.degrees_awarded.predominant") == 2]
        if a.limit:
            cc = cc[: a.limit]
        programs += [{"full": c["school.name"], "short": c["school.name"], "nickname": "", "city": c.get("school.city") or "", "state": "CA", "conf": "CCCAA", "div": "JUCO"} for c in cc]
        log(f"list cccaa: {len(cc)} California public two-year colleges to check")
    seen = set()  # a school listed twice (e.g. NJCAA and NWAC) keeps its first entry
    programs = [p for p in programs if not ((norm(p["full"]), p["state"]) in seen or seen.add((norm(p["full"]), p["state"])))]
    by_state: dict[str, list] = {}
    for c in sc:
        by_state.setdefault(c.get("school.state"), []).append(c)
    wiki_sites(programs)
    by_id = {str(c["id"]): c for c in sc}
    for p in programs:
        c = by_id.get(str(p.get("ipeds") or "")) or match(p, by_state)
        if c:
            p.update({"unitid": c["id"], "sc_name": c["school.name"], "lat": c.get("location.lat"), "lon": c.get("location.lon"), "enroll": c.get("latest.student.size"), "school_url": c.get("school.school_url") or ""})
    log(f"scorecard matched {sum(1 for p in programs if p.get('unitid'))} of {len(programs)}")
    counter = {"n": 0}

    def step(p):
        r = resolve(p)
        counter["n"] += 1
        if counter["n"] % 50 == 0:
            log(f"  checked {counter['n']} of {len(programs)}")
        return r

    with ThreadPoolExecutor(a.workers) as ex:
        done = list(ex.map(step, programs))

    cols = ["div", "conf", "short", "nickname", "full", "city", "state", "unitid", "sc_name", "lat", "lon", "enroll", "school_url", "wiki_site", "site_source", "athletics_base", "platform", "roster_path", "probe", "home_link", "home_diag"]
    part = ROOT / "data" / "discovered" / f"{a.levels.replace(',', '+')}.csv"
    part.parent.mkdir(parents=True, exist_ok=True)
    with part.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(done)
    log(f"wrote {len(done)} programs to {part}")
    if not a.no_merge:
        merge()


def merge():
    """Combines data/discovered/*.csv into data/discovered.csv and adds ready programs to schools.csv."""
    from collections import Counter
    done = []
    for f in sorted((ROOT / "data" / "discovered").glob("*.csv")):
        done += list(csv.DictReader(f.open()))
    if not done:
        return
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(done[0].keys())); w.writeheader(); w.writerows(done)
    existing = list(csv.DictReader(SCHOOLS.open()))
    fields = list(existing[0].keys())
    if "roster_path" not in fields:
        fields.append("roster_path")
    have_ids = {r["id"] for r in existing}
    have_unit = {r.get("unitid") for r in existing if r.get("unitid")}
    have_base = {domain(r["athletics_base"]) for r in existing}
    added = 0
    for p in done:
        if p["platform"] not in ("sidearm", "presto") or p.get("lat") in (None, ""):
            continue
        if (p.get("unitid") and str(p["unitid"]) in have_unit) or domain(p["athletics_base"]) in have_base:
            continue
        i = slug(p["short"])
        if i in have_ids:
            i = slug(p["short"] + p["state"])
        if i in have_ids:
            i = slug(p["full"] + p["state"])
        have_ids.add(i); have_base.add(domain(p["athletics_base"])); have_unit.add(str(p.get("unitid") or ""))
        existing.append({"id": i, "name": p.get("sc_name") or p["full"], "short": p["short"], "city": f"{p['city']}, {p['state']}", "div": p["div"], "conf": p["conf"],
                         "lat": round(float(p["lat"]), 3), "lon": round(float(p["lon"]), 3), "platform": p["platform"], "athletics_base": p["athletics_base"],
                         "unitid": p.get("unitid") or "", "roster_path": p["roster_path"]})
        added += 1
    with SCHOOLS.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n"); w.writeheader(); w.writerows(existing)
    summary = {
        "programs": len(done),
        "by_level": Counter(p["div"] for p in done),
        "ready": Counter(p["div"] for p in done if p["platform"] in ("sidearm", "presto")),
        "blocked": Counter(p["div"] for p in done if p["platform"] == "blocked"),
        "not_found": Counter(p["div"] for p in done if not p["platform"]),
        "no_scorecard_match": sum(1 for p in done if not p.get("unitid")),
        "schools_csv_rows": len(existing), "added_this_merge": added,
    }
    (ROOT / "data" / "discovery_summary.json").write_text(json.dumps(summary, indent=1))
    log(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
