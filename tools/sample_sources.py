"""Saves raw copies of the membership source pages so the discovery parser can be written against real markup."""
import json, re, sys, time
from pathlib import Path
from urllib.parse import urljoin
import requests

OUT = Path("samples"); OUT.mkdir(exist_ok=True)
S = requests.Session(); S.headers["User-Agent"] = "RecruitingProjectBot/1.0 (discovery sampling)"
log = []

def save(name, url, text=None, status=None):
    if text is None:
        try:
            r = S.get(url, timeout=30); text, status = r.text, r.status_code
        except Exception as e:
            text, status = f"ERROR {e}", 0
    (OUT / name).write_text(text)
    log.append({"name": name, "url": url, "status": status, "bytes": len(text)})
    time.sleep(1)
    return text

W = "https://en.wikipedia.org/w/api.php?action=parse&prop=text&format=json&formatversion=2&page="
for d, page in [("d1", "List_of_NCAA_Division_I_baseball_programs"), ("d2", "List_of_NCAA_Division_II_baseball_programs"), ("d3", "List_of_NCAA_Division_III_baseball_programs"), ("naia", "List_of_NAIA_baseball_programs")]:
    save(f"wiki_{d}.json", W + page)

for org, base in [("naia", "https://www.naia.org"), ("njcaa", "https://www.njcaa.org"), ("cccaa", "https://www.cccaasports.org"), ("nwac", "https://www.nwacsports.org")]:
    for season in ["2025-26", "2026-27"]:
        paths = [f"/sports/bsb/{season}/teams"] if org != "njcaa" else [f"/sports/bsb/{season}/div1/teams", f"/sports/bsb/{season}/div2/teams", f"/sports/bsb/{season}/div3/teams"]
        for p in paths:
            html = save(f"{org}_{season}_{p.strip('/').replace('/', '_')}.html", base + p)
            links = sorted(set(re.findall(r'href="([^"]*/teams/[^"?#]+)"', html)))
            for i, l in enumerate(links[:2]):
                save(f"{org}_{season}_{p.split('/')[-2]}_team{i}.html", urljoin(base, l))

# Wikidata official-website lookups for two athletics articles
q = S.get("https://en.wikipedia.org/w/api.php", params={"action": "query", "prop": "pageprops", "titles": "Rutgers Scarlet Knights|Ramapo Roadrunners|Keystone College", "redirects": 1, "format": "json", "formatversion": 2}, timeout=30).json()
save("wiki_pageprops.json", "pageprops", json.dumps(q), 200)
ids = [p.get("pageprops", {}).get("wikibase_item") for p in q["query"]["pages"] if p.get("pageprops")]
save("wikidata.json", "wbgetentities", S.get("https://www.wikidata.org/w/api.php", params={"action": "wbgetentities", "ids": "|".join(ids), "props": "claims|sitelinks", "format": "json"}, timeout=30).text, 200)
(OUT / "index.json").write_text(json.dumps(log, indent=1))
print(json.dumps(log, indent=1))
