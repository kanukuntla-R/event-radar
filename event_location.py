"""Pure location classifier: turns raw location text (an address, a building
name, "Virtual", or a meeting URL) into a normalized location_type/
location_area/priority. Deterministic keyword rules only - never guesses a
city from an unfamiliar building name with no textual evidence.
"""

ONLINE_KEYWORDS = ("virtual", "online", "webinar", "zoom", "teams.microsoft", "meet.google")
HYBRID_KEYWORDS = ("hybrid",)

# ASU-specific deterministic keyword -> area mapping. "poly"/"polytechnic" is
# ASU's Polytechnic campus, physically in Mesa, AZ - a fixed fact, not a
# guess. Order matters: more specific phrases before their generic substring.
AREA_KEYWORDS = (
    ("tempe", "tempe"),
    ("scottsdale", "scottsdale"),
    ("skysong", "scottsdale"),
    ("chandler", "chandler"),
    ("polytechnic", "mesa"),
    ("poly,", "mesa"),
    ("poly ", "mesa"),
    ("mesa", "mesa"),
    # Thunderbird School of Global Management's ASU campus is physically in
    # downtown Phoenix - a fixed fact, same category as the "poly" mapping.
    ("thunderbird", "phoenix"),
    ("downtown phoenix", "phoenix"),
    ("phoenix", "phoenix"),
)

PRIORITY_BY_AREA = {
    "tempe": "primary", "scottsdale": "primary", "chandler": "primary",
    "online": "primary", "hybrid": "primary",
    "phoenix": "nearby", "mesa": "nearby",
    "other": "other",
}


def classify_location(location_text):
    """Classify raw location text.

    Returns {"location_type", "location_area", "priority"}. Empty/None input
    returns all-None (no evidence at all, distinct from "other" which means
    evidence exists but maps to no known area). A known online/hybrid or
    ASU-area keyword match is used; anything else is "other" in_person -
    never a guessed city.
    """
    text = (location_text or "").strip()
    if not text:
        return {"location_type": None, "location_area": None, "priority": None}

    lowered = text.lower()

    if any(kw in lowered for kw in HYBRID_KEYWORDS):
        return {"location_type": "hybrid", "location_area": "hybrid", "priority": "primary"}

    if any(kw in lowered for kw in ONLINE_KEYWORDS):
        return {"location_type": "online", "location_area": "online", "priority": "primary"}

    for keyword, area in AREA_KEYWORDS:
        if keyword in lowered:
            return {"location_type": "in_person", "location_area": area, "priority": PRIORITY_BY_AREA[area]}

    return {"location_type": "in_person", "location_area": "other", "priority": "other"}
