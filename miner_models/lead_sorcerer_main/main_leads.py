"""
Integration wrapper for Lead Sorcerer model to be compatible with the existing miner system.

This module provides a get_leads() function that runs the Lead Sorcerer orchestrator
and converts the output to the format expected by the existing miner code.

Enrichment Pipeline:
- Parses hq_location into country/state/city
- Normalizes employee_count to LinkedIn format
- Normalizes LinkedIn URLs
- Searches for missing LinkedIn URLs via Google (GSE)
- Filters out incomplete leads (skips rather than submit invalid)
"""

import asyncio
import json
import os
import sys
import tempfile
import shutil
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import logging
from dotenv import load_dotenv

# Import enrichment functions
from miner_models.lead_sorcerer_main.src.enrichment import (
    parse_location,
    US_STATES,
    STATE_ABBR_TO_NAME,
    normalize_employee_count,
    normalize_linkedin_url,
    normalize_sub_industry,  # NEW: Map LLM sub_industries to taxonomy
    is_fake_address,  # NEW: Detect placeholder addresses
    is_fake_city,
    is_lead_complete,
    get_missing_fields,
    infer_state_from_city,
    validate_location,
    validate_industry_taxonomy,
    validate_role_format,
    validate_website_url,
    validate_description,
    validate_employee_count,
    validate_linkedin_url,
    validate_source_url,
    # Email pattern generation and TrueList validation
    get_verified_email,
    collect_email_candidates,
    batch_verify_emails,
    # GSE-based LinkedIn search (extract from snippets, no scraping)
    search_company_linkedin_with_data,
    search_person_linkedin_with_data,
    search_linkedin_executives,
    search_company_location,  # NEW: GSE fallback for location
    # LinkedIn-first pipeline functions
    search_linkedin_decision_makers,
    find_company_domain,
    clean_company_name_for_search,
    generate_linkedin_queries_with_llm,
    firecrawl_extract_company,
    # Pre-submission validation (matches gateway validation)
    validate_lead_pre_submission,
    normalize_location_for_submission,
    clean_role_for_submission,
    # Lead scoring and ranking
    rank_leads,
)
from miner_models.lead_sorcerer_main.src.enrichment_cache import EnrichmentCache
from miner_models.lead_sorcerer_main.src.enrichment_pipeline import (
    FieldProvenance,
    build_enrichment_config,
    enrich_company_fields,
    enrich_person_fields,
    get_company_domain_cached,
    passes_confidence_gating,
)

# Load environment variables from .env file
# Try multiple locations for .env
from pathlib import Path
_env_locations = [
    Path(__file__).parent.parent.parent / ".env",  # /root/LeadPoet-custom/.env
    Path(__file__).parent / ".env",  # lead_sorcerer_main/.env
    Path.cwd() / ".env",  # Current working directory
]
for _env_path in _env_locations:
    if _env_path.exists():
        load_dotenv(_env_path)
        break
else:
    load_dotenv()  # Fallback to default behavior


# Check for required dependencies first
def check_dependencies():
    """Check if required dependencies are available."""
    try:
        import phonenumbers
        import httpx
        import openai
        return True, None
    except ImportError as e:
        return False, str(e)


# Check dependencies before importing Lead Sorcerer components
deps_ok, error_msg = check_dependencies()
if not deps_ok:
    print(f"❌ Could not import Lead Sorcerer orchestrator: {error_msg}")
    print("   Please ensure the Lead Sorcerer model is properly installed")
    print(
        "   Run: pip install -r miner_models/lead_sorcerer_main/requirements.txt"
    )

    # Provide fallback function that returns empty results
    async def get_leads(num_leads: int,
                        industry: str = None,
                        region: str = None) -> List[Dict[str, Any]]:
        """Fallback function when dependencies are missing."""
        print(
            "⚠️ Lead Sorcerer dependencies not available, returning empty results"
        )
        return []
