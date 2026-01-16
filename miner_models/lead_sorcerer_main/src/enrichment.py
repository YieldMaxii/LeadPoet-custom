"""
Lead Enrichment Module
======================

Compatibility layer that re-exports enrichment helpers from modularized files.
"""

# Geo helpers
from miner_models.lead_sorcerer_main.src.enrichment_geo import (
    COUNTRY_ALIASES,
    INTERNATIONAL_CITY_ALIASES,
    STATE_ABBR_TO_NAME,
    US_CITY_ALIASES,
    US_STATE_ALIASES,
    VALID_COUNTRIES,
    US_STATES,
    CITIES_BY_COUNTRY,
    US_CITIES_BY_STATE,
    is_fake_address,
    is_fake_city,
    parse_location,
    infer_state_from_city,
    validate_location,
    normalize_location_for_submission,
)

# Normalization helpers
from miner_models.lead_sorcerer_main.src.enrichment_normalization import (
    EMPLOYEE_RANGE_MAP,
    VALID_EMPLOYEE_RANGES,
    normalize_employee_count,
    normalize_linkedin_url,
    normalize_sub_industry,
)

# Search & extraction helpers
from miner_models.lead_sorcerer_main.src.enrichment_search import (
    _gse_google_search,
    clean_company_name_for_search,
    search_company_linkedin_with_data,
    search_company_linkedin_by_domain,
    search_person_linkedin_with_data,
    search_linkedin_executives,
    search_linkedin_decision_makers,
    search_company_location,
    search_company_location_by_domain,
    search_company_employee_count,
    find_company_domain,
    generate_linkedin_queries_with_llm,
    firecrawl_extract_company,
)

# Email helpers
from miner_models.lead_sorcerer_main.src.enrichment_email import (
    TRUELIST_BATCH_URL,
    TRUELIST_POLL_INTERVAL,
    TRUELIST_TIMEOUT,
    _get_truelist_api_key,
    scrape_emails_from_website,
    search_email_google,
    generate_email_patterns,
    validate_emails_truelist,
    collect_email_candidates,
    batch_verify_emails,
    get_verified_email,
)

# Validation helpers
from miner_models.lead_sorcerer_main.src.enrichment_validation import (
    REQUIRED_FIELDS,
    RESTRICTED_SOURCES,
    VALID_SOURCE_TYPES,
    is_lead_complete,
    get_missing_fields,
    validate_role_format,
    validate_email_not_generic,
    validate_email_name_match,
    validate_industry_taxonomy,
    validate_website_url,
    validate_description,
    validate_linkedin_url,
    validate_source_url,
    validate_employee_count,
    validate_lead_pre_submission,
    clean_role_for_submission,
)

# Scoring helpers
from miner_models.lead_sorcerer_main.src.enrichment_scoring import (
    INDUSTRY_KEYWORDS,
    ROLE_SCORE_KEYWORDS,
    rank_leads,
)

__all__ = [
    # Geo
    "COUNTRY_ALIASES",
    "INTERNATIONAL_CITY_ALIASES",
    "STATE_ABBR_TO_NAME",
    "US_CITY_ALIASES",
    "US_STATE_ALIASES",
    "VALID_COUNTRIES",
    "US_STATES",
    "CITIES_BY_COUNTRY",
    "US_CITIES_BY_STATE",
    "is_fake_address",
    "is_fake_city",
    "parse_location",
    "infer_state_from_city",
    "validate_location",
    "normalize_location_for_submission",
    # Normalization
    "EMPLOYEE_RANGE_MAP",
    "VALID_EMPLOYEE_RANGES",
    "normalize_employee_count",
    "normalize_linkedin_url",
    "normalize_sub_industry",
    # Search
    "_gse_google_search",
    "clean_company_name_for_search",
    "search_company_linkedin_with_data",
    "search_company_linkedin_by_domain",
    "search_person_linkedin_with_data",
    "search_linkedin_executives",
    "search_linkedin_decision_makers",
    "search_company_location",
    "search_company_location_by_domain",
    "search_company_employee_count",
    "find_company_domain",
    "generate_linkedin_queries_with_llm",
    "firecrawl_extract_company",
    # Email
    "TRUELIST_BATCH_URL",
    "TRUELIST_POLL_INTERVAL",
    "TRUELIST_TIMEOUT",
    "_get_truelist_api_key",
    "scrape_emails_from_website",
    "search_email_google",
    "generate_email_patterns",
    "validate_emails_truelist",
    "collect_email_candidates",
    "batch_verify_emails",
    "get_verified_email",
    # Validation
    "REQUIRED_FIELDS",
    "RESTRICTED_SOURCES",
    "VALID_SOURCE_TYPES",
    "is_lead_complete",
    "get_missing_fields",
    "validate_role_format",
    "validate_email_not_generic",
    "validate_email_name_match",
    "validate_industry_taxonomy",
    "validate_website_url",
    "validate_description",
    "validate_linkedin_url",
    "validate_source_url",
    "validate_employee_count",
    "validate_lead_pre_submission",
    "clean_role_for_submission",
    # Scoring
    "INDUSTRY_KEYWORDS",
    "ROLE_SCORE_KEYWORDS",
    "rank_leads",
]
