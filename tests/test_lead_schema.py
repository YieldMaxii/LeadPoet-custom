"""
LeadPoet Lead Schema Validation Test Suite

Validates that leads from the sourcing pipeline have all required fields
with correct formats. Does NOT verify data accuracy - only structure completeness.

LEAD SCHEMA:
============
{
  "business": "SpaceX",                    # REQUIRED - company name
  "full_name": "Elon Musk",                # REQUIRED (or first + last)
  "first": "Elon",                         # REQUIRED (or full_name)
  "last": "Musk",                          # REQUIRED (or full_name)
  "email": "elon@spacex.com",              # REQUIRED - corporate email
  "role": "CEO",                           # REQUIRED
  "website": "https://spacex.com",         # REQUIRED
  "industry": "Science and Engineering",   # REQUIRED - must match taxonomy
  "sub_industry": "Aerospace",             # REQUIRED - must match taxonomy
  "country": "United States",              # REQUIRED
  "state": "California",                   # REQUIRED for US leads only
  "city": "Hawthorne",                     # REQUIRED
  "linkedin": "https://linkedin.com/in/elonmusk",           # REQUIRED
  "company_linkedin": "https://linkedin.com/company/spacex", # REQUIRED
  "source_url": "https://spacex.com/careers",               # REQUIRED
  "description": "Aerospace manufacturer...",               # REQUIRED
  "employee_count": "1,001-5,000",         # REQUIRED - must be valid range

  # OPTIONAL FIELDS (not validated, but useful):
  "source_type": "company_site",           # OPTIONAL
  "phone_numbers": ["+1-310-363-6000"],    # OPTIONAL
  "founded_year": 2002,                    # OPTIONAL
  "ownership_type": "Private",             # OPTIONAL
  "company_type": "Corporation",           # OPTIONAL
  "number_of_locations": 5,                # OPTIONAL
  "socials": {"twitter": "spacex"}         # OPTIONAL
}

Usage:
    pytest tests/test_lead_schema.py -v
    python tests/test_lead_schema.py
"""

import re
import sys
import os
import json
import argparse
from typing import Dict, Any, List
from dataclasses import dataclass
from enum import Enum

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validator_models.industry_taxonomy import INDUSTRY_TAXONOMY


# ============================================================================
# REQUIRED FIELDS DOCUMENTATION
# ============================================================================

# Fields checked by check_required_fields() in automated_checks.py
VALIDATOR_REQUIRED_FIELDS = {
    "industry": "Must match industry_taxonomy.py",
    "sub_industry": "Must match industry_taxonomy.py and be valid for industry",
    "role": "Job title (max 80 chars)",
    "country": "Country name",
    "city": "City name",
}

# Name requirement: either full_name OR (first AND last)
NAME_REQUIREMENT = "full_name OR (first AND last)"

# State required only for US leads
US_STATE_REQUIREMENT = "state is required ONLY for US leads"

# Fields required implicitly by other validation checks
IMPLICIT_REQUIRED_FIELDS = {
    "email": "Used by email format, name-email match, domain checks",
    "website": "Used by domain age, MX record, HEAD request checks",
    "business": "Used by company name lookups and verification",
    "linkedin": "Used by LinkedIn profile verification",
    "company_linkedin": "Used by company LinkedIn verification",
    "source_url": "Used by source provenance checks",
    "description": "Used by verification checks",
    "employee_count": "Must be valid range format",
}

# Optional fields (not validated but can be included)
OPTIONAL_FIELDS = [
    "source_type",
    "phone_numbers",
    "founded_year",
    "ownership_type",
    "company_type",
    "number_of_locations",
    "socials",
]

# All required fields combined (for schema checking)
ALL_REQUIRED_FIELDS = [
    "business",
    "email",
    "role",
    "website",
    "industry",
    "sub_industry",
    "country",
    "city",
    "linkedin",
    "company_linkedin",
    "source_url",
    "description",
    "employee_count",
    # Name: full_name OR (first AND last)
]


# ============================================================================
# CONSTANTS
# ============================================================================

US_COUNTRY_ALIASES = [
    "united states", "usa", "us", "u.s.", "u.s.a.",
    "america", "united states of america"
]

VALID_EMPLOYEE_COUNT_RANGES = [
    "0-1", "2-10", "11-50", "51-200", "201-500",
    "501-1,000", "1,001-5,000", "5,001-10,000", "10,001+"
]

