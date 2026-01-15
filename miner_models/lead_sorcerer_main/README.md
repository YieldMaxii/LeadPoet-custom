# Lead Sorcerer - Enhanced Pipeline

A comprehensive lead generation system for Bittensor Subnet 71 (LeadPoet).

## What's New (v2.0)

This version introduces significant improvements over the previous website-first approach.

### Major Changes

| Feature | Previous Version | New Version |
|---------|-----------------|-------------|
| **Primary Data Source** | Company websites | LinkedIn profiles via GSE |
| **Query Generation** | Hardcoded queries | Dynamic LLM generation |
| **Email Verification** | None | TrueList batch verification |
| **Missing Data Handling** | Validation failures | Firecrawl fallback + secondary GSE |
| **Pipeline Mode** | `website_first` only | `linkedin_first` (recommended) |

---

## New Features

### 1. LinkedIn-First Pipeline

**Why:** Company websites rarely list executive email addresses. LinkedIn profiles contain decision maker information more reliably.

**How it works:**
```
1. Generate LinkedIn search queries via LLM
2. Search Google for LinkedIn profiles (site:linkedin.com/in/)
3. Extract name, title, company from results
4. Find company domain via secondary GSE search
5. Enrich with company data (GSE → Firecrawl fallback)
6. Generate email patterns and verify via TrueList
```

**Enable in `icp_config.json`:**
```json
{
  "pipeline_mode": "linkedin_first"
}
```

### 2. Dynamic LLM Query Generation

Instead of maintaining hardcoded search queries, the pipeline now uses OpenRouter (Claude Haiku) to generate optimized LinkedIn search queries from your ICP description.

**Benefits:**
- Adapts to any ICP without manual query writing
- Generates varied queries to maximize coverage
- Includes industry-specific terminology automatically
- No more maintaining long query lists

**Example transformation:**
```
ICP Text: "Small and medium-sized US manufacturing companies (10-500 employees)
          including metal fabrication, machining, plastics..."

Generated Queries:
- site:linkedin.com/in/ "owner" "metal fabrication" USA
- site:linkedin.com/in/ "president" "CNC machining" company
- site:linkedin.com/in/ "CEO" "plastic injection molding"
- ... (25 unique queries)
```

### 3. TrueList Email Verification

Pre-submission email verification to reduce rejection rate.

**Flow:**
1. Submit up to 50 emails per batch
2. Poll for results (max 60 seconds)
3. Check deliverability status

**Pass statuses:** `deliverable`, `accept_all`
**Fail statuses:** `undeliverable`, `unknown`, `spamtrap`, `disposable`

### 4. Firecrawl Fallback

When GSE returns incomplete company data, automatically falls back to Firecrawl extraction.

**Extracted fields:**
- Company name, description, industry
- Location, employee count
- Contact information from website

### 5. Secondary Employee Count Search

Dedicated search specifically for company size when initial enrichment fails.

**Queries tried:**
1. `"{company}" employees company size`
2. `"{company}" number of employees`
3. `"{company}" linkedin company`

### 6. Pre-Submission Validation

All leads are validated before submission:
- Schema validation (all required fields present)
- Email format validation
- Role/title verification
- Company data completeness

---

## Bug Fixes

| Issue | Cause | Fix |
|-------|-------|-----|
| `role_contains_name: 'not' found in role` | Short name parts matching common words | Min length 4 chars + word skip list |
| TrueList "batch submit failed: 200" | Wrong status code check | Accept 200 and 201 |
| TrueList batch_id not found | Wrong field name | Use `id` field |
| Firecrawl 400 BAD_REQUEST | Wrong API format | Updated to v1 format |
| TRUELIST_API_KEY not found | Module-level import | Lazy loading at runtime |

---

## Configuration

### Environment Variables (`.env`)

```bash
# Required
GSE_API_KEY=your_google_api_key
GSE_CX=your_custom_search_engine_id
OPENROUTER_KEY=your_openrouter_key
FIRECRAWL_KEY=your_firecrawl_key
TRUELIST_API_KEY=your_truelist_key

# Optional
SCRAPINGDOG_API_KEY=your_scrapingdog_key
```

### ICP Config (`icp_config.json`)

```json
{
  "name": "US Manufacturing SMBs - Decision Makers",
  "icp_text": "Description for LLM query generation",
  "pipeline_mode": "linkedin_first",
  "queries": ["fallback queries if LLM fails"],
  "validation_config": {
    "industry_keywords": ["manufacturing", "fabrication", ...],
    "company_name_keywords": ["inc", "llc", "corp", ...]
  },
  "role_priority": {
    "owner": 1,
    "president": 1,
    "ceo": 1,
    "general manager": 2,
    ...
  }
}
```

