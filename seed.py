from db import get_db, init_db

# Real hackathon watchlist. Only fields explicitly known are filled in -
# everything else (registration details, times, descriptions) stays null
# until checked by hand or via a future collector.
HACKATHONS = [
    dict(name="MHacks", organizer="University of Michigan", location="Michigan",
         start_date="2026-10-03", end_date="2026-10-04", date_status="confirmed",
         source_url="https://www.mhacks.org/", travel_required=1),
    dict(name="Knight Hacks IX", organizer="University of Central Florida", location="UCF, Florida",
         start_date="2026-10-09", end_date="2026-10-11", date_status="confirmed",
         source_url="https://club.knighthacks.org/", travel_required=1),
    dict(name="HackHarvard", organizer="Harvard University", location="Cambridge, Massachusetts",
         start_date="2026-10-16", end_date="2026-10-18", date_status="confirmed",
         source_url="https://hhuh.io/", travel_required=1),
    dict(name="DubHacks", organizer="University of Washington", location="Seattle, Washington",
         start_date="2026-10-17", end_date="2026-10-18", date_status="confirmed",
         source_url="https://dh26.dubhacks.co/", travel_required=1),
    dict(name="HackPSU Fall", organizer="Penn State", location="Pennsylvania",
         start_date=None, end_date=None, date_status="tba",
         source_url="https://hackpsu.org/", travel_required=1),
    dict(name="Cal Hacks", organizer="UC Berkeley", location="Berkeley, California",
         start_date=None, end_date=None, date_status="tba",
         source_url="https://www.calhacks.io/", travel_required=1),
    dict(name="HackPrinceton Fall", organizer="Princeton University", location="Princeton, New Jersey",
         start_date="2026-11-06", end_date="2026-11-08", date_status="confirmed",
         source_url="https://www.hackprinceton.com/", travel_required=1),
    dict(name="HackUTD", organizer="UT Dallas", location="Richardson, Texas",
         start_date=None, end_date=None, date_status="tba",
         source_url="https://hackutd.co/", travel_required=1),
    dict(name="sunhacks", organizer="Arizona State University", location="Tempe, Arizona",
         start_date=None, end_date=None, date_status="tba",
         source_url="https://sunhacks.io/", travel_required=0),
    dict(name="Opportunity Hack Arizona", organizer="Opportunity Hack", location="Tempe, Arizona",
         start_date="2026-11-14", end_date="2026-11-15", date_status="confirmed",
         source_url="https://www.ohack.dev/hackathons/arizona", travel_required=0),
    dict(name="HopHacks Fall", organizer="Johns Hopkins University", location="Baltimore, Maryland",
         start_date=None, end_date=None, date_status="tba",
         source_url="https://hophacks.com/", travel_required=1),
    dict(name="HackNYU Fall", organizer="New York University", location="New York, New York",
         start_date=None, end_date=None, date_status="tba",
         source_url="https://hacknyu.org/", travel_required=1),
    dict(name="Hack Western 13", organizer="Western University", location="London, Ontario, Canada",
         start_date="2026-11-20", end_date="2026-11-22", date_status="confirmed",
         source_url="https://www.hackwestern.com/", travel_required=1),
    dict(name="DeltaHacks 13", organizer="McMaster University", location="Hamilton, Ontario, Canada",
         start_date="2027-01-09", end_date="2027-01-10", date_status="confirmed",
         source_url="https://www.deltahacks.com/", travel_required=1),
    dict(name="HackIllinois", organizer="UIUC", location="Urbana-Champaign, Illinois",
         start_date=None, end_date=None, date_status="tba",
         source_url="https://www.hackillinois.org/", travel_required=1),
    dict(name="TreeHacks", organizer="Stanford University", location="Stanford, California",
         start_date="2027-02-14", end_date="2027-02-16", date_status="confirmed",
         source_url="https://www.treehacks.com/", travel_required=1),
    dict(name="HackNYU", organizer="New York University", location="New York, New York",
         start_date=None, end_date=None, date_status="tba",
         source_url="https://hacknyu.org/", travel_required=1),
    dict(name="PennApps", organizer="University of Pennsylvania", location="Philadelphia, Pennsylvania",
         start_date=None, end_date=None, date_status="tba",
         source_url="https://pennapps.com/", travel_required=1),
    dict(name="Bitcamp", organizer="University of Maryland", location="College Park, Maryland",
         start_date=None, end_date=None, date_status="tba",
         source_url="https://bit.camp/", travel_required=1),
    dict(name="WildHacks", organizer="Northwestern University", location="Evanston, Illinois",
         start_date=None, end_date=None, date_status="tba",
         source_url="https://www.wildhacks.net/", travel_required=1),
    dict(name="LA Hacks", organizer="UCLA", location="Los Angeles, California",
         start_date=None, end_date=None, date_status="tba",
         source_url="https://www.lahacks.com/", travel_required=1),
]

DEFAULTS = dict(
    event_type="hackathon",
    description=None,
    location_type=None,
    source=None,
    registration_status="unknown",
    registration_url=None,
    application_deadline=None,
    last_checked=None,
    start_time=None,
    end_time=None,
    timezone=None,
)

# Exact names of the V0 demo/test rows this seed script used to insert.
# Kept here (not in a migrations table - see requirement to avoid a
# migration framework) purely so an old V0 database can be upgraded by
# removing precisely these rows and nothing else the user may have added.
V0_DEMO_EVENT_NAMES = (
    "PyCon Local Meetup",
    "Arch Linux Conf",
    "Home Server Hackathon",
    "Local Farmers Market Festival",
    "Self-Hosting Webinar",
    "Board Game Night",
    "NYC Tech Summit",
    "Winter Robotics Expo",
    "5K Charity Run",
)

INSERT_SQL = """
    INSERT INTO events
        (name, organizer, event_type, description, start_date, end_date, start_time,
         end_time, timezone, date_status, location, location_type, source, source_url,
         registration_status, registration_url, application_deadline, last_checked,
         travel_required)
    VALUES
        (:name, :organizer, :event_type, :description, :start_date, :end_date, :start_time,
         :end_time, :timezone, :date_status, :location, :location_type, :source, :source_url,
         :registration_status, :registration_url, :application_deadline, :last_checked,
         :travel_required)
"""


def _remove_v0_demo_events(conn):
    placeholders = ",".join("?" for _ in V0_DEMO_EVENT_NAMES)
    cur = conn.execute(
        f"DELETE FROM events WHERE name IN ({placeholders})", V0_DEMO_EVENT_NAMES
    )
    return cur.rowcount


def seed():
    init_db()
    conn = get_db()

    removed = _remove_v0_demo_events(conn)
    if removed:
        print(f"Removed {removed} V0 demo/test event(s).")

    for event in HACKATHONS:
        for field, value in DEFAULTS.items():
            event.setdefault(field, value)

    existing_names = {row["name"] for row in conn.execute("SELECT name FROM events")}
    to_insert = [e for e in HACKATHONS if e["name"] not in existing_names]

    if to_insert:
        conn.executemany(INSERT_SQL, to_insert)
        print(f"Inserted {len(to_insert)} new hackathon(s).")
    else:
        print("All hackathons already present, nothing to insert.")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    seed()
