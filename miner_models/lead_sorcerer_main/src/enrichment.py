"""
Lead Enrichment Module
======================

Provides enrichment functions for Lead Sorcerer to produce valid, submittable leads
with all 16 required fields.

Key Functions:
- parse_location: Parse hq_location into country/state/city
- normalize_employee_count: Convert to LinkedIn format (e.g., "51-200")
- normalize_linkedin_url: Normalize LinkedIn URLs to gateway format
- search_company_linkedin_with_data: GSE search with snippet data extraction
- search_person_linkedin_with_data: GSE search with snippet data extraction
- search_linkedin_executives: Find company executives via GSE
- search_linkedin_decision_makers: LinkedIn-first search for decision makers by query
- find_company_domain: Find company domain from company name
- generate_linkedin_queries_with_llm: Use OpenRouter to generate queries from ICP
- firecrawl_extract_company: Fallback extraction from company website
- is_lead_complete: Check if all required fields are present
- get_missing_fields: Get list of missing required fields
- generate_email_patterns: Generate email pattern variants
- get_verified_email: Generate patterns and validate via TrueList
- validate_lead_pre_submission: Pre-submission validation to reduce rejections
- validate_source_url: Validate source URL and type against restrictions
- normalize_location_for_submission: Normalize city/state/country
- clean_role_for_submission: Clean role field
"""

import re
import json
import os
import asyncio
from pathlib import Path
from typing import Tuple, List, Dict, Any, Optional
from urllib.parse import urlparse, unquote


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
# Valid employee count ranges (exact LinkedIn format)
# ============================================================================

# Numeric boundaries for mapping: (min, max, display_string)
EMPLOYEE_RANGE_MAP = [
    (0, 1, "0-1"),
    (2, 10, "2-10"),
    (11, 50, "11-50"),
    (51, 200, "51-200"),
    (201, 500, "201-500"),
    (501, 1000, "501-1,000"),
    (1001, 5000, "1,001-5,000"),
    (5001, 10000, "5,001-10,000"),
    (10001, float('inf'), "10,001+"),
]

# Derived from EMPLOYEE_RANGE_MAP for validation
VALID_EMPLOYEE_RANGES = [r[2] for r in EMPLOYEE_RANGE_MAP]


# ============================================================================
# Required fields for lead validation
# ============================================================================

REQUIRED_FIELDS = [
    "business", "full_name", "first", "last", "email", "role",
    "website", "industry", "sub_industry", "country", "city",
    "linkedin", "company_linkedin", "source_url", "description", "employee_count"
]


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

    # Check for fake cities
    for fake_city in FAKE_CITIES:
        if fake_city in addr_lower:
            return True

    return False


def is_fake_city(city: str) -> bool:
    """Check if a city name appears fake."""
    if not city:
        return False
    return city.lower().strip() in FAKE_CITIES


# ============================================================================
# Location Parser
# ============================================================================

def parse_location(hq_location: str) -> Tuple[str, str, str]:
    """
    Parse hq_location string into (country, state, city).

    Handles formats like:
    - "San Francisco, California, United States"
    - "London, United Kingdom"
    - "New York, NY, USA"
    - "123 Main St, Pomona, CA 91768" (full US street address)

    Returns:
        Tuple of (country, state, city) with proper title case.
        Returns ("", "", "") if cannot parse or validate.
    """
    if not hq_location or not isinstance(hq_location, str):
        return ("", "", "")

    # First, try to extract US address pattern: "City, ST 12345" or "City, ST"
    us_addr_match = re.search(
        r'([A-Za-z][A-Za-z\s]+),\s*([A-Z]{2})\s*(\d{5}(?:-\d{4})?)?',
        hq_location
    )
    if us_addr_match:
        city = us_addr_match.group(1).strip()
        state_abbr = us_addr_match.group(2).upper()

        # Convert state abbreviation to full name
        state_full = STATE_ABBR_TO_NAME.get(state_abbr.lower(), "")
        if state_full:
            return ("United States", _title_case(state_full), _title_case(city))

    # Split by comma and clean up
    parts = [p.strip() for p in hq_location.split(",") if p.strip()]

    if len(parts) < 2:
        return ("", "", "")

    # Work backwards: last part is likely country
    raw_country = parts[-1].lower()

    # Apply country aliases
    country = COUNTRY_ALIASES.get(raw_country, raw_country)

    # Validate country
    if country not in VALID_COUNTRIES:
        return ("", "", "")

    # Title case the country
    country_title = _title_case(country)

    # Handle based on number of parts
    if len(parts) == 2:
        # city, country (non-US or country without state)
        raw_city = parts[0].lower()

        # Validate city exists in country
        if country in CITIES_BY_COUNTRY and raw_city in CITIES_BY_COUNTRY[country]:
            return (country_title, "", _title_case(raw_city))

        return ("", "", "")

    elif len(parts) >= 3:
        # city, state, country (US) or city, region, country (other)
        raw_city = parts[0].lower()
        raw_state = parts[-2].lower()

        if country == "united states":
            # Handle US state abbreviations (dict keys are lowercase)
            if raw_state.lower() in STATE_ABBR_TO_NAME:
                raw_state = STATE_ABBR_TO_NAME[raw_state.lower()]

            # Handle state aliases
            raw_state = US_STATE_ALIASES.get(raw_state, raw_state)

            # Validate state
            if raw_state not in US_STATES:
                return ("", "", "")

            # Validate city in state
            if raw_city in US_CITIES_BY_STATE.get(raw_state, set()):
                return (country_title, _title_case(raw_state), _title_case(raw_city))

            # City not in our database - still return with country/state
            # Gateway may have more comprehensive city list
            return (country_title, _title_case(raw_state), _title_case(raw_city))

        else:
            # Non-US: ignore middle part (region/province), just use city/country
            if country in CITIES_BY_COUNTRY and raw_city in CITIES_BY_COUNTRY[country]:
                return (country_title, "", _title_case(raw_city))

            # City not in database - still return
            return (country_title, "", _title_case(raw_city))

    return ("", "", "")


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


# ============================================================================
# Employee Count Normalizer
# ============================================================================

def normalize_employee_count(raw: str) -> str:
    """
    Normalize employee count to exact LinkedIn format.

    Handles formats like:
    - "50 employees"
    - "100-200"
    - "~500"
    - "1000+"
    - "1,500"

    Returns:
        Exact LinkedIn format like "51-200" or "" if cannot normalize.
    """
    if not raw or not isinstance(raw, str):
        return ""

    # If already in valid format, return as-is
    if raw in VALID_EMPLOYEE_RANGES:
        return raw

    # Clean up the string
    raw = raw.lower().strip()

    # Remove common words
    raw = re.sub(r'\b(employees?|people|staff|team members?|workers?|approximately|about|around|over|under|less than|more than)\b', '', raw, flags=re.IGNORECASE)

    # Remove special characters except digits, commas, dashes, plus
    raw = re.sub(r'[~≈]', '', raw)

    # Extract numbers
    numbers = re.findall(r'[\d,]+', raw)

    if not numbers:
        return ""

    # Parse numbers (remove commas)
    parsed_nums = []
    for n in numbers:
        try:
            parsed_nums.append(int(n.replace(',', '')))
        except ValueError:
            continue

    if not parsed_nums:
        return ""

    # Check for plus sign (1000+)
    has_plus = '+' in raw

    # Use the appropriate number
    if len(parsed_nums) == 1:
        num = parsed_nums[0]
        if has_plus:
            # "1000+" means 1000 or more (bias upward to next range)
            if num >= 10000:
                num = max(num, 10001)
            elif num >= 1000:
                num = max(num, 1001)
    else:
        # Range like "100-200" - use midpoint or max
        num = max(parsed_nums)

    # Map to range
    for low, high, range_str in EMPLOYEE_RANGE_MAP:
        if low <= num <= high:
            return range_str

    return ""


# ============================================================================
# LinkedIn URL Normalizer
# ============================================================================

def normalize_linkedin_url(url: str, url_type: str) -> str:
    """
    Normalize LinkedIn URL to gateway format.

    Args:
        url: Raw LinkedIn URL
        url_type: "company" or "profile"

    Returns:
        Normalized URL like "https://linkedin.com/company/{slug}" or ""
    """
    if not url or not isinstance(url, str):
        return ""

    url = url.strip()

    # Parse the URL
    try:
        # Add scheme if missing
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        parsed = urlparse(url)

        # Must be LinkedIn domain
        if 'linkedin.com' not in parsed.netloc.lower():
            return ""

        # Get the path and clean it (strip query params and fragments)
        path = unquote(parsed.path).strip('/')

        # Extract slug based on type
        if url_type == "company":
            # Match /company/{slug}
            match = re.match(r'^company/([^/?\s]+)', path)
            if match:
                slug = match.group(1).lower()
                return f"https://linkedin.com/company/{slug}"

        elif url_type == "profile":
            # Match /in/{slug}
            match = re.match(r'^in/([^/?\s]+)', path)
            if match:
                slug = match.group(1).lower()
                return f"https://linkedin.com/in/{slug}"

        return ""

    except Exception:
        return ""


# ============================================================================
# LinkedIn Search via ScrapingDog Google Search API
# ============================================================================

SCRAPINGDOG_GOOGLE_URL = "https://api.scrapingdog.com/google"


async def _scrapingdog_google_search(query: str, num_results: int = 5) -> List[Dict[str, Any]]:
    """
    Perform Google search using ScrapingDog API.

    Args:
        query: Search query (supports site: operator)
        num_results: Number of results to return

    Returns:
        List of search result dicts with 'title', 'link', 'snippet'
    """
    import httpx

    api_key = os.getenv("SCRAPINGDOG_API_KEY")
    if not api_key:
        # Fallback to GSE if ScrapingDog not configured
        return await _gse_google_search(query, num_results)

    try:
        params = {
            "api_key": api_key,
            "query": query,
            "results": num_results,
            "country": "us",
            "page": 0,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(SCRAPINGDOG_GOOGLE_URL, params=params)

            if response.status_code != 200:
                print(f"   ⚠️ ScrapingDog Google search returned {response.status_code}")
                return []

            data = response.json()

        # Extract organic results
        organic = data.get("organic_data", []) or data.get("organic_results", []) or []
        results = []
        for item in organic[:num_results]:
            results.append({
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            })

        return results

    except Exception as e:
        print(f"   ⚠️ ScrapingDog Google search failed: {e}")
        return []


async def _gse_google_search(query: str, num_results: int = 5) -> List[Dict[str, Any]]:
    """
    Fallback: Perform Google search using GSE API (if configured).

    Args:
        query: Search query
        num_results: Number of results to return

    Returns:
        List of search result dicts with 'title', 'link', 'snippet'
    """
    import httpx

    gse_api_key = os.getenv("GSE_API_KEY")
    gse_cx = os.getenv("GSE_CX")

    if not gse_api_key or not gse_cx:
        return []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"key": gse_api_key, "cx": gse_cx, "q": query, "num": num_results}
            )

            if response.status_code != 200:
                return []

            data = response.json()

        items = data.get('items', [])
        results = []
        for item in items[:num_results]:
            results.append({
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            })

        return results

    except Exception as e:
        print(f"   ⚠️ GSE search failed: {e}")
        return []


async def search_company_linkedin_with_data(company_name: str) -> Dict[str, Any]:
    """
    Search Google for company LinkedIn page and extract data from snippet.

    This uses GSE to find LinkedIn pages and extracts data from Google's
    search result snippets - NO direct LinkedIn scraping required.

    Typical LinkedIn company snippet format:
    "Company Name | X followers on LinkedIn. Description... | Industry"

    Args:
        company_name: Company name to search

    Returns:
        Dict with:
        - linkedin_url: Normalized LinkedIn URL
        - company_name: Company name from snippet
        - description: Description from snippet
        - follower_count: Follower count if found
        - industry: Industry if found
        - employee_count: Employee count if found in snippet
    """
    if not company_name:
        return {}

    try:
        query = f'site:linkedin.com/company/ "{company_name}"'
        results = await _scrapingdog_google_search(query, num_results=3)

        for item in results:
            link = item.get('link', '')
            title = item.get('title', '')
            snippet = item.get('snippet', '')

            if 'linkedin.com/company/' not in link.lower():
                continue

            normalized = normalize_linkedin_url(link, "company")
            if not normalized:
                continue

            # Verify company name appears in slug
            slug = normalized.split('/company/')[-1].lower()
            company_words = company_name.lower().split()
            if not any(word in slug for word in company_words if len(word) > 2):
                # Skip if no match, unless it's first result
                if results.index(item) > 0:
                    continue

            # Extract data from snippet
            data = {
                "linkedin_url": normalized,
                "company_name": "",
                "description": "",
                "follower_count": "",
                "industry": "",
                "employee_count": "",
            }

            # Parse title: "Company Name | LinkedIn" or "Company Name - Overview | LinkedIn"
            if title:
                title_clean = re.sub(r'\s*[\|–-]\s*(LinkedIn|Overview).*$', '', title, flags=re.IGNORECASE).strip()
                if title_clean:
                    data["company_name"] = title_clean

            # Parse snippet for structured data
            if snippet:
                # Store full snippet as description (first 200 chars)
                data["description"] = snippet[:200].strip()

                # Look for follower count: "X followers on LinkedIn"
                follower_match = re.search(r'([\d,]+)\s*followers?\s+on\s+LinkedIn', snippet, re.IGNORECASE)
                if follower_match:
                    data["follower_count"] = follower_match.group(1).replace(',', '')

                # Look for employee count patterns
                emp_patterns = [
                    r'([\d,]+)\s*employees?',
                    r'(\d+[-–]\d+)\s*employees?',
                    r'Company size[:\s]*([\d,\-–]+)',
                ]
                for pattern in emp_patterns:
                    emp_match = re.search(pattern, snippet, re.IGNORECASE)
                    if emp_match:
                        raw_count = emp_match.group(1).replace(',', '')
                        data["employee_count"] = normalize_employee_count(raw_count)
                        break

                # Look for industry (often after pipe or at end)
                # Format: "... | Industry | Location"
                parts = re.split(r'\s*\|\s*', snippet)
                for part in parts[1:]:  # Skip first part (usually description)
                    part = part.strip()
                    # Skip if it's a location (contains city/state patterns)
                    if re.search(r'\b(CA|NY|TX|FL|IL|Area|City|State)\b', part):
                        continue
                    # Skip if it contains "followers" or "employees"
                    if re.search(r'(followers?|employees?|LinkedIn)', part, re.IGNORECASE):
                        continue
                    # Likely industry
                    if len(part) > 3 and len(part) < 50:
                        data["industry"] = part
                        break

            # If we found a LinkedIn URL but no employee_count, try a targeted search
            if data.get("linkedin_url") and not data.get("employee_count"):
                emp_data = await _search_company_employee_count(company_name)
                if emp_data:
                    data["employee_count"] = emp_data

            return data

        return {}

    except Exception as e:
        print(f"⚠️ LinkedIn company search failed for {company_name}: {e}")
        return {}


