"""
Search and extraction helpers for enrichment pipeline.
"""

import asyncio
import json
import os
import re
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from miner_models.lead_sorcerer_main.src.enrichment_geo import (
    COUNTRY_ALIASES,
    US_STATES,
    STATE_ABBR_TO_NAME,
    VALID_COUNTRIES,
    is_fake_address,
    is_fake_city,
    parse_location,
    validate_location,
)
from miner_models.lead_sorcerer_main.src.enrichment_normalization import (
    normalize_employee_count,
    normalize_linkedin_url,
)


# ============================================================================
# Google Search via Custom Search Engine (GSE)
# ============================================================================

async def _gse_google_search(query: str, num_results: int = 5) -> List[Dict[str, Any]]:
    """
    Perform Google search using Google Custom Search Engine (GSE).
    """
    import httpx

    gse_api_key = os.getenv("GSE_API_KEY")
    gse_cx = os.getenv("GSE_CX")

    if not gse_api_key or not gse_cx:
        return []

    # GSE API enforces 1-10 results per request
    if num_results < 1:
        num_results = 1
    if num_results > 10:
        num_results = 10

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"key": gse_api_key, "cx": gse_cx, "q": query, "num": num_results}
            )

            if response.status_code != 200:
                error_text = response.text
                print(f"   ⚠️ GSE search returned {response.status_code}: {error_text[:200]}")
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
    """
    if not company_name:
        return {}

    try:
        query = f'site:linkedin.com/company/ "{company_name}"'
        results = await _gse_google_search(query, num_results=3)

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
                emp_data = await _search_company_employee_count(company_name, data.get("linkedin_url", ""))
                if emp_data:
                    data["employee_count"] = emp_data

            return data

        return {}

    except Exception as e:
        print(f"⚠️ LinkedIn company search failed for {company_name}: {e}")
        return {}


async def search_company_linkedin_by_domain(domain: str) -> str:
    """
    Search Google for a LinkedIn company page using domain hints.
    """
    if not domain:
        return ""

    queries = [
        f'site:linkedin.com/company "{domain}"',
        f'"{domain}" "linkedin.com/company"',
        f'"{domain}" LinkedIn company',
    ]

    for query in queries:
        try:
            results = await _gse_google_search(query, num_results=3)
            for item in results:
                link = item.get('link', '')
                if 'linkedin.com/company/' not in link.lower():
                    continue

                normalized = normalize_linkedin_url(link, "company")
                if normalized:
                    return normalized
        except Exception:
            continue

    return ""


def _extract_employee_count_from_text(text: str) -> str:
    if not text:
        return ""

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
        emp_match = re.search(pattern, text, re.IGNORECASE)
        if emp_match:
            raw_count = emp_match.group(1)
            raw_count = raw_count.replace('to', '-').replace('–', '-').replace(' ', '')
            normalized = normalize_employee_count(raw_count)
            if normalized:
                return normalized

    return ""


def _linkedin_company_slug(linkedin_url: str) -> str:
    if not linkedin_url:
        return ""
    match = re.search(r'/company/([^/?#]+)', linkedin_url)
    if not match:
        return ""
    return match.group(1)


async def _search_company_employee_count(company_name: str, linkedin_url: str = "") -> str:
    """
    Secondary search specifically for company employee count.
    """
    if not company_name and not linkedin_url:
        return ""

    search_queries = []
    if linkedin_url:
        slug = _linkedin_company_slug(linkedin_url)
        if slug:
            search_queries.extend([
                f'site:linkedin.com/company/{slug} employees',
                f'"{linkedin_url}" employees',
                f'site:linkedin.com/company/{slug} "company size"',
            ])

    if company_name:
        search_queries.extend([
            f'"{company_name}" employees site:linkedin.com',
            f'"{company_name}" "company size" employees',
            f'"{company_name}" number of employees',
        ])

    for query in search_queries:
        try:
            results = await _gse_google_search(query, num_results=3)

            for item in results:
                snippet = item.get('snippet', '')
                title = item.get('title', '')
                combined = f"{title} {snippet}".strip()
                if not combined:
                    continue

                normalized = _extract_employee_count_from_text(combined)
                if normalized:
                    return normalized

        except Exception:
            continue

    return ""


async def search_company_employee_count(company_name: str, linkedin_url: str = "") -> str:
    """
    Public wrapper for employee count search.
    """
    return await _search_company_employee_count(company_name, linkedin_url)


def _extract_location_from_text(text: str) -> Optional[Dict[str, str]]:
    """
    Extract location info from a block of text.
    """
    if not text:
        return None

    def _is_suspect_city_phrase(city: str) -> bool:
        if not city:
            return True
        city_lower = city.lower().strip()
        if not city_lower:
            return True

        tokens = [t for t in re.split(r'[\s,]+', city_lower) if t]
        if len(tokens) > 4:
            return True

        bad_tokens = {
            "located", "throughout", "across", "location", "locations",
            "map", "showing", "deployment", "coverage", "nationwide",
            "worldwide", "global", "region", "regions", "area",
            "market", "markets", "service", "services"
        }
        if any(t in bad_tokens for t in tokens):
            return True

        if len(tokens) == 1 and tokens[0] in {"north", "south", "east", "west", "central"}:
            return True

        if city_lower.startswith(("located", "across", "throughout")):
            return True

        if city_lower.endswith(("area", "region", "regions")):
            return True

        return False

    # Pattern 1: Full address with ZIP - "123 Main St, City, ST 12345"
    addr_match = re.search(
        r'(\d+\s+[A-Za-z0-9\s\.]+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Court|Ct|Place|Pl|Highway|Hwy|Parkway|Pkwy)[\s,\-]+[A-Za-z\s]+[,\s]+([A-Z]{2})\s+(\d{5})(?:-\d{4})?)',
        text, re.IGNORECASE
    )
    if addr_match:
        full_addr = addr_match.group(1).strip()
        state = addr_match.group(2).upper()
        zip_code = addr_match.group(3)

        state_key = state.lower().strip().replace('.', '')
        state_key = STATE_ABBR_TO_NAME.get(state_key, state_key)
        if state_key in US_STATES and not is_fake_address(full_addr):
            # Extract city from address
            parts = re.split(r'[,\-]', full_addr)
            city = ""
            for part in reversed(parts[:-1]):  # Skip last part (state+zip)
                part = part.strip()
                if part and not re.match(r'^\d', part) and len(part) > 2:
                    city = part.title()
                    break

            if not city or _is_suspect_city_phrase(city):
                return None

            valid_loc, _ = validate_location(city, state_key.title(), "United States")
            if not valid_loc:
                return None

            print(f"   📍 GSE found address: {full_addr}")
            return {
                "hq_location": full_addr,
                "city": city,
                "state": state_key.title(),
                "country": "United States"
            }

    # Pattern 2: City, State ZIP - "Springfield, IL 62701"
    city_state_match = re.search(
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)[,\s]+([A-Z]{2})\s+(\d{5})(?:-\d{4})?',
        text
    )
    if city_state_match:
        city = city_state_match.group(1).strip()
        state = city_state_match.group(2).upper()
        zip_code = city_state_match.group(3)

        state_key = state.lower().strip().replace('.', '')
        state_key = STATE_ABBR_TO_NAME.get(state_key, state_key)
        if state_key in US_STATES and not is_fake_city(city) and not _is_suspect_city_phrase(city):
            valid_loc, _ = validate_location(city, state_key.title(), "United States")
            if not valid_loc:
                return None

            print(f"   📍 GSE found location: {city}, {state} {zip_code}")
            return {
                "hq_location": f"{city}, {state} {zip_code}",
                "city": city,
                "state": state_key.title(),
                "country": "United States"
            }

    # Pattern 3: City, State (no ZIP)
    city_state_match = re.search(
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)[,\s]+([A-Z]{2})(?!\s*\d)',
        text
    )
    if city_state_match:
        city = city_state_match.group(1).strip()
        state = city_state_match.group(2).upper()
        state_key = state.lower().strip().replace('.', '')
        state_key = STATE_ABBR_TO_NAME.get(state_key, state_key)
        if state_key in US_STATES and not is_fake_city(city) and not _is_suspect_city_phrase(city):
            valid_loc, _ = validate_location(city, state_key.title(), "United States")
            if not valid_loc:
                return None

            print(f"   📍 GSE found location: {city}, {state}")
            return {
                "hq_location": f"{city}, {state}",
                "city": city,
                "state": state_key.title(),
                "country": "United States"
            }

    # Pattern 4: City, Country (non-US)
    text_lower = text.lower()
    alias_terms = [term for term in COUNTRY_ALIASES.keys() if len(term) > 3]
    country_terms = sorted(set(alias_terms) | set(VALID_COUNTRIES), key=len, reverse=True)
    for term in country_terms:
        if term not in text_lower:
            continue

        canonical = COUNTRY_ALIASES.get(term, term)
        city_country_match = re.search(
            rf'([A-Z][A-Za-z\s\.\'\-]+)[,\s]+{re.escape(term)}\b',
            text,
            re.IGNORECASE
        )
        if not city_country_match:
            continue

        raw_city = city_country_match.group(1).strip()
        if is_fake_city(raw_city) or _is_suspect_city_phrase(raw_city):
            continue

        parsed_country, parsed_state, parsed_city = parse_location(f"{raw_city}, {canonical}")
        parsed_city = parsed_city or raw_city
        parsed_country = parsed_country or canonical.title()

        print(f"   📍 GSE found location: {parsed_city}, {parsed_country}")
        return {
            "hq_location": f"{parsed_city}, {parsed_country}",
            "city": parsed_city,
            "state": parsed_state or "",
            "country": parsed_country,
        }

    return None


async def search_company_location(company_name: str, domain: str = "") -> Dict[str, str]:
    """
    Search for company location using Google.
    """
    if not company_name:
        return {}

    queries = [
        f'"{company_name}" headquarters address',
        f'"{company_name}" location',
        f'"{company_name}" "{domain}" address',
        f'"{company_name}" "{domain}" "contact us"',
    ]

    for query in queries:
        try:
            results = await _gse_google_search(query, num_results=3)

            for item in results:
                snippet = item.get('snippet', '')
                title = item.get('title', '')
                combined = f"{title} {snippet}"

                location = _extract_location_from_text(combined)
                if location:
                    location["source_url"] = item.get("link", "")
                    return location

        except Exception:
            continue

    return {}


async def search_company_location_by_domain(domain: str) -> Dict[str, str]:
    """
    Search for company location using domain-only queries.
    """
    if not domain:
        return {}

    queries = [
        f'site:{domain} address',
        f'site:{domain} "contact us"',
        f'site:{domain} contact',
        f'site:{domain} location',
        f'site:{domain} headquarters',
    ]

    for query in queries:
        try:
            results = await _gse_google_search(query, num_results=3)
            for item in results:
                snippet = item.get('snippet', '')
                title = item.get('title', '')
                combined = f"{title} {snippet}"

                location = _extract_location_from_text(combined)
                if location:
                    location["source_url"] = item.get("link", "")
                    return location
        except Exception:
            continue

    return {}


async def search_person_linkedin_with_data(full_name: str, company_name: str = "") -> Dict[str, Any]:
    """
    Search Google for person LinkedIn profile and extract data from snippet.
    """
    if not full_name:
        return {}

    try:
        if company_name:
            query = f'site:linkedin.com/in/ "{full_name}" "{company_name}"'
        else:
            query = f'site:linkedin.com/in/ "{full_name}"'

        results = await _gse_google_search(query, num_results=3)

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
                title_clean = re.sub(r'\s*\|\s*LinkedIn\s*$', '', title, flags=re.IGNORECASE).strip()
                parts = re.split(r'\s*[-–]\s*', title_clean)

                if len(parts) >= 1:
                    name = parts[0].strip()
                    if name and len(name) > 2:
                        data["full_name"] = name
                        name_parts = name.split(maxsplit=1)
                        data["first_name"] = name_parts[0] if name_parts else ""
                        data["last_name"] = name_parts[1] if len(name_parts) > 1 else ""

                if len(parts) >= 2:
                    role_part = parts[1].strip()
                    at_match = re.match(r'^(.+?)\s+at\s+(.+)$', role_part, re.IGNORECASE)
                    if at_match:
                        data["role"] = at_match.group(1).strip()
                        data["company"] = at_match.group(2).strip()
                    else:
                        data["role"] = role_part

                if len(parts) >= 3 and not data["company"]:
                    data["company"] = parts[2].strip()

            # Parse snippet for additional data
            if snippet:
                loc_patterns = [
                    r'(Greater\s+[\w\s]+\s+Area)',
                    r'([A-Z][\w\s]+,\s*[A-Z]{2})\b',
                    r'([A-Z][\w\s]+,\s*[A-Z][\w\s]+)\b',
                ]
                for pattern in loc_patterns:
                    loc_match = re.search(pattern, snippet)
                    if loc_match:
                        data["location"] = loc_match.group(1).strip()
                        break

                if not data["role"]:
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
    """
    if not company_name:
        return []

    try:
        query = f'site:linkedin.com/in "{company_name}" (CEO OR Founder OR "Chief" OR President OR Director)'
        results = await _gse_google_search(query, num_results=5)

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
                if not name or len(name) < 3 or name.lower() == "linkedin":
                    continue

                # Extract first and last name
                name_parts = name.split()
                first_name = name_parts[0] if name_parts else ""
                last_name = name_parts[-1] if len(name_parts) > 1 else ""

                if not first_name or not last_name:
                    continue

                # Build contact
                contact = {
                    "full_name": name,
                    "first_name": first_name,
                    "last_name": last_name,
                    "role": role,
                    "linkedin": normalized_linkedin,
                    "email": "",  # Will be filled later if possible
                }

                contacts.append(contact)

        return contacts

    except Exception as e:
        print(f"⚠️ LinkedIn executive search failed for {company_name}: {e}")
        return []