VALID_SOURCE_TYPES = [
    "public_registry", "company_site", "first_party_form",
    "licensed_resale", "proprietary_database"
]

GENERAL_PURPOSE_EMAIL_PREFIXES = [
    'info@', 'hello@', 'owner@', 'ceo@', 'founder@', 'contact@', 'support@',
    'team@', 'admin@', 'office@', 'mail@', 'connect@', 'help@', 'hi@',
    'welcome@', 'inquiries@', 'general@', 'feedback@', 'ask@', 'outreach@',
    'communications@', 'crew@', 'staff@', 'community@', 'reachus@', 'talk@',
    'service@'
]

FREE_EMAIL_DOMAINS = {
    'gmail.com', 'googlemail.com', 'yahoo.com', 'yahoo.co.uk', 'yahoo.fr',
    'outlook.com', 'hotmail.com', 'live.com', 'msn.com', 'aol.com', 'mail.com',
    'protonmail.com', 'proton.me', 'icloud.com', 'me.com', 'mac.com',
    'zoho.com', 'yandex.com', 'gmx.com', 'mail.ru'
}


# ============================================================================
# VALIDATION CLASSES
# ============================================================================

class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass
class Issue:
    field: str
    message: str
    severity: Severity
    value: Any = None


@dataclass
class Result:
    is_valid: bool
    issues: List[Issue]
    lead: Dict[str, Any]

    @property
    def errors(self):
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self):
        return [i for i in self.issues if i.severity == Severity.WARNING]


# ============================================================================
# VALIDATION FUNCTIONS
# ============================================================================

def check_required_fields(lead: Dict) -> List[Issue]:
    """Check all required fields are present and non-empty."""
    issues = []

    # Check standard required fields
    for field in ALL_REQUIRED_FIELDS:
        value = lead.get(field)
        if value is None:
            issues.append(Issue(field, f"REQUIRED field '{field}' is missing", Severity.ERROR))
        elif isinstance(value, str) and not value.strip():
            issues.append(Issue(field, f"REQUIRED field '{field}' is empty", Severity.ERROR, value))

    # Check name requirement: full_name OR (first AND last)
    full_name = lead.get("full_name")
    first = lead.get("first")
    last = lead.get("last")

    has_full_name = full_name and str(full_name).strip()
    has_first_last = (first and str(first).strip()) and (last and str(last).strip())

    if not has_full_name and not has_first_last:
        issues.append(Issue("name", "REQUIRED: Must have 'full_name' OR both 'first' AND 'last'", Severity.ERROR))

    # US leads require state
    country = str(lead.get("country", "")).lower().strip()
    if country in US_COUNTRY_ALIASES:
        state = lead.get("state")
        if not state or (isinstance(state, str) and not state.strip()):
            issues.append(Issue("state", "REQUIRED for US leads: 'state' field is missing", Severity.ERROR))

    return issues


def check_email_format(lead: Dict) -> List[Issue]:
    """Check email format and domain rules."""
    issues = []
    email = lead.get("email", "")
    if not email:
        return issues

    # Basic format
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        issues.append(Issue("email", f"Invalid email format: {email}", Severity.ERROR, email))
        return issues

    local_part = email.split("@")[0]
    domain = email.split("@")[1].lower()

    # No + alias
    if "+" in local_part:
        issues.append(Issue("email", f"Email contains '+' alias (not allowed): {email}", Severity.ERROR, email))

    # No free email
    if domain in FREE_EMAIL_DOMAINS:
        issues.append(Issue("email", f"Free email domain not allowed: {domain}", Severity.ERROR, email))

    # No general purpose
    email_lower = email.lower()
    for prefix in GENERAL_PURPOSE_EMAIL_PREFIXES:
        if email_lower.startswith(prefix):
            issues.append(Issue("email", f"General purpose email not allowed: {prefix}", Severity.ERROR, email))
            break

    # Name in email (warning)
    first = str(lead.get("first", "")).lower()
    last = str(lead.get("last", "")).lower()
    local_lower = local_part.lower()
    if first and last and len(first) >= 3 and len(last) >= 3:
        if first[:3] not in local_lower and last[:3] not in local_lower:
            issues.append(Issue("email", f"Name not found in email - may fail validation", Severity.WARNING, email))

    return issues