async def _search_company_employee_count(company_name: str) -> str:
    """
    Secondary search specifically for company employee count.

    Tries multiple search strategies to find employee count from various sources.
    """
    if not company_name:
        return ""

    search_queries = [
        f'"{company_name}" employees site:linkedin.com',
        f'"{company_name}" "company size" employees',
        f'"{company_name}" number of employees',
    ]

    for query in search_queries:
        try:
            results = await _scrapingdog_google_search(query, num_results=3)

            for item in results:
                snippet = item.get('snippet', '')
                if not snippet:
                    continue

                # Look for employee count patterns
                emp_patterns = [
                    r'(\d{1,3}(?:,\d{3})*)\s*employees?',  # 1,000 employees
                    r'(\d+[-–]\d+)\s*employees?',  # 51-200 employees
                    r'(\d+\s*to\s*\d+)\s*employees?',  # 51 to 200 employees
                    r'Company size[:\s]*([\d,\-–\s]+)',
                    r'(\d+[-–]\d+)\s*on LinkedIn',
                    r'has\s+(\d+[-–]\d+)\s+employees',
                    r'employs?\s+(\d+[-–,\d]+)',
                ]

                for pattern in emp_patterns:
                    emp_match = re.search(pattern, snippet, re.IGNORECASE)
                    if emp_match:
                        raw_count = emp_match.group(1).replace(',', '').replace(' to ', '-')
                        normalized = normalize_employee_count(raw_count)
                        if normalized:
                            return normalized

        except Exception:
            continue

    return ""


async def search_company_location(company_name: str, domain: str = "") -> Dict[str, str]:
    """
    Search Google for company physical address/location.

    This is a fallback when Firecrawl fails to extract location from the website.
    Searches Google for company address and parses results.

    Args:
        company_name: Company name to search
        domain: Company domain for more accurate results (optional)

    Returns:
        Dict with: city, state, country, hq_location (full address)
    """
    if not company_name:
        return {}

    # US state abbreviations for validation
    US_STATES = {
        'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
        'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
        'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
        'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
        'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'
    }

    # Multiple search queries to try
    search_queries = []
    if domain:
        search_queries.append(f'"{company_name}" address location site:{domain}')
        search_queries.append(f'"{company_name}" headquarters address')
    search_queries.append(f'"{company_name}" company address')
    search_queries.append(f'"{company_name}" location headquarters')

    for query in search_queries:
        try:
            results = await _scrapingdog_google_search(query, num_results=5)

            for item in results:
                snippet = item.get('snippet', '')
                title = item.get('title', '')
                combined = f"{title} {snippet}"

                if not combined:
                    continue

                # Pattern 1: Full address with ZIP - "123 Main St, City, ST 12345"
                addr_match = re.search(
                    r'(\d+\s+[A-Za-z0-9\s\.]+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Court|Ct|Place|Pl|Highway|Hwy|Parkway|Pkwy)[\s,\-]+[A-Za-z\s]+[,\s]+([A-Z]{2})\s+(\d{5})(?:-\d{4})?)',
                    combined, re.IGNORECASE
                )
                if addr_match:
                    full_addr = addr_match.group(1).strip()
                    state = addr_match.group(2).upper()
                    zip_code = addr_match.group(3)

                    if state in US_STATES and not is_fake_address(full_addr):
                        # Extract city from address
                        # Format usually: "Street, City, ST ZIP"
                        parts = re.split(r'[,\-]', full_addr)
                        city = ""
                        for part in reversed(parts[:-1]):  # Skip last part (state+zip)
                            part = part.strip()
                            # City is usually before state
                            if part and not re.match(r'^\d', part) and len(part) > 2:
                                city = part.title()
                                break

                        print(f"   📍 GSE found address: {full_addr}")
                        return {
                            "hq_location": full_addr,
                            "city": city,
                            "state": state,
                            "country": "United States"
                        }

                # Pattern 2: City, State ZIP - "Springfield, IL 62701"
                city_state_match = re.search(
                    r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)[,\s]+([A-Z]{2})\s+(\d{5})(?:-\d{4})?',
                    combined
                )
                if city_state_match:
                    city = city_state_match.group(1).strip()
                    state = city_state_match.group(2).upper()
                    zip_code = city_state_match.group(3)

                    if state in US_STATES and not is_fake_city(city):
                        print(f"   📍 GSE found location: {city}, {state} {zip_code}")
                        return {
                            "hq_location": f"{city}, {state} {zip_code}",
                            "city": city,
                            "state": state,
                            "country": "United States"
                        }

        except Exception as e:
            continue

    return {}


async def search_person_linkedin_with_data(full_name: str, company_name: str = "") -> Dict[str, Any]:
    """
    Search Google for person LinkedIn profile and extract data from snippet.

    This uses GSE to find LinkedIn profiles and extracts data from Google's
    search result snippets - NO direct LinkedIn scraping required.

    Typical LinkedIn profile snippet format:
    Title: "John Smith - CEO - Company Name | LinkedIn"
    Snippet: "View John Smith's profile... CEO at Company Name. Location. X connections..."

    Args:
        full_name: Person's full name
        company_name: Company name for context (optional)

    Returns:
        Dict with:
        - linkedin_url: Normalized LinkedIn URL
        - full_name: Name from title
        - first_name: First name
        - last_name: Last name
        - role: Job title from title/snippet
        - company: Company from title/snippet
        - location: Location if found in snippet
    """
    if not full_name:
        return {}

    try:
        if company_name:
            query = f'site:linkedin.com/in/ "{full_name}" "{company_name}"'
        else:
            query = f'site:linkedin.com/in/ "{full_name}"'

        results = await _scrapingdog_google_search(query, num_results=3)

        for item in results:
            link = item.get('link', '')
            title = item.get('title', '')
            snippet = item.get('snippet', '')

            if 'linkedin.com/in/' not in link.lower():
                continue

            normalized = normalize_linkedin_url(link, "profile")
            if not normalized:
                continue

            # Extract data from title and snippet
            data = {
                "linkedin_url": normalized,
                "full_name": "",
                "first_name": "",
                "last_name": "",
                "role": "",
                "company": "",
                "location": "",
            }

            # Parse title: "John Smith - CEO - Company Name | LinkedIn"
            if title:
                # Remove "| LinkedIn" suffix
                title_clean = re.sub(r'\s*\|\s*LinkedIn\s*$', '', title, flags=re.IGNORECASE).strip()

                # Split by " - " to get parts: [Name, Role, Company] or [Name, Role at Company]
                parts = re.split(r'\s*[-–]\s*', title_clean)

                if len(parts) >= 1:
                    # First part is usually the name
                    name = parts[0].strip()
                    if name and len(name) > 2:
                        data["full_name"] = name
                        name_parts = name.split(maxsplit=1)
                        data["first_name"] = name_parts[0] if name_parts else ""
                        data["last_name"] = name_parts[1] if len(name_parts) > 1 else ""

                if len(parts) >= 2:
                    # Second part is usually role or "Role at Company"
                    role_part = parts[1].strip()

                    # Check if it contains "at" -> "CEO at Company"
                    at_match = re.match(r'^(.+?)\s+at\s+(.+)$', role_part, re.IGNORECASE)
                    if at_match:
                        data["role"] = at_match.group(1).strip()
                        data["company"] = at_match.group(2).strip()
                    else:
                        data["role"] = role_part

                if len(parts) >= 3 and not data["company"]:
                    # Third part might be company
                    data["company"] = parts[2].strip()

            # Parse snippet for additional data
            if snippet:
                # Look for location patterns
                # Common: "Greater Los Angeles Area" or "San Francisco, California"
                loc_patterns = [
                    r'(Greater\s+[\w\s]+\s+Area)',
                    r'([A-Z][\w\s]+,\s*[A-Z]{2})\b',  # City, STATE
                    r'([A-Z][\w\s]+,\s*[A-Z][\w\s]+)\b',  # City, Country/State
                ]
                for pattern in loc_patterns:
                    loc_match = re.search(pattern, snippet)
                    if loc_match:
                        data["location"] = loc_match.group(1).strip()
                        break

                # If role not found in title, look in snippet
                if not data["role"]:
                    # Pattern: "Role at Company" or just after name
                    role_match = re.search(r'(?:^|\.)\s*([A-Z][^\.]+?)\s+at\s+', snippet)
                    if role_match:
                        data["role"] = role_match.group(1).strip()

            return data

        return {}

    except Exception as e:
        print(f"⚠️ LinkedIn person search failed for {full_name}: {e}")
        return {}


async def search_linkedin_executives(company_name: str, domain: str = "") -> List[Dict[str, Any]]:
    """
    Search Google for company executives on LinkedIn when no contacts found on website.

    Args:
        company_name: Company name to search
        domain: Company domain for email guessing

    Returns:
        List of contact dicts with name, role, linkedin, and guessed email
    """
    if not company_name:
        return []

    try:
        query = f'site:linkedin.com/in "{company_name}" (CEO OR Founder OR "Chief" OR President OR Director)'
        results = await _scrapingdog_google_search(query, num_results=5)

        contacts = []

        for item in results:
            link = item.get('link', '')
            title = item.get('title', '')

            if 'linkedin.com/in/' not in link.lower():
                continue

            # Normalize the LinkedIn URL
            normalized_linkedin = normalize_linkedin_url(link, "profile")
            if not normalized_linkedin:
                continue

            # Parse name from title (format: "Name - Role - Company | LinkedIn")
            title = re.sub(r'\s*\|\s*LinkedIn\s*$', '', title, flags=re.IGNORECASE)
            parts = re.split(r'\s*[-|–]\s*', title)

            if len(parts) >= 1:
                name = parts[0].strip()
                role = parts[1].strip() if len(parts) > 1 else "Executive"

                # Skip if name looks invalid
                if len(name) < 3 or not name[0].isupper():
                    continue

                # Skip if name contains company name (probably not a person)
                if company_name.lower() in name.lower():
                    continue

                name_parts = name.split(maxsplit=1)
                first_name = name_parts[0] if name_parts else ""
                last_name = name_parts[1] if len(name_parts) > 1 else ""

                # Guess email if we have domain
                # Get verified email if domain provided (uses TrueList if available)
                email = ""
                if domain and first_name and last_name:
                    # Use get_verified_email which generates patterns and validates via TrueList
                    verified_email = await get_verified_email(first_name, last_name, domain)
                    if verified_email:
                        email = verified_email

                contacts.append({
                    "full_name": name,
                    "first_name": first_name,
                    "last_name": last_name,
                    "role": role,
                    "linkedin": normalized_linkedin,
                    "email": email,
                })

                if len(contacts) >= 3:
                    break

        return contacts

    except Exception as e:
        print(f"⚠️ LinkedIn executive search failed for {company_name}: {e}")
        return []


