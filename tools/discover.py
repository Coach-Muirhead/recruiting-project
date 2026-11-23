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
S = requests.Session(); S.headers["User-Agent"] = UA

LEVELS = {  # Baseball Cube list code -> (division, association label)
    "ncaa-1": ("D1", None), "ncaa-2": ("D2", None), "ncaa-3": ("D3", None),
    "naia": ("NAIA", None), "njcaa": ("JUCO", None), "CCCAA": ("JUCO", "CCCAA"), "nwac": ("JUCO", "NWAC"),
}
STATES = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY", "Puerto Rico": "PR"}
SEASONS = ["2026-27", "2025-26"]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def get(url, **kw):
    try:
        return S.get(url, timeout=kw.pop("timeout", 20), **kw)
    except requests.RequestException as e:
        return None


def norm(s: str) -> str:
    s = s.lower().replace("&", " and ").replace("saint ", "st ").replace("st. ", "st ")
    s = re.sub(r"\bthe\b|\bcommunity college\b|\bcc\b|\bcollege\b|\buniversity\b|\bof\b|[^a-z0-9 ]", " ", s)
    return " ".join(s.split())


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())[:28]


# ------------------------------------------------------------ 1. membership
def baseball_cube(code: str) -> list[dict]:
    url = f"https://thebaseballcube.com/content/schools/{code}/"
    rp = urllib.robotparser.RobotFileParser("https://thebaseballcube.com/robots.txt")
    try:
        rp.read()
        if not rp.can_fetch(UA, url):
            log(f"robots.txt disallows {url}"); return []
    except Exception:
        pass
    r = get(url, timeout=40)
    if not r or r.status_code != 200:
        log(f"list {code}: HTTP {r.status_code if r else 'error'}"); return []
    out = []
    for tr in re.findall(r"<tr class='data-row.*?</tr>", r.text, re.S):
        tds = [re.sub(r"<[^>]+>", "", td).strip() for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(tds) < 6:
            continue
        short, nick, conf, loc, full, year = tds[:6]
        city, _, state = loc.partition(",")
        if not year.isdigit() or int(year) < 2025:
            continue
        out.append({"short": short, "nickname": nick, "conf": conf, "city": city.strip(), "state": STATES.get(state.strip(), state.strip()), "full": full or short})
    log(f"list {code}: {len(out)} active programs"); time.sleep(2)
    return out


# ------------------------------------------------------------ 2. College Scorecard (bulk)
def scorecard_all(key: str) -> list[dict]:
    fields = "id,school.name,school.alias,school.city,school.state,school.school_url,location.lat,location.lon,latest.student.size"
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
def wiki_sites(programs: list[dict]) -> None:
    """Sets p['wiki_site'] from Wikidata P856 of the athletics article, when one exists."""
    titles = {}
    for p in programs:
        q = f'"{p["short"]} {p["nickname"]}"'
        r = get("https://en.wikipedia.org/w/api.php", params={"action": "query", "list": "search", "srsearch": q, "srlimit": 3, "format": "json"})
        hits = [h["title"] for h in (r.json().get("query", {}).get("search", []) if r and r.ok else [])]
        nick = p["nickname"].lower()
        t = next((h for h in hits if nick in h.lower() and "baseball" not in h.lower() and "season" not in h.lower()), None) \
            or next((h.replace(" baseball", "") for h in hits if nick in h.lower() and h.lower().endswith(" baseball")), None)
        if t:
            titles[t] = p
        time.sleep(0.1)
    items = {}
    keys = list(titles)
    for i in range(0, len(keys), 50):
        r = get("https://en.wikipedia.org/w/api.php", params={"action": "query", "prop": "pageprops", "titles": "|".join(keys[i:i + 50]), "redirects": 1, "format": "json", "formatversion": 2})
        if not r or not r.ok:
            continue
        j = r.json()["query"]
        back = {x["to"]: x["from"] for x in j.get("redirects", [])}
        for pg in j.get("pages", []):
            qid = pg.get("pageprops", {}).get("wikibase_item")
            if qid:
                items[qid] = titles.get(back.get(pg["title"], pg["title"])) or titles.get(pg["title"])
    ids = [q for q in items if items[q]]
    for i in range(0, len(ids), 50):
        r = get("https://www.wikidata.org/w/api.php", params={"action": "wbgetentities", "ids": "|".join(ids[i:i + 50]), "props": "claims", "format": "json"})
        if not r or not r.ok:
            continue
        for qid, e in r.json().get("entities", {}).items():
            vals = [c["mainsnak"].get("datavalue", {}).get("value") for c in e.get("claims", {}).get("P856", [])]
            vals = [v for v in vals if isinstance(v, str)]
            if vals and items.get(qid):
                items[qid]["wiki_site"] = vals[-1]
    log(f"wikipedia: athletics site found for {sum(1 for p in programs if p.get('wiki_site'))} of {len(programs)}")


def home_athletics_link(school_url: str) -> str | None:
    if not school_url:
        return None
    base = school_url if school_url.startswith("http") else "https://" + school_url
    r = get(base)
    if not r or r.status_code != 200:
        return None
    for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S | re.I):
        t = re.sub(r"<[^>]+>|\s+", " ", text).strip().lower()
        if t in ("athletics", "athletics website", "intercollegiate athletics", "sports", "varsity athletics") or (t.startswith("athletics") and len(t) < 30):
            return urljoin(r.url, href)
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
    link = home_athletics_link(p.get("school_url") or "")
    if link and domain(link) not in [domain(c) for c in cands]:
        cands.append(link)
    if p.get("school_url"):
        cands.append(p["school_url"])  # some schools host rosters on the main site
    for c in cands:
        res = probe(c)
        if res["platform"] in ("sidearm", "presto"):
            return {**p, **res, "site_source": "wikipedia" if c == p.get("wiki_site") else "homepage" if c == link else "school site"}
        last = res
    return {**p, **(last if cands else {"platform": "", "athletics_base": "", "roster_path": "", "probe": "no candidate site"}), "site_source": ""}