def check_industry_taxonomy(lead: Dict) -> List[Issue]:
    """Check industry/sub_industry are valid taxonomy values."""
    issues = []

    industry = str(lead.get("industry", "")).strip()
    sub_industry = str(lead.get("sub_industry", "")).strip()

    if not sub_industry:
        return issues

    # Find sub_industry in taxonomy (case-insensitive)
    matched_sub = None
    for key in INDUSTRY_TAXONOMY:
        if key.lower() == sub_industry.lower():
            matched_sub = key
            break

    if not matched_sub:
        issues.append(Issue("sub_industry", f"'{sub_industry}' not found in industry_taxonomy.py (723 valid options)", Severity.ERROR, sub_industry))
        return issues

    # Check industry is valid for this sub_industry
    if industry:
        valid_industries = INDUSTRY_TAXONOMY[matched_sub].get("industries", [])
        if not any(v.lower() == industry.lower() for v in valid_industries):
            issues.append(Issue("industry", f"'{industry}' not valid for sub_industry '{matched_sub}'. Valid options: {valid_industries}", Severity.ERROR, industry))

    return issues


def check_employee_count(lead: Dict) -> List[Issue]:
    """Check employee_count is a valid range."""
    issues = []
    emp = str(lead.get("employee_count", "")).strip()
    if emp and emp not in VALID_EMPLOYEE_COUNT_RANGES:
        issues.append(Issue("employee_count", f"Invalid format: '{emp}'. Valid ranges: {VALID_EMPLOYEE_COUNT_RANGES}", Severity.ERROR, emp))
    return issues


def check_url_formats(lead: Dict) -> List[Issue]:
    """Check URL fields have valid format."""
    issues = []
    url_pattern = r'^https?://[^\s/$.?#].[^\s]*$'

    for field in ["website", "linkedin", "company_linkedin", "source_url"]:
        value = lead.get(field, "")
        if not value:
            continue
        if field == "source_url" and value == "proprietary_database":
            continue
        if not re.match(url_pattern, value):
            issues.append(Issue(field, f"Invalid URL format: {value}", Severity.ERROR, value))

    # LinkedIn-specific format warnings
    linkedin = lead.get("linkedin", "")
    if linkedin and "linkedin.com/in/" not in linkedin.lower():
        issues.append(Issue("linkedin", f"Should contain 'linkedin.com/in/': {linkedin}", Severity.WARNING, linkedin))

    company_linkedin = lead.get("company_linkedin", "")
    if company_linkedin and "linkedin.com/company/" not in company_linkedin.lower():
        issues.append(Issue("company_linkedin", f"Should contain 'linkedin.com/company/': {company_linkedin}", Severity.WARNING, company_linkedin))

    return issues


def check_source_type(lead: Dict) -> List[Issue]:
    """Check source_type is valid if provided."""
    issues = []
    source_type = lead.get("source_type")
    if source_type and source_type not in VALID_SOURCE_TYPES:
        issues.append(Issue("source_type", f"Invalid: '{source_type}'. Valid: {VALID_SOURCE_TYPES}", Severity.WARNING, source_type))
    return issues


def check_role_format(lead: Dict) -> List[Issue]:
    """Check role field format."""
    issues = []
    role = str(lead.get("role", ""))
    if not role:
        return issues

    if len(role) > 80:
        issues.append(Issue("role", f"Role exceeds 80 char limit: {len(role)} chars", Severity.ERROR, role))

    business = str(lead.get("business", "")).lower()
    if business and business in role.lower():
        issues.append(Issue("role", "Role contains company name - may fail validation", Severity.WARNING, role))

    if " at " in role.lower():
        issues.append(Issue("role", "Role contains 'at [Company]' - may fail validation", Severity.WARNING, role))

    return issues


def validate_lead(lead: Dict) -> Result:
    """Run all validation checks on a lead."""
    issues = []
    issues.extend(check_required_fields(lead))
    issues.extend(check_email_format(lead))
    issues.extend(check_industry_taxonomy(lead))
    issues.extend(check_employee_count(lead))
    issues.extend(check_url_formats(lead))
    issues.extend(check_source_type(lead))
    issues.extend(check_role_format(lead))

    has_errors = any(i.severity == Severity.ERROR for i in issues)
    return Result(is_valid=not has_errors, issues=issues, lead=lead)


# ============================================================================
# SAMPLE LEAD - EXACT FORMAT FROM SCHEMA
# ============================================================================