async def search_linkedin_decision_makers(query: str, num_results: int = 10) -> List[Dict[str, Any]]:
    """
    LinkedIn-First Pipeline: Search Google for LinkedIn profiles of decision makers.

    This is the primary data source for LinkedIn-first pipeline. Instead of
    scraping company websites, we find decision makers directly on LinkedIn
    and extract their info from GSE snippets.

    Args:
        query: GSE query (should include site:linkedin.com/in)
        num_results: Max results to return

    Returns:
        List of candidate dicts with name, role, company, linkedin_url

    Example:
        candidates = await search_linkedin_decision_makers(
            'site:linkedin.com/in "owner" "manufacturing" USA',
            num_results=20
        )
    """
    if not query:
        return []

    try:
        results = await _scrapingdog_google_search(query, num_results=num_results)

        candidates = []
        seen_urls = set()

        for item in results:
            link = item.get('link', '')
            title = item.get('title', '')
            snippet = item.get('snippet', '')

            # Must be a LinkedIn profile URL
            if 'linkedin.com/in/' not in link.lower():
                continue

            # Normalize and dedupe
            normalized_linkedin = normalize_linkedin_url(link, "profile")
            if not normalized_linkedin or normalized_linkedin in seen_urls:
                continue
            seen_urls.add(normalized_linkedin)

            # Parse name and role from title
            # LinkedIn title formats vary:
            # - "Name - Role - Company | LinkedIn"
            # - "Name - Role at Company | LinkedIn"
            # - "Name - Company - Role | LinkedIn" (reversed!)
            title = re.sub(r'\s*\|\s*LinkedIn\s*$', '', title, flags=re.IGNORECASE)
            title = re.sub(r'\s*-\s*LinkedIn\s*$', '', title, flags=re.IGNORECASE)

            # Keywords to identify roles vs companies
            role_keywords = ['owner', 'ceo', 'president', 'founder', 'co-founder', 'principal',
                           'director', 'manager', 'vp', 'vice president', 'chief', 'head',
                           'partner', 'cto', 'cfo', 'coo', 'cmo', 'executive']
            company_keywords = ['inc', 'llc', 'corp', 'ltd', 'company', 'co.', 'group',
                              'solutions', 'services', 'manufacturing', 'industries', 'enterprises']

            def is_likely_role(text: str) -> bool:
                text_lower = text.lower()
                return any(kw in text_lower for kw in role_keywords)

            def is_likely_company(text: str) -> bool:
                text_lower = text.lower()
                return any(kw in text_lower for kw in company_keywords)

            name = ""
            role = ""
            company = ""

            # Split by common delimiters
            parts = re.split(r'\s*[-–|]\s*', title)
            if len(parts) >= 1:
                name = parts[0].strip()

            if len(parts) >= 2:
                # Check if second part contains "at" (e.g., "Owner at Acme Corp")
                role_part = parts[1].strip()
                if ' at ' in role_part.lower():
                    role_company = re.split(r'\s+at\s+', role_part, flags=re.IGNORECASE)
                    role = role_company[0].strip() if role_company else ""
                    company = role_company[1].strip() if len(role_company) > 1 else ""
                elif len(parts) == 2:
                    # Only 2 parts: determine if it's role or company

                    # First, check for comma-separated "Role, Company Name" pattern
                    # e.g., "Owner, American Metal Manufacturing Resources"
                    if ',' in role_part:
                        comma_parts = role_part.split(',', 1)
                        first_part = comma_parts[0].strip()
                        second_part = comma_parts[1].strip() if len(comma_parts) > 1 else ""

                        # If first part is clearly a role keyword
                        if is_likely_role(first_part) and not is_likely_company(first_part):
                            role = first_part
                            company = second_part
                        elif is_likely_company(first_part) and not is_likely_role(first_part):
                            company = role_part  # Keep whole thing as company
                        else:
                            # Default: first part is role, second is company
                            role = first_part
                            company = second_part
                    elif is_likely_role(role_part) and not is_likely_company(role_part):
                        role = role_part
                    elif is_likely_company(role_part) and not is_likely_role(role_part):
                        company = role_part
                    else:
                        # Ambiguous - shorter text is usually role
                        role = role_part
                else:
                    # 3+ parts: need to figure out which is role vs company
                    part2 = parts[1].strip()
                    part3 = parts[2].strip() if len(parts) >= 3 else ""

                    # Smart detection: check which part looks like a role vs company
                    if is_likely_role(part2) or (not is_likely_company(part2) and is_likely_company(part3)):
                        role = part2
                        company = part3
                    elif is_likely_company(part2) or is_likely_role(part3):
                        company = part2
                        role = part3
                    else:
                        # Default: assume Role - Company order
                        role = part2
                        company = part3

            # Try to extract company from snippet if not in title
            if not company and snippet:
                # Look for "at Company" or "Company · Industry"
                at_match = re.search(r'\bat\s+([A-Z][A-Za-z0-9\s&,\.]+?)(?:\s*[-·|]|\s*$)', snippet)
                if at_match:
                    company = at_match.group(1).strip()
                else:
                    # Try "Company name · Industry"
                    dot_match = re.search(r'^([A-Z][A-Za-z0-9\s&,\.]+?)\s*·', snippet)
                    if dot_match:
                        company = dot_match.group(1).strip()

            # Clean up company name
            company = re.sub(r'\s*(Inc\.?|LLC|Corp\.?|Ltd\.?|Co\.?)?\s*$', '', company, flags=re.IGNORECASE).strip()

            # Try to extract role from snippet if not in title
            if not role and snippet:
                # Look for role keywords in snippet
                for role_kw in ['owner', 'ceo', 'president', 'founder', 'director', 'manager', 'partner']:
                    role_match = re.search(rf'\b({role_kw})\b', snippet, re.IGNORECASE)
                    if role_match:
                        role = role_match.group(1).title()
                        break

            # Skip if name looks invalid (too short or not capitalized)
            if len(name) < 3 or not name[0].isupper():
                continue

            # Skip names that are clearly company names
            company_indicators = ['inc', 'llc', 'corp', 'ltd', 'company', 'manufacturing', 'industries']
            if any(ind in name.lower() for ind in company_indicators):
                continue

            # Parse name into first/last
            name_parts = name.split(maxsplit=1)
            first_name = name_parts[0] if name_parts else ""
            last_name = name_parts[1] if len(name_parts) > 1 else ""

            # Clean role - remove company mentions
            if role and company:
                role = re.sub(re.escape(company), '', role, flags=re.IGNORECASE).strip()
                role = re.sub(r'^(at|of|for)\s+', '', role, flags=re.IGNORECASE).strip()
                role = re.sub(r'\s+(at|of|for)$', '', role, flags=re.IGNORECASE).strip()

            # Try to extract location from snippet
            location = ""
            loc_match = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?),?\s*([A-Z]{2})\s*(?:Area|Metropolitan)?', snippet)
            if loc_match:
                city = loc_match.group(1)
                state = loc_match.group(2)
                location = f"{city}, {state}"

            candidates.append({
                "full_name": name,
                "first_name": first_name,
                "last_name": last_name,
                "role": role,
                "company": company,
                "linkedin_url": normalized_linkedin,
                "location": location,
                "snippet": snippet[:200] if snippet else "",
            })

            if len(candidates) >= num_results:
                break

        return candidates

    except Exception as e:
        print(f"⚠️ LinkedIn decision maker search failed: {e}")
        return []


async def find_company_domain(company_name: str) -> Optional[str]:
    """
    Find company domain from company name using GSE.

    Used in LinkedIn-first pipeline to get company website for email verification.

    Args:
        company_name: Company name to search

    Returns:
        Domain (e.g., "acme.com") or None
    """
    if not company_name or len(company_name) < 2:
        return None

    try:
        # Search for company website - exclude social media and people search sites
        # Note: Google limits site exclusions, so we add the worst offenders here
        query = (
            f'"{company_name}" company website official '
            f'-site:linkedin.com -site:facebook.com -site:twitter.com '
            f'-site:idcrawl.com -site:whitepages.com -site:spokeo.com '
            f'-site:zoominfo.com -site:glassdoor.com -site:indeed.com'
        )
        results = await _scrapingdog_google_search(query, num_results=5)

        # Common company domain patterns to prioritize
        company_name_clean = re.sub(r'[^\w\s]', '', company_name.lower())
        company_words = set(company_name_clean.split())

        for item in results:
            link = item.get('link', '')
            if not link:
                continue

            try:
                parsed = urlparse(link)
                domain = parsed.netloc.lower()

                # Skip social media, people search, and common non-company sites
                skip_domains = [
                    # Social media
                    'linkedin.com', 'facebook.com', 'twitter.com', 'instagram.com',
                    'youtube.com', 'tiktok.com', 'pinterest.com',
                    # People search / aggregators (these return wrong domains!)
                    'idcrawl.com', 'whitepages.com', 'spokeo.com', 'beenverified.com',
                    'truepeoplesearch.com', 'fastpeoplesearch.com', 'peoplefinders.com',
                    'intelius.com', 'pipl.com', 'zabasearch.com', 'radaris.com',
                    'thatsthem.com', 'anywho.com', 'addresses.com', 'familytreenow.com',
                    'ussearch.com', 'peoplesearchnow.com', 'checkpeople.com',
                    'instantcheckmate.com', 'publicrecords360.com', 'publicrecordsnow.com',
                    'mylife.com', 'clustrmaps.com', 'nuwber.com', 'cyberbackgroundchecks.com',
                    # Business directories / news
                    'wikipedia.org', 'yelp.com', 'glassdoor.com', 'indeed.com',
                    'bloomberg.com', 'crunchbase.com', 'zoominfo.com', 'dnb.com',
                    'bbb.org', 'mapquest.com', 'yellowpages.com', 'manta.com',
                    'bizapedia.com', 'buzzfile.com', 'owler.com', 'apollo.io',
                    'rocketreach.co', 'leadiq.com', 'lusha.com', 'clearbit.com',
                    'hunter.io', 'snov.io', 'signalhire.com',
                    # Government / legal
                    'opencorporates.com', 'sec.gov', 'corporationwiki.com',
                ]
                if any(skip in domain for skip in skip_domains):
                    continue

                # Remove www. prefix
                domain = re.sub(r'^www\.', '', domain)

                # Check if domain contains company name words (strong match)
                domain_words = set(re.sub(r'[^\w]', ' ', domain).split())
                if company_words & domain_words:
                    return domain

            except Exception:
                continue

        # Second pass: look for plausible company domains (no word match required)
        # Only accept domains that look like actual company websites
        for item in results:
            link = item.get('link', '')
            if not link:
                continue
            try:
                parsed = urlparse(link)
                domain = parsed.netloc.lower()
                domain = re.sub(r'^www\.', '', domain)

                if any(skip in domain for skip in skip_domains):
                    continue

                # Only accept short domains with company-like TLDs
                # Reject long/weird domains that are likely aggregators
                if len(domain) > 30:
                    continue

                # Must end with common company TLDs
                valid_tlds = ['.com', '.net', '.co', '.io', '.us', '.biz', '.org', '.inc']
                if not any(domain.endswith(tld) for tld in valid_tlds):
                    continue

                # Reject domains with suspicious patterns (numbers, dashes, generic words)
                suspicious_patterns = ['search', 'find', 'lookup', 'check', 'verify', 'people', 'info', 'data', 'records']
                if any(pattern in domain for pattern in suspicious_patterns):
                    continue

                return domain

            except Exception:
                continue

        return None

    except Exception as e:
        print(f"⚠️ Domain search failed for {company_name}: {e}")
        return None


async def generate_linkedin_queries_with_llm(icp_config: Dict[str, Any], num_queries: int = 25) -> List[str]:
    """
    Use OpenRouter LLM to generate optimal LinkedIn search queries from ICP config.

    This enables dynamic query generation based on any ICP text, making the
    LinkedIn-first pipeline flexible and adaptive.

    Args:
        icp_config: ICP configuration with icp_text, targeting, role_priority, etc.
        num_queries: Number of queries to generate (default 25)

    Returns:
        List of LinkedIn GSE search queries (site:linkedin.com/in format)
    """
    import aiohttp

    openrouter_key = os.getenv("OPENROUTER_KEY", "")
    if not openrouter_key:
        print("⚠️ OPENROUTER_KEY not set - cannot generate dynamic queries")
        return []

    icp_text = icp_config.get("icp_text", "")
    if not icp_text:
        print("⚠️ No icp_text in config - cannot generate queries")
        return []

    # Extract targeting info for context
    targeting = icp_config.get("targeting", {})
    role_priority = icp_config.get("role_priority", {})
    sub_industries = targeting.get("sub_industries", [])
    geographic_focus = targeting.get("geographic_focus", ["United States"])
    company_sizes = targeting.get("company_sizes", ["small", "medium"])
    validation_config = icp_config.get("validation_config", {})
    industry_keywords = validation_config.get("industry_keywords", [])

    # Get high-priority roles
    priority_roles = [role for role, priority in role_priority.items()
                      if isinstance(priority, int) and priority <= 2 and role != "default"]

    prompt = f"""You are an expert at crafting Google search queries to find LinkedIn profiles of business decision makers.

TARGET ICP (Ideal Customer Profile):
{icp_text}

CONTEXT:
- Priority Roles: {', '.join(priority_roles[:8]) if priority_roles else 'owner, CEO, president, founder'}
- Sub-industries: {', '.join(sub_industries[:8]) if sub_industries else 'See ICP text'}
- Industry Keywords: {', '.join(industry_keywords[:10]) if industry_keywords else 'See ICP text'}
- Geography: {', '.join(geographic_focus)}
- Company Size: {', '.join(company_sizes)}

CRITICAL RULES FOR EFFECTIVE QUERIES:

1. ALWAYS start with: site:linkedin.com/in

2. QUERY LENGTH: Use exactly 3-4 search terms after site:linkedin.com/in
   - TOO SHORT (bad): site:linkedin.com/in "owner"
   - TOO LONG (bad): site:linkedin.com/in "owner" "small" "manufacturing" "company" "metal" "USA"
   - OPTIMAL: site:linkedin.com/in "owner" "manufacturing" USA

3. QUOTES: Use quotes around multi-word phrases and role titles
   - GOOD: "metal fabrication" "machine shop" "plastic injection"
   - BAD: metal fabrication (without quotes = poor results)

4. ROLE KEYWORDS: Use specific decision-maker titles
   - BEST for SMBs: owner, president, founder, CEO
   - GOOD: "VP Operations" "general manager" "plant manager"
   - AVOID: manager (too generic), employee, worker

5. INDUSTRY TERMS: Use terms people put in their LinkedIn headlines
   - GOOD: "CNC" "fabrication" "machining" "injection molding"
   - People write: "Owner at ABC Manufacturing" or "President | Metal Fabrication"

6. GEOGRAPHY: Add USA or "United States" to 50% of queries
   - Helps filter to US-based profiles

7. VARIATION STRATEGIES - Mix these approaches:
   a) Role + Industry: "owner" "manufacturing"
   b) Role + Sub-industry: "president" "CNC machining"
   c) Role + Industry + Geo: "founder" "fabrication" USA
   d) Specific niche: "owner" "plastic injection molding"

BAD QUERIES (DO NOT GENERATE THESE):
❌ site:linkedin.com/in owner manufacturing (no quotes)
❌ site:linkedin.com/in "owner" "small" "medium" "manufacturing" "company" (too many terms)
❌ site:linkedin.com/in "employee" "manufacturing" (wrong role)
❌ site:linkedin.com/in "manufacturing" (no role keyword)
❌ site:linkedin.com/in "owner" (no industry keyword)

GOOD QUERIES (USE THESE PATTERNS):
✅ site:linkedin.com/in "owner" "manufacturing" USA
✅ site:linkedin.com/in "president" "metal fabrication"
✅ site:linkedin.com/in "CEO" "machine shop"
✅ site:linkedin.com/in "founder" "CNC machining"
✅ site:linkedin.com/in "owner" "plastic injection molding"
✅ site:linkedin.com/in "general manager" "manufacturing"
✅ site:linkedin.com/in "VP operations" "fabrication"
✅ site:linkedin.com/in "president" "tool and die"
✅ site:linkedin.com/in "owner" "contract manufacturing" USA

OUTPUT: Return ONLY {num_queries} queries, one per line. No numbering, no bullets, no explanations.

Generate {num_queries} optimized queries now:"""

    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {openrouter_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://leadpoet.ai",
            }

            payload = {
                "model": "anthropic/claude-3-haiku",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2000,
                "temperature": 0.3,  # Lower temperature for more consistent, focused queries
            }

            print(f"🤖 Generating LinkedIn queries with LLM...")

            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=30
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

                    # Parse queries from response
                    queries = []
                    for line in content.strip().split("\n"):
                        line = line.strip()
                        # Skip empty lines and non-query lines
                        if not line or not line.lower().startswith("site:linkedin.com"):
                            continue
                        # Clean up any numbering or bullets
                        line = re.sub(r'^[\d\.\-\*\)\]]+\s*', '', line)
                        if line.lower().startswith("site:linkedin.com"):
                            # Validate query has reasonable length (not too many terms)
                            term_count = len(line.split()) - 1  # -1 for site:linkedin.com/in
                            if 2 <= term_count <= 5:
                                queries.append(line)
                            else:
                                print(f"   ⚠️ Skipped query with {term_count} terms: {line[:60]}...")

                    print(f"✓ Generated {len(queries)} LinkedIn queries")
                    return queries[:num_queries]

                else:
                    error_text = await response.text()
                    print(f"⚠️ OpenRouter error {response.status}: {error_text[:100]}")
                    return []

    except Exception as e:
        print(f"⚠️ LLM query generation failed: {e}")
        return []


