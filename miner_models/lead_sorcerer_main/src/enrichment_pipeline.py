"""
Concurrency, caching, and provenance helpers for enrichment pipelines.
"""

import asyncio
import time
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from miner_models.lead_sorcerer_main.src.enrichment_cache import EnrichmentCache
from miner_models.lead_sorcerer_main.src.enrichment_geo import (
    is_fake_address,
    is_fake_city,
    parse_location,
)
from miner_models.lead_sorcerer_main.src.enrichment_normalization import (
    normalize_employee_count,
    normalize_sub_industry,
)
from miner_models.lead_sorcerer_main.src.enrichment import (
    search_company_linkedin_with_data,
    search_company_location,
    search_person_linkedin_with_data,
    firecrawl_extract_company,
    find_company_domain,
)
from miner_models.lead_sorcerer_main.src.enrichment_search import (
    search_company_linkedin_by_domain,
    search_company_employee_count,
    search_company_location_by_domain,
)


@dataclass
class FieldProvenance:
    source: str
    confidence: float
    method: str
    timestamp: float


@dataclass
class EnrichmentConfig:
    timeouts: Dict[str, float]
    confidence_thresholds: Dict[str, float]
    min_company_match: float


DEFAULT_TIMEOUTS = {
    "company_linkedin": 12.0,
    "company_location": 10.0,
    "firecrawl": 20.0,
    "person_linkedin": 12.0,
    "domain_lookup": 8.0,
}

DEFAULT_CONFIDENCE_THRESHOLDS = {
    "company_linkedin": 0.5,
    "employee_count": 0.45,
    "description": 0.5,
    "industry": 0.5,
    "sub_industry": 0.5,
    "role": 0.5,
    "linkedin": 0.5,
    "city": 0.45,
    "state": 0.45,
    "country": 0.45,
}


def build_enrichment_config(icp_config: Dict[str, Any]) -> EnrichmentConfig:
    thresholds = dict(DEFAULT_CONFIDENCE_THRESHOLDS)
    timeouts = dict(DEFAULT_TIMEOUTS)
    overrides = icp_config.get("enrichment_confidence_thresholds", {})
    thresholds.update(overrides)
    timeout_overrides = icp_config.get("enrichment_timeouts", {})
    timeouts.update(timeout_overrides)
    min_match = icp_config.get("min_company_match_score", 0.55)
    return EnrichmentConfig(timeouts=timeouts, confidence_thresholds=thresholds, min_company_match=min_match)


async def _with_timeout(coro, timeout: float, default: Any) -> Any:
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except Exception:
        return default


async def _return_value(value: Any) -> Any:
    return value