SAMPLE_LEAD = {
    # REQUIRED FIELDS
    "business": "SpaceX",
    "full_name": "Elon Musk",
    "first": "Elon",
    "last": "Musk",
    "email": "elon@spacex.com",
    "role": "CEO",
    "website": "https://spacex.com",
    "industry": "Science and Engineering",
    "sub_industry": "Aerospace",
    "country": "United States",
    "state": "California",
    "city": "Hawthorne",
    "linkedin": "https://linkedin.com/in/elonmusk",
    "company_linkedin": "https://linkedin.com/company/spacex",
    "source_url": "https://spacex.com/careers",
    "description": "Aerospace manufacturer focused on reducing space transportation costs",
    "employee_count": "1,001-5,000",

    # OPTIONAL FIELDS
    "source_type": "company_site",
    "phone_numbers": ["+1-310-363-6000"],
    "founded_year": 2002,
    "ownership_type": "Private",
    "company_type": "Corporation",
    "number_of_locations": 5,
    "socials": {"twitter": "spacex"}
}


# ============================================================================
# PYTEST TESTS
# ============================================================================

try:
    import pytest
    PYTEST_AVAILABLE = True
except ImportError:
    PYTEST_AVAILABLE = False


class TestRequiredFields:
    """Test required field validation."""

    def test_sample_lead_has_all_required(self):
        """Sample lead should have all required fields."""
        result = validate_lead(SAMPLE_LEAD)
        assert result.is_valid, f"Sample lead failed: {[e.message for e in result.errors]}"

    def test_missing_business(self):
        lead = {**SAMPLE_LEAD}
        del lead["business"]
        result = validate_lead(lead)
        assert not result.is_valid
        assert any(e.field == "business" for e in result.errors)

    def test_missing_email(self):
        lead = {**SAMPLE_LEAD}
        del lead["email"]
        result = validate_lead(lead)
        assert not result.is_valid
        assert any(e.field == "email" for e in result.errors)

    def test_missing_industry(self):
        lead = {**SAMPLE_LEAD}
        del lead["industry"]
        result = validate_lead(lead)
        assert not result.is_valid
        assert any(e.field == "industry" for e in result.errors)

    def test_missing_sub_industry(self):
        lead = {**SAMPLE_LEAD}
        del lead["sub_industry"]
        result = validate_lead(lead)
        assert not result.is_valid
        assert any(e.field == "sub_industry" for e in result.errors)

    def test_missing_role(self):
        lead = {**SAMPLE_LEAD}
        del lead["role"]
        result = validate_lead(lead)
        assert not result.is_valid
        assert any(e.field == "role" for e in result.errors)

    def test_missing_website(self):
        lead = {**SAMPLE_LEAD}
        del lead["website"]
        result = validate_lead(lead)
        assert not result.is_valid
        assert any(e.field == "website" for e in result.errors)

    def test_missing_linkedin(self):
        lead = {**SAMPLE_LEAD}
        del lead["linkedin"]
        result = validate_lead(lead)
        assert not result.is_valid
        assert any(e.field == "linkedin" for e in result.errors)

    def test_missing_company_linkedin(self):
        lead = {**SAMPLE_LEAD}
        del lead["company_linkedin"]
        result = validate_lead(lead)
        assert not result.is_valid
        assert any(e.field == "company_linkedin" for e in result.errors)

    def test_missing_source_url(self):
        lead = {**SAMPLE_LEAD}
        del lead["source_url"]
        result = validate_lead(lead)
        assert not result.is_valid
        assert any(e.field == "source_url" for e in result.errors)

    def test_missing_description(self):
        lead = {**SAMPLE_LEAD}
        del lead["description"]
        result = validate_lead(lead)
        assert not result.is_valid
        assert any(e.field == "description" for e in result.errors)

    def test_missing_employee_count(self):
        lead = {**SAMPLE_LEAD}
        del lead["employee_count"]
        result = validate_lead(lead)
        assert not result.is_valid
        assert any(e.field == "employee_count" for e in result.errors)

    def test_missing_country(self):
        lead = {**SAMPLE_LEAD}
        del lead["country"]
        result = validate_lead(lead)
        assert not result.is_valid
        assert any(e.field == "country" for e in result.errors)

    def test_missing_city(self):
        lead = {**SAMPLE_LEAD}
        del lead["city"]
        result = validate_lead(lead)
        assert not result.is_valid
        assert any(e.field == "city" for e in result.errors)