def _extract_us_address(text: str) -> Optional[str]:
    """Extract US address from text using regex patterns."""
    if not text:
        return None

    # US state abbreviations for validation
    US_STATES = {
        'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
        'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
        'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
        'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
        'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'
    }

    # Patterns ordered by specificity (most specific first)
    patterns = [
        # Full address with ZIP: "20 Park Way - Upper Saddle River NJ 07458"
        # Handles comma, hyphen, or no separator before city
        r'(\d+\s+[A-Za-z0-9\s\.]+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Court|Ct|Place|Pl|Highway|Hwy|Parkway|Pkwy|Circle|Cir)[\s,\-]+[A-Za-z\s]+[,\s]+[A-Z]{2}\s+\d{5}(?:-\d{4})?)',

        # Address without street type: "20 Park Way, City, ST 12345"
        r'(\d+\s+[A-Za-z\s]+[\s,\-]+[A-Za-z\s]+[,\s]+[A-Z]{2}\s+\d{5}(?:-\d{4})?)',

        # Just City, State ZIP (most common in footers): "Upper Saddle River, NJ 07458"
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*[,\s]+[A-Z]{2}\s+\d{5}(?:-\d{4})?)',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            # Validate that state abbreviation is real
            state_match = re.search(r'\b([A-Z]{2})\s+\d{5}', match, re.IGNORECASE)
            if state_match:
                state = state_match.group(1).upper()
                if state in US_STATES:
                    return match.strip()

    return None


async def firecrawl_extract_company(domain: str) -> Dict[str, Any]:
    """
    Extract company and contact data from domain using Firecrawl API.

    Scrapes multiple pages (homepage, contact, about) to find location data
    that may be in footers or contact pages.

    Args:
        domain: Company domain (e.g., "acme.com")

    Returns:
        Dict with company data: name, description, industry, sub_industry,
        hq_location, employee_count, contacts, etc.
    """
    import aiohttp

    firecrawl_key = os.getenv("FIRECRAWL_KEY", "")
    if not firecrawl_key:
        print("⚠️ FIRECRAWL_KEY not set - skipping website extraction")
        return {}

    if not domain:
        return {}

    api_url = "https://api.firecrawl.dev/v1/scrape"

    # Extraction schema for company data
    extract_schema = {
        "type": "object",
        "properties": {
            "company_name": {"type": "string"},
            "description": {"type": "string"},
            "industry": {"type": "string"},
            "sub_industry": {"type": "string"},
            "hq_location": {"type": "string", "description": "Full headquarters address including city, state, and ZIP code"},
            "city": {"type": "string"},
            "state": {"type": "string"},
            "country": {"type": "string"},
            "employee_count": {"type": "string"},
            "founded_year": {"type": "integer"},
            "contacts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "full_name": {"type": "string"},
                        "role": {"type": "string"},
                        "email": {"type": "string"},
                        "phone": {"type": "string"},
                    }
                }
            }
        }
    }

    # Pages to try for location data (often in footer/contact page)
    pages_to_try = [
        f"https://{domain}",
        f"https://{domain}/contact",
        f"https://{domain}/contact-us",
        f"https://{domain}/about",
        f"https://{domain}/about-us",
    ]

    combined_data = {}
    location_found = False

    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {firecrawl_key}",
                "Content-Type": "application/json"
            }

            for url in pages_to_try[:3]:  # Limit to 3 pages to save API calls
                # If we already have location, use simpler extraction
                if location_found:
                    break

                payload = {
                    "url": url,
                    "formats": ["extract", "markdown"],
                    "waitFor": 3000,  # Wait 3s for JS to render footer
                    "actions": [
                        {"type": "scroll", "direction": "down", "amount": 2000}  # Scroll to load footer
                    ],
                    "extract": {
                        "schema": extract_schema,
                        "prompt": """Extract company information from this page.

For hq_location: Look carefully in these locations:
- Page footer (bottom of page)
- Contact section
- "About Us" or "Contact Us" text
- Any visible street address with city, state, and ZIP code

Extract the EXACT address text you see on the page. Format as: "Street Address, City, ST ZIP"
Example: "123 Main St, Springfield, IL 62701"

If you see a real address on the page, extract it. If no address is visible anywhere on the page, return an empty string.

DO NOT make up addresses. Only extract what you actually see on the page."""
                    }
                }

                print(f"   🔥 Firecrawl extracting from {url}...")

                try:
                    async with session.post(api_url, json=payload, headers=headers, timeout=45) as response:
                        if response.status == 200:
                            result = await response.json()
                            if result.get("success"):
                                data = result.get("data", {})
                                json_data = data.get("extract", {}) or data.get("json", {})
                                markdown = data.get("markdown", "")

                                # DEBUG: Show ALL fields Firecrawl extracted
                                if json_data:
                                    all_keys = list(json_data.keys())
                                    print(f"      📋 Firecrawl extracted fields: {all_keys}")
                                    loc_fields = {k: v for k, v in json_data.items()
                                                  if k in ('hq_location', 'city', 'state', 'country')}
                                    # Check if location fields have actual values
                                    has_loc_values = any(v for v in loc_fields.values() if v)
                                    if has_loc_values:
                                        print(f"      📍 Location fields: {loc_fields}")
                                    else:
                                        print(f"      ⚠️ Location fields empty - checking markdown...")
                                        # Show markdown length and search for address indicators
                                        if markdown:
                                            print(f"      📄 Markdown length: {len(markdown)} chars")
                                            # Search for common address patterns in raw markdown
                                            import re as _re
                                            # Look for ZIP codes
                                            zips = _re.findall(r'\b\d{5}(?:-\d{4})?\b', markdown)
                                            # Look for state abbreviations with context
                                            states = _re.findall(r'[A-Z]{2}\s+\d{5}', markdown)
                                            # Look for street indicators
                                            streets = _re.findall(r'\d+\s+[A-Za-z]+\s+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Blvd|Boulevard|Ln|Lane|Way|Ct|Court)', markdown, _re.IGNORECASE)
                                            if zips or states or streets:
                                                print(f"      🔍 Found in markdown - ZIPs: {zips[:3]}, States: {states[:2]}, Streets: {streets[:2]}")
                                            else:
                                                print(f"      ❌ No address patterns found in markdown")
                                                # Show last 500 chars of markdown (footer is usually at the end)
                                                footer_snippet = markdown[-500:] if len(markdown) > 500 else markdown
                                                print(f"      📄 Markdown tail (footer area): {footer_snippet[:200]}...")

                                # Merge data (don't overwrite existing non-empty values)
                                for key, value in json_data.items():
                                    if value and not combined_data.get(key):
                                        # Filter out fake/hallucinated addresses
                                        if key == "hq_location" and is_fake_address(value):
                                            print(f"      ⚠️ Rejected hallucinated address: {value[:50]}...")
                                            continue
                                        if key == "city" and is_fake_city(value):
                                            print(f"      ⚠️ Rejected fake city: {value}")
                                            continue
                                        combined_data[key] = value

                                # Check if we found REAL location (not fake)
                                hq_loc = combined_data.get("hq_location", "")
                                city = combined_data.get("city", "")
                                if (hq_loc and not is_fake_address(hq_loc)) or (city and not is_fake_city(city)):
                                    location_found = True
                                    print(f"      ✓ Found location data")

                                # Fallback: extract address from markdown using regex
                                if not location_found and markdown:
                                    extracted_addr = _extract_us_address(markdown)
                                    if extracted_addr and not is_fake_address(extracted_addr):
                                        combined_data["hq_location"] = extracted_addr
                                        location_found = True
                                        print(f"      ✓ Extracted address from page: {extracted_addr[:50]}...")
                                    elif extracted_addr:
                                        print(f"      ⚠️ Rejected hallucinated regex address: {extracted_addr[:50]}...")
                                    else:
                                        # DEBUG: Show snippet of markdown to see if address exists but regex missed it
                                        md_lower = markdown.lower()
                                        # Look for ZIP codes as indicator of address
                                        import re as _re
                                        zip_matches = _re.findall(r'\b\d{5}(?:-\d{4})?\b', markdown)
                                        if zip_matches:
                                            print(f"      📝 Found ZIP codes in markdown: {zip_matches[:3]} - address may exist")
                                            # Show context around first ZIP
                                            for zc in zip_matches[:1]:
                                                idx = markdown.find(zc)
                                                if idx > 0:
                                                    snippet = markdown[max(0, idx-80):idx+20]
                                                    print(f"      📝 Context: ...{snippet.strip()}...")

                        elif response.status != 402:  # Skip logging for credit errors
                            pass  # Silently continue to next page

                except asyncio.TimeoutError:
                    continue
                except Exception:
                    continue

            if combined_data:
                print(f"   ✓ Firecrawl extraction successful")

                # Build hq_location from city/state if not present
                if not combined_data.get("hq_location"):
                    city = combined_data.get("city", "")
                    state = combined_data.get("state", "")
                    country = combined_data.get("country", "")
                    if city or state:
                        parts = [p for p in [city, state, country] if p]
                        combined_data["hq_location"] = ", ".join(parts)

                return combined_data
            else:
                print(f"   ⚠️ Firecrawl: No data extracted")
                return {}

    except Exception as e:
        print(f"⚠️ Firecrawl extraction failed for {domain}: {e}")
        return {}


# ============================================================================
# Lead Completeness Validation
# ============================================================================

def is_lead_complete(lead: Dict[str, Any]) -> bool:
    """
    Check if all required fields are present and non-empty.

    Args:
        lead: Lead dictionary

    Returns:
        True if all required fields are present
    """
    for field in REQUIRED_FIELDS:
        if not lead.get(field):
            return False

    # State required for US only
    if lead.get("country") == "United States" and not lead.get("state"):
        return False

    return True


def get_missing_fields(lead: Dict[str, Any]) -> List[str]:
    """
    Get list of missing required fields.

    Args:
        lead: Lead dictionary

    Returns:
        List of field names that are missing or empty
    """
    missing = [f for f in REQUIRED_FIELDS if not lead.get(f)]

    # Check state for US
    if lead.get("country") == "United States" and not lead.get("state"):
        if "state" not in missing:
            missing.append("state")

    return missing


# ============================================================================
# Email Pattern Generation & TrueList Validation
# ============================================================================

# TrueList constants - API key is read lazily to allow .env loading
TRUELIST_BATCH_URL = "https://api.truelist.io/api/v1/batches"
TRUELIST_POLL_INTERVAL = 5  # seconds between polls
TRUELIST_TIMEOUT = 1200  # max seconds to wait for batch (20 minutes for large batches)


def _get_truelist_api_key() -> str:
    """Get TrueList API key lazily (after .env is loaded)."""
    return os.getenv("TRUELIST_API_KEY", "")


