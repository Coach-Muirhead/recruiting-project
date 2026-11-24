"""Parser checks against small pages shaped like real Sidearm roster/coaches/RSS markup.
Run: python -m pytest -q   (or python test_parse.py)"""
from scrape import clean_roster, camp_date, parse_sidearm_roster, parse_sidearm_coaches, parse_rss_camps, norm_year, split_hometown

ROSTER_TABLE = """
<h2>2026 Baseball Roster</h2>
<table><thead><tr><th>#</th><th>Full Name</th><th>Pos.</th><th>Yr.</th><th>Ht.</th><th>Hometown / High School</th></tr></thead>
<tbody>
<tr><td>14</td><td>Chris Salmon</td><td>C</td><td>So.</td><td>6-1</td><td>Ewing, N.J. / Ewing</td></tr>
<tr><td>20</td><td>Connor Lamanteer</td><td>P</td><td>R-Jr.</td><td>5-10</td><td>Vineland, N.J. / Holy Spirit</td></tr>
<tr><td>9</td><td>Scott Newman</td><td>OF</td><td>5th</td><td>6-0</td><td>Glenview, IL / Glenbrook South</td></tr>
</tbody></table>"""

ROSTER_CARDS = """
<span>2027 Baseball Roster</span>
<li class="sidearm-roster-player"><div class="sidearm-roster-player-name"><h3>Ryan Jaros</h3></div>
<span class="sidearm-roster-player-position">INF</span><span class="sidearm-roster-player-academic-year">Sr.</span>
<span class="sidearm-roster-player-hometown">Cranford, N.J.</span></li>"""

COACHES = """
<table><tr><th>Name</th><th>Title</th><th>Email Address</th></tr>
<tr><th scope="row">Mike Dickson</th><td>Head Coach</td><td><a href="mailto:dickson@rowan.edu">dickson@rowan.edu</a></td></tr>
<tr><td>Chris Rollins</td><td>Assistant Coach</td><td></td></tr>
<tr><td>Adam Pallone</td><td>Senior Athletic Trainer (Baseball)</td><td></td></tr></table>"""

RSS = """<rss><channel>
<item><title>Baseball to Host Prospect Camp</title><link>https://x.edu/news/1</link><pubDate>Tue, 17 Jun 2026</pubDate></item>
<item><title>Baseball wins opener</title><link>https://x.edu/news/2</link></item>
</channel></rss>"""


def test_table_roster():
    season, rows = parse_sidearm_roster(ROSTER_TABLE)
    assert season == "2026"
    assert rows == ["Chris Salmon|C|So|Ewing, NJ", "Connor Lamanteer|P|Jr|Vineland, NJ", "Scott Newman|OF|Gr|Glenview, IL"]


def test_card_roster():
    season, rows = parse_sidearm_roster(ROSTER_CARDS)
    assert season == "2027" and rows == ["Ryan Jaros|INF|Sr|Cranford, NJ"]


def test_coaches():
    assert parse_sidearm_coaches(COACHES) == [["Mike Dickson", "Head Coach", "dickson@rowan.edu"], ["Chris Rollins", "Assistant Coach", ""]]


def test_camps_only():
    camps = parse_rss_camps(RSS)
    assert len(camps) == 1 and camps[0]["u"] == "https://x.edu/news/1"
    assert camp_date("BASEBALL TO HOLD PROSPECT CLINIC ON AUG. 29TH", "Mon, 20 Jul 2026 09:42:00 EST") == ("2026-08-29", True)
    assert camp_date("Winter camp registration open", "Mon, 20 Jul 2026 09:42:00 EST") == ("2026-07-20", False)


def test_normalisers():
    assert [norm_year(x) for x in ["FR", "Fy.", "R-So.", "GRD", "Fifth Year"]] == ["Fr", "Fr", "So", "Gr", "Gr"]
    assert split_hometown("Lansdale, Pa. / La Salle") == "Lansdale, PA"
    assert split_hometown("Hometown Mission Viejo, Calif.") == "Mission Viejo, CA"
    assert split_hometown("Shawnee, OK /") == "Shawnee, OK"
    assert split_hometown("Charleston, W.Va.") == "Charleston, WV"
    assert split_hometown("Daegu, South Korea") == "Daegu, South Korea"


def test_person_cards_skip_staff():
    card = lambda name, stats, home: f'''<div class="s-person-card"><span class="s-person-details__personal-single-line">{name}</span>
      {"".join(f'<span class="s-person-details__bio-stats-item">{x}</span>' for x in stats)}
      <span class="s-person-card__content__person__location-item">{home}</span></div>'''
    html = card("Sam York", ["Position INF", "Academic Year Jr."], "Hometown El Dorado, Ark.") + \
           card("John Dunn Associate Director of Equipment Services /Baseball Phone 292-1679", [], "") + \
           card("Elizabeth Allred Sports Dietitian", [], "")
    _, rows = parse_sidearm_roster(html)
    assert rows == ["Sam York|INF|Jr|El Dorado, AR"], rows



def test_clean_roster():
    kept = "Sam York|INF|Jr|Hometown El Dorado, Ark.\nMark Wasikowski|||\nLindsey Bingham Academic Advisor /Baseball Phone 541|||\nJo Lee||So|Ada, OK"
    assert clean_roster(kept) == "Sam York|INF|Jr|El Dorado, AR\nJo Lee||So|Ada, OK"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
