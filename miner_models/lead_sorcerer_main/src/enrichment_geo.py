"""
Geo normalization and validation helpers for enrichment pipeline.
"""

import json
import re
from pathlib import Path
from typing import Tuple


# ============================================================================
# Load geo data for location validation
# ============================================================================

_geo_path = Path(__file__).parent.parent.parent.parent.parent / "gateway" / "utils" / "geo_lookup_fast.json"
if not _geo_path.exists():
    # Fallback for different working directories
    _geo_path = Path("/root/LeadPoet-custom/gateway/utils/geo_lookup_fast.json")

with open(_geo_path, 'r', encoding='utf-8') as _f:
    _geo_data = json.load(_f)

# Build sets for O(1) validation lookups (all lowercase)
VALID_COUNTRIES = set(_geo_data['countries'])
US_STATES = set(_geo_data['us_states'].keys())
US_CITIES_BY_STATE = {state: set(cities) for state, cities in _geo_data['us_states'].items()}
CITIES_BY_COUNTRY = {country: set(cities) for country, cities in _geo_data['cities'].items()}
STATE_ABBR_TO_NAME = _geo_data['state_abbr']

del _geo_path, _f, _geo_data


# ============================================================================
# Country and state aliases for normalization
# ============================================================================

COUNTRY_ALIASES = {
    # USA variations
    "usa": "united states",
    "us": "united states",
    "u.s.": "united states",
    "u.s.a.": "united states",
    "u.s.a": "united states",
    "america": "united states",
    "united states of america": "united states",
    # UK variations
    "uk": "united kingdom",
    "u.k.": "united kingdom",
    "gb": "united kingdom",
    "great britain": "united kingdom",
    "britain": "united kingdom",
    "england": "united kingdom",
    "scotland": "united kingdom",
    "wales": "united kingdom",
    "northern ireland": "united kingdom",
    # UAE
    "uae": "united arab emirates",
    "u.a.e.": "united arab emirates",
    "emirates": "united arab emirates",
    # Korea
    "korea": "south korea",
    "republic of korea": "south korea",
    "rok": "south korea",
    # Others
    "holland": "netherlands",
    "the netherlands": "netherlands",
    "brasil": "brazil",
    "espana": "spain",
    "espanya": "spain",
    "italia": "italy",
    "czech": "czech republic",
    "czechia": "czech republic",
    "drc": "democratic republic of the congo",
    "dr congo": "democratic republic of the congo",
}

US_STATE_ALIASES = {
    "calif": "california",
    "cali": "california",
    "penn": "pennsylvania",
    "penna": "pennsylvania",
    "tex": "texas",
    "mass": "massachusetts",
    "wash": "washington",
    "mich": "michigan",
    "dc": "district of columbia",
    "d.c.": "district of columbia",
    "d c": "district of columbia",
}


# ============================================================================
# Fake/Hallucinated Address Detection
# ============================================================================

# Common patterns in LLM-hallucinated addresses
FAKE_ADDRESS_PATTERNS = [
    # Placeholder street numbers
    "1234 ",
    "123 main",
    "456 ",
    "789 ",
    "1000 industrial",
    "100 manufacturing",
    # Fake city names
    "anytown",
    "industry city",
    "sample city",
    "example city",
    "test city",
    "your city",
    "city name",
    # Famous placeholder ZIPs
    "90210",  # Beverly Hills 90210 TV show
    "12345",  # Sequential placeholder
    "00000",
    "99999",
    # Generic placeholder terms
    "suite 100,",
    "suite 101,",
    "manufacturing lane",
    "industrial rd",
    "industry ave",
    "business park",
    "corporate drive",
    "enterprise way",
    # Explicit placeholder text
    "not specified",
    "not available",
    "not provided",
    "n/a",
    "tbd",
]

# Known fake/placeholder cities
FAKE_CITIES = {
    "anytown", "industry city", "sample city", "example city",
    "test city", "your city", "placeholder", "n/a", "unknown",
    "city name", "metro area", "not specified", "not available",
    "not provided", "none", "tbd", "to be determined"
}


def is_fake_address(address: str) -> bool:
    """
    Detect if an address appears to be hallucinated/fake.

    Args:
        address: Address string to check

    Returns:
        True if address appears fake/hallucinated
    """
    if not address:
        return False

    addr_lower = address.lower()

    # Check for known fake patterns
    for pattern in FAKE_ADDRESS_PATTERNS:
        if pattern in addr_lower:
            return True

    # Check if too short to be a real address
    # Real addresses typically have at least street number and name
    if len(address.split()) < 3:
        return True

    # Check for patterns like "123 ABC" with no street suffix
    if re.match(r'^\d+\s+[A-Za-z]+\s*$', address):
        return True

    return False