async def scrape_emails_from_website(domain: str, target_name: str = None) -> List[str]:
    """
    Scrape email addresses from a company website.

    Crawls key pages (contact, about, team) and extracts email addresses.
    If target_name is provided, prioritizes emails that might belong to that person.

    Args:
        domain: Company domain (e.g., "acme.com")
        target_name: Optional name to prioritize (e.g., "John Smith")

    Returns:
        List of email addresses found, prioritized by relevance
    """
    import aiohttp

    firecrawl_key = os.getenv("FIRECRAWL_KEY", "")
    if not firecrawl_key:
        return []

    emails_found = []
    email_pattern = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')

    # Pages most likely to have contact emails
    pages_to_check = [
        f"https://{domain}",
        f"https://{domain}/contact",
        f"https://{domain}/contact-us",
        f"https://{domain}/about",
        f"https://{domain}/about-us",
        f"https://{domain}/team",
        f"https://{domain}/our-team",
        f"https://{domain}/leadership",
    ]

    print(f"   🔍 Scraping {domain} for email addresses...")

    try:
        async with aiohttp.ClientSession() as session:
            # Use Firecrawl to scrape pages
            headers = {
                "Authorization": f"Bearer {firecrawl_key}",
                "Content-Type": "application/json"
            }

            for url in pages_to_check[:4]:  # Limit to 4 pages to save API calls
                try:
                    payload = {
                        "url": url,
                        "formats": ["markdown"]
                    }

                    async with session.post(
                        "https://api.firecrawl.dev/v1/scrape",
                        json=payload,
                        headers=headers,
                        timeout=15
                    ) as response:
                        if response.status == 200:
                            result = await response.json()
                            if result.get("success"):
                                content = result.get("data", {}).get("markdown", "")

                                # Extract emails from content
                                found = email_pattern.findall(content)

                                # Filter to only emails at this domain
                                domain_emails = [e for e in found if domain.lower() in e.lower()]

                                for email in domain_emails:
                                    if email.lower() not in [e.lower() for e in emails_found]:
                                        emails_found.append(email)

                                if emails_found:
                                    print(f"      ✓ Found {len(emails_found)} email(s) on {url.split('/')[-1] or 'homepage'}")

                except asyncio.TimeoutError:
                    continue
                except Exception:
                    continue

    except Exception as e:
        print(f"   ⚠️ Email scraping error: {e}")
        return []

    if not emails_found:
        print(f"      No emails found on website")
        return []

    # Filter out generic/blocked emails (info@, contact@, etc.)
    blocked_prefixes = [
        'info', 'contact', 'hello', 'team', 'support', 'admin', 'sales',
        'marketing', 'hr', 'jobs', 'careers', 'press', 'media', 'help',
        'service', 'enquiries', 'office', 'general', 'mail', 'webmaster'
    ]
    filtered_emails = []
    for email in emails_found:
        local = email.split('@')[0].lower()
        is_blocked = any(local == prefix or local.startswith(prefix + '.') or local.startswith(prefix + '_')
                        for prefix in blocked_prefixes)
        if not is_blocked:
            filtered_emails.append(email)
        else:
            print(f"      ⚠️ Filtered generic email: {email}")

    emails_found = filtered_emails
    if not emails_found:
        print(f"      No personal emails found (all generic)")
        return []

    # Prioritize emails if we have a target name
    if target_name and emails_found:
        name_parts = target_name.lower().split()
        prioritized = []
        other = []

        for email in emails_found:
            email_local = email.split('@')[0].lower()
            # Check if any part of the name appears in the email
            if any(part in email_local for part in name_parts if len(part) > 2):
                prioritized.append(email)
            else:
                other.append(email)

        # Return prioritized first, then others
        emails_found = prioritized + other

        if prioritized:
            print(f"      ✓ Found potential match for {target_name}: {prioritized[0]}")

    print(f"   📧 Scraped {len(emails_found)} email(s) from website")
    return emails_found


async def search_email_google(first_name: str, last_name: str, domain: str) -> Optional[str]:
    """
    Search Google for a person's email address.

    Tries multiple search queries to find published email addresses.

    Args:
        first_name: Person's first name
        last_name: Person's last name
        domain: Company domain

    Returns:
        Email address if found, None otherwise
    """
    full_name = f"{first_name} {last_name}"
    email_pattern = re.compile(rf'[a-zA-Z0-9._%+-]+@{re.escape(domain)}', re.IGNORECASE)

    # Search queries to try
    queries = [
        f'"{full_name}" "@{domain}" email',
        f'"{full_name}" site:{domain} email',
        f'"{first_name}" "{last_name}" "@{domain}"',
    ]

    print(f"   🔍 Searching Google for {full_name}'s email...")

    for query in queries:
        try:
            results = await _scrapingdog_google_search(query, num_results=5)

            for item in results:
                snippet = item.get('snippet', '') + ' ' + item.get('title', '')

                # Look for email in snippet
                emails_found = email_pattern.findall(snippet)

                for email in emails_found:
                    email = email.lower()
                    # Skip generic emails
                    local = email.split('@')[0]
                    if local in ['info', 'contact', 'sales', 'support', 'admin', 'hello']:
                        continue

                    # Check if email contains part of the name
                    name_parts = [first_name.lower(), last_name.lower()]
                    if any(part in local for part in name_parts if len(part) > 2):
                        print(f"      ✓ Found email via Google: {email}")
                        return email

        except Exception:
            continue

    return None


def generate_email_patterns(first_name: str, last_name: str, domain: str) -> List[str]:
    """
    Generate TOP 5 most common email patterns from name and domain.

    Optimized to only include patterns that cover ~95% of business emails:
    - first.last@ (~45% of companies)
    - first@ (~20% - common in startups)
    - firstlast@ (~15%)
    - flast@ (~10%)
    - first_last@ (~5%)

    Args:
        first_name: Person's first name
        last_name: Person's last name
        domain: Company domain (e.g., "acme.com")

    Returns:
        List of 5 candidate email addresses
    """
    if not first_name or not last_name or not domain:
        return []

    # Clean names - remove non-alpha characters, lowercase
    first = re.sub(r'[^a-zA-Z]', '', first_name).lower()
    last = re.sub(r'[^a-zA-Z]', '', last_name).lower()

    if not first or not last:
        return []

    # TOP 5 patterns covering ~95% of business emails
    patterns = [
        f"{first}.{last}@{domain}",    # 1. first.last (~45%)
        f"{first}@{domain}",            # 2. first only (~20%)
        f"{first}{last}@{domain}",      # 3. firstlast (~15%)
        f"{first[0]}{last}@{domain}",   # 4. flast (~10%)
        f"{first}_{last}@{domain}",     # 5. first_last (~5%)
    ]

    return patterns