# ------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default=",".join(LEVELS))
    ap.add_argument("--limit", type=int, default=0, help="programs per level, for test runs")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    key = os.environ.get("SCORECARD_API_KEY")
    if not key:
        sys.exit("SCORECARD_API_KEY is required")

    programs = []
    for code in a.levels.split(","):
        div, assoc = LEVELS[code]
        rows = baseball_cube(code)
        if a.limit:
            rows = rows[: a.limit]
        for r in rows:
            r["div"] = div
            r["conf"] = f"{assoc} – {r['conf']}" if assoc and assoc not in r["conf"] else r["conf"]
            programs.append(r)

    sc = scorecard_all(key)
    by_state: dict[str, list] = {}
    for c in sc:
        by_state.setdefault(c.get("school.state"), []).append(c)
    for p in programs:
        c = match(p, by_state)
        if c:
            p.update({"unitid": c["id"], "sc_name": c["school.name"], "lat": c.get("location.lat"), "lon": c.get("location.lon"), "enroll": c.get("latest.student.size"), "school_url": c.get("school.school_url") or ""})
    log(f"scorecard matched {sum(1 for p in programs if p.get('unitid'))} of {len(programs)}")

    wiki_sites(programs)
    with ThreadPoolExecutor(a.workers) as ex:
        done = list(ex.map(resolve, programs))

    # write the full discovery table
    cols = ["div", "conf", "short", "nickname", "full", "city", "state", "unitid", "sc_name", "lat", "lon", "enroll", "school_url", "wiki_site", "site_source", "athletics_base", "platform", "roster_path", "probe"]
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(done)

    # add ready programs to schools.csv
    existing = list(csv.DictReader(SCHOOLS.open()))
    fields = list(existing[0].keys())
    for extra in ("roster_path",):
        if extra not in fields:
            fields.append(extra)
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
        have_ids.add(i); have_base.add(domain(p["athletics_base"]))
        existing.append({"id": i, "name": p.get("sc_name") or p["full"], "short": p["short"], "city": f"{p['city']}, {p['state']}", "div": p["div"], "conf": p["conf"],
                         "lat": round(float(p["lat"]), 3), "lon": round(float(p["lon"]), 3), "platform": p["platform"], "athletics_base": p["athletics_base"],
                         "unitid": p.get("unitid") or "", "roster_path": p["roster_path"]})
        added += 1
    with SCHOOLS.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n"); w.writeheader(); w.writerows(existing)

    from collections import Counter
    summary = {
        "programs": len(done),
        "by_level": Counter(p["div"] for p in done),
        "ready": Counter(p["div"] for p in done if p["platform"] in ("sidearm", "presto")),
        "platform": Counter(p["platform"] or "not found" for p in done),
        "no_scorecard_match": sum(1 for p in done if not p.get("unitid")),
        "added_to_schools_csv": added,
    }
    (ROOT / "data" / "discovery_summary.json").write_text(json.dumps(summary, indent=1))
    log(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