class TestNameRequirement:
    """Test name field requirements."""

    def test_full_name_only(self):
        """Lead with only full_name should pass."""
        lead = {**SAMPLE_LEAD}
        del lead["first"]
        del lead["last"]
        result = validate_lead(lead)
        name_errors = [e for e in result.errors if e.field == "name"]
        assert len(name_errors) == 0

    def test_first_last_only(self):
        """Lead with only first+last should pass."""
        lead = {**SAMPLE_LEAD}
        del lead["full_name"]
        result = validate_lead(lead)
        name_errors = [e for e in result.errors if e.field == "name"]
        assert len(name_errors) == 0

    def test_no_name_fails(self):
        """Lead with no name fields should fail."""
        lead = {**SAMPLE_LEAD}
        del lead["full_name"]
        del lead["first"]
        del lead["last"]
        result = validate_lead(lead)
        assert any(e.field == "name" for e in result.errors)

    def test_first_only_fails(self):
        """Lead with only first (no last) should fail."""
        lead = {**SAMPLE_LEAD}
        del lead["full_name"]
        del lead["last"]
        result = validate_lead(lead)
        assert any(e.field == "name" for e in result.errors)

    def test_last_only_fails(self):
        """Lead with only last (no first) should fail."""
        lead = {**SAMPLE_LEAD}
        del lead["full_name"]
        del lead["first"]
        result = validate_lead(lead)
        assert any(e.field == "name" for e in result.errors)


class TestUSStateRequirement:
    """Test US state requirement."""

    def test_us_lead_missing_state(self):
        """US lead without state should fail."""
        lead = {**SAMPLE_LEAD}
        del lead["state"]
        result = validate_lead(lead)
        assert not result.is_valid
        assert any(e.field == "state" for e in result.errors)

    def test_international_lead_no_state_ok(self):
        """International lead without state should pass."""
        lead = {**SAMPLE_LEAD}
        lead["country"] = "Germany"
        del lead["state"]
        result = validate_lead(lead)
        state_errors = [e for e in result.errors if e.field == "state"]
        assert len(state_errors) == 0

    def test_us_aliases_require_state(self):
        """All US country aliases should require state."""
        for alias in US_COUNTRY_ALIASES:
            lead = {**SAMPLE_LEAD}
            lead["country"] = alias
            del lead["state"]
            result = validate_lead(lead)
            assert any(e.field == "state" for e in result.errors), f"'{alias}' should require state"


class TestEmailValidation:
    """Test email format validation."""

    def test_valid_email(self):
        issues = check_email_format(SAMPLE_LEAD)
        errors = [i for i in issues if i.severity == Severity.ERROR]
        assert len(errors) == 0

    def test_plus_alias_rejected(self):
        lead = {**SAMPLE_LEAD, "email": "elon+work@spacex.com"}
        issues = check_email_format(lead)
        assert any("+" in i.message for i in issues)

    def test_free_email_rejected(self):
        lead = {**SAMPLE_LEAD, "email": "elon@gmail.com"}
        issues = check_email_format(lead)
        assert any("Free email" in i.message for i in issues)

    def test_general_purpose_rejected(self):
        lead = {**SAMPLE_LEAD, "email": "info@spacex.com"}
        issues = check_email_format(lead)
        assert any("General purpose" in i.message for i in issues)


class TestIndustryTaxonomy:
    """Test industry taxonomy validation."""

    def test_valid_taxonomy(self):
        issues = check_industry_taxonomy(SAMPLE_LEAD)
        errors = [i for i in issues if i.severity == Severity.ERROR]
        assert len(errors) == 0

    def test_invalid_sub_industry(self):
        lead = {**SAMPLE_LEAD, "sub_industry": "Fake Industry XYZ"}
        issues = check_industry_taxonomy(lead)
        assert any("not found in industry_taxonomy" in i.message for i in issues)

    def test_mismatched_industry(self):
        lead = {**SAMPLE_LEAD, "industry": "Agriculture and Farming"}
        issues = check_industry_taxonomy(lead)
        assert any("not valid" in i.message for i in issues)


class TestEmployeeCount:
    """Test employee count validation."""

    def test_valid_ranges(self):
        for emp_range in VALID_EMPLOYEE_COUNT_RANGES:
            lead = {**SAMPLE_LEAD, "employee_count": emp_range}
            issues = check_employee_count(lead)
            assert len(issues) == 0, f"'{emp_range}' should be valid"

    def test_invalid_format(self):
        lead = {**SAMPLE_LEAD, "employee_count": "500 employees"}
        issues = check_employee_count(lead)
        assert len(issues) > 0