**Pipeline Modes:**
- `linkedin_first` - Search LinkedIn profiles directly (recommended)
- `website_first` - Traditional company website crawling (fallback)

---

## Required Lead Fields

All fields must contain verified data (no defaults):

**Company:**
- `name` - Company name
- `industry` - Primary industry
- `sub_industry` - Specific sub-industry
- `hq_location` - Headquarters location
- `employee_count` - Company size
- `revenue_range` - Revenue bracket
- `company_type` - Business type
- `ownership_type` - Private/public

**Contact:**
- `full_name` - Contact's full name
- `email` - Verified email address
- `job_title` - Current role
- `phone` - Phone number (if available)
- `linkedin_url` - LinkedIn profile URL
- `decision_maker` - Boolean flag
- `seniority` - Seniority level

---

## Running the Pipeline

```bash
cd /root/LeadPoet-custom
python miner_models/lead_sorcerer_main/main_leads.py
```

### Expected Output

```
=== Lead Sorcerer Pipeline ===
Mode: linkedin_first

Generating LinkedIn queries via LLM...
Generated 25 queries for ICP: US Manufacturing SMBs

Searching LinkedIn profiles...
Found 47 potential decision makers

Enriching company data...
[████████████████████] 47/47

Verifying emails via TrueList...
TrueList batch submitted: abc-123-def
Batch completed: 38 deliverable, 9 failed

Pre-submission validation...
Passed: 35 leads
Failed: 12 leads (missing fields)

=== Results ===
Leads ready for submission: 35
```

---

## Cost Estimates

Per `config/costs.yaml`:

| Provider | Unit | Cost |
|----------|------|------|
| GSE | request | $0.005 |
| OpenRouter | 1k tokens | $0.002 |
| ScrapingDog | request | $0.001 |
| Firecrawl | extract | $0.001 |

**Caps (from `icp_config.json`):**
- Per company: $0.03
- Per contact: $0.04
- Per run max: $15.00

---

## Architecture

```
main_leads.py                    # Pipeline orchestration
├── get_leads()                  # Entry point, detects pipeline mode
├── run_linkedin_first_pipeline()# LinkedIn-first flow
└── run_website_first_pipeline() # Traditional flow (fallback)

src/enrichment.py                # Core functions
├── generate_linkedin_queries_with_llm()  # LLM query generation
├── search_linkedin_decision_makers()     # LinkedIn GSE search
├── find_company_domain()                 # Domain lookup
├── firecrawl_extract_company()           # Firecrawl fallback
├── truelist_verify_batch()               # Email verification
└── _search_company_employee_count()      # Secondary size search
```

---

## Reverting to Previous Version (v1.0 Website-First)

If the LinkedIn-first pipeline doesn't work well, you can revert to the previous website-first approach.

### Previous Version Behavior

The v1.0 pipeline worked as follows:

```
1. Execute hardcoded queries from icp_config.json (excluding LinkedIn)
2. Crawl company websites directly
3. Extract contact info from About/Contact pages
4. Submit leads without TrueList verification
```

**Sample v1.0 output (from logs):**
```
=== Lead Sorcerer Main ===
ICP: US Manufacturing SMBs - Decision Makers (mode=fast, threshold=0.5)
🔍 Query 1/25: manufacturing company owner contact us USA -site:linkedin.com
   GSE returned 10 results
🔍 Query 2/25: metal fabrication shop owner email -site:linkedin.com
   GSE returned 8 results
...
📊 Domain scoring complete: 45 domains above threshold
🌐 Crawling 45 domains...
   ✓ precisionmfg.com - Found 2 contacts
   ✓ acmefabrication.com - Found 1 contact
   ✗ industrialparts.com - No contacts found
...
=== Results ===
Total leads generated: 23
```

### How to Revert

#### Step 1: Change `icp_config.json`

Change `pipeline_mode` from `"linkedin_first"` to `"website_first"`:

```json
{
  "pipeline_mode": "website_first"
}
```

That's the only config change needed - the `get_leads()` function will detect this and use the old pipeline.

#### Step 2: Verify the queries exclude LinkedIn

The `queries` array should have `-site:linkedin.com` exclusions (already present):

```json
"queries": [
  "manufacturing company owner contact us USA -site:linkedin.com -site:indeed.com -site:glassdoor.com",
  "metal fabrication shop owner email -site:linkedin.com -site:indeed.com -site:glassdoor.com",
  ...
]
```

### What Each Pipeline Mode Does

#### `website_first` (v1.0 - Previous)

```python
# In main_leads.py - run_website_first_pipeline()

1. Load queries from icp_config.json
2. For each query:
   - Search GSE (Google Custom Search)
   - Extract company domains from results
3. Score domains against ICP
4. Crawl company websites for contact info
5. Return leads (no TrueList verification)
```

