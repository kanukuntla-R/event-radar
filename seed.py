from db import get_db, init_db

SEED_EVENTS = [
    dict(
        name="PyCon Local Meetup",
        event_type="meetup",
        description="Monthly Python meetup with lightning talks.",
        start_date="2026-09-18",
        end_date=None,
        date_status="confirmed",
        location="Downtown Library, Community Room",
        location_type="physical",
        source="meetup.com",
        source_url="https://meetup.com/example/pycon-local",
    ),
    dict(
        name="Arch Linux Conf",
        event_type="conference",
        description="Annual Arch Linux community conference.",
        start_date="2026-10-02",
        end_date="2026-10-04",
        date_status="confirmed",
        location="Berlin, Germany",
        location_type="physical",
        source="archlinux.org",
        source_url="https://archlinux.org/events/",
    ),
    dict(
        name="Home Server Hackathon",
        event_type="hackathon",
        description="Weekend hackathon for self-hosted tooling.",
        start_date=None,
        end_date=None,
        date_status="tba",
        location="Online",
        location_type="virtual",
        source="hackathon.io",
        source_url="https://hackathon.io/example",
    ),
    dict(
        name="Local Farmers Market Festival",
        event_type="festival",
        description="Seasonal festival with food trucks and live music.",
        start_date="2026-09-27",
        end_date="2026-09-27",
        date_status="confirmed",
        location="City Park",
        location_type="physical",
        source="citycalendar.gov",
        source_url="https://citycalendar.gov/events/festival",
    ),
    dict(
        name="Self-Hosting Webinar",
        event_type="webinar",
        description="Live webinar on self-hosted alternatives to SaaS.",
        start_date="2026-09-10",
        end_date=None,
        date_status="confirmed",
        location="Online",
        location_type="virtual",
        source="youtube.com",
        source_url="https://youtube.com/watch?v=example",
    ),
    dict(
        name="Board Game Night",
        event_type="social",
        description="Casual board game night at the local cafe.",
        start_date="2026-09-23",
        end_date=None,
        start_time="18:00",
        end_time="20:00",
        timezone="America/Phoenix",
        date_status="confirmed",
        location="Corner Cafe",
        location_type="physical",
        source="facebook.com",
        source_url="https://facebook.com/events/example",
    ),
    dict(
        name="NYC Tech Summit",
        event_type="conference",
        description="Travel conference - runs on Eastern time, not local Arizona time.",
        start_date="2026-11-05",
        end_date=None,
        start_time="09:00",
        end_time="17:00",
        timezone="America/New_York",
        date_status="confirmed",
        location="New York, NY",
        location_type="physical",
        source="techsummit.example.com",
        source_url="https://techsummit.example.com",
    ),
    dict(
        name="Winter Robotics Expo",
        event_type="expo",
        description="Robotics and automation showcase, exact dates pending venue confirmation.",
        start_date=None,
        end_date=None,
        date_status="tba",
        location="Convention Center",
        location_type="physical",
        source="expo.example.com",
        source_url="https://expo.example.com/robotics",
    ),
    dict(
        name="5K Charity Run",
        event_type="sports",
        description="Annual charity run through downtown.",
        start_date="2026-10-11",
        end_date=None,
        date_status="confirmed",
        location="Downtown Riverside Path",
        location_type="physical",
        source="runsignup.com",
        source_url="https://runsignup.com/example",
    ),
]


OPTIONAL_FIELDS = ("start_time", "end_time", "timezone")


def seed():
    init_db()
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    if count > 0:
        print(f"Events table already has {count} rows, skipping seed.")
        conn.close()
        return

    for event in SEED_EVENTS:
        for field in OPTIONAL_FIELDS:
            event.setdefault(field, None)

    conn.executemany(
        """
        INSERT INTO events
            (name, event_type, description, start_date, end_date, start_time,
             end_time, timezone, date_status, location, location_type, source, source_url)
        VALUES
            (:name, :event_type, :description, :start_date, :end_date, :start_time,
             :end_time, :timezone, :date_status, :location, :location_type, :source, :source_url)
        """,
        SEED_EVENTS,
    )
    conn.commit()
    conn.close()
    print(f"Seeded {len(SEED_EVENTS)} events.")


if __name__ == "__main__":
    seed()
