# Recruiting Project

A lookup and match-finder for high school baseball players choosing colleges.
For each program: roster by position and class year, where players are from,
coaches and their published emails, camps on campus, tuition and enrollment.

## Files
| Path | What it is |
|---|---|
| `scraper/schools.csv` | The list of programs to track. Add a row to add a school. |
| `scraper/scrape.py` | Visits each school's athletics site and updates `data/schools.json`. |
| `scraper/test_parse.py` | Checks the page parsers. Runs before every refresh. |
| `data/schools.json` | The current data. Hand-checked values are kept when a site can't be read. |
| `template.html` + `build.py` | Builds `site/index.html`, the page families use. |
| `.github/workflows/daily.yml` | Runs the refresh every morning and publishes the page. |

## Run it daily for free (GitHub)
1. Create a GitHub repository and upload this folder.
2. Settings → Pages → Source: **GitHub Actions**.
3. Optional, for tuition and enrollment: get a free key at https://api.data.gov/signup
   and add it under Settings → Secrets and variables → Actions as `SCORECARD_API_KEY`.
   Costs refresh on Mondays.
4. Actions tab → "Refresh recruiting data" → **Run workflow** once to test.
After that it runs every morning and the page updates itself.

## Run it on your own computer
```
pip install requests beautifulsoup4
python scraper/scrape.py --only rowan,tcnj
python build.py      # then open site/index.html
```

## Adding a school
Add a row to `scraper/schools.csv`. `platform` is `sidearm` for most NCAA sites
(addresses like `/sports/baseball/roster`) or `presto` for most junior colleges
(`/sports/bsb/2025-26/roster`). Anything else gets flagged on its profile until a parser is added.

## House rules the scraper follows
One request per page, a 3-second pause between schools, an honest user agent,
and it skips any page a site's robots.txt disallows. It only records emails a
school publishes on its own staff pages.
