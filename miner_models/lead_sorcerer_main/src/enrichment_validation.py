"""
Validation helpers for lead enrichment.
"""

import os
import re
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

from miner_models.lead_sorcerer_main.src.enrichment_geo import validate_location
from miner_models.lead_sorcerer_main.src.enrichment_normalization import (
    VALID_EMPLOYEE_RANGES,
    normalize_employee_count,
)


# Required fields for lead validation
REQUIRED_FIELDS = [
    "business", "full_name", "first", "last", "email", "role",
    "website", "industry", "sub_industry", "country", "city",
    "linkedin", "company_linkedin", "source_url", "description", "employee_count"
]


# ============================================================================
# Lead Completeness Validation
# ============================================================================

def is_lead_complete(lead: Dict[str, Any]) -> bool:
    """
    Check if all required fields are present and non-empty.
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
    """
    missing = [f for f in REQUIRED_FIELDS if not lead.get(f)]

    # Check state for US
    if lead.get("country") == "United States" and not lead.get("state"):
        if "state" not in missing:
            missing.append("state")

    return missing


# ============================================================================
# Role Validation
# ============================================================================

def validate_role_format(role: str, full_name: str = "", company: str = "") -> Tuple[bool, str]:
    """
    Validate role format to prevent "Invalid Role" rejections.
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
# Email Validation (README Requirements)
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


# ============================================================================
# Industry taxonomy validation
# ============================================================================

def validate_industry_taxonomy(industry: str, sub_industry: str) -> Tuple[bool, str]:
    """
    Validate that industry and sub_industry match the official taxonomy.
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


def validate_employee_count(employee_count: Any) -> Tuple[bool, str]:
    """
    Validate employee_count against the allowed LinkedIn ranges.
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


def validate_lead_pre_submission(lead: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Run all pre-submission validations on a lead.
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


def clean_role_for_submission(role: str, full_name: str = "", company: str = "") -> str:
    """
    Clean role field to improve acceptance rate.
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