**Pros:**
- Direct company website data
- No LinkedIn rate limits
- Simpler pipeline

**Cons:**
- Most company sites don't list executive emails
- Higher rejection rate at submission
- No email verification

#### `linkedin_first` (v2.0 - Current)

```python
# In main_leads.py - run_linkedin_first_pipeline()

1. Generate queries via LLM (or use linkedin_queries if defined)
2. Search GSE for LinkedIn profiles
3. Extract name, title, company from LinkedIn results
4. Find company domain via secondary search
5. Enrich with company data (GSE → Firecrawl fallback)
6. Generate email patterns
7. Verify emails via TrueList
8. Run pre-submission validation
9. Return validated leads
```

**Pros:**
- Better decision maker data from LinkedIn
- Email verification reduces rejections
- Fallback enrichment for missing data

**Cons:**
- More API calls (higher cost)
- Dependent on LinkedIn data in GSE results
- More complex pipeline

---

## Code Changes Summary

### Files Modified

#### 1. `main_leads.py`

**Added:**
- `run_linkedin_first_pipeline()` - New LinkedIn-first flow
- Pipeline mode detection in `get_leads()`
- Explicit `.env` path loading

**Changed:**
- `get_leads()` now checks `config.get("pipeline_mode")` to route to correct pipeline

**Location of key changes:**
```python
# Line ~50: .env loading
from pathlib import Path
_env_locations = [
    Path(__file__).parent.parent.parent / ".env",
    Path(__file__).parent / ".env",
    Path.cwd() / ".env",
]

# Line ~200: Pipeline routing
async def get_leads(num_leads: int, config: Dict) -> List[Dict]:
    pipeline_mode = config.get("pipeline_mode", "website_first")
    if pipeline_mode == "linkedin_first":
        return await run_linkedin_first_pipeline(num_leads, config)
    else:
        return await run_website_first_pipeline(num_leads, config)
```

#### 2. `src/enrichment.py`

**Added functions:**
| Function | Purpose | Line ~approx |
|----------|---------|--------------|
| `search_linkedin_decision_makers()` | Search LinkedIn via GSE | ~400 |
| `find_company_domain()` | Find domain from company name | ~500 |
| `generate_linkedin_queries_with_llm()` | LLM query generation | ~600 |
| `firecrawl_extract_company()` | Firecrawl fallback extraction | ~1280 |
| `truelist_verify_batch()` | TrueList email verification | ~1480 |
| `_search_company_employee_count()` | Secondary employee count search | ~800 |
| `_get_truelist_api_key()` | Lazy API key loading | ~1450 |

**Changed:**
| Change | Before | After |
|--------|--------|-------|
| Role validation min length | 3 chars | 4 chars |
| Role validation skip words | Role words only | + common English words |
| TrueList status check | `!= 201` | `not in (200, 201)` |
| TrueList batch ID field | `batch_id` | `id` or `batch_id` |
| Firecrawl payload format | Old format | v1 API format |

#### 3. `icp_config.json`

**Added:**
```json
"pipeline_mode": "linkedin_first"
```

**Unchanged:**
- `queries` array (used as fallback)
- All validation config
- All role priority settings

---

## Full Revert Checklist

If you need to completely revert to v1.0:

- [ ] Change `icp_config.json`: `"pipeline_mode": "website_first"`
- [ ] (Optional) Remove TrueList verification by setting `TRUELIST_API_KEY=""` in `.env`
- [ ] (Optional) Remove Firecrawl fallback by setting `FIRECRAWL_KEY=""` in `.env`

**Note:** The new functions in `enrichment.py` won't be called when `pipeline_mode` is `"website_first"`, so you don't need to remove them.

---

## Git Revert (Nuclear Option)

If you need to completely revert all code changes:

```bash
# See what files changed
git status

# Revert specific files to last commit
git checkout HEAD -- miner_models/lead_sorcerer_main/main_leads.py
git checkout HEAD -- miner_models/lead_sorcerer_main/src/enrichment.py
git checkout HEAD -- miner_models/lead_sorcerer_main/icp_config.json

# Or revert everything (WARNING: loses all changes)
git checkout HEAD -- .
```

---

## Troubleshooting

**"TRUELIST_API_KEY not set"**
- Ensure `.env` file is in the correct location
- Check that the key is not empty

**"Firecrawl error 400"**
- Verify FIRECRAWL_KEY is valid
- Check Firecrawl account has credits

**"No LinkedIn profiles found"**
- GSE quota may be exhausted
- Try different ICP text for query generation

**"Missing employee_count"**
- Secondary search failed
- Consider broader search terms in ICP

**Want to use old pipeline?**
- Change `"pipeline_mode": "website_first"` in `icp_config.json`