async def search_linkedin_decision_makers(query: str, num_results: int = 10) -> List[Dict[str, Any]]:
    """
    LinkedIn-first pipeline: search for decision makers via GSE snippets.
    """
    if not query:
        return []

    try:
        results = await _gse_google_search(query, num_results=num_results)

        candidates = []

        # Helper: check if string looks like a job title
        def is_likely_role(text: str) -> bool:
            role_keywords = [
                "ceo", "owner", "founder", "president", "director", "chief",
                "vp", "vice president", "manager", "head of", "principal",
                "partner", "co-founder"
            ]
            text_lower = text.lower()
            return any(k in text_lower for k in role_keywords)

        # Helper: check if string looks like company name
        def is_likely_company(text: str) -> bool:
            if not text:
                return False
            # Company names often have Inc, LLC, Co, etc.
            company_keywords = ["inc", "llc", "ltd", "corp", "company", "co"]
            text_lower = text.lower()
            return any(k in text_lower for k in company_keywords) or len(text.split()) <= 4

        for item in results:
            link = item.get("link", "")
            title = item.get("title", "")
            snippet = item.get("snippet", "")

            if "linkedin.com/in/" not in link:
                continue

            linkedin_url = normalize_linkedin_url(link, "profile")
            if not linkedin_url:
                continue

            # Parse title: "Name - Role - Company | LinkedIn"
            title = re.sub(r'\s*\|\s*LinkedIn\s*$', '', title, flags=re.IGNORECASE)
            parts = re.split(r'\s*[-–]\s*', title)

            full_name = parts[0].strip() if parts else ""
            role = ""
            company = ""

            if len(parts) >= 2:
                role = parts[1].strip()
            if len(parts) >= 3:
                company = parts[2].strip()

            # If role or company missing, try parse from snippet
            if not role or not company:
                match = re.search(r'([A-Z][^\.]+?)\s+at\s+([^\.]+)', snippet)
                if match:
                    if not role:
                        role = match.group(1).strip()
                    if not company:
                        company = match.group(2).strip()

            # Validate we have key fields
            if not full_name or not role or not company:
                continue

            # Filter out obvious false positives
            if not is_likely_role(role) or not is_likely_company(company):
                continue

            # Build candidate
            name_parts = full_name.split()
            first_name = name_parts[0] if name_parts else ""
            last_name = name_parts[-1] if len(name_parts) > 1 else ""

            candidates.append({
                "full_name": full_name,
                "first_name": first_name,
                "last_name": last_name,
                "role": role,
                "company": company,
                "linkedin_url": linkedin_url,
                "location": "",
            })

        return candidates

    except Exception as e:
        print(f"⚠️ LinkedIn decision maker search failed: {e}")
        return []


