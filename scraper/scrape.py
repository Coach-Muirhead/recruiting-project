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
PAUSE = 3.0  # seconds between schools
TODAY = dt.date.today()

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


def split_hometown(h: str) -> str:
    """'Wall, N.J. / Wall'  ->  'Wall, NJ'   (drops the high school part)"""
    h = clean(h).split(" / ")[0]
    h = re.sub(r"\bN\.?\s?J\.?$", "NJ", h)
    h = re.sub(r"\bN\.?\s?Y\.?$", "NY", h)
    h = re.sub(r"\bPa\.?$", "PA", h)
    h = re.sub(r"\bConn\.?$", "CT", h)
    h = re.sub(r"\bMass\.?$", "MA", h)
    h = re.sub(r"\bMd\.?$", "MD", h)
    h = re.sub(r"\bDel\.?$", "DE", h)
    h = re.sub(r"\bVa\.?$", "VA", h)
    h = re.sub(r"\bFla\.?$", "FL", h)
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
            if name:
                rows.append("|".join([name, pos, norm_year(yr), split_hometown(home)]))
    return season, rows


def parse_sidearm_coaches(html: str) -> list[list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for table in soup.find_all("table"):
        heads = [clean(th.get_text()).lower() for th in table.find_all("th")]
        if "name" not in heads or "title" not in heads:
            continue
        ni, ti = heads.index("name"), heads.index("title")
        for tr in table.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if len(tds) <= max(ni, ti):
                # some sites put the photo in a <th>; fall back to the row text
                continue
            mail = tr.select_one('a[href^="mailto:"]')
            email = mail["href"][7:].split("?")[0] if mail else ""
            out.append([clean(tds[ni].get_text(" ")), clean(tds[ti].get_text(" ")), email])
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
        camps.append({"n": title, "posted": pub, "u": link, "info": "From the team's news feed; open the post for dates and cost"})
    return camps


# ---------------------------------------------------------------- PrestoSports
def parse_presto_roster(html: str) -> tuple[str | None, list[str]]:
    # Presto renders a plain <table> with Name / Pos. / Cl. / Hometown headers,
    # so the Sidearm table mapper handles it too.
    return parse_sidearm_roster(html)


# ---------------------------------------------------------------- costs
def scorecard(row: dict, key: str) -> dict:
    """Looks a school up by its federal ID (unitid column) or, if blank, by name and state."""
    fields = "id,school.name,latest.cost.tuition.in_state,latest.cost.tuition.out_of_state,latest.student.size"
    q = f"id={row['unitid']}" if row.get("unitid") else f"school.name={requests.utils.quote(row['name'])}&school.state={row['city'][-2:]}"
    url = f"https://api.data.gov/ed/collegescorecard/v1/schools?{q}&fields={fields}&api_key={key}"
    try:
        r = session.get(url, timeout=25).json()["results"][0]
    except Exception as e:
        log(f"  scorecard failed for {row['name']}: {e}")
        return {}
    return {
        "inS": f"${r['latest.cost.tuition.in_state']:,}" if r.get("latest.cost.tuition.in_state") else "",
        "outS": f"${r['latest.cost.tuition.out_of_state']:,}" if r.get("latest.cost.tuition.out_of_state") else "",
        "enroll": r.get("latest.student.size"),
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
        roster_url = f"{base}/sports/baseball/roster"
        coach_url = f"{base}/sports/baseball/coaches"
        rss_url = f"{base}/rss.aspx?path=baseball"
        parse_roster = parse_sidearm_roster
    elif row["platform"] == "presto":
        season_slug = row.get("presto_season") or f"{TODAY.year if TODAY.month >= 8 else TODAY.year - 1}-{str((TODAY.year + 1) % 100 if TODAY.month >= 8 else TODAY.year % 100).zfill(2)}"
        roster_url = f"{base}/sports/bsb/{season_slug}/roster"
        coach_url = f"{base}/sports/bsb/coaches/index"
        rss_url = f"{base}/sports/bsb/headlines-featured?feed=rss_2.0"
        parse_roster = parse_presto_roster
    else:
        log(f"  {row['id']}: unsupported platform {row['platform']}")
        s.setdefault("flags", []).append("This site's layout isn't supported by the scraper yet.")
        return s

    s["rosterUrl"], s["coachUrl"] = roster_url, coach_url

    html = get(roster_url)
    if html:
        season, players = parse_roster(html)
        if players:
            s["roster"], s["season"] = "\n".join(players), season or s.get("season", "")
        else:
            flags.append(f"The {season or 'current'} roster page is up but lists no players yet.")
            s["roster"] = s.get("roster", "") if prev else ""
    else:
        flags.append("The roster page couldn't be reached today; showing the last good copy.")

    html = get(coach_url)
    if html:
        coaches = parse_sidearm_coaches(html)
        if coaches:
            s["coaches"] = coaches

    xml = get(rss_url)
    if xml:
        found = parse_rss_camps(xml)
        known = {c.get("u") for c in s.get("camps", [])}
        s["camps"] = s.get("camps", []) + [c for c in found if c["u"] not in known]

    if key:
        c = scorecard(row, key)
        if c.get("enroll"):
            s["enroll"], s["enrollNote"] = c["enroll"], "undergraduates (College Scorecard)"
        if c.get("inS"):
            s["tuition"] = {"yr": "latest", "inS": c["inS"], "inNote": "tuition", "outS": c["outS"], "outNote": "tuition", "u": "https://collegescorecard.ed.gov/"}

    if s.get("season") and int(s["season"]) < (TODAY.year + 1 if TODAY.month >= 8 else TODAY.year):
        flags.append(f"{int(s['season']) + 1} roster not posted yet. Seniors and grads shown have played their final season.")
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

    out = []
    with SCHOOLS_CSV.open() as f:
        for row in csv.DictReader(f):
            if only and row["id"] not in only:
                if row["id"] in prev:
                    out.append(prev[row["id"]])
                continue
            log(f"{row['id']}")
            out.append(scrape_school(row, prev.get(row["id"]), key))
            time.sleep(PAUSE)

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    log(f"wrote {len(out)} schools to {OUT}")


if __name__ == "__main__":
    main()
