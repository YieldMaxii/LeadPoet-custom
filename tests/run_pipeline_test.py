#!/usr/bin/env python3
"""
LeadPoet Pipeline Test Runner

Run the lead generation pipeline WITHOUT miner registration.
Validates output against the validator schema.

Usage:
    python3 tests/run_pipeline_test.py                    # Generate 2 leads (default)
    python3 tests/run_pipeline_test.py --num-leads 5      # Generate 5 leads
    python3 tests/run_pipeline_test.py --dry-run          # Show config, don't run pipeline
    python3 tests/run_pipeline_test.py --export leads.json # Export leads to file
"""

import asyncio
import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Dict, Any

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env file manually (no external dependency)
def _load_env_file(filepath: Path) -> bool:
    """Parse and load .env file into os.environ."""
    if not filepath.exists():
        return False
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and not os.getenv(key):  # Don't override existing env vars
                os.environ[key] = value
    return True

# Load .env from project root
_env_path = PROJECT_ROOT / ".env"
if _load_env_file(_env_path):
    print(f"Loaded .env from: {_env_path}")

# Import validation from the schema test
from tests.test_lead_schema import validate_lead, Result, Severity, VALID_EMPLOYEE_COUNT_RANGES


def print_header(title: str):
    print(f"\n{'='*60}")
    print(f" {title}")
    print(f"{'='*60}")


def print_lead_result(idx: int, lead: Dict, result: Result, show_json: bool = True):
    """Print validation result for a single lead."""
    name = lead.get("full_name") or f"{lead.get('first', '')} {lead.get('last', '')}".strip() or "Unknown"
    company = lead.get("business", "Unknown")

    status = "[PASS]" if result.is_valid else "[FAIL]"
    print(f"\n{idx}. {status} {name} @ {company}")

    if result.errors:
        print(f"   ERRORS ({len(result.errors)}):")
        for err in result.errors:
            print(f"      - {err.field}: {err.message}")

    if result.warnings:
        print(f"   WARNINGS ({len(result.warnings)}):")
        for warn in result.warnings:
            print(f"      - {warn.field}: {warn.message}")

    if show_json:
        print(f"\n   LEAD JSON:")
        # Show lead in the expected format
        formatted = {
            "business": lead.get("business", ""),
            "full_name": lead.get("full_name", ""),
            "first": lead.get("first", ""),
            "last": lead.get("last", ""),
            "email": lead.get("email", ""),
            "role": lead.get("role", ""),
            "website": lead.get("website", ""),
            "industry": lead.get("industry", ""),
            "sub_industry": lead.get("sub_industry", ""),
            "country": lead.get("country", ""),
            "state": lead.get("state", ""),
            "city": lead.get("city", ""),
            "linkedin": lead.get("linkedin", ""),
            "company_linkedin": lead.get("company_linkedin", ""),
            "source_url": lead.get("source_url", ""),
            "description": lead.get("description", ""),
            "employee_count": lead.get("employee_count", ""),
            "source_type": lead.get("source_type", ""),
        }
        print(json.dumps(formatted, indent=4))


def check_env_vars() -> tuple[bool, list[str]]:
    """Check required environment variables."""
    # Check which pipeline mode we're in
    icp_config_path = PROJECT_ROOT / "miner_models" / "lead_sorcerer_main" / "icp_config.json"
    pipeline_mode = "website_first"  # default

    if icp_config_path.exists():
        try:
            with open(icp_config_path) as f:
                config = json.load(f)
                pipeline_mode = config.get("pipeline_mode", "website_first")
        except:
            pass

    if pipeline_mode == "linkedin_first":
        required = ["GSE_API_KEY", "GSE_CX", "SCRAPINGDOG_API_KEY", "OPENROUTER_KEY"]
        optional = ["TRUELIST_API_KEY", "FIRECRAWL_KEY"]
    else:
        required = ["GSE_API_KEY", "GSE_CX", "SCRAPINGDOG_API_KEY", "OPENROUTER_KEY", "FIRECRAWL_KEY"]
        optional = ["TRUELIST_API_KEY"]

    missing = [var for var in required if not os.getenv(var)]

    return len(missing) == 0, missing, pipeline_mode, optional


