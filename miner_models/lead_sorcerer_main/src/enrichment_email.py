"""
Email discovery and verification helpers.
"""

import os
import re
from typing import Dict, List, Optional, Any

from miner_models.lead_sorcerer_main.src.enrichment_validation import (
    BLOCKED_EMAIL_PREFIXES,
    validate_email_name_match,
)

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
    import asyncio

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


def _filter_email_candidates(
    emails: List[str],
    first_name: str,
    last_name: str,
) -> List[str]:
    """
    Remove generic emails and those that don't match the lead's name.
    """
    if not emails:
        return []

    email_pattern = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    seen = set()
    filtered: List[str] = []

    for email in emails:
        email_lower = email.lower().strip()
        if not email_pattern.match(email_lower):
            continue

        local_part = email_lower.split('@')[0]
        if any(
            local_part == prefix or local_part.startswith(prefix + '.') or local_part.startswith(prefix + '_')
            for prefix in BLOCKED_EMAIL_PREFIXES
        ):
            continue

        if first_name and last_name:
            valid_match, _ = validate_email_name_match(email_lower, first_name, last_name)
            if not valid_match:
                continue

        if email_lower not in seen:
            seen.add(email_lower)
            filtered.append(email_lower)

    return filtered


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
    from miner_models.lead_sorcerer_main.src.enrichment_search import _gse_google_search

    full_name = f"{first_name} {last_name}"
    email_pattern = re.compile(rf'[a-zA-Z0-9._%+-]+@{re.escape(domain)}', re.IGNORECASE)

    # Search queries to try
    queries = [
        f'"{full_name}" "@{domain}" email',
        f'"{full_name}" site:{domain} email',
        f'"{full_name}" "{domain}" contact',
        f'"{full_name}" "{domain}" "@{domain}"',
    ]

    for query in queries:
        try:
            results = await _gse_google_search(query, num_results=5)

            for item in results:
                snippet = item.get('snippet', '') + ' ' + item.get('title', '')

                # Look for email in snippet
                emails_found = email_pattern.findall(snippet)

                for email in emails_found:
                    email = email.lower()
                    local = email.split('@')[0]
                    if any(
                        local == prefix or local.startswith(prefix + '.') or local.startswith(prefix + '_')
                        for prefix in BLOCKED_EMAIL_PREFIXES
                    ):
                        continue

                    valid_match, _ = validate_email_name_match(email, first_name, last_name)
                    if valid_match:
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

    return _filter_email_candidates(email_candidates, first_name, last_name)


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
    """
    full_name = f"{first_name} {last_name}"

    # === STEP 1: Try scraping emails from website ===
    scraped_emails = await scrape_emails_from_website(domain, target_name=full_name)

    if scraped_emails:
        print(f"   📧 Validating {len(scraped_emails)} scraped email(s) via TrueList...")
        scraped_emails = _filter_email_candidates(scraped_emails, first_name, last_name)
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
    patterns = _filter_email_candidates(patterns, first_name, last_name)

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
            if result.get("passed") is None or result.get("status") in ["unknown", "timeout", "error"]:
                print(f"   ⚠️ Using catch-all fallback: {patterns[0]}")
                return patterns[0]

    # No TrueList key - return first pattern
    if not _get_truelist_api_key():
        print(f"   ⚠️ No TrueList key - using unverified: {patterns[0]}")
        return patterns[0]

    print(f"   ❌ No valid email found for {first_name} {last_name}@{domain}")
    return None