def clean_company_name_for_search(company_name: str) -> str:
    """
    Clean noisy company strings from LinkedIn snippets/titles.
    """
    if not company_name:
        return ""

    cleaned = company_name.replace("\n", " ").strip()

    # Drop LinkedIn metadata segments
    for marker in ["·", "|"]:
        if marker in cleaned:
            cleaned = cleaned.split(marker)[0].strip()

    cleaned = re.sub(
        r'\b(Experience|Education|Location|Connections?)\b.*$',
        '',
        cleaned,
        flags=re.IGNORECASE
    ).strip()

    # If role text leaked into the company string, take the last "at" segment
    role_hints = ["owner", "founder", "president", "ceo", "cto", "cfo", "chief", "vp", "director", "manager"]
    lowered = cleaned.lower()
    if " at " in lowered and any(hint in lowered for hint in role_hints):
        cleaned = cleaned.split(" at ")[-1].strip()

    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def _company_tokens(name: str) -> List[str]:
    cleaned = name.lower()
    cleaned = cleaned.replace("&", " and ")
    cleaned = re.sub(r'[^a-z0-9\s]', ' ', cleaned)
    cleaned = re.sub(r'\b(inc|llc|ltd|corp|corporation|company|co|plc|the)\b', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    tokens = [t for t in cleaned.split() if len(t) > 2]
    return tokens


def _is_blocked_domain(domain: str) -> bool:
    blocked_suffixes = [
        "linkedin.com", "facebook.com", "instagram.com", "twitter.com",
        "x.com", "youtube.com", "wikipedia.org", "crunchbase.com",
        "bloomberg.com", "yelp.com", "yellowpages.com", "opencorporates.com",
        "zoominfo.com", "rocketreach.co", "signalhire.com", "dnb.com",
        "google.com", "googleusercontent.com"
    ]
    for suffix in blocked_suffixes:
        if domain == suffix or domain.endswith(f".{suffix}"):
            return True
    return False


def _score_domain_candidate(domain: str, title: str, snippet: str, tokens: List[str]) -> Optional[float]:
    if not tokens:
        return None
    if _is_blocked_domain(domain):
        return None

    domain_root = domain.split(":")[0].lower()
    domain_root = domain_root.replace("www.", "")
    base = domain_root.split(".")[0]
    base_compact = re.sub(r'[^a-z0-9]', '', base)

    combined = f"{title} {snippet}".lower()
    domain_hits = [t for t in tokens if t in base_compact]
    text_hits = [t for t in tokens if t in combined]
    matched_tokens = set(domain_hits + text_hits)

    if not domain_hits and not text_hits:
        return None

    # For longer company names, require more than a single token match
    if len(tokens) >= 3 and len(matched_tokens) < 2:
        return None

    score = 0.0
    if domain_hits:
        score += 0.6 + min(len(domain_hits) * 0.1, 0.3)
    if text_hits:
        score += min(len(text_hits) * 0.05, 0.2)
    if "official website" in combined or "official site" in combined:
        score += 0.2

    if domain_root.endswith(".gov") or domain_root.endswith(".edu"):
        gov_tokens = {"city", "county", "state", "department", "university", "college", "school", "district", "town", "village"}
        if not any(t in gov_tokens for t in tokens):
            score -= 0.4

    return score


async def find_company_domain(company_name: str) -> Optional[str]:
    """
    Find company domain using Google search.
    """
    if not company_name:
        return None

    cleaned_name = clean_company_name_for_search(company_name)
    if cleaned_name and cleaned_name != company_name:
        print(f"   ℹ️ Cleaned company name for domain search: {company_name} -> {cleaned_name}")
    company_name = cleaned_name or company_name

    tokens = _company_tokens(company_name)
    if not tokens:
        return None

    queries = [
        f"{company_name} official website",
        f"{company_name} company website",
        f"{company_name} domain",
    ]

    best_domain = None
    best_score = -1.0
    seen_domains = set()

    for query in queries:
        try:
            results = await _gse_google_search(query, num_results=3)
            for item in results:
                link = item.get("link", "")
                title = item.get("title", "")
                snippet = item.get("snippet", "")
                if not link:
                    continue

                match = re.match(r'https?://([^/]+)/?', link)
                if not match:
                    continue

                domain = match.group(1).lower().replace("www.", "")
                if "." not in domain or domain in seen_domains:
                    continue

                seen_domains.add(domain)
                score = _score_domain_candidate(domain, title, snippet, tokens)
                if score is None:
                    continue

                if score > best_score:
                    best_domain = domain
                    best_score = score

                if best_score >= 0.8:
                    return best_domain
        except Exception:
            continue

    if best_domain and best_score >= 0.4:
        return best_domain

    if best_domain:
        print(f"   ⚠️ Low-confidence domain match for {company_name}: {best_domain} (score={best_score:.2f})")

    return None


async def generate_linkedin_queries_with_llm(icp_config: Dict[str, Any], num_queries: int = 25) -> List[str]:
    """
    Generate LinkedIn search queries using LLM based on ICP configuration.
    """
    import aiohttp

    openrouter_key = os.getenv("OPENROUTER_KEY")
    if not openrouter_key:
        print("⚠️ OPENROUTER_KEY not set - cannot generate LinkedIn queries")
        return []

    # Build prompt from ICP config
    icp_text = icp_config.get("icp_text", "")
    target_roles = icp_config.get("role_priority", {})
    region = icp_config.get("region", "")

    roles = [role for role, priority in target_roles.items() if priority <= 2]
    if not roles:
        roles = ["CEO", "Founder", "Owner"]

    # Shuffle roles to reduce repetition across runs
    random.shuffle(roles)
    role_str = ", ".join(roles[:8])

    # Optional industry terms for diversity
    targeting = icp_config.get("targeting", {})
    sub_industries = targeting.get("sub_industries", [])
    industries = targeting.get("industries", [])
    industry_terms = list(dict.fromkeys(sub_industries + industries))
    random.shuffle(industry_terms)

    diversity_seed = f"{int(time.time()) % 100000}-{random.randint(0, 999)}"

    max_terms = icp_config.get("search", {}).get("max_terms_per_query", 6)
    min_terms = icp_config.get("search", {}).get("min_terms_per_query", 2)

    prompt = f"""
Generate {num_queries} Google search queries to find LinkedIn profiles of decision makers.

Requirements:
- Each query must start with: site:linkedin.com/in
- Include the role keywords: {role_str}
- Use quotes around specific terms
- Include {icp_text}
- Region focus: {region if region else 'global'}
- Avoid repeating the same query phrasing; vary role and industry terms.
- Diversity seed: {diversity_seed}
- Optional industry terms to mix in: {', '.join(industry_terms[:10]) if industry_terms else 'N/A'}

Return one query per line, no numbering or extra text.
"""

    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {openrouter_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://leadpoet.ai",
            }

            temperature = icp_config.get("search", {}).get("llm_temperature", 0.6)
            payload = {
                "model": "anthropic/claude-3-haiku",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2000,
                "temperature": temperature,
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
                    queries: List[str] = []
                    seen = set()

                    def normalize_query(q: str) -> str:
                        q = q.lower().strip()
                        q = re.sub(r'\s+', ' ', q)
                        return q

                    for line in content.strip().split("\n"):
                        line = line.strip()
                        if not line or not line.lower().startswith("site:linkedin.com"):
                            continue
                        line = re.sub(r'^[\d\.\-\*\)\]]+\s*', '', line)
                        if line.lower().startswith("site:linkedin.com"):
                            term_count = len(line.split()) - 1
                            if min_terms <= term_count <= max_terms:
                                norm = normalize_query(line)
                                if norm not in seen:
                                    seen.add(norm)
                                    queries.append(line)
                            else:
                                print(f"   ⚠️ Skipped query with {term_count} terms: {line[:60]}...")

                    # Fill shortfalls with deterministic variations
                    if len(queries) < num_queries:
                        role_samples = roles[:6]
                        term_samples = industry_terms[:6] if industry_terms else []
                        fallback = []
                        for role in role_samples:
                            if term_samples:
                                for term in term_samples:
                                    fallback.append(f'site:linkedin.com/in "{role}" "{term}"')
                            else:
                                fallback.append(f'site:linkedin.com/in "{role}" "{region or "company"}"')
                        random.shuffle(fallback)
                        for q in fallback:
                            norm = normalize_query(q)
                            if norm not in seen:
                                seen.add(norm)
                                queries.append(q)
                            if len(queries) >= num_queries:
                                break

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

    patterns = [
        r'(\d+\s+[A-Za-z0-9\s\.]+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Court|Ct|Place|Pl|Highway|Hwy|Parkway|Pkwy|Circle|Cir)[\s,\-]+[A-Za-z\s]+[,\s]+[A-Z]{2}\s+\d{5}(?:-\d{4})?)',
        r'(\d+\s+[A-Za-z\s]+[\s,\-]+[A-Za-z\s]+[,\s]+[A-Z]{2}\s+\d{5}(?:-\d{4})?)',
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*[,\s]+[A-Z]{2}\s+\d{5}(?:-\d{4})?)',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            state_match = re.search(r'\b([A-Z]{2})\s+\d{5}', match, re.IGNORECASE)
            if state_match:
                state = state_match.group(1).upper()
                if state in US_STATES:
                    return match.strip()

    return None


async def firecrawl_extract_company(domain: str) -> Dict[str, Any]:
    """
    Extract company and contact data from domain using Firecrawl API.
    """
    import aiohttp

    firecrawl_key = os.getenv("FIRECRAWL_KEY", "")
    if not firecrawl_key:
        print("⚠️ FIRECRAWL_KEY not set - skipping website extraction")
        return {}

    if not domain:
        return {}

    api_url = "https://api.firecrawl.dev/v1/scrape"

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
            "ownership_type": {"type": "string"},
            "company_type": {"type": "string"},
            "number_of_locations": {"type": "integer"},
            "phone_numbers": {
                "type": "array",
                "items": {"type": "string"}
            },
            "socials": {"type": "object"},
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

    # Include legacy "aboutus.asp" style pages (common on older manufacturing sites)
    pages_to_try = [
        f"https://{domain}",
        f"https://{domain}/aboutus.asp",
        f"https://{domain}/aboutus.aspx",
        f"https://{domain}/aboutus",
        f"https://{domain}/about",
        f"https://{domain}/about-us",
        f"https://{domain}/about-us.asp",
        f"https://{domain}/about-us.aspx",
        f"https://{domain}/company",
        f"https://{domain}/company-info",
        f"https://{domain}/company-info.asp",
        f"https://{domain}/companyinfo",
        f"https://{domain}/companyinfo.asp",
        f"https://{domain}/contact",
        f"https://{domain}/contact-us",
        f"https://{domain}/contactus",
        f"https://{domain}/contactus.asp",
        f"https://{domain}/contact-us.asp",
        f"https://{domain}/contact-us.aspx",
    ]

    combined_data = {}
    location_found = False

    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {firecrawl_key}",
                "Content-Type": "application/json"
            }

            # Limit total pages to save API calls, but include about/contact variants
            for url in pages_to_try[:8]:
                if location_found:
                    break

                payload = {
                    "url": url,
                    "formats": ["extract", "markdown"],
                    "waitFor": 3000,
                    "actions": [
                        {"type": "scroll", "direction": "down", "amount": 2000}
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

                try:
                    async with session.post(api_url, json=payload, headers=headers, timeout=20) as response:
                        if response.status == 200:
                            result = await response.json()
                            if result.get("success"):
                                data = result.get("data", {})
                                extracted = data.get("extract", {})
                                markdown = data.get("markdown", "")

                                for key, value in extracted.items():
                                    if not value:
                                        continue

                                    if key == "hq_location" and is_fake_address(value):
                                        # Treat city/state-only strings as location, not fake address
                                        fc_country, fc_state, fc_city = parse_location(value)
                                        if fc_city and not is_fake_city(fc_city):
                                            combined_data.setdefault("city", fc_city)
                                            if fc_state:
                                                combined_data.setdefault("state", fc_state)
                                            if fc_country:
                                                combined_data.setdefault("country", fc_country)
                                            print(f"      ℹ️ Parsed non-address location: {value}")
                                            continue

                                        print(f"      ⚠️ Rejected fake address: {value}")
                                        continue

                                    if key == "city" and is_fake_city(value):
                                        print(f"      ⚠️ Rejected fake city: {value}")
                                        continue

                                    combined_data[key] = value

                                # Track the exact page used for extraction
                                combined_data.setdefault("source_url", url)

                                hq_loc = combined_data.get("hq_location", "")
                                city = combined_data.get("city", "")
                                if (hq_loc and not is_fake_address(hq_loc)) or (city and not is_fake_city(city)):
                                    location_found = True
                                    print(f"      ✓ Found location data")

                                if not location_found and markdown:
                                    extracted_addr = _extract_us_address(markdown)
                                    if extracted_addr and not is_fake_address(extracted_addr):
                                        combined_data["hq_location"] = extracted_addr
                                        location_found = True
                                        print(f"      ✓ Extracted address from page: {extracted_addr[:50]}...")
                                    elif extracted_addr:
                                        print(f"      ⚠️ Rejected hallucinated regex address: {extracted_addr[:50]}...")
                                    else:
                                        md_lower = markdown.lower()
                                        import re as _re
                                        zip_matches = _re.findall(r'\b\d{5}(?:-\d{4})?\b', markdown)
                                        if zip_matches:
                                            print(f"      📝 Found ZIP codes in markdown: {zip_matches[:3]} - address may exist")
                                            for zc in zip_matches[:1]:
                                                idx = markdown.find(zc)
                                                if idx > 0:
                                                    snippet = markdown[max(0, idx-80):idx+20]
                                                    print(f"      📝 Context: ...{snippet.strip()}...")

                        elif response.status != 402:
                            pass

                except asyncio.TimeoutError:
                    continue
                except Exception:
                    continue

            if combined_data:
                print(f"   ✓ Firecrawl extraction successful")

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