async def validate_emails_truelist(emails: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Validate a batch of emails using TrueList API.

    This allows miners to pre-validate guessed emails before submission,
    reducing rejection rates and improving efficiency.

    Args:
        emails: List of email addresses to validate

    Returns:
        Dict mapping email -> {status, passed, sub_status}

    Example:
        results = await validate_emails_truelist(["john@acme.com", "jane@acme.com"])
        # results = {
        #     "john@acme.com": {"status": "email_ok", "passed": True},
        #     "jane@acme.com": {"status": "failed_no_mailbox", "passed": False}
        # }
    """
    import aiohttp
    import asyncio

    truelist_api_key = _get_truelist_api_key()
    if not truelist_api_key:
        print("⚠️ TRUELIST_API_KEY not set - skipping email validation")
        # Return all as unknown (not validated)
        return {email: {"status": "unknown", "passed": None, "reason": "no_api_key"} for email in emails}

    if not emails:
        return {}

    # Deduplicate and filter valid format
    email_pattern = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    valid_emails = list(set(e for e in emails if email_pattern.match(e)))

    if not valid_emails:
        return {}

    # Track original emails before any padding
    original_emails = valid_emails.copy()

    # TrueList requires minimum 2 emails per batch - add padding if needed
    padding_email = None
    if len(valid_emails) == 1:
        # Add a known-invalid padding email that we'll ignore in results
        padding_email = "truelist.padding.invalid@example.com"
        valid_emails.append(padding_email)

    print(f"📧 TrueList: Validating {len(original_emails)} email(s)...")

    def filter_results(results: Dict) -> Dict:
        """Remove padding email from results."""
        return {k: v for k, v in results.items() if k in original_emails}

    try:
        async with aiohttp.ClientSession() as session:
            # Step 1: Submit batch
            headers = {
                "Authorization": f"Bearer {truelist_api_key}",
                "Content-Type": "application/json"
            }

            # Format emails as array of arrays (TrueList format)
            email_data = [[email] for email in valid_emails]

            payload = {
                "data": email_data,
                "validation_strategy": "fast",
                "name": f"miner_batch_{int(asyncio.get_event_loop().time())}"
            }

            async with session.post(TRUELIST_BATCH_URL, json=payload, headers=headers, timeout=30) as response:
                if response.status == 401:
                    print("⚠️ TrueList: Invalid API key")
                    return filter_results({email: {"status": "error", "passed": None, "reason": "invalid_api_key"} for email in valid_emails})
                elif response.status == 402:
                    print("⚠️ TrueList: Insufficient credits")
                    return filter_results({email: {"status": "error", "passed": None, "reason": "no_credits"} for email in valid_emails})
                elif response.status not in (200, 201):
                    error_text = await response.text()
                    print(f"⚠️ TrueList batch submit failed: {response.status} - {error_text[:100]}")
                    return filter_results({email: {"status": "error", "passed": None, "reason": "submit_failed"} for email in valid_emails})

                result = await response.json()
                # TrueList API returns "id" not "batch_id"
                batch_id = result.get("id") or result.get("batch_id")

                if not batch_id:
                    print(f"⚠️ TrueList: No batch_id in response: {result}")
                    return filter_results({email: {"status": "error", "passed": None, "reason": "no_batch_id"} for email in valid_emails})

                print(f"   TrueList batch submitted: {batch_id}")

            print(f"   📋 Batch ID: {batch_id}")

            # Step 2: Poll for completion
            start_time = asyncio.get_event_loop().time()

            while asyncio.get_event_loop().time() - start_time < TRUELIST_TIMEOUT:
                await asyncio.sleep(TRUELIST_POLL_INTERVAL)

                async with session.get(f"{TRUELIST_BATCH_URL}/{batch_id}", headers=headers) as poll_response:
                    if poll_response.status != 200:
                        continue

                    poll_data = await poll_response.json()
                    state = poll_data.get("batch_state", "")

                    if state == "completed":
                        ok_count = poll_data.get("ok_count", 0)
                        fail_count = poll_data.get("email_count", len(valid_emails)) - ok_count
                        print(f"   ✅ Batch completed: {ok_count} OK, {fail_count} failed")

                        # Simple logic: use ok_count to determine results
                        # Emails are in priority order, so mark first ok_count as passed
                        results = {}
                        for i, email in enumerate(valid_emails):
                            if i < ok_count:
                                results[email] = {"status": "valid", "passed": True}
                            else:
                                results[email] = {"status": "failed", "passed": False}
                        return filter_results(results)

                    elif state == "failed":
                        print(f"   ❌ Batch failed")
                        return filter_results({email: {"status": "error", "passed": None, "reason": "batch_failed"} for email in valid_emails})

                    else:
                        processed = poll_data.get("processed_count", 0)
                        total = poll_data.get("email_count", len(valid_emails))
                        print(f"   ⏳ Processing: {processed}/{total}...")

            print("⚠️ TrueList: Timeout waiting for batch")
            return filter_results({email: {"status": "timeout", "passed": None} for email in valid_emails})

    except Exception as e:
        print(f"⚠️ TrueList validation error: {e}")
        return filter_results({email: {"status": "error", "passed": None, "reason": str(e)} for email in valid_emails})


async def collect_email_candidates(first_name: str, last_name: str, domain: str) -> List[str]:
    """
    Collect all possible email candidates for a person WITHOUT verifying them.

    Used for batch verification - collect emails from multiple leads first,
    then verify all at once for efficiency.

    Returns:
        List of candidate emails (may contain duplicates, caller should dedupe)
    """
    email_candidates = []
    full_name = f"{first_name} {last_name}"

    # Step 1: Try scraping emails from website
    try:
        scraped_emails = await scrape_emails_from_website(domain, target_name=full_name)
        if scraped_emails:
            email_candidates.extend(scraped_emails)
    except Exception as e:
        print(f"   ⚠️ Email scraping failed: {e}")

    # Step 2: Google search for published email
    try:
        google_email = await search_email_google(first_name, last_name, domain)
        if google_email:
            email_candidates.append(google_email)
    except Exception as e:
        print(f"   ⚠️ Google email search failed: {e}")

    # Step 3: Generate common patterns
    patterns = generate_email_patterns(first_name, last_name, domain)
    email_candidates.extend(patterns)

    return email_candidates


async def batch_verify_emails(email_candidates_by_lead: Dict[int, List[str]]) -> Dict[int, Optional[str]]:
    """
    Batch verify emails for multiple leads at once.

    Args:
        email_candidates_by_lead: Dict mapping lead index to list of candidate emails

    Returns:
        Dict mapping lead index to verified email (or None if no email verified)
    """
    # Collect all unique emails
    all_emails = set()
    for emails in email_candidates_by_lead.values():
        all_emails.update(emails)

    if not all_emails:
        return {idx: None for idx in email_candidates_by_lead.keys()}

    print(f"\n📧 Batch verifying {len(all_emails)} unique emails for {len(email_candidates_by_lead)} leads...")

    # Verify all at once
    results = await validate_emails_truelist(list(all_emails))

    # Map results back to leads - for each lead, find the first verified email
    verified_by_lead = {}
    for idx, candidates in email_candidates_by_lead.items():
        verified_email = None
        for email in candidates:
            result = results.get(email, {})
            if result.get("passed") is True:
                verified_email = email
                break
        verified_by_lead[idx] = verified_email

    verified_count = sum(1 for v in verified_by_lead.values() if v)
    print(f"   ✅ Verified emails for {verified_count}/{len(email_candidates_by_lead)} leads")

    return verified_by_lead


async def get_verified_email(first_name: str, last_name: str, domain: str) -> Optional[str]:
    """
    Find and verify an email for a person at a company.

    Strategy (in order):
    1. Website scraping - most reliable if found
    2. Google search - find published emails
    3. Pattern generation + TrueList - validate common patterns
    4. Catch-all fallback - use most common pattern if domain accepts all

    Args:
        first_name: Person's first name
        last_name: Person's last name
        domain: Company domain

    Returns:
        First verified email address, or None if all fail/timeout
    """
    full_name = f"{first_name} {last_name}"

    # === STEP 1: Try scraping emails from website ===
    scraped_emails = await scrape_emails_from_website(domain, target_name=full_name)

    if scraped_emails:
        print(f"   📧 Validating {len(scraped_emails)} scraped email(s) via TrueList...")
        scraped_results = await validate_emails_truelist(scraped_emails)

        # Return first verified scraped email
        for email in scraped_emails:
            result = scraped_results.get(email, {})
            if result.get("passed") is True:
                print(f"   ✅ Verified scraped email: {email}")
                return email

        # Check name-matched emails even if not explicitly verified
        name_parts = [p.lower() for p in full_name.split() if len(p) > 2]
        for email in scraped_emails:
            email_local = email.split('@')[0].lower()
            if any(part in email_local for part in name_parts):
                result = scraped_results.get(email, {})
                if result.get("passed") is not False:
                    print(f"   ✅ Using name-matched scraped email: {email}")
                    return email

    # === STEP 2: Google search for published email ===
    google_email = await search_email_google(first_name, last_name, domain)
    if google_email:
        print(f"   ✅ Found email via Google search: {google_email}")
        return google_email

    # === STEP 3: Pattern generation + validation ===
    patterns = generate_email_patterns(first_name, last_name, domain)

    if not patterns:
        return None

    print(f"   📧 Validating {len(patterns)} email patterns for {first_name} {last_name}@{domain}")

    # Validate patterns
    results = await validate_emails_truelist(patterns)

    # Return first verified email
    for pattern in patterns:
        result = results.get(pattern, {})
        if result.get("passed") is True:
            print(f"   ✅ Verified email: {pattern}")
            return pattern

    # === STEP 4: Catch-all fallback ===
    # Many SMBs use catch-all email servers that accept any address
    # If TrueList said 0 OK but didn't explicitly fail, use the most common pattern
    if _get_truelist_api_key():
        # Check if any pattern didn't explicitly fail (might be catch-all)
        for pattern in patterns:
            result = results.get(pattern, {})
            # If status is not explicitly "failed", domain might accept all
            if result.get("passed") is None or result.get("status") in ["unknown", "timeout", "error"]:
                print(f"   ⚠️ Using catch-all fallback: {patterns[0]}")
                return patterns[0]

    # No TrueList key - return first pattern
    if not _get_truelist_api_key():
        print(f"   ⚠️ No TrueList key - using unverified: {patterns[0]}")
        return patterns[0]

    print(f"   ❌ No valid email found for {first_name} {last_name}@{domain}")
    return None


# ============================================================================
# PRE-SUBMISSION VALIDATION
# ============================================================================
# These functions validate leads BEFORE submission to reduce rejections.

def validate_employee_count(employee_count: Any) -> Tuple[bool, str]:
    """
    Validate employee_count against the allowed LinkedIn ranges.

    Accepts values already in valid format or values that can be normalized.
    """
    if employee_count is None:
        return False, "employee_count_empty"

    raw = str(employee_count).strip()
    if not raw:
        return False, "employee_count_empty"

    if raw in VALID_EMPLOYEE_RANGES:
        return True, ""

    normalized = normalize_employee_count(raw)
    if normalized in VALID_EMPLOYEE_RANGES:
        return True, ""

    return False, f"employee_count_invalid: '{raw}' not in valid ranges"

# Based on common rejection reasons from validators:
#   - Invalid Region: 20.9%
#   - Invalid Role: 18.6%
#   - Invalid Website: 10.5%
#   - Invalid Description: 10.0%
#   - LinkedIn URL format: 4.9%
# ============================================================================

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


def validate_role_format(role: str, full_name: str = "", company: str = "") -> Tuple[bool, str]:
    """
    Validate role format to prevent "Invalid Role" rejections.

    Matches validator's validate_role_format() checks.

    Args:
        role: Job title/role
        full_name: Person's full name (to check it's not embedded)
        company: Company name (to check it's not embedded)

    Returns:
        (is_valid, error_message)
    """
    if not role or not role.strip():
        return False, "role_empty"

    role = role.strip()
    role_lower = role.lower()

    # CHECK 1: Role too long (legitimate titles are < 80 chars)
    if len(role) > 80:
        return False, f"role_too_long: {len(role)} chars > 80"

    # CHECK 2: Marketing sentences/taglines (period followed by sentence)
    if re.search(r'\.\s+[A-Z][a-z]+\s+[a-z]+\s+[a-z]+', role):
        return False, "role_has_tagline: contains marketing sentence"

    # CHECK 3: Role ends with country/city names (geographic gaming)
    geographic_patterns = [
        r'[-–,]\s*(Vietnam|Cambodia|India|China|Philippines|Indonesia|Thailand|Malaysia|Singapore)',
        r'[-–,]\s*(Mexico|Canada|Brazil|Argentina|Chile|Colombia)',
        r'[-–,]\s*(Germany|France|UK|Spain|Italy|Netherlands|Belgium|Switzerland)',
        r'[-–,]\s*(Japan|Korea|Taiwan|Hong Kong)',
        r'[-–,]\s*(Australia|New Zealand)',
        r'[-–,]\s*(UAE|Saudi Arabia|Qatar|Kuwait)',
        r'[-–,]\s*(United States|United Kingdom)',
        r'[-–]\s*(APAC|EMEA|LATAM|MENA)\s*$',
        r'[-–]\s*(Asia Pacific|Asia-Pacific)\s*$',
    ]
    for pattern in geographic_patterns:
        if re.search(pattern, role, re.IGNORECASE):
            return False, "role_has_geography: location should be in region field"

    # CHECK 4: "at [Company]" pattern embedded in role
    if company:
        company_lower = company.lower().strip()
        company_escaped = re.escape(company_lower)
        if re.search(rf'\bat\s+{company_escaped}', role_lower):
            return False, f"role_contains_company: 'at {company}' found"
        if role_lower.rstrip().endswith(company_lower):
            return False, f"role_ends_with_company: ends with '{company}'"

    # CHECK 5: Person's name embedded in role
    if full_name:
        name_parts = full_name.lower().split()
        # Words that are both common names AND appear in job titles/roles
        common_role_words = ['grant', 'case', 'mark', 'bill', 'will', 'ray', 'joy', 'hope', 'faith', 'grace', 'dean', 'chase']
        # Common English words that might appear in names but aren't actually name matches
        common_english_words = ['not', 'the', 'and', 'for', 'all', 'can', 'her', 'was', 'one', 'our', 'out', 'are', 'but', 'van', 'von', 'del', 'los', 'las', 'san']
        skip_words = set(common_role_words + common_english_words)
        for name_part in name_parts:
            # Skip short words (likely particles, prefixes, or common words)
            if len(name_part) < 4:
                continue
            if name_part in skip_words:
                continue
            if re.search(rf'\b{re.escape(name_part)}\b', role_lower):
                return False, f"role_contains_name: '{name_part}' found in role"

    # CHECK 6: Multiple C-suite titles
    c_suite_patterns = ['ceo', 'cto', 'cfo', 'coo', 'cmo', 'cio', 'cpo', 'chief executive', 'chief technology',
                        'chief financial', 'chief operating', 'chief marketing', 'chief information', 'chief product']
    c_suite_count = sum(1 for p in c_suite_patterns if re.search(rf'\b{re.escape(p)}\b', role_lower))
    if c_suite_count > 1:
        return False, "role_multiple_csuite: multiple C-suite titles"

    # CHECK 7: Known garbage patterns
    garbage_single_words = ['enthusiasm', 'passion', 'expert', 'professional', 'leader',
                           'currently', 'former', 'previously', 'now', 'recent',
                           'executive', 'officer', 'director', 'manager']  # Too generic alone
    if role_lower in garbage_single_words:
        return False, "role_too_generic: single generic word"

    # CHECK 8: Starts with articles/prepositions
    if role_lower.startswith(('the ', 'a ', 'an ', 'to ', 'for ', 'at ', 'in ', 'of ', 'by ', 'as ')):
        return False, "role_starts_with_article: starts with article/preposition"

    return True, ""


# ============================================================================
# EMAIL VALIDATION (README Requirements)
# ============================================================================

# General purpose emails that are BLOCKED by validators
BLOCKED_EMAIL_PREFIXES = [
    'info', 'contact', 'hello', 'team', 'support', 'admin', 'sales',
    'marketing', 'hr', 'jobs', 'careers', 'press', 'media', 'help',
    'service', 'enquiries', 'enquiry', 'office', 'general', 'mail',
    'webmaster', 'postmaster', 'noreply', 'no-reply', 'donotreply'
]


def validate_email_not_generic(email: str) -> Tuple[bool, str]:
    """
    Check if email is a blocked general purpose address.

    From README: "No general purpose emails - Addresses like hello@, info@,
    team@, support@, contact@ are not accepted"

    Args:
        email: Email address to check

    Returns:
        (is_valid, error_message)
    """
    if not email:
        return False, "email_empty"

    local_part = email.split('@')[0].lower()

    for prefix in BLOCKED_EMAIL_PREFIXES:
        if local_part == prefix or local_part.startswith(prefix + '.') or local_part.startswith(prefix + '_'):
            return False, f"email_generic: '{local_part}@' is a blocked general purpose email"

    return True, ""


def validate_email_name_match(email: str, first_name: str, last_name: str) -> Tuple[bool, str]:
    """
    Validate that contact's name appears in email address.

    From README: "Contact's first or last name must appear in the email address.
    We accept 26 common patterns plus partial matches."

    The 26 valid patterns (for john doe):
    Starting with first name:
      johndoe, john.doe, john_doe, john-doe
      johnd, john.d, john_d, john-d
      jdoe, j.doe, j_doe, j-doe

    Starting with last name:
      doejohn, doe.john, doe_john, doe-john
      doej, doe.j, doe_j, doe-j
      djohn, d.john, d_john, d-john

    Single tokens:
      john, doe

    Args:
        email: Email address
        first_name: Contact's first name
        last_name: Contact's last name

    Returns:
        (is_valid, error_message)
    """
    if not email or not first_name or not last_name:
        return False, "email_name_match_missing_data"

    local_part = email.split('@')[0].lower()
    first = re.sub(r'[^a-zA-Z]', '', first_name).lower()
    last = re.sub(r'[^a-zA-Z]', '', last_name).lower()

    if not first or not last:
        return False, "email_name_match_invalid_name"

    first_initial = first[0]
    last_initial = last[0]

    # All 26 valid patterns from README
    valid_patterns = [
        # Starting with first name (full)
        f"{first}{last}",      # johndoe
        f"{first}.{last}",     # john.doe
        f"{first}_{last}",     # john_doe
        f"{first}-{last}",     # john-doe

        # Starting with first name (initial last)
        f"{first}{last_initial}",     # johnd
        f"{first}.{last_initial}",    # john.d
        f"{first}_{last_initial}",    # john_d
        f"{first}-{last_initial}",    # john-d

        # Starting with first initial
        f"{first_initial}{last}",     # jdoe
        f"{first_initial}.{last}",    # j.doe
        f"{first_initial}_{last}",    # j_doe
        f"{first_initial}-{last}",    # j-doe

        # Starting with last name (full)
        f"{last}{first}",      # doejohn
        f"{last}.{first}",     # doe.john
        f"{last}_{first}",     # doe_john
        f"{last}-{first}",     # doe-john

        # Starting with last name (initial first)
        f"{last}{first_initial}",     # doej
        f"{last}.{first_initial}",    # doe.j
        f"{last}_{first_initial}",    # doe_j
        f"{last}-{first_initial}",    # doe-j

        # Starting with last initial
        f"{last_initial}{first}",     # djohn
        f"{last_initial}.{first}",    # d.john
        f"{last_initial}_{first}",    # d_john
        f"{last_initial}-{first}",    # d-john

        # Single tokens
        first,   # john
        last,    # doe
    ]

    # Check if local part matches any valid pattern
    for pattern in valid_patterns:
        if local_part == pattern:
            return True, ""

    # Also check if the local part STARTS WITH any pattern (for variations like john.doe2)
    for pattern in valid_patterns:
        if local_part.startswith(pattern):
            return True, ""

    return False, f"email_name_mismatch: '{local_part}' does not match name patterns for {first_name} {last_name}"


# Mapping of common LLM-extracted sub_industries to valid taxonomy values
# Format: "extracted_value": ("sub_industry", "industry") - both verified against taxonomy
SUB_INDUSTRY_NORMALIZATION = {
    # Automotive variations → "Automotive" (Transportation)
    "automotive parts manufacturing": ("Automotive", "Transportation"),
    "automotive parts": ("Automotive", "Transportation"),
    "automotive manufacturing": ("Automotive", "Transportation"),
    "auto parts": ("Automotive", "Transportation"),
    "auto manufacturing": ("Automotive", "Transportation"),
    "vehicle manufacturing": ("Automotive", "Transportation"),
    "car manufacturing": ("Automotive", "Transportation"),
    # Robotics variations → "Robotics" (Hardware)
    "robotics manufacturing": ("Robotics", "Hardware"),
    "industrial robotics": ("Robotics", "Hardware"),
    "automation robotics": ("Robotics", "Hardware"),
    "robotics": ("Robotics", "Hardware"),
    "robotic": ("Robotics", "Hardware"),
    "3d scanning": ("Robotics", "Hardware"),
    # Boat/Marine - no boat manufacturing in taxonomy, use generic Manufacturing
    "boat manufacturing": ("Manufacturing", "Manufacturing"),
    "marine manufacturing": ("Manufacturing", "Manufacturing"),
    "shipbuilding": ("Manufacturing", "Manufacturing"),
    # Machinery variations → "Machinery Manufacturing" (Manufacturing)
    "machinery manufacturing": ("Machinery Manufacturing", "Manufacturing"),
    "industrial machinery": ("Machinery Manufacturing", "Manufacturing"),
    "heavy machinery": ("Machinery Manufacturing", "Manufacturing"),
    "machinery": ("Machinery Manufacturing", "Manufacturing"),
    # Precision/CNC variations → "Machinery Manufacturing" (Manufacturing)
    "precision manufacturing": ("Machinery Manufacturing", "Manufacturing"),
    "precision machining": ("Machinery Manufacturing", "Manufacturing"),
    "cnc machining": ("Machinery Manufacturing", "Manufacturing"),
    "cnc manufacturing": ("Machinery Manufacturing", "Manufacturing"),
    "cnc machinery": ("Machinery Manufacturing", "Manufacturing"),
    "cnc": ("Machinery Manufacturing", "Manufacturing"),
    # Metal variations → "Machinery Manufacturing" (Manufacturing)
    "metal fabrication": ("Machinery Manufacturing", "Manufacturing"),
    "metal manufacturing": ("Machinery Manufacturing", "Manufacturing"),
    "metal components": ("Machinery Manufacturing", "Manufacturing"),
    "metal components manufacturing": ("Machinery Manufacturing", "Manufacturing"),
    "sheet metal": ("Machinery Manufacturing", "Manufacturing"),
    # Plastic variations → "Plastics and Rubber Manufacturing" (Manufacturing)
    "plastic manufacturing": ("Plastics and Rubber Manufacturing", "Manufacturing"),
    "plastic injection molding": ("Plastics and Rubber Manufacturing", "Manufacturing"),
    "injection molding": ("Plastics and Rubber Manufacturing", "Manufacturing"),
    "plastics": ("Plastics and Rubber Manufacturing", "Manufacturing"),
    # Electronics variations → "Electronics" (Hardware)
    "electronics manufacturing": ("Electronics", "Hardware"),
    "electronic manufacturing": ("Electronics", "Hardware"),
    "pcb manufacturing": ("Electronics", "Hardware"),
    # General manufacturing → "Manufacturing" (Manufacturing)
    "general manufacturing": ("Manufacturing", "Manufacturing"),
    "contract manufacturing": ("Manufacturing", "Manufacturing"),
    "custom manufacturing": ("Manufacturing", "Manufacturing"),
    "manufacturing": ("Manufacturing", "Manufacturing"),
}


def normalize_sub_industry(sub_industry: str, industry: str = None) -> tuple:
    """
    Normalize LLM-extracted sub_industry to match taxonomy values.

    Args:
        sub_industry: Raw sub_industry from extraction
        industry: Raw industry from extraction (optional)

    Returns:
        Tuple of (normalized_sub_industry, normalized_industry)
    """
    if not sub_industry:
        return (sub_industry, industry)

    # Check exact match in normalization map (case-insensitive)
    sub_lower = sub_industry.lower().strip()
    if sub_lower in SUB_INDUSTRY_NORMALIZATION:
        return SUB_INDUSTRY_NORMALIZATION[sub_lower]

    # Check if contains key phrases
    for phrase, normalized in SUB_INDUSTRY_NORMALIZATION.items():
        if phrase in sub_lower:
            return normalized

    return (sub_industry, industry)


def validate_industry_taxonomy(industry: str, sub_industry: str) -> Tuple[bool, str]:
    """
    Validate that industry and sub_industry match the official taxonomy.

    From README: "Industry & Sub-Industry: Must be exact values from
    validator_models/industry_taxonomy.py"

    Args:
        industry: Industry category
        sub_industry: Sub-industry category

    Returns:
        (is_valid, error_message)
    """
    # Import taxonomy - only when needed to avoid circular imports
    try:
        import sys
        import os
        # Add project root to path if needed
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        from validator_models.industry_taxonomy import INDUSTRY_TAXONOMY
    except ImportError:
        # Fallback to direct file import if module path is unavailable
        import importlib.util
        taxonomy_path = os.path.join(project_root, "validator_models", "industry_taxonomy.py")
        if not os.path.exists(taxonomy_path):
            return False, "industry_taxonomy_missing"
        spec = importlib.util.spec_from_file_location("industry_taxonomy", taxonomy_path)
        if not spec or not spec.loader:
            return False, "industry_taxonomy_missing"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        INDUSTRY_TAXONOMY = getattr(module, "INDUSTRY_TAXONOMY", None)
        if not INDUSTRY_TAXONOMY:
            return False, "industry_taxonomy_missing"

    if not sub_industry:
        return False, "sub_industry_empty"

    # Check if sub_industry exists in taxonomy
    if sub_industry not in INDUSTRY_TAXONOMY:
        # Try case-insensitive match
        sub_lower = sub_industry.lower()
        matched = None
        for key in INDUSTRY_TAXONOMY:
            if key.lower() == sub_lower:
                matched = key
                break

        if not matched:
            return False, f"sub_industry_invalid: '{sub_industry}' not in taxonomy"

    # If industry provided, check it's valid for this sub_industry
    if industry:
        taxonomy_entry = INDUSTRY_TAXONOMY.get(sub_industry, {})
        valid_industries = taxonomy_entry.get("industries", [])

        if valid_industries and industry not in valid_industries:
            # Try case-insensitive match
            industry_lower = industry.lower()
            matched = False
            for valid in valid_industries:
                if valid.lower() == industry_lower:
                    matched = True
                    break

            if not matched:
                return False, f"industry_mismatch: '{industry}' not valid for sub_industry '{sub_industry}'"

    return True, ""


def validate_website_url(url: str) -> Tuple[bool, str]:
    """
    Validate website URL format.

    Args:
        url: Website URL

    Returns:
        (is_valid, error_message)
    """
    if not url or not url.strip():
        return False, "website_empty"

    url = url.strip()

    # Add protocol if missing
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    try:
        parsed = urlparse(url)

        # Must have netloc (domain)
        if not parsed.netloc:
            return False, "website_no_domain"

        # Domain must have at least one dot
        if '.' not in parsed.netloc:
            return False, "website_invalid_domain: no TLD"

        # Check for common invalid patterns
        invalid_patterns = ['example.com', 'test.com', 'localhost', '127.0.0.1', 'n/a', 'none']
        netloc_lower = parsed.netloc.lower()
        if any(inv in netloc_lower for inv in invalid_patterns):
            return False, f"website_placeholder: '{parsed.netloc}' is not valid"

    except Exception as e:
        return False, f"website_parse_error: {str(e)}"

    return True, ""


def validate_description(description: str) -> Tuple[bool, str]:
    """
    Validate description content.

    Args:
        description: Company/lead description

    Returns:
        (is_valid, error_message)
    """
    if not description or not description.strip():
        return False, "description_empty"

    description = description.strip()

    # Must be at least 20 chars
    if len(description) < 20:
        return False, f"description_too_short: {len(description)} chars < 20"

    # Check for placeholder/garbage text
    garbage_patterns = [
        r'^n/?a$',
        r'^none$',
        r'^null$',
        r'^undefined$',
        r'^test$',
        r'lorem ipsum',
        r'^company description$',
        r'^business description$',
    ]
    desc_lower = description.lower()
    for pattern in garbage_patterns:
        if re.search(pattern, desc_lower):
            return False, "description_placeholder: contains placeholder text"

    return True, ""


def validate_linkedin_url(url: str, profile_type: str = "person") -> Tuple[bool, str]:
    """
    Validate LinkedIn URL format.

    Args:
        url: LinkedIn URL
        profile_type: "person" (requires /in/) or "company" (requires /company/)

    Returns:
        (is_valid, error_message)
    """
    if not url or not url.strip():
        return False, "linkedin_empty"

    url = url.strip()

    # Add protocol if missing
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"linkedin_parse_error: {str(e)}"

    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    # Must be linkedin.com or a subdomain
    if netloc != "linkedin.com" and not netloc.endswith(".linkedin.com"):
        return False, "linkedin_not_linkedin: not a linkedin.com URL"

    path = parsed.path.lower()

    if profile_type == "person":
        # Person profiles must have /in/
        if '/in/' not in path:
            # Check if it's accidentally a company page
            if '/company/' in path:
                return False, "linkedin_wrong_type: company URL for person field"
            return False, "linkedin_missing_in: person URL must have /in/"

        # Extract slug and validate it's not empty
        match = re.search(r'/in/([^/?#]+)', path)
        if not match or not match.group(1):
            return False, "linkedin_empty_slug: /in/ has no profile slug"

    elif profile_type == "company":
        # Company profiles must have /company/
        if '/company/' not in path:
            # Check if it's accidentally a person page
            if '/in/' in path:
                return False, "linkedin_wrong_type: person URL for company field"
            return False, "linkedin_missing_company: company URL must have /company/"

        # Extract slug and validate
        match = re.search(r'/company/([^/?#]+)', path)
        if not match or not match.group(1):
            return False, "linkedin_empty_slug: /company/ has no company slug"

    return True, ""


# Restricted data sources (prohibited without license)
RESTRICTED_SOURCES = [
    "zoominfo.com", "apollo.io", "people-data-labs.com", "peopledatalabs.com",
    "rocketreach.co", "hunter.io", "snov.io", "lusha.com", "clearbit.com",
    "leadiq.com", "seamless.ai", "cognism.com", "uplead.com", "salesintel.io",
]

# Valid source types (from validator)
VALID_SOURCE_TYPES = [
    "public_registry",      # LinkedIn, Crunchbase, .gov
    "company_site",         # Direct from company website
    "first_party_form",     # Contact/form pages
    "licensed_resale",      # Licensed data broker (requires license_doc_hash)
    "proprietary_database", # Proprietary database
]


def validate_source_url(source_url: str, source_type: str) -> Tuple[bool, str]:
    """
    Validate source URL and source type.

    Args:
        source_url: URL where lead was sourced
        source_type: Type of source (company_site, public_registry, etc.)

    Returns:
        (is_valid, error_message)
    """
    # Check source_url is present
    if not source_url or not source_url.strip():
        return False, "source_url_empty"

    source_url = source_url.strip()

    # Check source_type is present
    if not source_type or not source_type.strip():
        return False, "source_type_empty"

    source_type = source_type.strip().lower()

    # Check source_type is valid
    if source_type not in VALID_SOURCE_TYPES:
        return False, f"source_type_invalid: '{source_type}' not in {VALID_SOURCE_TYPES}"

    # Allow explicit proprietary database marker
    if source_type == "proprietary_database" and source_url.lower() == "proprietary_database":
        return True, ""

    # Add protocol if missing
    if not source_url.startswith(('http://', 'https://')):
        source_url = 'https://' + source_url

    try:
        parsed = urlparse(source_url)

        # Must have netloc (domain)
        if not parsed.netloc:
            return False, "source_url_no_domain"

        # Domain must have at least one dot
        if '.' not in parsed.netloc:
            return False, "source_url_invalid_domain"

        # Check against restricted sources
        domain_lower = parsed.netloc.lower()
        if domain_lower.startswith("www."):
            domain_lower = domain_lower[4:]

        for restricted in RESTRICTED_SOURCES:
            if restricted in domain_lower:
                return False, f"source_url_restricted: {restricted} is a prohibited data source"

    except Exception as e:
        return False, f"source_url_parse_error: {str(e)}"

    return True, ""


def validate_lead_pre_submission(lead: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Run all pre-submission validations on a lead.

    This catches issues BEFORE submission to avoid wasting rate limit quota
    on leads that will definitely be rejected.

    Args:
        lead: Lead dictionary

    Returns:
        (is_valid, list_of_errors)
    """
    errors = []

    # Validate location (20.9% of rejections)
    city = lead.get("city", "")
    state = lead.get("state", "")
    country = lead.get("country", "")

    loc_valid, loc_error = validate_location(city, state, country)
    if not loc_valid:
        errors.append(f"REGION: {loc_error}")

    # Validate role (18.6% of rejections)
    role = lead.get("role", "")
    full_name = lead.get("full_name", "")
    company = lead.get("business", "")

    role_valid, role_error = validate_role_format(role, full_name, company)
    if not role_valid:
        errors.append(f"ROLE: {role_error}")

    # Validate website (10.5% of rejections)
    website = lead.get("website", "")

    web_valid, web_error = validate_website_url(website)
    if not web_valid:
        errors.append(f"WEBSITE: {web_error}")

    # Validate description (10.0% of rejections)
    description = lead.get("description", "")

    desc_valid, desc_error = validate_description(description)
    if not desc_valid:
        errors.append(f"DESCRIPTION: {desc_error}")

    # Validate employee count format (required field)
    employee_count = lead.get("employee_count", "")
    emp_valid, emp_error = validate_employee_count(employee_count)
    if not emp_valid:
        errors.append(f"EMPLOYEE_COUNT: {emp_error}")

    # Validate LinkedIn URLs (4.9% of rejections)
    person_linkedin = lead.get("linkedin", "")
    company_linkedin = lead.get("company_linkedin", "")

    if person_linkedin:
        li_valid, li_error = validate_linkedin_url(person_linkedin, "person")
        if not li_valid:
            errors.append(f"LINKEDIN: {li_error}")

    if company_linkedin:
        cli_valid, cli_error = validate_linkedin_url(company_linkedin, "company")
        if not cli_valid:
            errors.append(f"COMPANY_LINKEDIN: {cli_error}")

    # Validate source URL (5.3% of rejections)
    source_url = lead.get("source_url", "")
    source_type = lead.get("source_type", "")

    src_valid, src_error = validate_source_url(source_url, source_type)
    if not src_valid:
        errors.append(f"SOURCE: {src_error}")

    # Validate email is not generic (README requirement)
    email = lead.get("email", "")
    if email:
        generic_valid, generic_error = validate_email_not_generic(email)
        if not generic_valid:
            errors.append(f"EMAIL: {generic_error}")

    # Validate email matches name patterns (README requirement - 26 patterns)
    first_name = lead.get("first", "")
    last_name = lead.get("last", "")
    if email and first_name and last_name:
        name_match_valid, name_match_error = validate_email_name_match(email, first_name, last_name)
        if not name_match_valid:
            errors.append(f"EMAIL: {name_match_error}")

    # Validate industry/sub_industry against taxonomy (README requirement)
    industry = lead.get("industry", "")
    sub_industry = lead.get("sub_industry", "")
    if sub_industry:
        tax_valid, tax_error = validate_industry_taxonomy(industry, sub_industry)
        if not tax_valid:
            errors.append(f"INDUSTRY: {tax_error}")

    return len(errors) == 0, errors


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


def clean_role_for_submission(role: str, full_name: str = "", company: str = "") -> str:
    """
    Clean role field to improve acceptance rate.

    Removes common patterns that cause rejections.

    Args:
        role: Raw role
        full_name: Person's name
        company: Company name

    Returns:
        Cleaned role
    """
    if not role:
        return ""

    role = role.strip()

    # Remove "at [Company]" pattern
    if company:
        company_escaped = re.escape(company)
        role = re.sub(rf'\s+at\s+{company_escaped}.*$', '', role, flags=re.IGNORECASE)
        role = re.sub(rf',\s*{company_escaped}\s*$', '', role, flags=re.IGNORECASE)

    # Remove geographic endings
    role = re.sub(r'\s*[-–,]\s*(APAC|EMEA|LATAM|MENA|Asia Pacific|Asia-Pacific)\s*$', '', role, flags=re.IGNORECASE)
    role = re.sub(r'\s*[-–,]\s*(Vietnam|Cambodia|India|China|Philippines|Indonesia|Thailand|Malaysia|Singapore)\s*$', '', role, flags=re.IGNORECASE)
    role = re.sub(r'\s*[-–,]\s*(United States|United Kingdom|UK|US|USA)\s*$', '', role, flags=re.IGNORECASE)

    # Remove taglines (everything after ". ")
    if '. ' in role:
        parts = role.split('. ')
        # Keep only the first part if it looks like a title
        role = parts[0]

    # Truncate if too long
    if len(role) > 80:
        role = role[:77] + "..."

    return role.strip()


# ============================================================================
# Lead Scoring & Ranking (from intent_model.py)
# ============================================================================

# Industry keywords for heuristic scoring
INDUSTRY_KEYWORDS = {
    "manufacturing": ["manufacturing", "fabrication", "machining", "production", "assembly", "industrial", "cnc", "metal", "plastic"],
    "technology": ["tech", "software", "saas", "cloud", "ai", "ml", "data", "cyber", "digital", "platform", "app"],
    "healthcare": ["health", "medical", "clinical", "pharma", "biotech", "hospital", "patient", "therapeutic", "diagnostic"],
    "finance": ["finance", "fintech", "bank", "investment", "insurance", "capital", "fund", "trading", "wealth"],
    "retail": ["retail", "ecommerce", "e-commerce", "store", "shop", "consumer", "brand", "product", "merchandise"],
}

# Role keywords for scoring
ROLE_SCORE_KEYWORDS = {
    "executive": ["ceo", "president", "owner", "founder", "co-founder", "principal", "managing director"],
    "c_suite": ["cto", "cfo", "coo", "cmo", "cio", "cso", "cro", "chief"],
    "vp_director": ["vp", "vice president", "director", "head of"],
    "manager": ["manager", "lead", "senior"],
}

# LLM Batch Scoring Prompt - loaded from external file for maintainability
_LEAD_SCORING_PROMPT_PATH = Path(__file__).parent.parent / "config" / "prompts" / "lead_scoring.txt"

def _get_lead_scoring_prompt() -> str:
    """Load the lead scoring prompt from external file."""
    if _LEAD_SCORING_PROMPT_PATH.exists():
        return _LEAD_SCORING_PROMPT_PATH.read_text().strip()
    # Fallback if file not found
    return "You are a B2B lead qualification specialist. Score leads 0.0-1.0 based on ICP fit. Return JSON: [{\"lead_index\": 0, \"score\": 0.5}, ...]"


def _normalize_text(txt: str) -> str:
    """Normalize text for matching."""
    return re.sub(r"[^a-z0-9 ]+", " ", (txt or "").lower()).strip()


def _heuristic_score(lead: Dict[str, Any], icp_config: Dict[str, Any]) -> float:
    """
    Fast heuristic scoring when LLM is unavailable.

    Scores based on:
    - Industry keyword matching
    - Role priority from ICP config
    - Employee count (smaller = better for SMB targeting)
    """
    score = 0.3  # Base score

    # Get ICP text keywords
    icp_text = _normalize_text(icp_config.get("icp_text", ""))
    icp_words = set(icp_text.split())

    # Industry matching
    lead_industry = _normalize_text(lead.get("industry", ""))
    lead_sub = _normalize_text(lead.get("sub_industry", ""))
    lead_desc = _normalize_text(lead.get("description", ""))

    lead_text = f"{lead_industry} {lead_sub} {lead_desc}"
    lead_words = set(lead_text.split())

    # Check overlap with ICP
    overlap = icp_words & lead_words
    if overlap:
        score += min(len(overlap) * 0.1, 0.3)

    # Check industry keywords from config
    industry_keywords = icp_config.get("validation_config", {}).get("industry_keywords", [])
    for kw in industry_keywords:
        if kw.lower() in lead_text:
            score += 0.05
            if score >= 0.9:
                break

    # Role priority scoring
    role = _normalize_text(lead.get("role", ""))
    role_priority = icp_config.get("role_priority", {})

    # Check against role priority map
    best_role_score = 0
    for role_key, priority in role_priority.items():
        if role_key.lower() in role:
            if priority == 1:
                best_role_score = 0.3
            elif priority == 2:
                best_role_score = max(best_role_score, 0.2)
            elif priority == 3:
                best_role_score = max(best_role_score, 0.1)
            break

    score += best_role_score

    # Employee count bonus (prefer smaller companies for SMB targeting)
    emp_count = lead.get("employee_count", "")
    if emp_count in ["2-10", "11-50"]:
        score += 0.1
    elif emp_count in ["51-200"]:
        score += 0.05

    return min(score, 1.0)


async def _llm_batch_score(leads: List[Dict[str, Any]], icp_config: Dict[str, Any]) -> List[float]:
    """
    Score leads using LLM batch processing.

    Args:
        leads: List of lead dictionaries
        icp_config: ICP configuration

    Returns:
        List of scores (0.0-1.0) for each lead
    """
    import httpx

    openrouter_key = os.getenv("OPENROUTER_KEY")
    if not openrouter_key or not leads:
        return [_heuristic_score(lead, icp_config) for lead in leads]

    # Build ICP description
    icp_text = icp_config.get("icp_text", "")
    targeting = icp_config.get("targeting", {})
    sub_industries = targeting.get("sub_industries", [])

    icp_description = f"""ICP: {icp_text}
Target sub-industries: {', '.join(sub_industries[:5])}
Geographic focus: {', '.join(targeting.get('geographic_focus', ['Any']))}
Company sizes: {', '.join(targeting.get('company_sizes', ['Any']))}"""

    # Build lead descriptions
    prompt_parts = [f"BUYER'S IDEAL CUSTOMER PROFILE (ICP):\n{icp_description}\n\nLEADS TO EVALUATE ({len(leads)} total):\n"]

    for i, lead in enumerate(leads):
        prompt_parts.append(
            f"\nLead #{i}:\n"
            f"  Company: {lead.get('business', 'Unknown')}\n"
            f"  Industry: {lead.get('industry', 'Unknown')}\n"
            f"  Sub-industry: {lead.get('sub_industry', 'Unknown')}\n"
            f"  Contact Role: {lead.get('role', 'Unknown')}\n"
            f"  Employee Count: {lead.get('employee_count', 'Unknown')}\n"
            f"  Location: {lead.get('city', '')}, {lead.get('state', '')}, {lead.get('country', '')}\n"
        )

    prompt_user = "".join(prompt_parts)

    print(f"\n🎯 LEAD SCORING: Scoring {len(leads)} leads via LLM...")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "openai/gpt-4o-mini",
                    "temperature": 0.2,
                    "max_tokens": 2000,
                    "messages": [
                        {"role": "system", "content": _get_lead_scoring_prompt()},
                        {"role": "user", "content": prompt_user}
                    ]
                }
            )
            response.raise_for_status()
            result = response.json()

            raw = result["choices"][0]["message"]["content"].strip()

            # Parse JSON response
            if raw.startswith("```"):
                raw = raw.strip("`").lstrip("json").strip()

            # Extract JSON array
            start = raw.find("[")
            end = raw.rfind("]")
            if start != -1 and end != -1:
                json_str = raw[start:end+1]
                scores_data = json.loads(json_str)

                # Build scores list
                scores = [_heuristic_score(lead, icp_config) for lead in leads]  # Default
                for item in scores_data:
                    idx = item.get("lead_index", -1)
                    score = item.get("score", 0.0)
                    if 0 <= idx < len(leads):
                        scores[idx] = float(score)

                print(f"   ✅ LLM scoring complete")
                return scores
            else:
                print(f"   ⚠️ Could not parse LLM response, using heuristic")
                return [_heuristic_score(lead, icp_config) for lead in leads]

    except Exception as e:
        print(f"   ⚠️ LLM scoring failed: {e}, using heuristic")
        return [_heuristic_score(lead, icp_config) for lead in leads]