class TestUrlFormats:
    """Test URL format validation."""

    def test_valid_urls(self):
        issues = check_url_formats(SAMPLE_LEAD)
        errors = [i for i in issues if i.severity == Severity.ERROR]
        assert len(errors) == 0

    def test_invalid_website(self):
        lead = {**SAMPLE_LEAD, "website": "not-a-url"}
        issues = check_url_formats(lead)
        assert any("Invalid URL" in i.message for i in issues)

    def test_proprietary_database_ok(self):
        lead = {**SAMPLE_LEAD, "source_url": "proprietary_database"}
        issues = check_url_formats(lead)
        errors = [i for i in issues if i.severity == Severity.ERROR]
        assert len(errors) == 0


class TestOptionalFields:
    """Test that optional fields don't cause errors when missing."""

    def test_optional_fields_can_be_missing(self):
        """Lead without optional fields should still be valid."""
        lead = {**SAMPLE_LEAD}
        for field in OPTIONAL_FIELDS:
            if field in lead:
                del lead[field]
        result = validate_lead(lead)
        assert result.is_valid, f"Lead without optional fields should be valid: {[e.message for e in result.errors]}"


# ============================================================================
# CLI
# ============================================================================

def print_schema_documentation():
    """Print the full schema documentation."""
    print("\n" + "="*70)
    print("LEADPOET LEAD SCHEMA DOCUMENTATION")
    print("="*70)

    print("\nREQUIRED FIELDS:")
    print("-"*70)
    for field in ALL_REQUIRED_FIELDS:
        print(f"  - {field}")

    print(f"\nNAME REQUIREMENT: {NAME_REQUIREMENT}")
    print(f"US STATE REQUIREMENT: {US_STATE_REQUIREMENT}")

    print("\nIMPLICIT REQUIREMENTS (used by validation checks):")
    print("-"*70)
    for field, desc in IMPLICIT_REQUIRED_FIELDS.items():
        print(f"  - {field}: {desc}")

    print("\nOPTIONAL FIELDS:")
    print("-"*70)
    for field in OPTIONAL_FIELDS:
        print(f"  - {field}")

    print("\nVALID EMPLOYEE_COUNT RANGES:")
    print("-"*70)
    for r in VALID_EMPLOYEE_COUNT_RANGES:
        print(f"  - {r}")

    print("\nVALID SOURCE_TYPES:")
    print("-"*70)
    for s in VALID_SOURCE_TYPES:
        print(f"  - {s}")

    print("\n" + "="*70)


def print_result(result: Result):
    """Print validation result."""
    lead = result.lead
    print("\n" + "="*60)
    print(f"LEAD: {lead.get('full_name', 'N/A')} @ {lead.get('business', 'N/A')}")
    print("="*60)

    if result.is_valid:
        print("[PASS] All required fields present and valid")
    else:
        print(f"[FAIL] {len(result.errors)} error(s) found")

    if result.warnings:
        print(f"[WARN] {len(result.warnings)} warning(s)")

    if result.issues:
        print("\nIssues:")
        for issue in result.issues:
            sev = "[ERROR]" if issue.severity == Severity.ERROR else "[WARN ]"
            print(f"  {sev} {issue.field}: {issue.message}")

    print("-"*60)


def main():
    parser = argparse.ArgumentParser(description="Validate lead schema for LeadPoet")
    parser.add_argument("--lead", type=str, help="JSON string of lead to validate")
    parser.add_argument("--file", type=str, help="JSON file with leads to validate")
    parser.add_argument("--schema", action="store_true", help="Print schema documentation")
    args = parser.parse_args()

    if args.schema:
        print_schema_documentation()
        return

    if args.lead:
        lead = json.loads(args.lead)
        result = validate_lead(lead)
        print_result(result)
        sys.exit(0 if result.is_valid else 1)

    if args.file:
        with open(args.file) as f:
            data = json.load(f)
        leads = data if isinstance(data, list) else [data]
        all_valid = True
        for lead in leads:
            result = validate_lead(lead)
            print_result(result)
            if not result.is_valid:
                all_valid = False
        sys.exit(0 if all_valid else 1)

    # Default: show schema and test sample
    print_schema_documentation()
    print("\n\nTesting sample lead (SpaceX format):")
    result = validate_lead(SAMPLE_LEAD)
    print_result(result)


if __name__ == "__main__":
    main()