def show_config():
    """Show current pipeline configuration."""
    print_header("PIPELINE CONFIGURATION")

    # Check env vars
    ok, missing, pipeline_mode, optional = check_env_vars()

    print(f"\nPipeline Mode: {pipeline_mode.upper()}")

    # Show ICP config summary
    icp_config_path = PROJECT_ROOT / "miner_models" / "lead_sorcerer_main" / "icp_config.json"
    if icp_config_path.exists():
        try:
            with open(icp_config_path) as f:
                config = json.load(f)

            print(f"\nICP Config: {icp_config_path}")
            print(f"  Target Industries: {config.get('targeting', {}).get('sub_industries', ['Not set'])}")
            print(f"  Target Roles: {config.get('targeting', {}).get('roles', ['Not set'])[:3]}...")
            print(f"  LinkedIn Queries: {len(config.get('linkedin_queries', []))} defined")

            caps = config.get("caps", {})
            print(f"  Max Domains/Run: {caps.get('max_domains_per_run', 'Not set')}")
            print(f"  Max Crawl/Run: {caps.get('max_crawl_per_run', 'Not set')}")
        except Exception as e:
            print(f"  Error reading config: {e}")

    # Show env var status
    print(f"\nRequired Environment Variables:")
    all_required = ["GSE_API_KEY", "GSE_CX", "SCRAPINGDOG_API_KEY", "OPENROUTER_KEY", "FIRECRAWL_KEY", "TRUELIST_API_KEY"]
    for var in all_required:
        status = "[SET]" if os.getenv(var) else "[MISSING]"
        print(f"  {status} {var}")

    if missing:
        print(f"\n[WARNING] Missing required vars for {pipeline_mode} mode: {missing}")
        return False

    print(f"\n[OK] All required environment variables are set")
    return True


async def run_pipeline(num_leads: int) -> List[Dict[str, Any]]:
    """Run the lead generation pipeline."""
    # Import here to avoid loading dependencies until needed
    from miner_models.lead_sorcerer_main.main_leads import get_leads

    print_header(f"RUNNING PIPELINE (requesting {num_leads} leads)")

    leads = await get_leads(num_leads)
    return leads


def validate_leads(leads: List[Dict]) -> tuple[int, int, List[Result]]:
    """Validate all leads against the schema."""
    passed = 0
    failed = 0
    results = []

    for lead in leads:
        result = validate_lead(lead)
        results.append(result)
        if result.is_valid:
            passed += 1
        else:
            failed += 1

    return passed, failed, results


def export_leads(leads: List[Dict], filepath: str):
    """Export leads to JSON file."""
    with open(filepath, 'w') as f:
        json.dump(leads, f, indent=2)
    print(f"\nExported {len(leads)} leads to: {filepath}")


async def main():
    parser = argparse.ArgumentParser(
        description="Test the LeadPoet pipeline without miner registration"
    )
    parser.add_argument(
        "--num-leads", "-n",
        type=int,
        default=2,
        help="Number of leads to generate (default: 2)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show configuration without running pipeline"
    )
    parser.add_argument(
        "--export", "-e",
        type=str,
        metavar="FILE",
        help="Export leads to JSON file"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show full lead JSON output"
    )
    args = parser.parse_args()

    print_header("LEADPOET PIPELINE TEST RUNNER")
    print("Testing lead generation WITHOUT miner registration")

    # Always show config
    config_ok = show_config()

    if args.dry_run:
        print("\n[DRY RUN] Exiting without running pipeline")
        return 0

    if not config_ok:
        print("\n[ERROR] Cannot run pipeline - missing required environment variables")
        print("Set the missing variables in your .env file or environment")
        return 1

    # Run the pipeline
    try:
        leads = await run_pipeline(args.num_leads)
    except Exception as e:
        print(f"\n[ERROR] Pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

    if not leads:
        print("\n[WARNING] Pipeline returned no leads")
        print("Check your ICP config and environment variables")
        return 1

    # Validate leads
    print_header(f"VALIDATING {len(leads)} LEADS")

    passed, failed, results = validate_leads(leads)

    for idx, (lead, result) in enumerate(zip(leads, results), 1):
        print_lead_result(idx, lead, result)

        if args.verbose:
            print(f"\n   Full JSON:")
            print(json.dumps(lead, indent=4, default=str))

    # Summary
    print_header("SUMMARY")
    print(f"  Leads Generated: {len(leads)}")
    print(f"  Passed Validation: {passed}")
    print(f"  Failed Validation: {failed}")
    print(f"  Pass Rate: {passed/len(leads)*100:.1f}%")

    if failed > 0:
        print(f"\n  [WARNING] {failed} lead(s) would be rejected by validators")
    else:
        print(f"\n  [SUCCESS] All leads passed schema validation!")

    # Export if requested
    if args.export:
        export_leads(leads, args.export)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