def is_fake_city(city: str) -> bool:
    """Check if city is a placeholder or fake."""
    if not city:
        return False
    return city.lower().strip() in FAKE_CITIES


def parse_location(hq_location: str) -> Tuple[str, str, str]:
    """
    Parse hq_location string into country, state, city.

    Handles formats:
    - "City, State, Country"
    - "City, State"
    - "City, Country"
    - "State, Country" (if no city)
    - "City"

    Args:
        hq_location: Location string from data source

    Returns:
        Tuple of (country, state, city)
    """
    if not hq_location or not isinstance(hq_location, str):
        return ("", "", "")

    # Clean and split
    parts = [p.strip() for p in hq_location.split(',') if p.strip()]

    # Default empty
    country = ""
    state = ""
    city = ""

    # Helper to normalize country
    def normalize_country(c: str) -> str:
        c_lower = c.lower().strip()
        c_lower = COUNTRY_ALIASES.get(c_lower, c_lower)
        return c_lower

    # Helper to normalize state
    def normalize_state(s: str) -> str:
        s_lower = s.lower().strip().replace('.', '')
        s_lower = US_STATE_ALIASES.get(s_lower, s_lower)
        s_lower = STATE_ABBR_TO_NAME.get(s_lower, s_lower)
        return s_lower

    # Process based on number of parts
    if len(parts) == 1:
        # Single part: could be city or country
        part = parts[0]
        part_lower = part.lower()

        # Check if it's a country
        if normalize_country(part) in VALID_COUNTRIES:
            country = normalize_country(part)
        else:
            # Assume it's a city, default to US?
            city = part

    elif len(parts) == 2:
        # Two parts: could be "City, State" or "City, Country"
        first, second = parts

        # Check if second is a country
        second_norm = normalize_country(second)
        if second_norm in VALID_COUNTRIES:
            country = second_norm
            city = first
        else:
            # Assume state, default country to US
            state = normalize_state(second)
            city = first
            country = "united states"

    else:
        # Three or more parts: typically "City, State, Country"
        city = parts[0]
        state = normalize_state(parts[1])
        country = normalize_country(parts[-1])

    # Return normalized values (title case)
    return (_title_case(country), _title_case(state), _title_case(city))


def infer_state_from_city(city: str) -> str:
    """
    Infer US state from a city name when the state is missing.

    Returns a title-cased state name if the city maps uniquely.
    """
    if not city or not isinstance(city, str):
        return ""

    city_lower = city.lower().strip()
    if not city_lower:
        return ""

    # Remove common suffixes
    city_lower = re.sub(r'\b(area|metro|metropolitan|region)\b', '', city_lower).strip()

    # Use first segment if hyphenated (e.g., "Eau Claire-Menomonie Area")
    for sep in ["-", "–", "—"]:
        if sep in city_lower:
            city_lower = city_lower.split(sep)[0].strip()
            break

    city_lower = US_CITY_ALIASES.get(city_lower, city_lower)

    matching_states = []
    for state, cities in US_CITIES_BY_STATE.items():
        if city_lower in cities:
            matching_states.append(state)

    if len(matching_states) == 1:
        return _title_case(matching_states[0])

    return ""


def _title_case(s: str) -> str:
    """Convert to title case, handling special cases."""
    if not s:
        return s

    # Special cases
    special = {
        "district of columbia": "District of Columbia",
        "united states": "United States",
        "united kingdom": "United Kingdom",
        "united arab emirates": "United Arab Emirates",
        "south korea": "South Korea",
        "north korea": "North Korea",
        "new zealand": "New Zealand",
        "south africa": "South Africa",
        "czech republic": "Czech Republic",
        "costa rica": "Costa Rica",
        "puerto rico": "Puerto Rico",
        "el salvador": "El Salvador",
        "sri lanka": "Sri Lanka",
        "hong kong": "Hong Kong",
        "saudi arabia": "Saudi Arabia",
    }

    if s.lower() in special:
        return special[s.lower()]

    return ' '.join(word.capitalize() for word in s.split())


