"""
Lightweight in-memory caches for enrichment pipeline.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


def _key(*parts: str) -> str:
    return "::".join([p.strip().lower() for p in parts if p])


@dataclass
class EnrichmentCache:
    company_linkedin: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    person_linkedin: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    company_location: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    firecrawl_company: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    company_domain: Dict[str, str] = field(default_factory=dict)

    def get_company_linkedin(self, company_name: str) -> Optional[Dict[str, Any]]:
        return self.company_linkedin.get(_key(company_name))

    def set_company_linkedin(self, company_name: str, value: Dict[str, Any]) -> None:
        self.company_linkedin[_key(company_name)] = value

    def get_person_linkedin(self, full_name: str, company_name: str = "") -> Optional[Dict[str, Any]]:
        return self.person_linkedin.get(_key(full_name, company_name))

    def set_person_linkedin(self, full_name: str, company_name: str, value: Dict[str, Any]) -> None:
        self.person_linkedin[_key(full_name, company_name)] = value

    def get_company_location(self, company_name: str, domain: str = "") -> Optional[Dict[str, Any]]:
        return self.company_location.get(_key(company_name, domain))

    def set_company_location(self, company_name: str, domain: str, value: Dict[str, Any]) -> None:
        self.company_location[_key(company_name, domain)] = value

    def get_firecrawl_company(self, domain: str) -> Optional[Dict[str, Any]]:
        return self.firecrawl_company.get(_key(domain))

    def set_firecrawl_company(self, domain: str, value: Dict[str, Any]) -> None:
        self.firecrawl_company[_key(domain)] = value

    def get_company_domain(self, company_name: str) -> Optional[str]:
        return self.company_domain.get(_key(company_name))

    def set_company_domain(self, company_name: str, domain: str) -> None:
        self.company_domain[_key(company_name)] = domain
