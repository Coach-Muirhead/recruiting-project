"""Finds the download link for the federal Equity in Athletics data files."""
import re, requests
S = requests.Session(); S.headers["User-Agent"] = "RecruitingProjectBot/1.0 (+https://coach-muirhead.github.io/recruiting-project/)"
for u in ["https://ope.ed.gov/athletics/", "https://ope.ed.gov/athletics/api/dataFiles/fileList", "https://ope.ed.gov/athletics/api/dataFiles/list",
          "https://ope.ed.gov/athletics/api/datafiles", "https://ope.ed.gov/athletics/api/dataFile/list"]:
    try:
        r = S.get(u, timeout=30); print("==", u, r.status_code, r.headers.get("content-type")); print(r.text[:1500])
        for js in re.findall(r'src="([^"]+\.js)"', r.text)[:6]:
            j = S.get(requests.compat.urljoin(u, js), timeout=30).text
            print("  js", js, len(j), sorted(set(re.findall(r'["\'](/?api/[A-Za-z/]+)["\']', j)))[:40], sorted(set(re.findall(r'[\w./-]*(?:datafile|dataFile)[\w./?=-]*', j)))[:30])
    except Exception as e:
        print("==", u, "error", e)