def _normalize_company_name(name: str) -> str:
    if not name:
        return ""
    cleaned = name.lower()
    cleaned = cleaned.replace("&", " and ")
    cleaned = re.sub(r'[^a-z0-9\s]', ' ', cleaned)
    cleaned = re.sub(r'\b(inc|llc|ltd|corp|corporation|company|co|plc)\b', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def _company_match_score(candidate: str, target: str) -> float:
    cand = _normalize_company_name(candidate)
    tgt = _normalize_company_name(target)
    if not cand or not tgt:
        return 0.0
    if cand == tgt:
        return 1.0
    # Token overlap + sequence ratio
    cand_tokens = set(cand.split())
    tgt_tokens = set(tgt.split())
    if not cand_tokens or not tgt_tokens:
        return 0.0
    overlap = len(cand_tokens & tgt_tokens) / max(len(tgt_tokens), 1)
    ratio = SequenceMatcher(None, cand, tgt).ratio()
    return max(overlap, ratio * 0.8)


def _add_candidate(
    candidates: Dict[str, List[Tuple[Any, FieldProvenance]]],
    field: str,
    value: Any,
    source: str,
    confidence: float,
    method: str,
) -> None:
    if value is None or value == "":
        return
    candidates.setdefault(field, []).append(
        (value, FieldProvenance(source=source, confidence=confidence, method=method, timestamp=time.time()))
    )


def _select_best(
    candidates: Dict[str, List[Tuple[Any, FieldProvenance]]],
    thresholds: Dict[str, float],
) -> Tuple[Dict[str, Any], Dict[str, FieldProvenance]]:
    selected: Dict[str, Any] = {}
    provenance: Dict[str, FieldProvenance] = {}
    for field, options in candidates.items():
        if not options:
            continue
        best_value, best_prov = max(options, key=lambda x: x[1].confidence)
        selected[field] = best_value
        provenance[field] = best_prov

        threshold = thresholds.get(field)
        if threshold is not None and best_prov.confidence < threshold:
            print(f"   ⚠️ Low-confidence {field} ({best_prov.confidence:.2f}) from {best_prov.source}")
    return selected, provenance


def passes_confidence_gating(
    required_fields: List[str],
    provenance: Dict[str, FieldProvenance],
    thresholds: Dict[str, float],
) -> Tuple[bool, List[str]]:
    failed = []
    for field in required_fields:
        threshold = thresholds.get(field)
        if threshold is None:
            continue
        prov = provenance.get(field)
        if prov and prov.confidence < threshold:
            failed.append(field)
    return len(failed) == 0, failed


async def get_company_domain_cached(company_name: str, cache: EnrichmentCache, config: EnrichmentConfig) -> Optional[str]:
    cached = cache.get_company_domain(company_name)
    if cached:
        return cached
    domain = await _with_timeout(find_company_domain(company_name), config.timeouts["domain_lookup"], "")
    if domain:
        cache.set_company_domain(company_name, domain)
    return domain or None


async def enrich_company_fields(
    company_name: str,
    domain: str,
    cache: EnrichmentCache,
    config: EnrichmentConfig,
) -> Tuple[Dict[str, Any], Dict[str, FieldProvenance]]:
    candidates: Dict[str, List[Tuple[Any, FieldProvenance]]] = {}

    # Run GSE company search and location search concurrently
    cached_company = cache.get_company_linkedin(company_name)
    cached_location = cache.get_company_location(company_name, domain)

    company_task = _with_timeout(
        search_company_linkedin_with_data(company_name),
        config.timeouts["company_linkedin"],
        {}
    ) if cached_company is None else _return_value(cached_company)

    location_task = _with_timeout(
        search_company_location(company_name, domain),
        config.timeouts["company_location"],
        {}
    ) if cached_location is None else _return_value(cached_location)

    company_data, location_data = await asyncio.gather(company_task, location_task)
    if company_data:
        cache.set_company_linkedin(company_name, company_data)
    if location_data:
        cache.set_company_location(company_name, domain, location_data)

    # Company data candidates (GSE)
    if company_data:
        _add_candidate(candidates, "company_linkedin", company_data.get("linkedin_url"), "gse", 0.7, "company_linkedin_snippet")
        _add_candidate(candidates, "employee_count", company_data.get("employee_count"), "gse", 0.6, "company_employee_snippet")
        _add_candidate(candidates, "description", company_data.get("description"), "gse", 0.6, "company_description_snippet")
        _add_candidate(candidates, "industry", company_data.get("industry"), "gse", 0.6, "company_industry_snippet")

    # Employee count from LinkedIn company page (GSE) if we have a LinkedIn URL
    linkedin_url_hint = company_data.get("linkedin_url", "") if company_data else ""
    if linkedin_url_hint:
        emp_count_linkedin = await _with_timeout(
            search_company_employee_count(company_name, linkedin_url_hint),
            config.timeouts["company_linkedin"],
            ""
        )
        if emp_count_linkedin:
            _add_candidate(candidates, "employee_count", emp_count_linkedin, "gse", 0.75, "company_employee_linkedin")

    # Company LinkedIn fallback using domain hints
    if not candidates.get("company_linkedin") and domain:
        linkedin_url = await _with_timeout(
            search_company_linkedin_by_domain(domain),
            config.timeouts["company_linkedin"],
            ""
        )
        if linkedin_url:
            _add_candidate(candidates, "company_linkedin", linkedin_url, "gse", 0.55, "company_linkedin_domain")

    # Employee count fallback from GSE even if LinkedIn URL wasn't found
    if not candidates.get("employee_count"):
        emp_count = await _with_timeout(
            search_company_employee_count(company_name),
            config.timeouts["company_linkedin"],
            ""
        )
        if emp_count:
            _add_candidate(candidates, "employee_count", emp_count, "gse", 0.55, "company_employee_snippet_fallback")

    # Location data candidates
    if location_data:
        hq = location_data.get("hq_location")
        if hq and not is_fake_address(hq):
            _add_candidate(candidates, "hq_location", hq, "gse", 0.6, "company_location_snippet")
        _add_candidate(candidates, "city", location_data.get("city"), "gse", 0.6, "company_location_snippet")
        _add_candidate(candidates, "state", location_data.get("state"), "gse", 0.6, "company_location_snippet")
        _add_candidate(candidates, "country", location_data.get("country"), "gse", 0.6, "company_location_snippet")
        _add_candidate(candidates, "source_url", location_data.get("source_url"), "gse", 0.55, "company_location_snippet")

    # Firecrawl fallback if missing critical fields
    needs_firecrawl = False
    for field in ["description", "industry", "employee_count", "company_linkedin", "hq_location", "city", "state", "country"]:
        if not candidates.get(field):
            needs_firecrawl = True
            break

    if needs_firecrawl and domain:
        firecrawl_data = cache.get_firecrawl_company(domain)
        if firecrawl_data is None:
            firecrawl_data = await _with_timeout(
                firecrawl_extract_company(domain),
                config.timeouts["firecrawl"],
                {}
            )
            if firecrawl_data:
                cache.set_firecrawl_company(domain, firecrawl_data)

        if firecrawl_data:
            _add_candidate(candidates, "description", firecrawl_data.get("description"), "firecrawl", 0.8, "company_extract")
            _add_candidate(candidates, "industry", firecrawl_data.get("industry"), "firecrawl", 0.75, "company_extract")
            _add_candidate(candidates, "sub_industry", firecrawl_data.get("sub_industry"), "firecrawl", 0.7, "company_extract")
            _add_candidate(candidates, "source_url", firecrawl_data.get("source_url"), "firecrawl", 0.75, "company_extract")
            _add_candidate(candidates, "phone_numbers", firecrawl_data.get("phone_numbers"), "firecrawl", 0.65, "company_extract")
            _add_candidate(candidates, "founded_year", firecrawl_data.get("founded_year"), "firecrawl", 0.6, "company_extract")
            _add_candidate(candidates, "ownership_type", firecrawl_data.get("ownership_type"), "firecrawl", 0.6, "company_extract")
            _add_candidate(candidates, "company_type", firecrawl_data.get("company_type"), "firecrawl", 0.6, "company_extract")
            _add_candidate(candidates, "number_of_locations", firecrawl_data.get("number_of_locations"), "firecrawl", 0.6, "company_extract")
            _add_candidate(candidates, "socials", firecrawl_data.get("socials"), "firecrawl", 0.6, "company_extract")
            fc_employee = firecrawl_data.get("employee_count")
            if fc_employee:
                normalized_fc_emp = normalize_employee_count(str(fc_employee))
                if normalized_fc_emp:
                    _add_candidate(candidates, "employee_count", normalized_fc_emp, "firecrawl", 0.7, "company_extract")

            fc_hq = firecrawl_data.get("hq_location")
            if fc_hq and not is_fake_address(fc_hq):
                _add_candidate(candidates, "hq_location", fc_hq, "firecrawl", 0.8, "company_extract")
            elif fc_hq:
                # Try to salvage city/state from partial locations like "City, ST"
                fc_country, fc_state, fc_city = parse_location(fc_hq)
                if fc_city and not is_fake_city(fc_city):
                    _add_candidate(candidates, "city", fc_city, "firecrawl", 0.55, "company_extract_hq_fallback")
                if fc_state:
                    _add_candidate(candidates, "state", fc_state, "firecrawl", 0.55, "company_extract_hq_fallback")
                if fc_country:
                    _add_candidate(candidates, "country", fc_country, "firecrawl", 0.55, "company_extract_hq_fallback")

            fc_city = firecrawl_data.get("city")
            fc_state = firecrawl_data.get("state")
            fc_country = firecrawl_data.get("country")
            if fc_city and not is_fake_city(fc_city):
                _add_candidate(candidates, "city", fc_city, "firecrawl", 0.75, "company_extract")
                _add_candidate(candidates, "state", fc_state, "firecrawl", 0.75, "company_extract")
                _add_candidate(candidates, "country", fc_country, "firecrawl", 0.75, "company_extract")

    # Domain-based GSE location fallback if scraping didn't yield location
    location_missing = not candidates.get("city") or not candidates.get("state") or not candidates.get("country")
    if location_missing and domain:
        domain_location = await _with_timeout(
            search_company_location_by_domain(domain),
            config.timeouts["company_location"],
            {}
        )
        if domain_location:
            hq = domain_location.get("hq_location")
            if hq and not is_fake_address(hq):
                _add_candidate(candidates, "hq_location", hq, "gse_domain", 0.55, "company_location_domain")
            _add_candidate(candidates, "city", domain_location.get("city"), "gse_domain", 0.55, "company_location_domain")
            _add_candidate(candidates, "state", domain_location.get("state"), "gse_domain", 0.55, "company_location_domain")
            _add_candidate(candidates, "country", domain_location.get("country"), "gse_domain", 0.55, "company_location_domain")
            _add_candidate(candidates, "source_url", domain_location.get("source_url"), "gse_domain", 0.5, "company_location_domain")

    selected, provenance = _select_best(candidates, config.confidence_thresholds)

    # Normalize employee count and sub-industry if present
    if selected.get("employee_count"):
        normalized = normalize_employee_count(str(selected["employee_count"]))
        if normalized:
            selected["employee_count"] = normalized
        else:
            selected["employee_count"] = ""

    if selected.get("sub_industry"):
        sub, ind = normalize_sub_industry(selected.get("sub_industry"), selected.get("industry"))
        selected["sub_industry"] = sub
        if ind:
            selected["industry"] = ind

    # Parse location from hq_location if city not set
    if selected.get("hq_location") and not selected.get("city"):
        country, state, city = parse_location(selected.get("hq_location"))
        if city:
            selected["city"] = city
        if state:
            selected["state"] = state
        if country:
            selected["country"] = country

    return selected, provenance


async def enrich_person_fields(
    full_name: str,
    company_name: str,
    cache: EnrichmentCache,
    config: EnrichmentConfig,
) -> Tuple[Dict[str, Any], Dict[str, FieldProvenance]]:
    candidates: Dict[str, List[Tuple[Any, FieldProvenance]]] = {}

    cached_person = cache.get_person_linkedin(full_name, company_name)
    person_data = cached_person if cached_person is not None else await _with_timeout(
        search_person_linkedin_with_data(full_name, company_name),
        config.timeouts["person_linkedin"],
        {}
    )
    if person_data:
        cache.set_person_linkedin(full_name, company_name, person_data)

    if not person_data:
        return {}, {}

    match_score = _company_match_score(person_data.get("company", ""), company_name)
    if match_score < config.min_company_match:
        print(f"   ⚠️ Person/company mismatch for {full_name}: '{person_data.get('company', '')}' vs '{company_name}'")

    confidence = 0.7 if match_score >= config.min_company_match else 0.3

    _add_candidate(candidates, "linkedin", person_data.get("linkedin_url"), "gse", max(confidence, 0.4), "person_profile_snippet")
    _add_candidate(candidates, "role", person_data.get("role"), "gse", confidence, "person_title_snippet")

    if person_data.get("location"):
        _add_candidate(candidates, "location", person_data.get("location"), "gse", 0.6, "person_location_snippet")

    selected, provenance = _select_best(candidates, config.confidence_thresholds)
    return selected, provenance