else:
    # Get the absolute path to the lead_sorcerer_main directory
    lead_sorcerer_dir = Path(__file__).parent.absolute()
    src_path = lead_sorcerer_dir / "src"
    config_path = lead_sorcerer_dir / "config"

    # Add the src directory to the path so we can import the orchestrator
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    try:
        # Try to import with absolute path first
        import sys
        old_path = sys.path.copy()

        # Add both the lead_sorcerer_main directory and its src subdirectory
        sys.path.insert(0, str(lead_sorcerer_dir))
        sys.path.insert(0, str(src_path))

        from orchestrator import LeadSorcererOrchestrator
        LEAD_SORCERER_AVAILABLE = True

        # Restore original path but keep our additions
        for path in [str(lead_sorcerer_dir), str(src_path)]:
            if path not in old_path and path in sys.path:
                continue  # Keep our additions

    except ImportError as e:
        print(f"❌ Could not import Lead Sorcerer orchestrator: {e}")
        print(f"   Tried to import from: {src_path}")
        print(f"   Directory exists: {src_path.exists()}")
        if src_path.exists():
            print(f"   Contents: {list(src_path.iterdir())}")
        LEAD_SORCERER_AVAILABLE = False

    # Suppress verbose logging from the lead sorcerer
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    # ───────────────────────────────────────────────────────────────────
    #  Load canonical ICP template (must exist)
    # ───────────────────────────────────────────────────────────────────
    try:
        ICP_TEMPLATE_PATH = lead_sorcerer_dir / "icp_config.json"
        with open(ICP_TEMPLATE_PATH, "r", encoding="utf-8") as _f:
            BASE_ICP_CONFIG: Dict[str, Any] = json.load(_f)
    except Exception as e:
        raise RuntimeError(
            f"Lead Sorcerer wrapper: required icp_config.json not found or unreadable "
            f"at {ICP_TEMPLATE_PATH}. Error: {e}") from e

    def create_industry_specific_config(
            industry: str | None = None) -> Dict[str, Any]:
        """
        Clone the canonical icp_config.json and (optionally) tweak `icp_text`
        and `queries` if the caller requested a specific industry.

        NOTE: There is deliberately NO generic default – if the template file
        is absent we abort early.
        """
        config = json.loads(json.dumps(BASE_ICP_CONFIG))  # deep-copy

        if not industry:
            return config

        ind = industry.lower()

        # minimal heuristic tweak (keeps the rest of the template intact)
        if any(k in ind for k in ("tech", "software", "ai")):
            config["icp_text"] = "Technology companies needing contacts."
            config["queries"] = ["technology company contact information"]
        elif any(k in ind for k in ("finance", "fintech", "bank")):
            config[
                "icp_text"] = "Finance / FinTech organisations needing contacts."
            config["queries"] = ["fintech company contact information"]
        elif any(k in ind for k in ("health", "med", "clinic")):
            config[
                "icp_text"] = "Healthcare & wellness businesses needing contacts."
            config["queries"] = ["healthcare company contact information"]
        # add more branches as desired …

        return config

    def setup_temp_environment(temp_dir: str):
        """Set up the temporary environment with required config files."""
        temp_path = Path(temp_dir)

        # Create config directory in temp
        temp_config_dir = temp_path / "config"
        temp_config_dir.mkdir(exist_ok=True)

        # Copy required config files
        source_config_dir = config_path

        # Copy costs.yaml (required)
        costs_file = source_config_dir / "costs.yaml"
        if costs_file.exists():
            shutil.copy2(costs_file, temp_config_dir / "costs.yaml")

        # Copy prompts directory if it exists
        source_prompts = source_config_dir / "prompts"
        if source_prompts.exists():
            temp_prompts = temp_config_dir / "prompts"
            shutil.copytree(source_prompts, temp_prompts, dirs_exist_ok=True)

        # NEW: copy the JSON-schema directory so validation works
        source_schemas = lead_sorcerer_dir / "schemas"
        if source_schemas.exists():
            temp_schemas = temp_path / "schemas"
            shutil.copytree(source_schemas, temp_schemas, dirs_exist_ok=True)

    def convert_lead_record_to_legacy_format(
            lead_record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert a Lead Sorcerer lead record to the format expected by the existing miner code.

        Includes enrichment:
        - Parses hq_location into country/state/city
        - Normalizes employee_count to LinkedIn format
        - Normalizes LinkedIn URLs
        - Extracts company_linkedin from socials
        - Sets source_url and source_type

        Args:
            lead_record: Lead record from Lead Sorcerer in unified schema format

        Returns:
            Lead in the format expected by the existing miner system
        """
        company = lead_record.get("company", {})
        contacts = lead_record.get("contacts", [])

        # Get the best contact (prefer one with an email, then any contact)
        best_contact = None
        if contacts:
            # Prefer contacts with email addresses
            email_contacts = [c for c in contacts if c.get("email")]
            if email_contacts:
                best_contact = email_contacts[0]
            else:
                # Otherwise use the first contact
                best_contact = contacts[0]

        # Extract contact information
        if best_contact:
            # Handle both full_name (from crawl tool) and first_name/last_name (legacy)
            full_name = best_contact.get("full_name") or ""
            first_name = best_contact.get("first_name") or ""
            last_name = best_contact.get("last_name") or ""

            # If we have full_name but not first/last, try to split
            if full_name and not (first_name or last_name):
                name_parts = full_name.split(maxsplit=1)
                first_name = name_parts[0] if len(name_parts) > 0 else ""
                last_name = name_parts[1] if len(name_parts) > 1 else ""
            # If we have first/last but not full_name, combine them
            elif not full_name and (first_name or last_name):
                full_name = f"{first_name} {last_name}".strip()

            email = best_contact.get("email") or ""
            # Handle both 'role' (from crawl tool) and 'job_title' (legacy)
            job_title = best_contact.get("role") or best_contact.get(
                "job_title") or ""
            # Extract LinkedIn URL (can be full URL or path like "/in/username")
            linkedin_raw = best_contact.get("linkedin") or best_contact.get("linkedin_url") or ""
        else:
            first_name = ""
            last_name = ""
            full_name = ""
            email = ""
            job_title = ""
            linkedin_raw = ""

        # Helper function to safely get string values
        def safe_str(value, default=""):
            """Safely convert value to string, handling None values."""
            if value is None:
                return default
            return str(value)

        # ============================================================
        # ENRICHMENT: Parse and normalize fields
        # ============================================================

        # Parse hq_location into country/state/city
        hq_location = safe_str(company.get("hq_location"))
        country, state, city = parse_location(hq_location)

        # Normalize employee count to LinkedIn format
        raw_employee_count = safe_str(company.get("employee_count"))
        employee_count = normalize_employee_count(raw_employee_count)

        # Normalize person LinkedIn URL
        linkedin = normalize_linkedin_url(linkedin_raw, "profile")
        # Fallback to default LinkedIn from ICP config if not found
        if not linkedin and BASE_ICP_CONFIG.get("default_contact_linkedin"):
            linkedin = normalize_linkedin_url(
                BASE_ICP_CONFIG["default_contact_linkedin"], "profile"
            )

        # Extract and normalize company LinkedIn from socials
        company_linkedin_raw = company.get("socials", {}).get("linkedin", "")
        company_linkedin = normalize_linkedin_url(company_linkedin_raw, "company")

        # Set source_url and source_type
        domain = safe_str(lead_record.get('domain'))
        source_url = f"https://{domain}" if domain else ""
        source_type = "company_site" if source_url else ""

        # Build website URL
        website = f"https://{domain}" if domain else ""

        # ============================================================
        # Build the enhanced format with all required fields
        # ============================================================
        legacy_lead = {
            # Core business fields
            "business": safe_str(company.get("name")),
            "description": safe_str(company.get("description")),
            "website": website,
            "industry": safe_str(company.get("industry")),
            "sub_industry": safe_str(company.get("sub_industry")),

            # Contact fields
            "full_name": full_name,
            "first": first_name,
            "last": last_name,
            "email": email,
            "role": job_title,
            "linkedin": linkedin,

            # Location fields (parsed from hq_location)
            "country": country,
            "state": state,
            "city": city,

            # NEW required fields
            "company_linkedin": company_linkedin,
            "employee_count": employee_count,
            "source_url": source_url,
            "source_type": source_type,

            # Optional fields
            "phone_numbers": company.get("phone_numbers", []),
            "region": hq_location,  # Keep original for reference
            "founded_year": safe_str(company.get("founded_year")),
            "ownership_type": safe_str(company.get("ownership_type")),
            "company_type": safe_str(company.get("company_type")),
            "number_of_locations": safe_str(company.get("number_of_locations")),
            "socials": company.get("socials", {}),
        }

        return legacy_lead

    async def run_lead_sorcerer_pipeline(
            num_leads: int,
            industry: str = None,
            region: str = None) -> List[Dict[str, Any]]:
        """
        Run the Lead Sorcerer pipeline and extract leads.
        
        Args:
            num_leads: Number of leads to generate
            industry: Target industry (optional)
            region: Target region (optional)
            
        Returns:
            List of lead records from Lead Sorcerer
        """
        if not LEAD_SORCERER_AVAILABLE:
            return []

        # Create a temporary directory for this run
        with tempfile.TemporaryDirectory() as temp_dir:
            # Set up the temporary environment with config files
            setup_temp_environment(temp_dir)

            # Set the data directory
            os.environ["LEADPOET_DATA_DIR"] = temp_dir

            # Change to temp directory so relative paths work
            original_cwd = os.getcwd()
            try:
                # Ensure names are always defined inside the processing scope
                first_name = candidate.get("first_name", "") if "first_name" in candidate else first_name
                last_name = candidate.get("last_name", "") if "last_name" in candidate else last_name
                if full_name and (not first_name or not last_name):
                    name_parts = full_name.split(maxsplit=1)
                    first_name = first_name or (name_parts[0] if name_parts else "")
                    last_name = last_name or (name_parts[1] if len(name_parts) > 1 else "")

                os.chdir(temp_dir)

                # Create configuration
                config = create_industry_specific_config(industry)

                # Use higher caps for better throughput
                # Top miners process 50+ domains per run to maximize lead output
                config["caps"]["max_domains_per_run"] = min(
                    max(num_leads * 10, 30), 50)  # 30-50 domains
                config["caps"]["max_crawl_per_run"] = min(
                    max(num_leads * 10, 30), 50)  # 30-50 crawls

                # Save config to temporary file
                config_file = Path(temp_dir) / "icp_config.json"
                with open(config_file, "w") as f:
                    json.dump(config, f, indent=2)

                try:
                    # Initialize and run orchestrator
                    orchestrator = LeadSorcererOrchestrator(
                        str(config_file), batch_size=num_leads)

                    async with orchestrator:  # Use async context manager for proper cleanup
                        result = await orchestrator.run_pipeline()

                        if not result.get("success"):
                            print(
                                f"⚠️ Lead Sorcerer pipeline failed: {result.get('errors', [])}"
                            )
                            return []

                        # Extract leads from the result - look in exports directory
                        leads = []

                        # Look for exported leads in the exports directory
                        exports_dir = Path(temp_dir) / "exports"
                        print(f"📂 DEBUG: Looking for exports in: {exports_dir}")
                        print(f"📂 DEBUG: exports_dir exists: {exports_dir.exists()}")

                        if exports_dir.exists():
                            # Find the most recent export directory
                            export_dirs = list(exports_dir.glob("*/*"))
                            print(f"📂 DEBUG: Found {len(export_dirs)} export directories")
                            for ed in export_dirs[:3]:
                                print(f"   - {ed}")

                            if export_dirs:
                                latest_export = max(
                                    export_dirs,
                                    key=lambda x: x.stat().st_mtime)
                                leads_file = latest_export / "leads.jsonl"
                                print(f"📂 DEBUG: Latest export: {latest_export}")
                                print(f"📂 DEBUG: leads_file exists: {leads_file.exists()}")

                                if leads_file.exists():
                                    line_count = 0
                                    valid_count = 0
                                    with open(leads_file, "r") as f:
                                        for line in f:
                                            line_count += 1
                                            if line.strip():
                                                try:
                                                    lead_record = json.loads(line)
                                                    company_name = lead_record.get("company", {}).get("name")
                                                    domain = lead_record.get("domain", "")
                                                    # Include ALL leads - LinkedIn enrichment will add contacts
                                                    # Only require company name to be present
                                                    if (company_name and len(leads) < num_leads * 3):
                                                        leads.append(lead_record)
                                                        valid_count += 1
                                                    elif not company_name:
                                                        print(f"   ⚠️ Skipped lead (no company name): domain={domain}")
                                                except json.JSONDecodeError as e:
                                                    print(f"   ⚠️ JSON decode error on line {line_count}: {e}")
                                                    continue
                                    print(f"📂 DEBUG: Read {line_count} lines, {valid_count} valid leads from exports")
                                else:
                                    print(f"📂 DEBUG: leads.jsonl not found at {leads_file}")

                        # Fallback: also check the traditional locations
                        if not leads:
                            print(f"📂 DEBUG: No leads from exports, trying domain_pass.jsonl fallback")
                            domain_pass_file = Path(
                                temp_dir) / "domain_pass.jsonl"
                            print(f"📂 DEBUG: domain_pass_file exists: {domain_pass_file.exists()}")

                            # Try to read from domain results
                            if domain_pass_file.exists():
                                fallback_count = 0
                                with open(domain_pass_file, "r") as f:
                                    for line in f:
                                        if line.strip():
                                            try:
                                                lead_record = json.loads(line)
                                                # Include leads that passed ICP - contacts will be added via LinkedIn
                                                if (lead_record.get("icp", {}).get("pre_pass")
                                                        and lead_record.get("company", {}).get("name")
                                                        and len(leads) < num_leads * 3):
                                                    leads.append(lead_record)
                                                    fallback_count += 1
                                            except json.JSONDecodeError:
                                                continue
                                print(f"📂 DEBUG: Loaded {fallback_count} leads from domain_pass.jsonl fallback")

                        print(f"📂 DEBUG: Returning {len(leads)} leads from run_lead_sorcerer_pipeline")
                        return leads  # Return all leads - filtering happens after enrichment

                except Exception as e:
                    print(f"❌ Error running Lead Sorcerer pipeline: {e}")
                    return []

            finally:
                # Always restore the original working directory
                os.chdir(original_cwd)

    async def run_linkedin_first_pipeline(
            num_leads: int,
            config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        LinkedIn-First Pipeline: Find decision makers directly on LinkedIn.

        This is more efficient than the website-first approach because:
        1. LinkedIn profiles have accurate job titles
        2. Direct targeting of decision makers (Owner, CEO, President, etc.)
        3. Less noise from garbage website data
        4. Lower cost (skip Firecrawl, just GSE + TrueList)

        Pipeline steps:
        1. GSE search for LinkedIn profiles of decision makers
        2. Parse name, role, company from LinkedIn GSE snippets
        3. Find company domain from company name
        4. Get company LinkedIn for employee count, description
        5. Generate and verify email via TrueList
        6. Pre-submission validation
        7. Return leads

        Args:
            num_leads: Number of leads to generate
            config: ICP config with linkedin_queries

        Returns:
            List of complete, validated leads
        """
        print(f"\n{'='*60}")
        print("🔗 LINKEDIN-FIRST PIPELINE")
        print(f"{'='*60}")

        # Get LinkedIn queries - use pre-defined or generate with LLM
        linkedin_queries = config.get("linkedin_queries", [])

        if not linkedin_queries:
            print(f"📋 No pre-defined linkedin_queries - generating with LLM...")
            linkedin_queries = await generate_linkedin_queries_with_llm(config, num_queries=25)

            if not linkedin_queries:
                print("⚠️ Failed to generate LinkedIn queries - check OPENROUTER_KEY")
                return []
        else:
            print(f"📋 Using {len(linkedin_queries)} pre-defined LinkedIn queries")

        print(f"🎯 Target: {num_leads} complete leads")

        all_candidates = []
        seen_linkedin_urls = set()
        results_per_query = config.get("search", {}).get("max_results_per_query", 10)

        # Step 1: Search LinkedIn for decision makers
        print(f"\n📍 Step 1: Searching LinkedIn for decision makers...")
        for i, query in enumerate(linkedin_queries):
            if len(all_candidates) >= num_leads * 3:
                print(f"   ✓ Collected enough candidates ({len(all_candidates)}), stopping search")
                break

            print(f"   [{i+1}/{len(linkedin_queries)}] {query[:60]}...")
            try:
                candidates = await search_linkedin_decision_makers(query, num_results=results_per_query)

                # Dedupe by LinkedIn URL
                new_count = 0
                for c in candidates:
                    linkedin_url = c.get("linkedin_url", "")
                    if linkedin_url and linkedin_url not in seen_linkedin_urls:
                        seen_linkedin_urls.add(linkedin_url)
                        all_candidates.append(c)
                        new_count += 1

                print(f"      → Found {len(candidates)} profiles, {new_count} new")

            except Exception as e:
                print(f"      ⚠️ Query failed: {e}")
                continue

        print(f"\n📊 LinkedIn search complete: {len(all_candidates)} unique candidates")

        if not all_candidates:
            print("⚠️ No candidates found, try different LinkedIn queries")
            return []

        # ================================================================
        # PHASE 1: Enrich all candidates (domain, company data, location)
        #          and collect email candidates (async, no blocking verification)
        # ================================================================
        print(f"\n📍 Phase 1: Enriching candidates and collecting email candidates...")

        enriched_candidates = []  # Store enriched data per candidate
        email_candidates_by_idx = {}  # Map idx -> list of candidate emails
        skipped_count = 0
        firecrawl_used_count = 0
        enrichment_cache = EnrichmentCache()
        enrichment_config = build_enrichment_config(config)

        # Process more candidates than needed since some will fail email verification
        candidates_to_process = min(len(all_candidates), num_leads * 3)

        def _should_verify_email(enriched: Dict[str, Any]) -> Tuple[bool, List[str]]:
            errors: List[str] = []

            company = enriched.get("company", "")
            description = enriched.get("description", "")
            industry = enriched.get("industry", "")
            sub_industry = enriched.get("sub_industry", "")
            country = enriched.get("country", "")
            state = enriched.get("state", "")
            city = enriched.get("city", "")
            role = enriched.get("role", "")
            full_name = enriched.get("full_name", "")
            first_name = enriched.get("first_name", "")
            last_name = enriched.get("last_name", "")
            linkedin_url = enriched.get("linkedin_url", "")
            company_linkedin = enriched.get("company_linkedin", "")
            employee_count = enriched.get("employee_count", "")
            domain = enriched.get("domain", "")
            source_url = enriched.get("source_url") or (f"https://{domain}" if domain else "")

            if not company:
                errors.append("business_empty")
            if not full_name or not first_name or not last_name:
                errors.append("name_missing")
            if not domain:
                errors.append("domain_missing")

            website = f"https://{domain}" if domain else ""
            if website:
                ok, err = validate_website_url(website)
                if not ok:
                    errors.append(f"website: {err}")
            else:
                errors.append("website_empty")

            if not description:
                if industry:
                    description = f"{company} - {industry}"
                else:
                    description = f"{company} - Manufacturing company"

            ok, err = validate_description(description)
            if not ok:
                errors.append(f"description: {err}")

            ok, err = validate_employee_count(employee_count)
            if not ok:
                errors.append(f"employee_count: {err}")

            ok, err = validate_role_format(role, full_name, company)
            if not ok:
                errors.append(f"role: {err}")

            if linkedin_url:
                ok, err = validate_linkedin_url(linkedin_url, "person")
                if not ok:
                    errors.append(f"linkedin: {err}")
            else:
                errors.append("linkedin_empty")

            if company_linkedin:
                ok, err = validate_linkedin_url(company_linkedin, "company")
                if not ok:
                    errors.append(f"company_linkedin: {err}")
            else:
                errors.append("company_linkedin_empty")

            if source_url:
                ok, err = validate_source_url(source_url, "company_site")
                if not ok:
                    errors.append(f"source_url: {err}")
            else:
                errors.append("source_url_empty")

            ok, err = validate_location(city, state, country)
            if not ok:
                errors.append(f"location: {err}")

            ok, err = validate_industry_taxonomy(industry, sub_industry)
            if not ok:
                errors.append(f"industry: {err}")

            return len(errors) == 0, errors

        for idx, candidate in enumerate(all_candidates[:candidates_to_process]):
            full_name = candidate.get("full_name", "")
            company = candidate.get("company", "")
            role = candidate.get("role", "")
            linkedin_url = candidate.get("linkedin_url", "")
            first_name = candidate.get("first_name", "")
            last_name = candidate.get("last_name", "")
            if full_name and (not first_name or not last_name):
                name_parts = full_name.split(maxsplit=1)
                first_name = first_name or (name_parts[0] if name_parts else "")
                last_name = last_name or (name_parts[1] if len(name_parts) > 1 else "")

            cleaned_company = clean_company_name_for_search(company)
            if cleaned_company and cleaned_company != company:
                print(f"   ℹ️ Cleaned company name: {company} -> {cleaned_company}")
                company = cleaned_company

            # Skip if missing critical data
            if not full_name or not company:
                skipped_count += 1
                continue

            print(f"\n🏢 [{idx+1}] {full_name} - {role} at {company}")

            try:
                # Step 2: Find company domain (cached)
                print(f"   🔍 Finding company domain...")
                domain = await get_company_domain_cached(company, enrichment_cache, enrichment_config)
                if domain:
                    print(f"   ✓ Found domain: {domain}")
                else:
                    print(f"   ⚠️ Could not find domain for {company}")
                    skipped_count += 1
                    continue

                # Step 3: Enrich company fields (cached + concurrent)
                print(f"   🔍 Enriching company data...")
                person_location = candidate.get("location", "")
                person_task = None
                if not role or not linkedin_url or not person_location:
                    person_task = enrich_person_fields(
                        full_name, company, enrichment_cache, enrichment_config
                    )

                company_task = enrich_company_fields(
                    company, domain, enrichment_cache, enrichment_config
                )

                if person_task:
                    (company_fields, company_prov), (person_fields, person_prov) = await asyncio.gather(
                        company_task, person_task
                    )
                else:
                    company_fields, company_prov = await company_task
                    person_fields, person_prov = {}, {}
                if any(p.source == "firecrawl" for p in company_prov.values()):
                    firecrawl_used_count += 1

                company_linkedin = company_fields.get("company_linkedin", "")
                employee_count = company_fields.get("employee_count", "")
                description = company_fields.get("description", "")
                industry = company_fields.get("industry", "")
                sub_industry = company_fields.get("sub_industry", "")
                hq_location = company_fields.get("hq_location", "")
                company_city = company_fields.get("city", "")
                company_state = company_fields.get("state", "")
                company_country = company_fields.get("country", "")
                company_source_url = company_fields.get("source_url", "")
                company_phone_numbers = company_fields.get("phone_numbers", [])
                company_founded_year = company_fields.get("founded_year", "")
                company_ownership_type = company_fields.get("ownership_type", "")
                company_company_type = company_fields.get("company_type", "")
                company_number_of_locations = company_fields.get("number_of_locations", "")
                company_socials = company_fields.get("socials", {})

                if company_linkedin:
                    print(f"   ✓ Company LinkedIn: {company_linkedin[:50]}...")
                if employee_count:
                    print(f"   ✓ Employee count: {employee_count}")

                # Step 3b: Enrich person fields if needed
                lead_provenance = dict(company_prov)
                if role:
                    lead_provenance["role"] = FieldProvenance(
                        source="linkedin_first",
                        confidence=0.7,
                        method="decision_maker_query",
                        timestamp=time.time()
                    )
                if linkedin_url:
                    lead_provenance["linkedin"] = FieldProvenance(
                        source="linkedin_first",
                        confidence=0.7,
                        method="decision_maker_query",
                        timestamp=time.time()
                    )

                if person_fields:
                    if not role and person_fields.get("role"):
                        role = person_fields.get("role")
                    if not linkedin_url and person_fields.get("linkedin"):
                        linkedin_url = person_fields.get("linkedin")
                    if not person_location and person_fields.get("location"):
                        person_location = person_fields.get("location")
                lead_provenance.update(person_prov)

                # Parse location - try LinkedIn/person location first
                location = person_location or ""
                country, state, city = parse_location(location)
                if city and not is_fake_city(city):
                    temp_country = country or "United States"
                    valid_loc, _ = validate_location(city, state, temp_country)
                    if valid_loc:
                        country = temp_country
                        lead_provenance["city"] = FieldProvenance(
                            source="linkedin_first",
                            confidence=0.6,
                            method="person_location",
                            timestamp=time.time()
                        )
                        lead_provenance["state"] = FieldProvenance(
                            source="linkedin_first",
                            confidence=0.6,
                            method="person_location",
                            timestamp=time.time()
                        )
                        lead_provenance["country"] = FieldProvenance(
                            source="linkedin_first",
                            confidence=0.6,
                            method="person_location",
                            timestamp=time.time()
                        )
                    else:
                        city, state, country = "", "", ""

                # If location not from LinkedIn, try company city/state/country
                if not city and company_city:
                    city = company_city
                    state = company_state or state
                    country = company_country or country
                    if "city" in company_prov:
                        lead_provenance["city"] = company_prov["city"]
                    if "state" in company_prov:
                        lead_provenance["state"] = company_prov["state"]
                    if "country" in company_prov:
                        lead_provenance["country"] = company_prov["country"]

                # If still missing, try parsing hq_location
                if not city and hq_location:
                    fc_country, fc_state, fc_city = parse_location(hq_location)

                    if fc_state:
                        state_key = fc_state.lower().strip().replace('.', '')
                        state_key = STATE_ABBR_TO_NAME.get(state_key, state_key)
                        if state_key not in US_STATES:
                            fc_state = ""
                            fc_city = ""
                        else:
                            fc_state = state_key.title()

                    if fc_city:
                        city = fc_city
                        lead_provenance["city"] = FieldProvenance(
                            source="hq_location",
                            confidence=0.6,
                            method="parse_location",
                            timestamp=time.time()
                        )
                    if fc_state:
                        state = fc_state
                        lead_provenance["state"] = FieldProvenance(
                            source="hq_location",
                            confidence=0.6,
                            method="parse_location",
                            timestamp=time.time()
                        )
                    if fc_country:
                        country = fc_country
                        lead_provenance["country"] = FieldProvenance(
                            source="hq_location",
                            confidence=0.6,
                            method="parse_location",
                            timestamp=time.time()
                        )

                # Default to US if not detected
                if not country:
                    country = "United States"

                # Drop invalid US state values and try to infer from city
                if country == "United States":
                    if state:
                        state_key = state.lower().strip().replace('.', '')
                        state_key = STATE_ABBR_TO_NAME.get(state_key, state_key)
                        if state_key not in US_STATES:
                            print(f"   ⚠️ Dropping invalid state parsed from location: {state}")
                            state = ""
                        else:
                            state = state_key.title()

                    if city and not state:
                        inferred_state = infer_state_from_city(city)
                        if inferred_state:
                            state = inferred_state
                            lead_provenance["state"] = FieldProvenance(
                                source="city_infer",
                                confidence=0.55,
                                method="city_state_lookup",
                                timestamp=time.time()
                            )

                # Determine sub_industry - from Firecrawl, config, or default
                # Apply normalization to ensure it matches taxonomy
                raw_sub = sub_industry or config.get("targeting", {}).get("sub_industries", ["Manufacturing"])[0]
                final_sub_industry, final_industry = normalize_sub_industry(raw_sub, industry)
                # Use normalized industry if available, otherwise keep original
                if final_industry:
                    industry = final_industry

                # Store enriched data (email will be added after batch verification)
                enriched_data = {
                    "candidate": candidate,
                    "domain": domain,
                    "company": company,
                    "company_linkedin": company_linkedin,
                    "employee_count": employee_count,
                    "description": description,
                    "industry": industry,
                    "sub_industry": final_sub_industry,
                    "hq_location": hq_location,
                    "source_url": company_source_url,
                    "phone_numbers": company_phone_numbers,
                    "founded_year": company_founded_year,
                    "ownership_type": company_ownership_type,
                    "company_type": company_company_type,
                    "number_of_locations": company_number_of_locations,
                    "socials": company_socials,
                    "full_name": full_name,
                    "first_name": first_name,
                    "last_name": last_name,
                    "role": role,
                    "linkedin_url": linkedin_url,
                    "country": country,
                    "state": state,
                    "city": city,
                    "provenance": lead_provenance,
                }
                should_verify, verify_errors = _should_verify_email(enriched_data)
                if not should_verify:
                    print(f"   ⚠️ Skipping email verification for incomplete lead: {', '.join(verify_errors[:4])}")
                    skipped_count += 1
                    continue

                # Step 4: Collect email candidates (don't verify yet)
                if first_name and last_name and domain:
                    print(f"   📧 Collecting email candidates for {first_name} {last_name}@{domain}...")
                    email_candidates = await collect_email_candidates(first_name, last_name, domain)
                    if email_candidates:
                        print(f"   ✓ Found {len(email_candidates)} email candidates")
                        email_candidates_by_idx[len(enriched_candidates)] = email_candidates
                    else:
                        print(f"   ⚠️ No email candidates found")
                        skipped_count += 1
                        continue
                else:
                    print(f"   ⚠️ Missing name/domain for email generation")
                    skipped_count += 1
                    continue

                enriched_candidates.append(enriched_data)
                print(f"   ✓ Candidate enriched (pending email verification)")

            except Exception as e:
                print(f"   ⚠️ Error processing candidate: {e}")
                skipped_count += 1
                continue

        print(f"\n📊 Phase 1 complete: {len(enriched_candidates)} candidates enriched, {len(email_candidates_by_idx)} pending email verification")

        # ================================================================
        # PHASE 2: Batch verify all emails at once (single TrueList call)
        # ================================================================
        print(f"\n📍 Phase 2: Batch email verification...")

        if email_candidates_by_idx:
            verified_emails = await batch_verify_emails(email_candidates_by_idx)
        else:
            verified_emails = {}

        email_found_count = sum(1 for v in verified_emails.values() if v)

        # ================================================================
        # PHASE 3: Build final leads with verified emails and validate
        # ================================================================
        print(f"\n📍 Phase 3: Building and validating final leads...")

        enriched_leads = []
        validation_failed_count = 0
        no_email_count = 0

        for idx, enriched_data in enumerate(enriched_candidates):
            if len(enriched_leads) >= num_leads:
                break

            # Get verified email for this candidate
            verified_email = verified_emails.get(idx)
            if not verified_email:
                no_email_count += 1
                continue

            full_name = enriched_data["full_name"]
            company = enriched_data["company"]
            print(f"\n   ✓ [{idx+1}] {full_name} at {company} - email: {verified_email}")

            lead_provenance = dict(enriched_data.get("provenance", {}))
            lead_provenance["email"] = FieldProvenance(
                source="truelist",
                confidence=0.95,
                method="batch_verify",
                timestamp=time.time()
            )

            # Build lead record with ALL required fields
            source_url = enriched_data.get("source_url") or f"https://{enriched_data['domain']}"
            phone_numbers = enriched_data.get("phone_numbers") or []
            if isinstance(phone_numbers, str):
                phone_numbers = [phone_numbers]

            legacy_lead = {
                # Required company fields
                "business": enriched_data["company"],
                "description": enriched_data["description"] or f"{company} - {enriched_data['industry']}" if enriched_data["industry"] else f"{company} - Manufacturing company",
                "website": f"https://{enriched_data['domain']}",
                "industry": enriched_data["industry"] or "Manufacturing",
                "sub_industry": enriched_data["sub_industry"],
                # Required contact fields
                "full_name": enriched_data["full_name"],
                "first": enriched_data["first_name"],
                "last": enriched_data["last_name"],
                "email": verified_email,
                "role": enriched_data["role"],
                "linkedin": enriched_data["linkedin_url"],
                # Required location fields
                "country": enriched_data["country"],
                "state": enriched_data["state"],
                "city": enriched_data["city"],
                # Required company links and data
                "company_linkedin": enriched_data["company_linkedin"],
                "employee_count": enriched_data["employee_count"],
                # Required source tracking
                "source_url": source_url,
                "source_type": "company_site",
                # Optional fields (best effort)
                "phone_numbers": phone_numbers,
                "founded_year": enriched_data.get("founded_year") or "",
                "ownership_type": enriched_data.get("ownership_type") or "",
                "company_type": enriched_data.get("company_type") or "",
                "number_of_locations": enriched_data.get("number_of_locations") or "",
                "socials": enriched_data.get("socials") or {},
            }

            # Normalize employee count early (avoid invalid placeholders)
            employee_count_norm = normalize_employee_count(legacy_lead.get("employee_count", ""))
            if employee_count_norm:
                legacy_lead["employee_count"] = employee_count_norm
            else:
                legacy_lead["employee_count"] = ""

            # Enforce industry taxonomy (fallback to ICP defaults if needed)
            tax_valid, _ = validate_industry_taxonomy(
                legacy_lead.get("industry", ""),
                legacy_lead.get("sub_industry", "")
            )
            if not tax_valid:
                target_cfg = config.get("targeting", {})
                fallback_sub = (target_cfg.get("sub_industries") or [legacy_lead.get("sub_industry", "")])[0]
                fallback_ind = (target_cfg.get("industries") or [legacy_lead.get("industry", "")])[0]
                sub_norm, ind_norm = normalize_sub_industry(fallback_sub, fallback_ind)
                if sub_norm:
                    legacy_lead["sub_industry"] = sub_norm
                if ind_norm:
                    legacy_lead["industry"] = ind_norm

            # Check for missing required fields BEFORE validation
            required_fields = [
                "business", "full_name", "first", "last", "email", "role",
                "website", "industry", "sub_industry", "country", "city",
                "linkedin", "company_linkedin", "description", "employee_count",
                "source_url"
            ]

            gate_ok, gate_failed = passes_confidence_gating(
                required_fields, lead_provenance, enrichment_config.confidence_thresholds
            )
            if not gate_ok:
                print(f"      ⚠️ Low-confidence fields: {', '.join(gate_failed)}")
                skipped_count += 1
                continue
            missing_required = [f for f in required_fields if not legacy_lead.get(f)]

            # State is required for US only
            if legacy_lead.get("country") == "United States" and not legacy_lead.get("state"):
                missing_required.append("state")

            if missing_required:
                print(f"      ⚠️ Missing required fields: {', '.join(missing_required)}")
                skipped_count += 1
                continue

            # Normalize location
            city_norm, state_norm, country_norm = normalize_location_for_submission(
                legacy_lead.get("city", ""),
                legacy_lead.get("state", ""),
                legacy_lead.get("country", "")
            )
            legacy_lead["city"] = city_norm
            legacy_lead["state"] = state_norm
            legacy_lead["country"] = country_norm

            # Clean role
            role_cleaned = clean_role_for_submission(
                legacy_lead.get("role", ""),
                legacy_lead.get("full_name", ""),
                legacy_lead.get("business", "")
            )
            legacy_lead["role"] = role_cleaned

            # Validate
            is_valid, validation_errors = validate_lead_pre_submission(legacy_lead)

            if is_valid:
                enriched_leads.append(legacy_lead)
                print(f"      ✅ Lead passed validation - ready for submission")
            else:
                print(f"      ⚠️ Validation failed:")
                for error in validation_errors:
                    print(f"         - {error}")
                validation_failed_count += 1

        # Summary
        print(f"\n{'='*60}")
        print(f"📊 LINKEDIN-FIRST PIPELINE SUMMARY")
        print(f"{'='*60}")
        print(f"   LinkedIn profiles found: {len(all_candidates)}")
        print(f"   Candidates enriched: {len(enriched_candidates)}")
        print(f"   Firecrawl fallbacks used: {firecrawl_used_count}")
        print(f"   Emails verified: {email_found_count}")
        print(f"   No email verified: {no_email_count}")
        print(f"   Complete leads ready: {len(enriched_leads)}")
        print(f"   Skipped (incomplete): {skipped_count}")
        print(f"   Failed validation: {validation_failed_count}")

        # Step 7: Rank leads by ICP fit (submit best leads first)
        if enriched_leads:
            print(f"\n📍 Step 7: Ranking leads by ICP fit...")
            enriched_leads = await rank_leads(enriched_leads, config, use_llm=True)

        return enriched_leads

    async def get_leads(num_leads: int,
                        industry: str = None,
                        region: str = None) -> List[Dict[str, Any]]:
        """
        Generate leads using the Lead Sorcerer model with enrichment pipeline.

        This function:
        1. Runs the Lead Sorcerer pipeline to generate raw leads
        2. Converts leads to legacy format with enrichment (location parsing, etc.)
        3. Attempts to enrich missing LinkedIn URLs via Google search (GSE)
        4. Filters out incomplete leads (missing required fields)

        Args:
            num_leads: Number of leads to generate
            industry: Target industry (optional)
            region: Target region (optional)

        Returns:
            List of complete, enriched leads in the format expected by the miner
        """
        # Check pipeline mode from config
        pipeline_mode = BASE_ICP_CONFIG.get("pipeline_mode", "website_first")

        # LinkedIn-first requires GSE + OpenRouter (for query generation)
        # Firecrawl is optional fallback for missing fields
        if pipeline_mode == "linkedin_first":
            required_env_vars = ["GSE_API_KEY", "GSE_CX", "OPENROUTER_KEY"]
            print("\n🔗 Pipeline Mode: LINKEDIN-FIRST")
            print("   → Primary source: LinkedIn profiles via GSE")
            print("   → OpenRouter: Dynamic query generation from ICP")
            print("   → Firecrawl: Fallback for missing fields only")
            print("   → Higher accuracy for decision makers")
        else:
            required_env_vars = [
                "GSE_API_KEY", "GSE_CX", "OPENROUTER_KEY", "FIRECRAWL_KEY"
            ]
            print("\n🌐 Pipeline Mode: WEBSITE-FIRST")
            print("   → Primary source: Company websites via Firecrawl")

        missing_vars = [var for var in required_env_vars if not os.getenv(var)]

        if missing_vars:
            print(
                f"⚠️ Lead Sorcerer missing required environment variables: {missing_vars}"
            )
            print("   Please set these in your .env file or environment")
            return []

        # Check optional TrueList for email verification (HIGHLY RECOMMENDED)
        if os.getenv("TRUELIST_API_KEY"):
            print("ℹ️  Using TrueList for: Email pattern verification (pre-submission)")
        else:
            print("⚠️  TRUELIST_API_KEY not set - email pattern guessing will be unverified")
            print("   Get a free account at https://truelist.io ($60/mo unlimited or 250/day free)")

        # ============================================================
        # LINKEDIN-FIRST PIPELINE
        # ============================================================
        if pipeline_mode == "linkedin_first":
            try:
                leads = await run_linkedin_first_pipeline(num_leads, BASE_ICP_CONFIG)
                if leads:
                    print(f"\n✅ LinkedIn-first pipeline returned {len(leads)} leads")
                    return leads[:num_leads]
                else:
                    print("⚠️ LinkedIn-first pipeline returned no leads")
                    print("   Tip: Check linkedin_queries in icp_config.json")
                    return []
            except Exception as e:
                print(f"❌ LinkedIn-first pipeline error: {e}")
                import traceback
                traceback.print_exc()
                return []

        # ============================================================
        # WEBSITE-FIRST PIPELINE (original)
        # ============================================================
        if not LEAD_SORCERER_AVAILABLE:
            print("⚠️ Lead Sorcerer not available, returning empty results")
            return []

        try:
            # Run the Lead Sorcerer pipeline
            # Request more leads than needed since some will be filtered out
            pipeline_leads = num_leads * 3  # Request 3x to account for filtering
            lead_records = await run_lead_sorcerer_pipeline(
                pipeline_leads, industry, region)

            if not lead_records:
                print("⚠️ Lead Sorcerer produced no leads")
                return []

            print(f"📊 Lead Sorcerer produced {len(lead_records)} raw leads")

            # Convert and enrich leads
            enriched_leads = []
            skipped_count = 0
            enriched_linkedin_count = 0
            verified_email_count = 0
            validation_failed_count = 0
            enrichment_cache = EnrichmentCache()
            enrichment_config = build_enrichment_config(BASE_ICP_CONFIG)

            for record in lead_records:
                try:
                    company_name = record.get("company", {}).get("name", "")
                    domain = record.get("domain", "")

                    # Skip leads without company name
                    if not company_name:
                        skipped_count += 1
                        continue

                    print(f"\n🏢 Processing: {company_name}")

                    # ============================================================
                    # EXECUTIVE SEARCH: If no contacts, search LinkedIn for execs
                    # ============================================================
                    contacts = record.get("contacts", [])
                    if not contacts:
                        print(f"   🔍 No contacts on website, searching LinkedIn for executives...")
                        try:
                            exec_contacts = await search_linkedin_executives(company_name, domain)
                            if exec_contacts:
                                record["contacts"] = exec_contacts
                                print(f"   ✅ Found {len(exec_contacts)} executive(s) on LinkedIn")
                                enriched_linkedin_count += len(exec_contacts)
                            else:
                                print(f"   ⚠️ No executives found on LinkedIn for {company_name}")
                        except Exception as e:
                            print(f"   ⚠️ Executive search failed: {e}")

                    # Convert to legacy format (includes initial enrichment)
                    legacy_lead = convert_lead_record_to_legacy_format(record)
                    lead_provenance = {}

                    # Skip if no business name (can't enrich without it)
                    if not legacy_lead.get("business"):
                        print(f"   ⚠️ No business name, skipping")
                        skipped_count += 1
                        continue

                    # ============================================================
                    # Enrichment: company + person fields (cached + concurrent)
                    # ============================================================
                    try:
                        person_task = None
                        if not legacy_lead.get("linkedin") or not legacy_lead.get("role"):
                            person_task = enrich_person_fields(
                                legacy_lead.get("full_name", ""),
                                legacy_lead.get("business", ""),
                                enrichment_cache,
                                enrichment_config
                            )

                        company_task = enrich_company_fields(
                            legacy_lead.get("business", ""), domain, enrichment_cache, enrichment_config
                        )

                        if person_task:
                            (company_fields, company_prov), (person_fields, person_prov) = await asyncio.gather(
                                company_task, person_task
                            )
                        else:
                            company_fields, company_prov = await company_task
                            person_fields, person_prov = {}, {}

                        lead_provenance.update(company_prov)
                        lead_provenance.update(person_prov)

                        if company_fields:
                            if company_fields.get("company_linkedin") and not legacy_lead.get("company_linkedin"):
                                legacy_lead["company_linkedin"] = company_fields["company_linkedin"]
                                enriched_linkedin_count += 1
                                print(f"   🔍 Found company LinkedIn: {company_fields['company_linkedin'][:50]}...")

                            if company_fields.get("employee_count") and not legacy_lead.get("employee_count"):
                                legacy_lead["employee_count"] = company_fields["employee_count"]
                                print(f"   📊 Enriched employee_count = {company_fields['employee_count']}")

                            if company_fields.get("description") and not legacy_lead.get("description"):
                                legacy_lead["description"] = company_fields["description"]
                                print(f"   📝 Enriched description")

                            if company_fields.get("industry") and not legacy_lead.get("industry"):
                                legacy_lead["industry"] = company_fields["industry"]
                                print(f"   🏭 Enriched industry = {company_fields['industry']}")

                            if company_fields.get("sub_industry") and not legacy_lead.get("sub_industry"):
                                legacy_lead["sub_industry"] = company_fields["sub_industry"]

                            if not legacy_lead.get("city") and company_fields.get("city"):
                                legacy_lead["city"] = company_fields["city"]
                            if not legacy_lead.get("state") and company_fields.get("state"):
                                legacy_lead["state"] = company_fields["state"]
                            if not legacy_lead.get("country") and company_fields.get("country"):
                                legacy_lead["country"] = company_fields["country"]

                        if person_fields:
                            if person_fields.get("linkedin") and not legacy_lead.get("linkedin"):
                                legacy_lead["linkedin"] = person_fields["linkedin"]
                                enriched_linkedin_count += 1
                                print(f"   🔍 Found person LinkedIn: {person_fields['linkedin'][:50]}...")

                            if person_fields.get("role") and not legacy_lead.get("role"):
                                legacy_lead["role"] = person_fields["role"]
                                print(f"   👔 Enriched role = {person_fields['role']}")

                            if person_fields.get("location") and not legacy_lead.get("city"):
                                loc = person_fields["location"]
                                city, state, country = parse_location(loc)
                                if city and not legacy_lead.get("city"):
                                    legacy_lead["city"] = city
                                if state and not legacy_lead.get("state"):
                                    legacy_lead["state"] = state
                                if country and not legacy_lead.get("country"):
                                    legacy_lead["country"] = country
                                print(f"   📍 Enriched location = {loc}")
                    except Exception as e:
                        print(f"   ⚠️ Enrichment failed: {e}")

                    # ============================================================
                    # EMAIL PATTERN VERIFICATION: If no email, try pattern guessing
                    # ============================================================
                    if not legacy_lead.get("email") and legacy_lead.get("full_name") and domain:
                        first_name = legacy_lead.get("first", "")
                        last_name = legacy_lead.get("last", "")

                        if first_name and last_name:
                            print(f"   📧 No email found - trying pattern verification...")
                            try:
                                verified_email = await get_verified_email(first_name, last_name, domain)
                                if verified_email:
                                    legacy_lead["email"] = verified_email
                                    verified_email_count += 1
                                    lead_provenance["email"] = FieldProvenance(
                                        source="truelist",
                                        confidence=0.95,
                                        method="pattern_verify",
                                        timestamp=time.time()
                                    )
                                    print(f"   ✅ Found verified email: {verified_email}")
                                else:
                                    print(f"   ⚠️ No verified email pattern found")
                            except Exception as e:
                                print(f"   ⚠️ Email verification failed: {e}")

                    # If email already present, mark provenance
                    if legacy_lead.get("email") and "email" not in lead_provenance:
                        lead_provenance["email"] = FieldProvenance(
                            source="lead_sorcerer",
                            confidence=0.8,
                            method="website_extraction",
                            timestamp=time.time()
                        )

                    # ============================================================
                    # COMPLETENESS CHECK: Skip incomplete leads
                    # ============================================================
                    if is_lead_complete(legacy_lead):
                        # ============================================================
                        # PRE-SUBMISSION VALIDATION: Catch issues before submission
                        # This prevents wasting rate limit quota on leads that will
                        # definitely be rejected by validators.
                        # ============================================================
                        required_fields = [
                            "business", "full_name", "first", "last", "email", "role",
                            "website", "industry", "sub_industry", "country", "city",
                            "linkedin", "company_linkedin", "description", "employee_count",
                            "source_url"
                        ]
                        gate_ok, gate_failed = passes_confidence_gating(
                            required_fields, lead_provenance, enrichment_config.confidence_thresholds
                        )
                        if not gate_ok:
                            print(f"   ⚠️ Low-confidence fields: {', '.join(gate_failed)}")
                            skipped_count += 1
                            continue

                        # Step 1: Normalize location fields to match gateway expectations
                        city_norm, state_norm, country_norm = normalize_location_for_submission(
                            legacy_lead.get("city", ""),
                            legacy_lead.get("state", ""),
                            legacy_lead.get("country", "")
                        )
                        legacy_lead["city"] = city_norm
                        legacy_lead["state"] = state_norm
                        legacy_lead["country"] = country_norm

                        # Step 2: Clean role field
                        role_cleaned = clean_role_for_submission(
                            legacy_lead.get("role", ""),
                            legacy_lead.get("full_name", ""),
                            legacy_lead.get("business", "")
                        )
                        legacy_lead["role"] = role_cleaned

                        # Step 2b: Normalize employee count
                        employee_count_norm = normalize_employee_count(legacy_lead.get("employee_count", ""))
                        if employee_count_norm:
                            legacy_lead["employee_count"] = employee_count_norm

                        # Step 3: Run all validations
                        is_valid, validation_errors = validate_lead_pre_submission(legacy_lead)

                        if is_valid:
                            enriched_leads.append(legacy_lead)
                            print(f"   ✅ Complete lead passed validation, ready for submission")
                        else:
                            print(f"   ⚠️ Validation failed:")
                            for error in validation_errors:
                                print(f"      - {error}")
                            validation_failed_count += 1
                    else:
                        missing = get_missing_fields(legacy_lead)
                        print(f"   ⚠️ Incomplete - Missing: {', '.join(missing)}")
                        skipped_count += 1

                    # Stop if we have enough complete leads
                    if len(enriched_leads) >= num_leads:
                        break

                except Exception as e:
                    print(f"⚠️ Error processing lead record: {e}")
                    skipped_count += 1
                    continue

            # Summary
            print(f"\n📊 Enrichment Summary:")
            print(f"   Raw leads processed: {len(lead_records)}")
            print(f"   LinkedIn fields enriched: {enriched_linkedin_count}")
            print(f"   Emails verified via patterns: {verified_email_count}")
            print(f"   Complete leads ready: {len(enriched_leads)}")
            print(f"   Skipped (incomplete): {skipped_count}")
            print(f"   Failed validation: {validation_failed_count}")
            if validation_failed_count > 0:
                print(f"   ℹ️ Validation prevented {validation_failed_count} bad leads from wasting rate limit")

            # Rank leads by ICP fit (submit best leads first)
            if enriched_leads:
                print(f"\n📍 Ranking leads by ICP fit...")
                enriched_leads = await rank_leads(enriched_leads, BASE_ICP_CONFIG, use_llm=True)
                print(f"✅ Returning top {min(num_leads, len(enriched_leads))} ranked leads")
            else:
                print("⚠️ No complete leads after enrichment - try different ICP or increase raw lead count")

            return enriched_leads[:num_leads]

        except Exception as e:
            print(f"❌ Lead Sorcerer error: {e}")
            import traceback
            traceback.print_exc()
            return []


# Fallback function if dependencies are not available
if not deps_ok:

    async def get_leads(num_leads: int,
                        industry: str = None,
                        region: str = None) -> List[Dict[str, Any]]:
        """Fallback function when dependencies are missing."""
        print(
            "⚠️ Lead Sorcerer dependencies not available, returning empty results"
        )
        return []


# For backward compatibility and testing
if __name__ == "__main__":
    # Test the function
    import time

    async def test_async():
        start_time = time.time()

        print("🧪 Testing Lead Sorcerer integration...")
        test_leads = await get_leads(2, "Technology")

        print(
            f"⏱️ Generated {len(test_leads)} leads in {time.time() - start_time:.2f}s"
        )

        for i, lead in enumerate(test_leads, 1):
            print(f"\n{i}. {lead.get('business', 'Unknown')}")
            print(
                f"   Contact: {lead.get('full_name', 'Unknown')} ({lead.get('email', 'No email')})"
            )
            print(f"   Industry: {lead.get('industry', 'Unknown')}")
            print(f"   Website: {lead.get('website', 'No website')}")

    asyncio.run(test_async())