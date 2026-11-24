#!/usr/bin/env python3
"""
Recruiting Project - daily scraper.

Reads schools.csv, visits each program's athletics site, and writes
data/schools.json in the shape the Recruiting Project page reads.

What it collects
  - roster: name, position, class year, hometown   (daily)
  - coaches: name, title, published email          (daily)
  - camps: camp/clinic posts from the team's news feed (daily)
  - tuition + enrollment from the federal College Scorecard API (weekly is plenty)

Platforms handled
  - Sidearm Sports  (most NCAA sites: /sports/baseball/roster, /sports/baseball/coaches)
  - PrestoSports    (many junior colleges / NAIA: /sports/bsb/<season>/roster)
Anything else is logged as "unsupported" so a person can add a parser.

Usage
  python scrape.py                      # all schools
  python scrape.py --only rowan,tcnj    # a subset
  SCORECARD_API_KEY=... python scrape.py --with-costs

Be a good neighbour: one request per page, a pause between schools,
an honest User-Agent, and it respects robots.txt.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
SCHOOLS_CSV = ROOT / "scraper" / "schools.csv"
OUT = ROOT / "data" / "schools.json"
UA = "RecruitingProjectBot/1.0 (+contact: coach email in README)"
PAUSE = 2.0  # seconds between pages of one school
WORKERS = 6
TODAY = dt.date.today()

REPORT: dict[str, dict] = {}
session = requests.Session()
session.headers["User-Agent"] = UA
_robots: dict[str, urllib.robotparser.RobotFileParser] = {}


# ---------------------------------------------------------------- fetching
def allowed(url: str) -> bool:
    host = urlparse(url).scheme + "://" + urlparse(url).netloc
    if host not in _robots:
        rp = urllib.robotparser.RobotFileParser(host + "/robots.txt")
        try:
            rp.read()
        except Exception:
            rp = None
        _robots[host] = rp
    rp = _robots[host]
    return True if rp is None else rp.can_fetch(UA, url)


def get(url: str) -> str | None:
    if not allowed(url):
        log(f"  robots.txt disallows {url}")
        return None
    try:
        r = session.get(url, timeout=25)
        time.sleep(1)  # one school's pages are fetched one at a time, a second apart
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        log(f"  fetch failed {url}: {e}")
        return None


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------- normalising
HEADER_KEYS = {
    "name": re.compile(r"^(full )?name$", re.I),
    "pos": re.compile(r"^pos(ition)?\.?$", re.I),
    "yr": re.compile(r"^(yr|cl|class|year|academic year)\.?$", re.I),
    "home": re.compile(r"^hometown", re.I),
}


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def norm_year(y: str) -> str:
    t = clean(y).lower().replace(".", "")
    t = re.sub(r"^(r-|rs-|redshirt )", "", t)
    if t.startswith(("fr", "fy", "first")):
        return "Fr"
    if t.startswith("so"):
        return "So"
    if t.startswith("jr") or t.startswith("jun"):
        return "Jr"
    if t.startswith("sr") or t.startswith("sen"):
        return "Sr"
    if t.startswith(("gr", "5th", "fifth", "grad")):
        return "Gr"
    return clean(y)


AP_STATES = {"Ala": "AL", "Alaska": "AK", "Ariz": "AZ", "Ark": "AR", "Calif": "CA", "Cal": "CA", "Colo": "CO", "Conn": "CT", "Del": "DE", "D.C": "DC", "Fla": "FL",
             "Ga": "GA", "Hawaii": "HI", "Idaho": "ID", "Ill": "IL", "Ind": "IN", "Iowa": "IA", "Kan": "KS", "Kans": "KS", "Ky": "KY", "La": "LA", "Maine": "ME",
             "Md": "MD", "Mass": "MA", "Mich": "MI", "Minn": "MN", "Miss": "MS", "Mo": "MO", "Mont": "MT", "Neb": "NE", "Nebr": "NE", "Nev": "NV", "N.H": "NH",
             "N.J": "NJ", "N.M": "NM", "N.Y": "NY", "N.C": "NC", "N.D": "ND", "Ohio": "OH", "Okla": "OK", "Ore": "OR", "Oreg": "OR", "Pa": "PA", "Penn": "PA",
             "R.I": "RI", "S.C": "SC", "S.D": "SD", "Tenn": "TN", "Texas": "TX", "Tex": "TX", "Utah": "UT", "Vt": "VT", "Va": "VA", "Wash": "WA", "W.Va": "WV",
             "W. Va": "WV", "Wis": "WI", "Wisc": "WI", "Wyo": "WY"}
FULL_STATES = {"Alabama": "AL", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "Florida": "FL",
               "Georgia": "GA", "Illinois": "IL", "Indiana": "IN", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maryland": "MD", "Massachusetts": "MA",
               "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH",
               "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Oklahoma": "OK", "Oregon": "OR",
               "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Vermont": "VT", "Virginia": "VA",
               "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY"}


STAFF = re.compile(r"\b(director|coordinator|trainer|dietitian|manager|coach|strength|equipment|communications|advisor|phone)\b|@", re.I)


def clean_roster(text: str) -> str:
    """Drops staff rows (no position and no class year, or a staff title) and tidies hometowns."""
    keep = []
    for line in text.splitlines():
        parts = (line.split("|") + ["", "", "", ""])[:4]
        if STAFF.search(parts[0]) or not (parts[1].strip() or parts[2].strip()):
            continue
        parts[3] = split_hometown(parts[3])
        keep.append("|".join(parts))
    return "\n".join(keep)


def split_hometown(h: str) -> str:
    """'Hometown Wall, N.J. / Wall HS'  ->  'Wall, NJ'   (drops the label and the high school part)"""
    h = re.sub(r"^\s*(hometown|home town)\s*:?\s*", "", clean(h), flags=re.I)
    h = re.split(r"\s+/\s*|\s*/\s+|\s*/$", h)[0].strip(" ,/")
    if "," in h:
        city, _, st = h.rpartition(",")
        st = st.strip().rstrip(".")
        code = AP_STATES.get(st) or FULL_STATES.get(st) or (st.upper() if re.fullmatch(r"[A-Za-z]{2}", st) else None)
        if code:
            return f"{city.strip()}, {code}"
    return h


# ---------------------------------------------------------------- Sidearm
def parse_sidearm_roster(html: str) -> tuple[str | None, list[str]]:
    """Returns (season, ['Name|Pos|Yr|Hometown', ...])."""
    soup = BeautifulSoup(html, "html.parser")
    season = None
    m = re.search(r"(20\d\d)(?:-\d\d)? Baseball Roster", soup.get_text(" "))
    if m:
        season = m.group(1)

    rows: list[str] = []
    # 1) table view: map columns by header text so column order never matters
    for table in soup.find_all("table"):
        heads = [clean(th.get_text()) for th in table.find_all("th")]
        idx = {}
        for k, rx in HEADER_KEYS.items():
            for i, h in enumerate(heads):
                if rx.search(h):
                    idx[k] = i
                    break
        if not {"name", "pos", "yr"} <= idx.keys():
            continue
        for tr in table.find_all("tr"):
            cells = [clean(td.get_text(" ")) for td in tr.find_all(["td", "th"])]
            if len(cells) <= max(idx.values()) or cells == heads:
                continue
            name = cells[idx["name"]]
            if not name or HEADER_KEYS["name"].search(name):
                continue
            home = split_hometown(cells[idx["home"]]) if "home" in idx else ""
            rows.append("|".join([name, cells[idx["pos"]], norm_year(cells[idx["yr"]]), home]))
        if rows:
            return season, rows

    # 2) card view (classic Sidearm list items)
    for li in soup.select("li.sidearm-roster-player"):
        def t(sel):
            el = li.select_one(sel)
            return clean(el.get_text(" ")) if el else ""
        name = t(".sidearm-roster-player-name h3") or t(".sidearm-roster-player-name")
        pos = t(".sidearm-roster-player-position-long-short") or t(".sidearm-roster-player-position")
        yr = t(".sidearm-roster-player-academic-year")
        home = split_hometown(t(".sidearm-roster-player-hometown"))
        if name:
            rows.append("|".join([name, pos.split()[0] if pos else "", norm_year(yr), home]))

    # 3) newer Sidearm "s-person-card" layout
    if not rows:
        for card in soup.select(".s-person-card"):
            name = clean((card.select_one(".s-person-details__personal-single-line") or card).get_text(" "))
            bits = [clean(x.get_text(" ")) for x in card.select(".s-person-details__bio-stats-item")]
            pos = next((b.replace("Position", "").strip() for b in bits if b.lower().startswith("position")), "")
            yr = next((b.replace("Academic Year", "").strip() for b in bits if "year" in b.lower()), "")
            home = clean((card.select_one(".s-person-card__content__person__location-item") or BeautifulSoup("", "html.parser")).get_text(" "))
            staff = re.search(r"\b(director|coordinator|trainer|dietitian|manager|coach|strength|equipment|communications|phone)\b|@", name, re.I)
            if name and (pos or yr) and not staff:  # staff cards share this layout but have no position or class year
                rows.append("|".join([name, pos, norm_year(yr), split_hometown(home)]))
    return season, rows


SUPPORT = re.compile(r"trainer|communications|manager|nutrition|psycholog|strength|academic|equipment|video|intern|sport administrator|student-athlete development", re.I)
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def camp_date(title: str, posted: str) -> tuple[str, bool]:
    """Best guess at the camp date from a headline like 'Prospect Clinic on Aug. 29th'.
    Returns (iso date, True) when found in the title, else (posted date, False)."""
    try:
        p = dt.datetime.strptime(" ".join(posted.split()[1:4]), "%d %b %Y").date()
    except Exception:
        p = TODAY
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})", title, re.I)
    if m:
        d = dt.date(p.year, MONTHS[m.group(1).lower()], int(m.group(2)))
        if d < p:
            d = dt.date(p.year + 1, d.month, d.day)
        return d.isoformat(), True
    return p.isoformat(), False


def parse_sidearm_coaches(html: str) -> list[list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for table in soup.find_all("table"):
        first = table.find("tr")
        heads = [clean(c.get_text()).lower() for c in first.find_all(["th", "td"])] if first else []
        if "name" not in heads or "title" not in heads:
            continue
        ni, ti = heads.index("name"), heads.index("title")
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all(["td", "th"])
            if len(cells) != len(heads):
                cells = tr.find_all("td")
            if len(cells) <= max(ni, ti):
                continue
            name, title = clean(cells[ni].get_text(" ")), clean(cells[ti].get_text(" "))
            if not name or "@" in name or SUPPORT.search(title):
                continue
            mail = tr.select_one('a[href^="mailto:"]')
            email = mail["href"][7:].split("?")[0] if mail else ""
            out.append([name, title, email])
        if out:
            break
    return out


def parse_rss_camps(xml: str) -> list[dict]:
    # regex, not an HTML parser: HTML parsers treat <link> as an empty tag and lose the URL
    def tag(block, name):
        m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", block, re.S | re.I)
        return clean(re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))) if m else ""
    camps = []
    for block in re.findall(r"<item\b.*?</item>", xml, re.S | re.I):
        title = tag(block, "title")
        if not re.search(r"\b(camp|clinic|showcase)s?\b", title, re.I):
            continue
        link, pub = tag(block, "link"), tag(block, "pubDate")
        d, exact = camp_date(title, pub)
        info = "Date from the headline; open the post for times and cost" if exact else "Posted on this date; open the post for the camp date"
        camps.append({"n": title.title() if title.isupper() else title, "d": d, "u": link, "info": info})
    return camps


# ---------------------------------------------------------------- PrestoSports
def parse_presto_roster(html: str) -> tuple[str | None, list[str]]:
    # Presto renders a plain <table> with Name / Pos. / Cl. / Hometown headers,
    # so the Sidearm table mapper handles it too.
    return parse_sidearm_roster(html)


# ---------------------------------------------------------------- costs
SC_ALL: dict[str, dict] = {}


def scorecard_bulk(key: str) -> None:
    """Every operating institution in ~70 requests (the per-school lookup would exceed the API's hourly limit)."""
    fields = "id,school.name,latest.cost.tuition.in_state,latest.cost.tuition.out_of_state,latest.student.size"
    page = 0
    while True:
        try:
            j = session.get(f"https://api.data.gov/ed/collegescorecard/v1/schools?school.operating=1&fields={fields}&per_page=100&page={page}&api_key={key}", timeout=60).json()
        except Exception as e:
            log(f"scorecard bulk page {page} failed: {e}"); return
        for r in j.get("results", []):
            SC_ALL[str(r["id"])] = r
        if (page + 1) * 100 >= j.get("metadata", {}).get("total", 0):
            break
        page += 1
    log(f"scorecard: {len(SC_ALL)} institutions")


def scorecard(row: dict, key: str) -> dict:
    """Looks a school up by its federal ID (unitid column) or, if blank, by name and state."""
    row = {**row, "name": row.get("scorecard_name") or row["name"]}  # campuses reported under a parent school
    r = SC_ALL.get(str(row.get("unitid") or ""))
    if r:
        return {"inS": f"${r['latest.cost.tuition.in_state']:,}" if r.get("latest.cost.tuition.in_state") else "",
                "outS": f"${r['latest.cost.tuition.out_of_state']:,}" if r.get("latest.cost.tuition.out_of_state") else "",
                "enroll": r.get("latest.student.size"), "unitid": r["id"]}
    fields = "id,school.name,latest.cost.tuition.in_state,latest.cost.tuition.out_of_state,latest.student.size"
    q = f"id={row['unitid']}" if row.get("unitid") else f"school.name={requests.utils.quote(row['name'])}&school.state={row['city'][-2:]}"
    url = f"https://api.data.gov/ed/collegescorecard/v1/schools?{q}&fields={fields}&per_page=20&api_key={key}"
    try:
        results = session.get(url, timeout=25).json()["results"]
        # name search is fuzzy: insist on the exact school, or skip rather than show another school's numbers
        want = re.sub(r"[^a-z]", "", row["name"].lower())
        r = next(x for x in results if row.get("unitid") or re.sub(r"[^a-z]", "", x["school.name"].lower()) == want)
    except StopIteration:
        log(f"  scorecard: no exact match for {row['name']}")
        return {}
    except Exception as e:
        log(f"  scorecard failed for {row['name']}: {e}")
        return {}
    return {
        "inS": f"${r['latest.cost.tuition.in_state']:,}" if r.get("latest.cost.tuition.in_state") else "",
        "outS": f"${r['latest.cost.tuition.out_of_state']:,}" if r.get("latest.cost.tuition.out_of_state") else "",
        "enroll": r.get("latest.student.size"),
        "unitid": r.get("id"),
    }


# ---------------------------------------------------------------- one school
def scrape_school(row: dict, prev: dict | None, key: str | None) -> dict:
    base = row["athletics_base"].rstrip("/")
    s = dict(prev or {})
    s.update({k: row[k] for k in ("id", "name", "short", "city", "div", "conf")})
    s["lat"], s["lon"] = float(row["lat"]), float(row["lon"])
    s["checked"] = TODAY.isoformat()
    flags = []

    if row["platform"] == "sidearm":
        rp = row.get("roster_path") or "/sports/baseball/roster"
        roster_url = base + rp
        coach_url = base + rp.replace("/roster", "/coaches")
        rss_url = f"{base}/rss.aspx?path=baseball"
        parse_roster = parse_sidearm_roster
    elif row["platform"] == "presto":
        season_slug = row.get("presto_season") or f"{TODAY.year if TODAY.month >= 8 else TODAY.year - 1}-{str((TODAY.year + 1) % 100 if TODAY.month >= 8 else TODAY.year % 100).zfill(2)}"
        roster_url = f"{base}/sports/bsb/{season_slug}/roster"
        coach_url = f"{base}/sports/bsb/coaches/index"
        rss_url = f"{base}/sports/bsb/headlines-featured?feed=rss_2.0"
        parse_roster = parse_presto_roster
    elif row["platform"] == "table":  # any site whose roster is a table with Name / Pos / Yr headers
        roster_url = base + row["roster_path"]
        coach_url = base + row["coaches_path"] if row.get("coaches_path") else ""
        rss_url = ""
        parse_roster = parse_sidearm_roster
    elif row["platform"] == "listing":  # confirmed program whose roster we can't read: directory entry only
        s.update({"roster": "", "coaches": [], "camps": [], "rosterUrl": base, "coachUrl": base, "listing": row.get("note") or "Roster not available here."})
        s["flags"] = [s["listing"] + " Use the link to see them on the school's own site."]
        REPORT.setdefault(row["id"], {})["roster"] = "directory entry (not scraped)"
        return s
    else:
        log(f"  {row['id']}: unsupported platform {row['platform']}")
        s.setdefault("flags", []).append("This site's layout isn't supported by the scraper yet.")
        return s

    s["rosterUrl"], s["coachUrl"] = roster_url, coach_url

    html = get(roster_url)
    if html:
        season, players = parse_roster(html)
        REPORT[row["id"]] = {"roster": len(players), "season": season}
        if players:
            s["roster"], s["season"] = "\n".join(players), season or s.get("season", "")
        else:
            flags.append(f"The {season or 'current'} roster page is up but lists no players yet.")
            s["roster"] = s.get("roster", "") if prev else ""
    else:
        flags.append("The roster page couldn't be reached today; showing the last good copy.")
        REPORT[row["id"]] = {"roster": "unreachable"}

    html = get(coach_url) if coach_url else None
    if html:
        coaches = parse_sidearm_coaches(html)
        REPORT.setdefault(row["id"], {})["coaches"] = len(coaches) or "none parsed, kept last copy"
        if coaches:
            s["coaches"] = coaches

    xml = get(rss_url) if rss_url else None
    if xml:
        found = {c["u"]: c for c in parse_rss_camps(xml)}
        kept = [c for c in s.get("camps", []) if c.get("u") not in found]
        s["camps"] = kept + list(found.values())
    for c in s.get("camps", []):  # older entries saved before camp dates were parsed
        if not c.get("d"):
            c["d"], exact = camp_date(c.get("n", ""), c.pop("posted", ""))
            c["n"] = c["n"].title() if c.get("n", "").isupper() else c.get("n", "")

    if key:
        c = scorecard(row, key)
        REPORT.setdefault(row["id"], {})["costs"] = f"ok (federal id {c['unitid']})" if c.get("enroll") else "no exact match - pin the unitid in schools.csv"
        if c.get("enroll"):
            s["enroll"], s["enrollNote"] = c["enroll"], "undergraduates (College Scorecard)"
        # federal tuition lags a year or two: only fill a gap, never replace a rate read off the school's own page
        t = s.get("tuition") or {}
        federal = "collegescorecard" in t.get("u", "")
        # a federal figure is refreshed (e.g. after a school is re-matched); a rate read off the school's own page is kept
        if c.get("inS") and (not t or t.get("missing") or (federal and str(t.get("unitid", "")) != str(c["unitid"]))):
            s["tuition"] = {"yr": "most recent federal figure", "inS": c["inS"], "inNote": "tuition (College Scorecard; may lag a year)", "outS": c["outS"], "outNote": "tuition (College Scorecard)", "u": "https://collegescorecard.ed.gov/", "unitid": c["unitid"]}

    s["flags"] = flags
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated school ids")
    ap.add_argument("--with-costs", action="store_true", help="refresh tuition/enrollment from College Scorecard")
    args = ap.parse_args()

    prev = {s["id"]: s for s in json.loads(OUT.read_text())} if OUT.exists() else {}
    key = os.environ.get("SCORECARD_API_KEY") if args.with_costs else None
    only = set(args.only.split(",")) if args.only else None

    if key:
        scorecard_bulk(key)
    rows = list(csv.DictReader(SCHOOLS_CSV.open()))
    todo = [r for r in rows if not only or r["id"] in only]

    def one(row):
        log(row["id"])
        res = scrape_school(row, prev.get(row["id"]), key)
        time.sleep(PAUSE)
        return res

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(WORKERS) as ex:  # different schools are different sites; each site still sees one request at a time
        done = {r["id"]: r for r in ex.map(one, todo)}
    out = [done.get(r["id"]) or prev[r["id"]] for r in rows if r["id"] in done or r["id"] in prev]

    for prof in out:  # also cleans copies kept from earlier runs
        prof["roster"] = clean_roster(prof.get("roster") or "")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    (OUT.parent / "report.json").write_text(json.dumps({"date": TODAY.isoformat(), "schools": REPORT}, indent=1))
    log(f"wrote {len(out)} schools to {OUT}")


if __name__ == "__main__":
    main()
