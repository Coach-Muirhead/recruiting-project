#!/usr/bin/env python3
"""Builds the published site from data/schools.json:

  site/index.html          the page (template.html)
  site/data/index.json     one small record per school: what search, matching and My List need
  site/data/s/<id>.json    the full profile (roster, coaches, camps), loaded when a school is opened
"""
import datetime as dt
import json
import re
import shutil
from pathlib import Path

root = Path(__file__).resolve().parent
YRS = ["Fr", "So", "Jr", "Sr", "Gr"]
POS = ["P", "C", "INF", "OF", "UTIL", "NA"]


def pos(p: str) -> str:  # same rules as the page
    t = re.sub(r"[^A-Z0-9 -]", "", p.split("/")[0].upper()).strip()
    if re.search(r"HP\b|PITCH|^[SR]?P$|^(RIGHT|LEFT)[ -]?HAND", t):
        return "P"
    if t == "C" or "CATCH" in t:
        return "C"
    if t in ("CF", "LF", "RF") or t.startswith("OF") or "OUTF" in t:
        return "OF"
    if t.startswith("UT") or t == "DH":
        return "UTIL"
    if t in ("MI", "MIF", "CIF", "1B", "2B", "3B", "SS", "FIRST", "SECOND", "THIRD") or t.startswith(("IF", "INF")) or re.search("INF|SHORT|BASE", t):
        return "INF"
    return "NA"


def yr(y: str) -> str:
    t = re.sub(r"^R-", "", y).replace(".", "").lower()
    for k in ("fr", "so", "jr", "sr"):
        if t.startswith(k):
            return k.capitalize()
    return "Gr"


def summary(s: dict, today: str) -> dict:
    cnt = {k: [0] * 5 for k in POS}
    n = 0
    for line in (s.get("roster") or "").strip().splitlines():
        parts = line.split("|")
        if len(parts) < 3:
            continue
        cnt[pos(parts[1])][YRS.index(yr(parts[2]))] += 1
        n += 1
    coaches = s.get("coaches") or []
    hc = next((c for c in coaches if c[2]), coaches[0] if coaches else None)
    camps = sorted((c for c in s.get("camps") or [] if c.get("d") and (c.get("e") or c["d"]) >= today), key=lambda c: c["d"])
    t = s.get("tuition") or {}
    return {
        "id": s["id"], "name": s["name"], "short": s.get("short", ""), "city": s.get("city", ""), "div": s.get("div", ""), "conf": s.get("conf", ""),
        "lat": s.get("lat"), "lon": s.get("lon"), "enroll": s.get("enroll"), "season": s.get("season", ""), "checked": s.get("checked", ""),
        "ls": 1 if s.get("listing") else 0,
        "n": n, "lv": sum(cnt[k][3] + cnt[k][4] for k in POS), "cnt": cnt, "hc": hc,
        "camp": {k: camps[0][k] for k in ("n", "d", "e") if k in camps[0]} if camps else None,
        "tuition": {k: t[k] for k in ("yr", "inS", "outS") if t.get(k)},
    }


def main() -> None:
    data = json.loads((root / "data" / "schools.json").read_text())
    today = max((s.get("checked") or "" for s in data), default=dt.date.today().isoformat())
    site = root / "site"
    if (site / "data").exists():
        shutil.rmtree(site / "data")
    (site / "data" / "s").mkdir(parents=True)
    index = sorted((summary(s, today) for s in data if s.get("lat") is not None), key=lambda x: x["name"])
    (site / "data" / "index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")))
    for s in data:
        (site / "data" / "s" / f"{s['id']}.json").write_text(json.dumps(s, ensure_ascii=False, separators=(",", ":")))
    (site / "index.html").write_text((root / "template.html").read_text())
    kb = (site / "data" / "index.json").stat().st_size // 1024
    print(f"built site: {len(index)} schools, index {kb} KB")


if __name__ == "__main__":
    main()