# City aliases for US (matches gateway/utils/geo_normalize.py)
US_CITY_ALIASES = {
    'new york': 'new york city',
    'nyc': 'new york city',
    'la': 'los angeles',
    'sf': 'san francisco',
    'dc': 'washington',
    'washington dc': 'washington',
    'washington d.c.': 'washington',
    'philly': 'philadelphia',
    'vegas': 'las vegas',
    'salt lake': 'salt lake city',
    'st louis': 'st. louis',
    'saint louis': 'st. louis',
    'ft. lauderdale': 'fort lauderdale',
    'ft lauderdale': 'fort lauderdale',
    'ft. worth': 'fort worth',
    'ft worth': 'fort worth',
}

# International city aliases
INTERNATIONAL_CITY_ALIASES = {
    'zurich': 'zürich',
    'munich': 'münchen',
    'cologne': 'köln',
    'copenhagen': 'københavn',
    'bangalore': 'bengaluru',
    'bombay': 'mumbai',
    'gurgaon': 'gurugram',
    'calcutta': 'kolkata',
    'gothenburg': 'göteborg',
    'montreal': 'montréal',
    'sao paulo': 'são paulo',
    'bogota': 'bogotá',
    'medellin': 'medellín',
    'mexico city': 'ciudad de méxico',
    'krakow': 'kraków',
    'kiev': 'kyiv',
}


def validate_location(city: str, state: str, country: str) -> Tuple[bool, str]:
    """
    Validate location against geo_lookup_fast.json data.

    Matches gateway validation exactly to prevent "Invalid Region" rejections.

    Args:
        city: City name
        state: State/province name
        country: Country name

    Returns:
        (is_valid, error_message) - (True, "") if valid, (False, "reason") if invalid
    """
    if not country:
        return False, "country_empty"

    # Normalize country
    country_lower = country.lower().strip()
    country_lower = COUNTRY_ALIASES.get(country_lower, country_lower)

    # Check country is valid
    if country_lower not in VALID_COUNTRIES:
        return False, f"country_invalid: '{country}' not in 199 valid countries"

    # For US: validate state AND city
    if country_lower == 'united states':
        if not city:
            return False, "city_empty"
        if not state:
            return False, "state_empty_for_usa"

        # Normalize state
        state_lower = state.lower().strip().replace('.', '')
        state_lower = US_STATE_ALIASES.get(state_lower, state_lower)
        state_lower = STATE_ABBR_TO_NAME.get(state_lower, state_lower)

        if state_lower not in US_STATES:
            return False, f"state_invalid: '{state}' not a valid US state"

        # Normalize city
        city_lower = city.lower().strip()
        city_lower = US_CITY_ALIASES.get(city_lower, city_lower)

        # Check city exists in state
        state_cities = US_CITIES_BY_STATE.get(state_lower, set())
        if city_lower not in state_cities:
            return False, f"city_invalid: '{city}' not found in {state}"

    # For international: validate city exists in country
    else:
        if not city:
            return False, "city_empty"

        # Normalize city
        city_lower = city.lower().strip()
        city_lower = INTERNATIONAL_CITY_ALIASES.get(city_lower, city_lower)

        # Handle country name -> city lookup key mapping
        country_lookup = country_lower
        if country_lower == 'czech republic':
            country_lookup = 'czechia'

        country_cities = CITIES_BY_COUNTRY.get(country_lookup, set())
        if city_lower not in country_cities:
            return False, f"city_invalid: '{city}' not found in {country}"

    return True, ""


def normalize_location_for_submission(city: str, state: str, country: str) -> Tuple[str, str, str]:
    """
    Normalize location fields to match gateway expectations.

    Applies aliases and proper casing to maximize acceptance rate.

    Args:
        city: Raw city
        state: Raw state
        country: Raw country

    Returns:
        (normalized_city, normalized_state, normalized_country)
    """
    # Normalize country first
    country_lower = country.lower().strip() if country else ""
    country_lower = COUNTRY_ALIASES.get(country_lower, country_lower)

    # Proper case for country
    country_norm = country_lower.title() if country_lower else ""

    # Normalize state
    state_lower = state.lower().strip().replace('.', '') if state else ""
    state_lower = US_STATE_ALIASES.get(state_lower, state_lower)

    # Convert abbreviation to full name
    state_lower = STATE_ABBR_TO_NAME.get(state_lower, state_lower)

    # Proper case for state
    state_norm = state_lower.title() if state_lower else ""

    # Normalize city
    city_lower = city.lower().strip() if city else ""

    # Apply aliases based on country
    if country_lower == 'united states':
        city_lower = US_CITY_ALIASES.get(city_lower, city_lower)
    else:
        city_lower = INTERNATIONAL_CITY_ALIASES.get(city_lower, city_lower)

    # Proper case for city
    city_norm = city_lower.title() if city_lower else ""

    return city_norm, state_norm, country_norm