async def rank_leads(
    leads: List[Dict[str, Any]],
    icp_config: Dict[str, Any],
    use_llm: bool = True
) -> List[Dict[str, Any]]:
    """
    Score and rank leads by ICP fit.

    Uses LLM batch scoring to evaluate leads against the ICP configuration.
    Falls back to heuristic scoring if LLM is unavailable.

    Adds 'lead_score' field to each lead and returns sorted list.

    Args:
        leads: List of lead dictionaries
        icp_config: ICP configuration with targeting criteria
        use_llm: Whether to use LLM scoring (default: True)

    Returns:
        List of leads sorted by score (highest first)
    """
    if not leads:
        return []

    print(f"\n{'='*60}")
    print("📊 LEAD RANKING")
    print(f"{'='*60}")
    print(f"   Leads to rank: {len(leads)}")
    print(f"   Scoring method: {'LLM + Heuristic' if use_llm else 'Heuristic only'}")

    # Get scores
    if use_llm and os.getenv("OPENROUTER_KEY"):
        scores = await _llm_batch_score(leads, icp_config)
    else:
        print(f"   ℹ️ Using heuristic scoring (no OPENROUTER_KEY)")
        scores = [_heuristic_score(lead, icp_config) for lead in leads]

    # Add scores to leads
    for lead, score in zip(leads, scores):
        lead["lead_score"] = round(score, 3)

    # Sort by score (highest first)
    sorted_leads = sorted(leads, key=lambda x: x.get("lead_score", 0), reverse=True)

    # Print top leads
    print(f"\n📊 TOP RANKED LEADS:")
    for i, lead in enumerate(sorted_leads[:10], 1):
        company = lead.get('business', 'Unknown')[:35]
        role = lead.get('role', 'Unknown')[:20]
        score = lead.get('lead_score', 0)
        print(f"   {i:2}. {company:35s} | {role:20s} | score={score:.3f}")

    if len(sorted_leads) > 10:
        print(f"   ... and {len(sorted_leads) - 10} more leads")

    # Score distribution
    high_score = sum(1 for l in sorted_leads if l.get("lead_score", 0) >= 0.7)
    mid_score = sum(1 for l in sorted_leads if 0.4 <= l.get("lead_score", 0) < 0.7)
    low_score = sum(1 for l in sorted_leads if l.get("lead_score", 0) < 0.4)

    print(f"\n📈 SCORE DISTRIBUTION:")
    print(f"   High (0.7+):  {high_score} leads")
    print(f"   Medium (0.4-0.7): {mid_score} leads")
    print(f"   Low (<0.4):   {low_score} leads")

    return sorted_leads
