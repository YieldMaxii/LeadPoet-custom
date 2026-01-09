"""
Lead Auditor - Pre-submission validation for LeadPoet miners
============================================================

Validates leads against all gateway and validator checks before submission.
Catches issues that would cause instant rejection or consensus failure.

Usage:
    # Audit a single lead
    python -m miner_models.lead_auditor lead.json

    # Audit with live duplicate checking (default)
    python -m miner_models.lead_auditor lead.json --mode online

    # Audit without network (offline mode)
    python -m miner_models.lead_auditor lead.json --mode offline

    # Sync duplicate cache for offline use
    python -m miner_models.lead_auditor --sync-cache

    # Output to file
    python -m miner_models.lead_auditor lead.json -o results.json
"""

import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Set, Tuple
import os


# ============================================================
# CONSTANTS - Role Validation Patterns
# ============================================================

ROLE_TYPOS = {
    "manager": ["manger", "maneger", "managr", "mangaer", "manaager", "managar", "manageer", "maanger", "mamager", "mnager", "mangager", "mananger", "managaer", "managre", "maager", "managerr", "mananager", "manater", "maneger"],
    "management": ["managment", "managemnt", "manegement", "managament", "mangement", "managenment", "managerment", "managemnet", "managemetn"],
    "director": ["directer", "directur", "direcor", "directot", "drector", "direktor", "diector", "directior", "directr", "direcctor", "dirctor", "directore", "derector", "directoer", "direcotr", "directoor"],
    "engineer": ["enginer", "enginear", "enigneer", "engineeer", "enginner", "enginere", "engeneer", "engnieer", "engieer", "engieneer", "engineerr", "enginee", "enegineer", "engineir", "engenear", "engineear", "engienear", "engineerng"],
    "engineering": ["enginnering", "enginering", "engeneering", "enginneering", "engineerig", "engineerng", "engieneering", "engineerring"],
    "developer": ["develper", "developper", "devloper", "develoer", "develeoper", "devoloper", "developor", "develope", "develoepr", "developr", "devleoper", "develpoer", "develloper", "developeer"],
    "development": ["developement", "devlopment", "develoment", "developmnet", "develepoment", "developmentt", "developmnt", "develepment", "developmet"],
    "analyst": ["analist", "analys", "analysit", "anlyst", "analyist", "annalyst", "analayst", "analst", "analyyst", "analyet", "analyts", "anaylst"],
    "consultant": ["consulant", "consultent", "consutant", "consaltant", "consultat", "consultnat", "consulent", "consultannt", "consultanat", "consulatnt"],
    "specialist": ["specalist", "specilist", "specialst", "spcialist", "specailist", "specialits", "speicalist", "specialsit", "speciliast"],
    "coordinator": ["cordinator", "coodinator", "coordiantor", "coordinater", "cooridnator", "coordnator", "corrdinator", "coordiator", "coordintor", "cordinater"],
    "executive": ["excutive", "exective", "executiv", "executve", "exucutive", "exectuive", "executivee", "exectutive", "executibe", "execuitve", "executuve"],
    "assistant": ["assitant", "asistant", "assistnat", "assisant", "assitent", "asisstant", "assistent", "assistatn", "assisatnt", "assisstant"],
    "president": ["presidant", "presedent", "presiden", "presidnet", "presindent", "presient", "presidnt", "presidenet"],
    "senior": ["senoir", "seinor", "snior", "senioe", "seior", "senor", "senioor"],
    "junior": ["junoir", "junioe", "jnior", "junor", "juior"],
    "architect": ["architec", "arcitect", "architecht", "architech", "artchitect", "architct", "archtiect", "architectt", "archirect"],
    "accountant": ["accountent", "acountant", "accountan", "acocuntant", "accoutant", "accountnat", "accounant", "accountat", "accontant"],
    "administrator": ["administator", "adminstrator", "adminisrator", "adminsitrator", "adminstator", "administrater", "adminitrator", "administraor"],
    "representative": ["representive", "represenative", "representaive", "represetative", "representatie", "representativee", "representatve", "represntative"],
    "supervisor": ["superviser", "supervior", "supervsior", "suprevisor", "supervisior", "supervisr", "superivsor", "superviosr", "supervisoor"],
    "marketing": ["markting", "marketng", "makreting", "markeeting", "marketign", "marekting", "martketing", "marketinng", "marketin"],
    "operations": ["opertions", "operatons", "oprations", "operatiosn", "opeartions", "operatins", "operaions", "opertaions"],
    "finance": ["finace", "finanace", "financ", "fianance", "finacne", "fiannce"],
    "financial": ["finacial", "financal", "finanical", "financila", "finanaical"],
    "associate": ["assocaite", "asociate", "assoicate", "assocate", "associte", "associat", "asscoiate", "associae"],
    "founder": ["foundr", "foundre", "founer", "foudner", "foundeer", "founde"],
    "officer": ["oficer", "offcer", "officr", "offcier", "offier", "officeer"],
    "partner": ["partener", "parter", "partnr", "partenr", "partne", "partneer"],
    "technician": ["techician", "techncian", "technicain", "technican", "technicina"],
    "technical": ["techincal", "tehcnical", "tecnical", "techical", "technicla"],
    "technology": ["technolgy", "techonology", "technoloy", "techology", "technologyy"],
    "recruiter": ["recuiter", "recruter", "recruitor", "recrutier", "recruitr"],
    "attorney": ["attoney", "attorny", "atorney", "attornet", "attornney", "attoreny"],
    "designer": ["desinger", "desginer", "designr", "deisgner", "designeer", "desiner"],
    "strategist": ["strategis", "startegist", "stratagist", "stategist", "strategsit"],
    "business": ["busines", "buisness", "bussiness", "busniess", "buisines", "bussines", "businss", "busienss", "busniness", "bsuiness"],
    "customer": ["custmer", "cutomer", "custormer", "cusotmer", "customr", "custommer"],
    "product": ["prodcut", "porduct", "prduct", "proudct", "produc", "producct"],
    "project": ["porject", "proejct", "projec", "prject", "proect", "projct"],
    "software": ["sofware", "sotware", "softwre", "softare", "sofwtare", "softwear"],
    "professional": ["proffesional", "profesional", "proffessional", "professinal", "professionl", "professonal", "profssional", "professsional"],
    "corporate": ["corproate", "coporate", "corporat", "corpoarte", "coroprate"],
    "support": ["suport", "supprot", "supprt", "suppoort", "suppport"],
    "service": ["servcie", "serivce", "sevice", "servic", "servcice"],
    "security": ["secuirty", "securtiy", "secutiry", "securty", "secuiryt"],
    "resource": ["resoruce", "resourc", "resrouce", "reource"],
    "principal": ["princpal", "prinicpal", "pricipal", "principla", "prnicipal"],
    "commercial": ["commerical", "comercial", "commericial", "commercail"],
    "regional": ["reginal", "regioanl", "regioal", "regionl", "reagional"],
    "national": ["natioal", "nationl", "natoinal", "nationla", "naitonal"],
    "international": ["internatioanl", "internatinal", "interantional", "internationl"],
    "general": ["genral", "generl", "genaral", "generla", "genearl"],
    "compliance": ["complience", "compliace", "compliane", "complicance"],
    "procurement": ["procurment", "procuremnt", "procuremennt", "procurrement"],
    "acquisition": ["aquisition", "acquistion", "acquisiton", "acqusition"],
    "logistics": ["logisitics", "logisitcs", "logstics", "logistcs"],
    "manufacturing": ["manufacuring", "manufaturing", "manufacturng", "manufactruing"],
    "pharmaceutical": ["pharmeceutical", "pharamceutical", "pharmaceuitcal"],
    "healthcare": ["heathcare", "helathcare", "healtcare", "healthcre"],
    "education": ["educaton", "educaiton", "educaion", "eductaion"],
    "government": ["goverment", "governmnet", "govenrment", "govermnent"],
    "investment": ["investmnet", "investement", "investemnt", "invesment"],
    "insurance": ["insurace", "insurnace", "insurence", "insurancce"],
    "leadership": ["leaderhsip", "leadershi", "leadershp", "leadeership"],
    "entrepreneur": ["entrepeneur", "entreprener", "entreprenuer", "entreprneur", "enterprenuer", "entreperneur", "entreprenur"],
    "certified": ["certifed", "certiifed", "cetified", "cretified", "certifeid"],
}

ROLE_PLACEHOLDERS = [
    "asdfgh", "qwerty", "zxcvbn", "asdf", "qwer", "zxcv",
    "aaaaaa", "bbbbbb", "test", "testing", "xxx", "yyy", "zzz",
    "null", "undefined", "none", "n/a", "na", "tbd", "tba",
    "placeholder", "temp", "todo", "fixme", "abc", "xyz"
]

ROLE_SCAM_PATTERNS = [
    "work from home", "work at home", "make money", "earn money",
    "passive income", "get rich", "easy money", "be your own boss",
    "mlm", "multi level marketing", "network marketing",
    "crypto trader", "forex trader", "bitcoin trader", "day trader",
    "investment opportunity", "financial freedom", "side hustle",
    "join my team", "dm me", "click link", "link in bio",
    "work online", "online business", "home based"
]

ROLE_URL_TLDS = [
    "com", "org", "io", "ai", "co", "dev", "app", "xyz", "me", "ly", "gg",
    "edu", "gov", "info", "biz", "tech", "cloud", "online", "site", "store",
    "us", "uk", "ca", "de", "fr", "in", "au", "nl", "es", "it", "br", "jp", "kr", "cn", "ru",
    "tv", "fm", "games", "life", "work", "realty", "agency", "digital", "media",
    "world", "global", "solutions", "services", "consulting", "network", "systems",
    "ventures", "capital", "finance", "money", "company", "group", "team", "studio",
    "design", "marketing", "software", "tools", "health", "healthcare", "legal", "law",
    "news", "blog", "space", "zone", "link", "click", "today", "one", "pro", "expert",
    "careers", "jobs"
]

ROLE_JOB_KEYWORDS = [
    "manager", "director", "engineer", "developer", "analyst", "consultant",
    "specialist", "coordinator", "assistant", "executive", "officer", "lead",
    "head", "chief", "president", "vp", "vice", "senior", "junior", "associate",
    "partner", "founder", "owner", "ceo", "cto", "cfo", "coo", "cmo", "cio",
    "sales", "marketing", "operations", "admin", "supervisor", "representative",
    "architect", "designer", "writer", "editor", "producer", "teacher", "professor",
    "coach", "trainer", "nurse", "doctor", "attorney", "lawyer", "accountant",
    "advisor", "adviser", "strategist", "planner", "recruiter", "broker"
]


# ============================================================
# CONSTANTS - Email Validation
# ============================================================

BLOCKED_EMAIL_PREFIXES = [
    'info@', 'hello@', 'owner@', 'ceo@', 'founder@', 'contact@', 'support@',
    'team@', 'admin@', 'office@', 'mail@', 'connect@', 'help@', 'hi@',
    'welcome@', 'inquiries@', 'general@', 'feedback@', 'ask@', 'outreach@',
    'communications@', 'crew@', 'staff@', 'community@', 'reachus@', 'talk@', 'service@'
]

FREE_EMAIL_DOMAINS = {
    'gmail.com', 'googlemail.com', 'yahoo.com', 'yahoo.co.uk', 'yahoo.fr',
    'outlook.com', 'hotmail.com', 'live.com', 'msn.com', 'aol.com', 'mail.com',
    'protonmail.com', 'proton.me', 'icloud.com', 'me.com', 'mac.com',
    'zoho.com', 'yandex.com', 'gmx.com', 'mail.ru'
}


# ============================================================
# CONSTANTS - Employee Count & Location
# ============================================================

VALID_EMPLOYEE_COUNTS = [
    "0-1", "2-10", "11-50", "51-200", "201-500",
    "501-1,000", "1,001-5,000", "5,001-10,000", "10,001+"
]

REQUIRED_FIELDS = [
    "business", "full_name", "first", "last", "email", "role",
    "website", "industry", "sub_industry", "country", "city",
    "linkedin", "company_linkedin", "source_url", "description", "employee_count"
]

# ============================================================
# CONSTANTS - Source Provenance (Validator Stage 0.5)
# ============================================================

VALID_SOURCE_TYPES = [
    "public_registry", "company_site", "first_party_form",
    "licensed_resale", "proprietary_database"
]

RESTRICTED_SOURCES = [
    "zoominfo.com", "apollo.io", "people-data-labs.com", "peopledatalabs.com",
    "rocketreach.co", "hunter.io", "snov.io", "lusha.com", "clearbit.com",
    "leadiq.com", "seamless.ai", "cognism.com", "uplead.com", "lead411.com"
]

# ============================================================
# CONSTANTS - Terms Attestation Fields (Validator Stage -1)
# ============================================================

ATTESTATION_FIELDS = [
    "wallet_ss58", "terms_version_hash", "lawful_collection",
    "no_restricted_sources", "license_granted"
]

# ============================================================
# CONSTANTS - Role Anti-Gaming Patterns
# ============================================================

ROLE_GEOGRAPHIC_ENDINGS = [
    "-vietnam", "-cambodia", "-apac", "-emea", "-latam", "-amer",
    "-asia", "-europe", "-africa", "-india", "-china", "-japan",
    "- vietnam", "- cambodia", "- apac", "- emea", "- latam"
]

ROLE_CSUITE_TITLES = ["ceo", "cfo", "cto", "coo", "cmo", "cio", "cso", "cro", "cco", "cpo"]

# ============================================================
# CONSTANTS - Location Anti-Gaming Patterns
# ============================================================

LOCATION_GARBAGE_PATTERNS = [
    # Business terms
    "software", "technology", "solutions", "services", "consulting",
    "marketing", "sales", "engineering", "development", "management",
    # URL patterns
    "http://", "https://", "www.", ".com", ".org", ".io",
    # Department names
    "department", "division", "team", "group", "unit",
    # Street address indicators
    "street", "avenue", "boulevard", "road", "suite", "floor",
    # Generic placeholders
    "n/a", "none", "null", "undefined", "test", "asdf"
]

LOCATION_MAX_LENGTH = 50

US_COUNTRY_ALIASES = ["united states", "usa", "us", "america", "u.s.", "u.s.a."]

# Major hub cities by country (canonical names)
MAJOR_HUBS_BY_COUNTRY = {
    "united states": {
        "new york city", "manhattan", "brooklyn", "san francisco", "los angeles",
        "san diego", "san jose", "seattle", "portland", "austin", "dallas", "houston",
        "chicago", "boston", "denver", "miami", "washington", "atlanta", "phoenix",
    },
    "canada": {"toronto", "vancouver", "montréal", "montreal"},
    "united kingdom": {"london", "manchester", "edinburgh", "cambridge", "oxford"},
    "germany": {"berlin", "münchen", "munich", "frankfurt am main", "frankfurt", "hamburg"},
    "france": {"paris"},
    "netherlands": {"amsterdam", "rotterdam"},
    "switzerland": {"zürich", "zurich", "genève", "geneva"},
    "ireland": {"dublin"},
    "sweden": {"stockholm"},
    "spain": {"barcelona", "madrid"},
    "hong kong": {"hong kong"},
    "singapore": {"singapore"},
    "japan": {"tokyo", "osaka"},
    "south korea": {"seoul"},
    "china": {"shanghai", "beijing", "shenzhen"},
    "india": {"bengaluru", "bangalore", "mumbai", "new delhi", "hyderabad", "pune"},
    "australia": {"sydney", "melbourne"},
    "new zealand": {"auckland"},
    "israel": {"tel aviv"},
    "united arab emirates": {"dubai", "abu dhabi"},
    "brazil": {"são paulo", "sao paulo"},
}


# ============================================================
# CONSTANTS - ICP Definitions
# ============================================================

ICP_DEFINITIONS = [
    {
        "name": "Fuel/Energy Operations",
        "sub_industries": ["fuel", "oil and gas", "fossil fuels", "energy"],
        "roles": ["coo", "chief operating officer", "director of operations", "vp of operations",
                  "cto", "chief technology officer", "vp of engineering", "cio", "chief information officer"]
    },
    {
        "name": "Agriculture/Farming",
        "sub_industries": ["agriculture", "farming", "agtech", "livestock", "aquaculture"],
        "roles": ["coo", "chief operating officer", "vp of operations", "cto", "chief technology officer",
                  "vp of engineering", "cio", "chief information officer"]
    },
    {
        "name": "Renewable Energy",
        "sub_industries": ["solar", "wind energy", "renewable energy", "clean energy",
                          "biomass energy", "energy storage", "energy efficiency"],
        "roles": ["coo", "vp of operations", "cto", "vp of engineering", "cio",
                  "asset manager", "site manager", "plant manager", "facility manager"]
    },
    {
        "name": "Winery/Horticulture",
        "sub_industries": ["winery", "wine and spirits", "horticulture", "farming", "agriculture", "hydroponics"],
        "roles": ["coo", "vp of operations", "cto", "vp of engineering", "cio",
                  "farm manager", "vineyard manager", "head grower", "production manager"]
    },
    {
        "name": "E-Commerce/Retail Marketing",
        "sub_industries": ["e-commerce", "e-commerce platforms", "retail", "retail technology"],
        "roles": ["vp ecommerce", "vp e-commerce", "director of ecommerce", "head of growth",
                  "vp of growth", "chief growth officer", "cmo", "chief marketing officer",
                  "vp of marketing", "founder", "co-founder", "ceo"]
    },
    {
        "name": "Digital Marketing/Advertising",
        "sub_industries": ["digital marketing", "email marketing", "marketing",
                          "marketing automation", "advertising", "advertising platforms",
                          "affiliate marketing", "content marketing"],
        "roles": ["founder", "co-founder", "ceo", "director of partnerships", "vp of partnerships",
                  "head of strategy", "chief strategy officer", "cmo", "managing director", "president"]
    },
    {
        "name": "AI/ML Technical",
        "sub_industries": ["artificial intelligence", "machine learning",
                          "natural language processing", "predictive analytics"],
        "roles": ["ceo", "founder", "co-founder", "cto", "vp of engineering", "head of engineering",
                  "vp of ai", "head of ai", "director of ai", "vp of machine learning",
                  "chief ai officer", "chief data officer", "software engineer"]
    },
    {
        "name": "Real Estate Investment",
        "sub_industries": ["real estate", "real estate investment", "residential",
                          "commercial real estate", "property development", "property management"],
        "roles": ["ceo", "owner", "co-owner", "sole operator", "founder", "co-founder",
                  "managing partner", "managing director", "principal", "president", "partner"]
    },
    {
        "name": "Wealth Management/Family Office",
        "sub_industries": ["asset management", "venture capital", "hedge funds",
                          "financial services", "impact investing"],
        "roles": ["ceo", "president", "managing director", "managing partner", "principal", "partner",
                  "founder", "cio", "chief investment officer", "portfolio manager", "wealth manager",
                  "coo", "cfo", "family office manager"]
    },
    {
        "name": "FinTech/Banking Risk & Compliance",
        "sub_industries": ["fintech", "banking", "payments", "financial services",
                          "credit cards", "mobile payments", "transaction processing"],
        "roles": ["cro", "chief risk officer", "vp of risk", "head of risk", "director of risk",
                  "cco", "chief compliance officer", "vp of compliance", "head of compliance",
                  "compliance officer", "bsa officer", "aml officer", "kyc manager"]
    },
    {
        "name": "Clinical Research/Labs",
        "sub_industries": ["clinical trials", "biotechnology", "pharmaceutical", "biopharma", "life science"],
        "roles": ["data scientist", "data manager", "clinical data manager", "biostatistician",
                  "ceo", "cto", "coo", "cso", "chief scientific officer", "vp of operations"]
    },
    {
        "name": "Research/Academic",
        "sub_industries": ["higher education", "life science", "biotechnology", "neuroscience", "genetics"],
        "roles": ["principal investigator", "lead researcher", "senior researcher", "research director",
                  "professor", "associate professor", "assistant professor", "research scientist",
                  "lab director", "department head"]
    },
    {
        "name": "Biotech/Pharma",
        "sub_industries": ["biotechnology", "biopharma", "pharmaceutical", "genetics", "life science", "bioinformatics"],
        "roles": ["ceo", "founder", "cto", "cso", "chief scientific officer", "coo", "cmo",
                  "vp of business development", "head of business development", "vp of partnerships"]
    },
    {
        "name": "Broadcasting/Media (Africa)",
        "sub_industries": ["broadcasting", "video", "digital media", "content",
                          "content delivery network", "telecommunications", "digital entertainment"],
        "roles": ["cto", "cfo", "head of engineering", "vp of engineering",
                  "head of video", "head of streaming", "head of ott", "director of ott",
                  "head of product", "chief product officer"],
        "regions": ["africa", "nigeria", "south africa", "kenya", "ghana", "egypt", "morocco",
                    "ethiopia", "tanzania", "uganda", "algeria", "sudan", "angola", "cameroon"]
    },
    {
        "name": "Hospitality/Hotels (US)",
        "sub_industries": ["hospitality", "hotel", "resorts", "travel accommodations",
                          "vacation rental", "tourism"],
        "roles": ["business development", "vp of business development", "owner", "co-owner",
                  "founder", "ceo", "president", "general manager", "gm",
                  "operations manager", "director of operations", "hotel manager"],
        "regions": US_COUNTRY_ALIASES
    },
    {
        "name": "Small/Local Businesses (US)",
        "sub_industries": ["local business", "local", "retail", "restaurants", "food and beverage",
                          "professional services", "home services", "real estate", "construction",
                          "automotive", "health care", "fitness", "beauty", "consulting"],
        "roles": ["owner", "co-owner", "business owner", "sole proprietor", "sole operator",
                  "franchise owner", "franchisee", "store owner", "founder", "ceo", "principal", "partner"],
        "regions": US_COUNTRY_ALIASES
    }
]


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class AuditResult:
    passed: bool
    blocking_errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    duplicate_status: Dict = field(default_factory=dict)
    score_preview: Dict = field(default_factory=dict)


# ============================================================
# SECTION 1: REQUIRED FIELD VALIDATION
# ============================================================

def validate_required_fields(lead: dict) -> List[str]:
    """Check all 16 required fields present and non-empty."""
    errors = []
    for field_name in REQUIRED_FIELDS:
        if field_name not in lead or not str(lead[field_name]).strip():
            errors.append(f"missing_field:{field_name}")

    # State required for US leads only
    country = str(lead.get("country", "")).lower()
    if any(alias in country for alias in US_COUNTRY_ALIASES):
        if not lead.get("state") or not str(lead.get("state")).strip():
            errors.append("missing_field:state (required for US leads)")

    return errors


# ============================================================
# SECTION 1b: SOURCE PROVENANCE VALIDATION (Stage 0.5)
# ============================================================

def validate_source_provenance(lead: dict) -> List[str]:
    """Validate source_url, source_type, and restricted sources."""
    errors = []

    # Check source_url is present
    source_url = str(lead.get("source_url", "")).strip()
    if not source_url:
        errors.append("missing_field:source_url")
    else:
        # Check against restricted sources denylist
        source_url_lower = source_url.lower()
        for restricted in RESTRICTED_SOURCES:
            if restricted in source_url_lower:
                errors.append(f"restricted_source:{restricted}")
                break

    # Check source_type is present and valid
    source_type = str(lead.get("source_type", "")).strip()
    if not source_type:
        errors.append("missing_field:source_type")
    elif source_type not in VALID_SOURCE_TYPES:
        errors.append(f"source_type_invalid:{source_type} (valid: {', '.join(VALID_SOURCE_TYPES)})")

    # If source_type is licensed_resale, license_doc_hash is required
    if source_type == "licensed_resale":
        license_hash = str(lead.get("license_doc_hash", "")).strip()
        if not license_hash:
            errors.append("missing_license_doc_hash (required for licensed_resale)")
        elif not re.match(r'^[a-fA-F0-9]{64}$', license_hash):
            errors.append("license_doc_hash_invalid_format (must be 64 hex chars SHA-256)")

    return errors


# ============================================================
# SECTION 1c: TERMS ATTESTATION VALIDATION (Stage -1)
# ============================================================

def validate_attestation(lead: dict) -> List[str]:
    """Validate terms attestation fields."""
    errors = []

    # Check wallet_ss58
    wallet = str(lead.get("wallet_ss58", "")).strip()
    if not wallet:
        errors.append("missing_field:wallet_ss58")

    # Check terms_version_hash
    terms_hash = str(lead.get("terms_version_hash", "")).strip()
    if not terms_hash:
        errors.append("missing_field:terms_version_hash")

    # Check boolean attestations (must be explicitly True)
    bool_fields = ["lawful_collection", "no_restricted_sources", "license_granted"]
    for field_name in bool_fields:
        value = lead.get(field_name)
        if value is not True:
            errors.append(f"attestation_false_or_missing:{field_name}")

    return errors


# ============================================================
# SECTION 1d: LOCATION VALIDATION
# ============================================================

def validate_location(city: str, country: str, region: str = "") -> List[str]:
    """Validate location fields against anti-gaming patterns."""
    errors = []

    for location_value, field_name in [(city, "city"), (country, "country"), (region, "region")]:
        if not location_value:
            continue

        loc = str(location_value).strip()
        loc_lower = loc.lower()

        # Max length check (validator uses 50 chars)
        if len(loc) > LOCATION_MAX_LENGTH:
            errors.append(f"{field_name}_too_long:{len(loc)}/{LOCATION_MAX_LENGTH}")

        # Garbage pattern rejection
        for pattern in LOCATION_GARBAGE_PATTERNS:
            if pattern in loc_lower:
                errors.append(f"{field_name}_garbage_pattern:{pattern}")
                break

        # Duplicate word rejection (e.g., "Modotech Modotech")
        words = loc_lower.split()
        if len(words) >= 2:
            for word in set(words):
                if len(word) > 3 and words.count(word) >= 2:
                    errors.append(f"{field_name}_duplicate_word:{word}")
                    break

        # Starting with articles rejection
        if loc_lower.startswith(("the ", "a ", "an ")):
            errors.append(f"{field_name}_starts_with_article")

    return errors


# ============================================================
# SECTION 2: ROLE VALIDATION (24 checks)
# ============================================================

def validate_role(role: str) -> List[str]:
    """Apply all 24 role validation checks."""
    errors = []
    if not role:
        return ["role_empty"]

    role = str(role).strip()

    # 1. Length checks (validator uses 80 chars max)
    if len(role) < 2:
        errors.append("role_too_short")
    if len(role) > 80:
        errors.append(f"role_too_long:{len(role)}/80")

    # 2. Must contain letters
    if not re.search(r'[a-zA-Z]', role):
        errors.append("role_no_letters")

    # 3. Cannot be mostly numbers
    digits = sum(c.isdigit() for c in role)
    if len(role) > 0 and digits / len(role) > 0.5:
        errors.append("role_mostly_numbers")

    # 4. Placeholder detection
    role_lower = role.lower()
    if role_lower in ROLE_PLACEHOLDERS:
        errors.append("role_placeholder")

    # 5. Repeated characters (4+)
    if re.search(r'(.)\1{3,}', role):
        errors.append("role_repeated_chars")

    # 6. Repeated words (3+)
    words = role_lower.split()
    for word in set(words):
        if words.count(word) >= 3:
            errors.append("role_repeated_words")
            break

    # 7. Scam patterns
    for pattern in ROLE_SCAM_PATTERNS:
        if pattern in role_lower:
            errors.append(f"role_scam_pattern:{pattern}")
            break

    # 8. URL detection
    if re.search(r'https?://', role) or re.search(r'www\.', role):
        errors.append("role_contains_url")

    # 9. Email detection
    if re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', role):
        errors.append("role_contains_email")

    # 10. Phone detection
    if re.search(r'\+?[\d\s\-\(\)]{10,}', role):
        errors.append("role_contains_phone")

    # 11. Non-Latin characters (CJK, Arabic, Thai, Cyrillic, Hebrew)
    if re.search(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af\u0600-\u06ff\u0e00-\u0e7f\u0400-\u04ff\u0590-\u05ff]', role):
        errors.append("role_non_english")

    # 12. TLD detection (.com, .io, etc.)
    for tld in ROLE_URL_TLDS:
        if f'.{tld}' in role_lower:
            errors.append("role_contains_website")
            break

    # 13. Typo detection (whole word matches only)
    words_in_role = set(re.findall(r'\b\w+\b', role_lower))
    for correct, typos in ROLE_TYPOS.items():
        for typo in typos:
            if typo in words_in_role:
                errors.append(f"role_typo:{typo}->{correct}")
                break

    # 14. Min letters
    letters = sum(c.isalpha() for c in role)
    if letters < 3:
        errors.append("role_too_few_letters")

    # 15. Starts with special char
    if role and role[0] in '!@#$%^&*()_+-=[]{}|;:\'",.<>?/\\`~':
        errors.append("role_starts_special_char")

    # 16. Achievement statements
    if re.search(r'^\d+[xX]\s', role) or re.search(r'\$\d+[MmKkBb]?\+?', role):
        errors.append("role_achievement_statement")

    # 17. Incomplete title (ends with "of")
    if re.search(r'\bof\s*$', role_lower):
        errors.append("role_incomplete_title")

    # 18. Company name in role
    if re.search(r'\s(?:at|@)\s+[A-Z]', role) or re.search(r'\b(?:inc\.|llc|ltd\.|corp\.)\b', role_lower):
        errors.append("role_contains_company")

    # 19. Emoji detection
    if re.search(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F900-\U0001F9FF\U0001F1E0-\U0001F1FF\U00002705\U0000274C\U00002714]', role):
        errors.append("role_contains_emoji")

    # 20. Hiring markers
    if re.search(r'\*{2,}hiring\*{2,}', role_lower) or "we're hiring" in role_lower or "now hiring" in role_lower:
        errors.append("role_hiring_marker")

    # 21. Bio description
    if re.search(r'^(?:i am|i\'m|we are|we\'re|helping|passionate|dedicated|committed|driven)\s', role_lower):
        errors.append("role_bio_description")

    # 22. Long roles need job keywords
    if len(role) > 60:
        has_keyword = any(kw in role_lower for kw in ROLE_JOB_KEYWORDS)
        if not has_keyword:
            errors.append("role_no_job_keywords")

    # 23. Gibberish (vowel ratio)
    if letters > 5:
        vowels = sum(c.lower() in 'aeiou' for c in role)
        if vowels / letters < 0.1:
            errors.append("role_gibberish")

    # ============================================================
    # ANTI-GAMING CHECKS (from validator validate_role_format)
    # ============================================================

    # 24. Geographic endings (e.g., "-Vietnam", "-APAC")
    for ending in ROLE_GEOGRAPHIC_ENDINGS:
        if role_lower.endswith(ending):
            errors.append(f"role_geographic_ending:{ending}")
            break

    # 25. Marketing sentences (period followed by capital letter = tagline)
    if re.search(r'\.\s*[A-Z]', role):
        errors.append("role_marketing_sentence")

    # 26. Multiple C-suite titles (e.g., "CEO, CFO")
    csuite_found = [title for title in ROLE_CSUITE_TITLES if title in role_lower]
    if len(csuite_found) > 1:
        errors.append(f"role_multiple_csuite:{','.join(csuite_found)}")

    # 27. Comma-separated role stuffing (3+ role keywords separated by commas)
    if ',' in role:
        parts = [p.strip().lower() for p in role.split(',')]
        role_keyword_count = sum(1 for p in parts if any(kw in p for kw in ROLE_JOB_KEYWORDS))
        if role_keyword_count >= 3:
            errors.append("role_comma_stuffing")

    # 28. Pipe-separated roles (role stuffing variant)
    if '|' in role:
        parts = [p.strip().lower() for p in role.split('|')]
        if len(parts) >= 2 and all(any(kw in p for kw in ROLE_JOB_KEYWORDS) for p in parts if p):
            errors.append("role_pipe_stuffing")

    return errors


# ============================================================
# SECTION 3: DESCRIPTION VALIDATION (13 checks)
# ============================================================

def validate_description(description: str) -> Tuple[List[str], List[str]]:
    """Apply all 13 description validation checks.

    Returns:
        Tuple of (errors, warnings) - short description is now a warning, not error.
    """
    errors = []
    warnings = []
    if not description:
        return ["description_empty"], []

    description = str(description).strip()

    # 1. Length checks - short is warning (validator doesn't hard-fail on this)
    if len(description) < 70:
        warnings.append(f"desc_short:{len(description)}/70 (quality warning)")
    if len(description) > 2000:
        errors.append("desc_too_long")

    # 2. Must contain letters
    if not re.search(r'[a-zA-Z]', description):
        errors.append("desc_no_letters")

    # 3. Min letter count
    letters = sum(c.isalpha() for c in description)
    if letters < 50:
        errors.append(f"desc_too_few_letters:{letters}/50")

    # 4. Truncation detection
    if description.rstrip().endswith('...'):
        errors.append("desc_truncated")

    # 5. LinkedIn follower patterns (multiple languages)
    follower_patterns = [
        r'\d+\s*followers?\s*(?:on\s*)?linkedin',
        r'\d+\s*seguidores',  # Spanish
        r'\d+\s*abonnés',     # French
    ]
    for pattern in follower_patterns:
        if re.search(pattern, description.lower()):
            errors.append("desc_linkedin_followers")
            break

    # 6. Navigation/UI text
    nav_patterns = ['report this company', 'close menu', 'skip to main', 'cookie policy',
                    'accept cookies', 'privacy settings']
    for pattern in nav_patterns:
        if pattern in description.lower():
            errors.append("desc_navigation_text")
            break

    # 7. Garbled Unicode (CJK mixed with Latin in short text)
    has_cjk = bool(re.search(r'[\u4e00-\u9fff]', description))
    has_latin = bool(re.search(r'[a-zA-Z]', description))
    if has_cjk and has_latin and len(description) < 200:
        errors.append("desc_garbled_unicode")

    # 7b. Arabic mixed with English
    has_arabic = bool(re.search(r'[\u0600-\u06ff]', description))
    if has_arabic and has_latin and len(description) < 200:
        errors.append("desc_arabic_mixed")

    # 7c. Thai mixed with English
    has_thai = bool(re.search(r'[\u0e00-\u0e7f]', description))
    if has_thai and has_latin and len(description) < 200:
        errors.append("desc_thai_mixed")

    # 8. Gibberish (vowel ratio)
    if letters > 30:
        vowels = sum(c.lower() in 'aeiou' for c in description)
        if vowels / letters < 0.15:
            errors.append("desc_gibberish")

    # 9. Placeholder text
    placeholders = ['lorem ipsum', 'n/a', 'placeholder', 'test description', 'tbd', 'coming soon']
    for p in placeholders:
        if p in description.lower():
            errors.append("desc_placeholder")
            break

    # 10. Repeated characters (5+)
    if re.search(r'(.)\1{4,}', description):
        errors.append("desc_repeated_chars")

    # 11. Just a URL
    if re.match(r'^https?://\S+$', description.strip()):
        errors.append("desc_just_url")

    # 12. Email takes >30%
    email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', description)
    if email_match and len(email_match.group()) / len(description) > 0.3:
        errors.append("desc_mostly_email")

    # 13. Starts with pipe
    if description.startswith('|'):
        errors.append("desc_formatting_junk")

    return errors, warnings


# ============================================================
# SECTION 4: EMAIL VALIDATION
# ============================================================

# Try to import disposable email domains blocklist
try:
    from disposable_email_domains import blocklist as DISPOSABLE_DOMAINS
except ImportError:
    # Fallback to a basic list if package not installed
    DISPOSABLE_DOMAINS = {
        'tempmail.com', 'temp-mail.org', 'guerrillamail.com', 'mailinator.com',
        'throwaway.email', '10minutemail.com', 'fakeinbox.com', 'trashmail.com',
        'maildrop.cc', 'getnada.com', 'yopmail.com', 'sharklasers.com',
        'dispostable.com', 'mailnesia.com', 'tempail.com', 'tempr.email'
    }


def validate_email(email: str, first: str, last: str) -> List[str]:
    """Validate email format, name match, and blocked patterns."""
    errors = []
    if not email:
        return ["email_empty"]

    email = str(email).strip()
    email_lower = email.lower()

    # RFC-5322 format (ASCII) OR RFC-6531 (Unicode/Internationalized)
    pattern_ascii = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    pattern_unicode = r'^[\w._%+-]+@[\w.-]+\.[a-zA-Z]{2,}$'
    is_valid_ascii = bool(re.match(pattern_ascii, email))
    is_valid_unicode = bool(re.match(pattern_unicode, email, re.UNICODE))

    if not (is_valid_ascii or is_valid_unicode):
        errors.append("email_invalid_format")

    # No + character (prevents aliasing)
    local_part = email.split('@')[0] if '@' in email else email
    if '+' in local_part:
        errors.append("email_plus_alias")

    # Blocked prefixes
    for prefix in BLOCKED_EMAIL_PREFIXES:
        if email_lower.startswith(prefix):
            errors.append(f"email_blocked_prefix:{prefix.rstrip('@')}")
            break

    # Free email domains
    domain = email_lower.split('@')[1] if '@' in email_lower else ''
    if domain in FREE_EMAIL_DOMAINS:
        errors.append(f"email_free_domain:{domain}")

    # Disposable email domains (validator Stage 0)
    if domain in DISPOSABLE_DOMAINS:
        errors.append(f"email_disposable:{domain}")

    # Name-email match (first OR last, min 3 chars)
    local_lower = local_part.lower()
    first_lower = str(first or '').lower().strip()
    last_lower = str(last or '').lower().strip()

    name_found = False
    if len(first_lower) >= 3 and first_lower in local_lower:
        name_found = True
    if len(last_lower) >= 3 and last_lower in local_lower:
        name_found = True

    # Only flag if both names are long enough but neither matches
    if not name_found and len(first_lower) >= 3 and len(last_lower) >= 3:
        errors.append("email_no_name_match")

    return errors


# ============================================================
# SECTION 4b: NETWORK VALIDATION (requires external calls)
# ============================================================
# These checks require network access and are run as warnings/info
# since they cannot be fully validated offline.

def validate_network_checks(website: str, email: str) -> List[str]:
    """
    Perform network-dependent validation checks.

    These checks match validator Stage 0-2 requirements:
    - Domain age (>= 7 days) - validator: check_domain_age()
    - MX records present - validator: check_mx_record()
    - Website accessible (HTTP 200) - validator: check_head_request()
    - Not on DNSBL blacklist - validator: check_dnsbl()

    Returns list of warnings (not blocking errors) since full validation
    requires network access that may not be available.
    """
    warnings = []

    domain = ""
    if email and '@' in email:
        domain = email.split('@')[1].lower()

    website_domain = ""
    if website:
        # Extract domain from website URL
        website_clean = website.lower().replace('https://', '').replace('http://', '')
        website_domain = website_clean.split('/')[0].split('?')[0]

    # Try to perform actual checks if network modules available
    try:
        import dns.resolver

        # MX Record check
        if domain:
            try:
                mx_records = dns.resolver.resolve(domain, 'MX')
                if not mx_records:
                    warnings.append(f"mx_record_missing:{domain}")
            except Exception:
                warnings.append(f"mx_record_check_failed:{domain}")

    except ImportError:
        warnings.append("network_check_skipped:dns.resolver not installed (pip install dnspython)")

    # Domain age check (requires whois)
    try:
        import whois

        check_domain = website_domain or domain
        if check_domain:
            try:
                w = whois.whois(check_domain)
                if w.creation_date:
                    from datetime import datetime, timedelta
                    creation = w.creation_date
                    if isinstance(creation, list):
                        creation = creation[0]
                    if isinstance(creation, datetime):
                        age_days = (datetime.now() - creation).days
                        if age_days < 7:
                            warnings.append(f"domain_too_new:{check_domain} ({age_days} days, need >= 7)")
            except Exception:
                pass  # WHOIS failures are common, don't warn

    except ImportError:
        pass  # whois package optional

    # Website accessibility check
    try:
        import requests

        if website:
            try:
                resp = requests.head(website, timeout=5, allow_redirects=True)
                if resp.status_code != 200:
                    warnings.append(f"website_not_accessible:{website} (HTTP {resp.status_code})")
            except Exception:
                warnings.append(f"website_unreachable:{website}")

    except ImportError:
        pass  # requests package commonly available, but optional here

    # DNSBL check (Cloudflare DBL via Spamhaus)
    try:
        import dns.resolver

        if domain:
            try:
                # Query Spamhaus DBL
                query = f"{domain}.dbl.spamhaus.org"
                dns.resolver.resolve(query, 'A')
                # If we get a response, domain is blacklisted
                warnings.append(f"domain_blacklisted:{domain} (Spamhaus DBL)")
            except dns.resolver.NXDOMAIN:
                pass  # Not blacklisted (expected)
            except Exception:
                pass  # Query failed, skip

    except ImportError:
        pass  # Already warned about dns.resolver above

    return warnings


# ============================================================
# SECTION 5: EMPLOYEE COUNT VALIDATION
# ============================================================

def validate_employee_count(count: str) -> List[str]:
    """Check employee count is exact match from valid values."""
    if not count:
        return ["employee_count_empty"]
    count = str(count).strip()
    if count not in VALID_EMPLOYEE_COUNTS:
        return [f"employee_count_invalid:{count} (valid: {', '.join(VALID_EMPLOYEE_COUNTS)})"]
    return []


# ============================================================
# SECTION 6: INDUSTRY/SUB-INDUSTRY VALIDATION
# ============================================================

_TAXONOMY_CACHE = None

def load_industry_taxonomy() -> Dict[str, List[str]]:
    """Load taxonomy from validator_models/industry_taxonomy.py"""
    global _TAXONOMY_CACHE
    if _TAXONOMY_CACHE is not None:
        return _TAXONOMY_CACHE

    # Try to import the taxonomy directly
    try:
        taxonomy_path = Path(__file__).parent.parent / "validator_models" / "industry_taxonomy.py"
        if taxonomy_path.exists():
            # Parse INDUSTRY_TAXONOMY from the file
            with open(taxonomy_path, 'r') as f:
                content = f.read()

            # Find INDUSTRY_TAXONOMY = { and extract it
            import ast

            # Find the start of the dict
            start = content.find("INDUSTRY_TAXONOMY = {")
            if start == -1:
                raise ValueError("INDUSTRY_TAXONOMY not found")

            # Extract the dict by matching braces
            brace_count = 0
            dict_start = content.find("{", start)
            dict_end = dict_start

            for i, char in enumerate(content[dict_start:], dict_start):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        dict_end = i + 1
                        break

            dict_str = content[dict_start:dict_end]
            taxonomy_raw = ast.literal_eval(dict_str)

            # Build lookup: {sub_industry_lower: [valid_industries_lower]}
            taxonomy = {}
            for sub_industry, data in taxonomy_raw.items():
                industries = data.get("industries", [])
                taxonomy[sub_industry.lower()] = [ind.lower() for ind in industries]

            _TAXONOMY_CACHE = taxonomy
            return taxonomy
    except Exception as e:
        print(f"Warning: Could not load industry taxonomy: {e}")
        return {}

    return {}


def validate_industry_pair(industry: str, sub_industry: str) -> List[str]:
    """Validate industry/sub-industry against taxonomy."""
    errors = []
    taxonomy = load_industry_taxonomy()

    if not taxonomy:
        return ["taxonomy_not_loaded"]

    sub_lower = str(sub_industry).lower().strip() if sub_industry else ''
    ind_lower = str(industry).lower().strip() if industry else ''

    if not sub_lower:
        errors.append("sub_industry_empty")
        return errors

    if sub_lower not in taxonomy:
        errors.append(f"sub_industry_invalid:{sub_industry}")
        return errors

    if not ind_lower:
        errors.append("industry_empty")
        return errors

    valid_industries = taxonomy[sub_lower]
    if ind_lower not in valid_industries:
        errors.append(f"industry_mismatch:{industry} not valid for {sub_industry} (valid: {', '.join(valid_industries)})")

    return errors


# ============================================================
# SECTION 7: LINKEDIN URL VALIDATION
# ============================================================

def normalize_linkedin_url(url: str, url_type: str = "profile") -> str:
    """Normalize LinkedIn URL to canonical form."""
    if not url:
        return ""

    url = str(url).strip().lower()
    url = re.sub(r'^https?://', '', url)
    url = re.sub(r'^www\.', '', url)

    if not url.startswith('linkedin.com'):
        return ""

    url = url.split('?')[0].split('#')[0]
    url = re.sub(r'/+', '/', url)
    url = url.rstrip('/')

    if url_type == "profile":
        match = re.search(r'linkedin\.com/in/([^/]+)', url)
        return f"linkedin.com/in/{match.group(1)}" if match else ""
    elif url_type == "company":
        match = re.search(r'linkedin\.com/company/([^/]+)', url)
        return f"linkedin.com/company/{match.group(1)}" if match else ""
    return ""


def validate_linkedin_urls(linkedin: str, company_linkedin: str) -> List[str]:
    """Validate both LinkedIn URLs."""
    errors = []
    if not normalize_linkedin_url(linkedin, "profile"):
        errors.append(f"linkedin_invalid_format:{linkedin}")
    if not normalize_linkedin_url(company_linkedin, "company"):
        errors.append(f"company_linkedin_invalid_format:{company_linkedin}")
    return errors


# ============================================================
# SECTION 8: DUPLICATE CHECKING
# ============================================================

def compute_email_hash(email: str) -> str:
    """Compute SHA256 hash of lowercase email."""
    return hashlib.sha256(email.lower().strip().encode()).hexdigest()


def compute_linkedin_combo_hash(linkedin: str, company_linkedin: str) -> str:
    """Compute SHA256 hash of normalized linkedin || company_linkedin."""
    norm_profile = normalize_linkedin_url(linkedin, "profile")
    norm_company = normalize_linkedin_url(company_linkedin, "company")
    if not norm_profile or not norm_company:
        return ""
    combined = f"{norm_profile}||{norm_company}"
    return hashlib.sha256(combined.encode()).hexdigest()


class DuplicateChecker:
    """Check for duplicate leads against transparency log."""

    def __init__(self, mode: str = "online"):
        self.mode = mode
        self.cache_dir = Path.home() / ".leadpoet"
        self.cache_file = self.cache_dir / "duplicate_cache.json"
        self.cache = self._load_cache()

    def _load_cache(self) -> dict:
        if self.cache_file.exists():
            try:
                return json.loads(self.cache_file.read_text())
            except:
                pass
        return {"email_hashes": {}, "linkedin_hashes": {}, "synced_at": None}

    def _save_cache(self):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(json.dumps(self.cache, indent=2))

    def sync_cache(self, since_hours: int = 24) -> int:
        """Download recent submissions to local cache."""
        try:
            from supabase import create_client
            from datetime import datetime, timedelta

            # Get Supabase credentials
            url = os.environ.get("SUPABASE_URL") or os.environ.get("LEADPOET_SUPABASE_URL")
            key = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("LEADPOET_SUPABASE_ANON_KEY")

            if not url or not key:
                print("Warning: Supabase credentials not found. Set SUPABASE_URL and SUPABASE_ANON_KEY")
                return 0

            client = create_client(url, key)

            # Query recent CONSENSUS_RESULT entries
            since = (datetime.utcnow() - timedelta(hours=since_hours)).isoformat()

            result = client.table("transparency_log") \
                .select("email_hash, linkedin_combo_hash, payload, created_at") \
                .eq("event_type", "CONSENSUS_RESULT") \
                .gte("created_at", since) \
                .execute()

            count = 0
            for record in result.data or []:
                email_hash = record.get("email_hash")
                linkedin_hash = record.get("linkedin_combo_hash")
                payload = record.get("payload", {})
                decision = payload.get("final_decision", "unknown")

                if email_hash:
                    self.cache["email_hashes"][email_hash] = {
                        "decision": decision,
                        "timestamp": record.get("created_at")
                    }
                    count += 1

                if linkedin_hash:
                    self.cache["linkedin_hashes"][linkedin_hash] = {
                        "decision": decision,
                        "timestamp": record.get("created_at")
                    }

            self.cache["synced_at"] = datetime.utcnow().isoformat()
            self._save_cache()
            return count

        except ImportError:
            print("Warning: supabase package not installed. Run: pip install supabase")
            return 0
        except Exception as e:
            print(f"Warning: Failed to sync cache: {e}")
            return 0

    def check(self, email: str, linkedin: str, company_linkedin: str) -> dict:
        """Check for duplicates."""
        result = {"is_duplicate": False, "reason": "new", "can_resubmit": True, "source": self.mode}

        email_hash = compute_email_hash(email) if email else ""
        linkedin_hash = compute_linkedin_combo_hash(linkedin, company_linkedin)

        if self.mode == "online":
            # Try to query transparency_log directly
            try:
                from supabase import create_client

                url = os.environ.get("SUPABASE_URL") or os.environ.get("LEADPOET_SUPABASE_URL")
                key = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("LEADPOET_SUPABASE_ANON_KEY")

                if url and key:
                    client = create_client(url, key)

                    # Check email hash
                    if email_hash:
                        email_result = client.table("transparency_log") \
                            .select("payload") \
                            .eq("email_hash", email_hash) \
                            .eq("event_type", "CONSENSUS_RESULT") \
                            .order("created_at", desc=True) \
                            .limit(1) \
                            .execute()

                        if email_result.data:
                            decision = email_result.data[0]["payload"].get("final_decision")
                            if decision == "approve":
                                return {"is_duplicate": True, "reason": "email_already_approved", "can_resubmit": False, "source": "online"}
                            else:
                                return {"is_duplicate": False, "reason": "email_was_denied_can_resubmit", "can_resubmit": True, "source": "online"}

                    # Check linkedin combo hash
                    if linkedin_hash:
                        li_result = client.table("transparency_log") \
                            .select("payload") \
                            .eq("linkedin_combo_hash", linkedin_hash) \
                            .eq("event_type", "CONSENSUS_RESULT") \
                            .order("created_at", desc=True) \
                            .limit(1) \
                            .execute()

                        if li_result.data:
                            decision = li_result.data[0]["payload"].get("final_decision")
                            if decision == "approve":
                                return {"is_duplicate": True, "reason": "linkedin_combo_already_approved", "can_resubmit": False, "source": "online"}

                    # Check for pending submission
                    if email_hash:
                        pending = client.table("transparency_log") \
                            .select("created_at") \
                            .eq("email_hash", email_hash) \
                            .eq("event_type", "SUBMISSION") \
                            .limit(1) \
                            .execute()

                        if pending.data:
                            # Check if there's no consensus result yet
                            return {"is_duplicate": True, "reason": "email_pending_processing", "can_resubmit": False, "source": "online"}

            except ImportError:
                print("Warning: supabase not installed, falling back to cache")
                self.mode = "offline"
            except Exception as e:
                print(f"Warning: Online check failed ({e}), falling back to cache")
                self.mode = "offline"

        # Offline mode - check local cache
        if self.mode == "offline":
            result["source"] = "cache"

            if email_hash and email_hash in self.cache["email_hashes"]:
                info = self.cache["email_hashes"][email_hash]
                if info["decision"] == "approve":
                    return {"is_duplicate": True, "reason": "email_already_approved", "can_resubmit": False, "source": "cache"}
                else:
                    return {"is_duplicate": False, "reason": "email_was_denied_can_resubmit", "can_resubmit": True, "source": "cache"}

            if linkedin_hash and linkedin_hash in self.cache["linkedin_hashes"]:
                info = self.cache["linkedin_hashes"][linkedin_hash]
                if info["decision"] == "approve":
                    return {"is_duplicate": True, "reason": "linkedin_combo_already_approved", "can_resubmit": False, "source": "cache"}

        return result


# ============================================================
# SECTION 9: SCORE PREVIEW
# ============================================================

def check_icp_match(sub_industry: str, role: str, country: str = "", city: str = "") -> dict:
    """Check if lead matches any ICP definition."""
    sub_lower = str(sub_industry).lower().strip() if sub_industry else ""
    role_lower = str(role).lower().strip() if role else ""
    country_lower = str(country).lower().strip() if country else ""
    city_lower = str(city).lower().strip() if city else ""

    # Expand role abbreviations for matching
    role_expanded = role_lower
    role_expansions = {
        "chief executive officer": "ceo",
        "chief technology officer": "cto",
        "chief operating officer": "coo",
        "chief financial officer": "cfo",
        "chief marketing officer": "cmo",
        "chief information officer": "cio",
        "chief scientific officer": "cso",
        "chief risk officer": "cro",
        "chief compliance officer": "cco",
        "chief product officer": "cpo",
        "chief ai officer": "caio",
        "chief data officer": "cdo",
        "vice president": "vp",
    }
    for full, abbr in role_expansions.items():
        if full in role_lower:
            role_expanded = f"{role_lower} {abbr}"

    for icp in ICP_DEFINITIONS:
        # Check sub-industry match
        sub_match = any(sub_lower == s or sub_lower in s or s in sub_lower
                        for s in icp["sub_industries"])
        if not sub_match:
            continue

        # Check role match (check both original and expanded)
        role_match = any(r in role_lower or r in role_expanded for r in icp["roles"])
        if not role_match:
            continue

        # Check region filter if present
        if "regions" in icp:
            region_match = any(r in country_lower or r in city_lower for r in icp["regions"])
            if not region_match:
                continue

        return {"matches": True, "icp_name": icp["name"], "bonus": 50}

    return {"matches": False, "icp_name": None, "bonus": 0}


def is_major_hub(city: str, country: str) -> bool:
    """Check if city is a major hub in the given country."""
    city_lower = str(city).lower().strip() if city else ""
    country_lower = str(country).lower().strip() if country else ""

    for hub_country, hub_cities in MAJOR_HUBS_BY_COUNTRY.items():
        if hub_country in country_lower or country_lower in hub_country:
            if city_lower in hub_cities:
                return True
    return False


def parse_employee_count(count_str: str) -> Tuple[int, int]:
    """Parse employee count string to min/max tuple."""
    if count_str == "0-1":
        return (0, 1)
    elif count_str == "2-10":
        return (2, 10)
    elif count_str == "11-50":
        return (11, 50)
    elif count_str == "51-200":
        return (51, 200)
    elif count_str == "201-500":
        return (201, 500)
    elif count_str == "501-1,000":
        return (501, 1000)
    elif count_str == "1,001-5,000":
        return (1001, 5000)
    elif count_str == "5,001-10,000":
        return (5001, 10000)
    elif count_str == "10,001+":
        return (10001, 100000)
    return (0, 0)


def calculate_size_adjustment(employee_count: str, city: str, country: str) -> Tuple[int, str]:
    """Calculate employee size bonus/penalty."""
    if not employee_count or employee_count not in VALID_EMPLOYEE_COUNTS:
        return (0, "no_employee_count")

    emp_min, emp_max = parse_employee_count(employee_count)
    is_hub = is_major_hub(city, country)

    # Small company in major hub (+50)
    if emp_max <= 10 and is_hub:
        return (50, f"small_company_major_hub (<=10 employees in {city})")

    # Small company (+20)
    if emp_max <= 50:
        return (20, "small_company (<=50 employees)")

    # Large company penalties
    if 5000 < emp_min < 10001:
        return (-15, "large_company_penalty (5k-10k employees)")
    if emp_min > 1000:
        return (-10, "large_company_penalty (>1k employees)")

    return (0, "mid_size_company")


def preview_score(lead: dict) -> dict:
    """Generate score preview for lead."""
    icp = check_icp_match(
        lead.get("sub_industry", ""),
        lead.get("role", ""),
        lead.get("country", ""),
        lead.get("city", "")
    )

    size_adj, size_reason = calculate_size_adjustment(
        lead.get("employee_count", ""),
        lead.get("city", ""),
        lead.get("country", "")
    )

    # Cap bonus at 50
    total_bonus = min(50, icp["bonus"] + max(0, size_adj))

    # Penalties stack after capping bonus
    if size_adj < 0:
        total_adjustment = total_bonus + size_adj
    else:
        total_adjustment = total_bonus

    recommendations = []
    if not icp["matches"]:
        recommendations.append("Consider targeting ICP categories for +50 bonus")
    if size_adj < 0:
        recommendations.append("Large companies receive penalties - consider smaller targets")
    if size_adj == 0 and not icp["matches"]:
        recommendations.append("Small companies (<=50 employees) receive +20 bonus")

    return {
        "icp_match": icp["matches"],
        "icp_name": icp["icp_name"],
        "icp_bonus": icp["bonus"],
        "size_adjustment": size_adj,
        "size_reason": size_reason,
        "estimated_adjustment": total_adjustment,
        "recommendations": recommendations
    }


# ============================================================
# SECTION 10: MAIN AUDIT FUNCTION
# ============================================================

def audit_lead(lead: dict, duplicate_mode: str = "online", run_network_checks: bool = True) -> AuditResult:
    """Run full audit on a lead.

    Args:
        lead: Dictionary containing lead data
        duplicate_mode: "online" or "offline" for duplicate checking
        run_network_checks: If True, run network-dependent checks (domain age, MX, etc.)

    Returns:
        AuditResult with pass/fail status, errors, and warnings
    """
    blocking_errors = []
    warnings = []

    # ============================================================
    # STAGE -1: Terms Attestation (validator first check)
    # ============================================================
    blocking_errors.extend(validate_attestation(lead))

    # ============================================================
    # STAGE 0: Required Fields
    # ============================================================
    blocking_errors.extend(validate_required_fields(lead))

    # ============================================================
    # STAGE 0.5: Source Provenance
    # ============================================================
    blocking_errors.extend(validate_source_provenance(lead))

    # ============================================================
    # STAGE 1-2: Network Checks (warnings only - require external calls)
    # ============================================================
    if run_network_checks:
        network_warnings = validate_network_checks(
            lead.get("website", ""),
            lead.get("email", "")
        )
        warnings.extend(network_warnings)

    # ============================================================
    # Field Validations
    # ============================================================

    # Role validation (includes anti-gaming checks)
    blocking_errors.extend(validate_role(lead.get("role", "")))

    # Description validation (returns tuple of errors, warnings)
    desc_errors, desc_warnings = validate_description(lead.get("description", ""))
    blocking_errors.extend(desc_errors)
    warnings.extend(desc_warnings)

    # Email validation (includes disposable check, Unicode support)
    blocking_errors.extend(validate_email(
        lead.get("email", ""),
        lead.get("first", ""),
        lead.get("last", "")
    ))

    # Employee count
    blocking_errors.extend(validate_employee_count(lead.get("employee_count", "")))

    # Industry/sub-industry
    blocking_errors.extend(validate_industry_pair(
        lead.get("industry", ""),
        lead.get("sub_industry", "")
    ))

    # LinkedIn URLs
    blocking_errors.extend(validate_linkedin_urls(
        lead.get("linkedin", ""),
        lead.get("company_linkedin", "")
    ))

    # Location validation (anti-gaming checks)
    blocking_errors.extend(validate_location(
        lead.get("city", ""),
        lead.get("country", ""),
        lead.get("region", lead.get("state", ""))
    ))

    # ============================================================
    # Duplicate Check
    # ============================================================
    checker = DuplicateChecker(mode=duplicate_mode)
    dup_status = checker.check(
        lead.get("email", ""),
        lead.get("linkedin", ""),
        lead.get("company_linkedin", "")
    )
    if dup_status["is_duplicate"] and not dup_status["can_resubmit"]:
        blocking_errors.append(f"duplicate:{dup_status['reason']}")

    # Score preview
    score = preview_score(lead)

    return AuditResult(
        passed=len(blocking_errors) == 0,
        blocking_errors=blocking_errors,
        warnings=warnings,
        duplicate_status=dup_status,
        score_preview=score
    )


# ============================================================
# CLI ENTRY POINT
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Lead Auditor - Pre-submission validation for LeadPoet miners",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m miner_models.lead_auditor lead.json
  python -m miner_models.lead_auditor leads.json --mode offline
  python -m miner_models.lead_auditor lead.json --skip-network
  python -m miner_models.lead_auditor --sync-cache
        """
    )
    parser.add_argument("input", nargs="?", help="JSON file with lead(s) to audit")
    parser.add_argument("--mode", choices=["online", "offline"], default="online",
                        help="Duplicate check mode (default: online)")
    parser.add_argument("--skip-network", action="store_true",
                        help="Skip network-dependent checks (domain age, MX, website, DNSBL)")
    parser.add_argument("--sync-cache", action="store_true",
                        help="Sync duplicate cache from transparency log")
    parser.add_argument("--output", "-o", help="Output file for results (JSON)")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Only output JSON, no summary")
    args = parser.parse_args()

    if args.sync_cache:
        print("Syncing duplicate cache from transparency log...")
        checker = DuplicateChecker(mode="online")
        count = checker.sync_cache(since_hours=168)  # Last 7 days
        print(f"Synced {count} records to cache at {checker.cache_file}")
        return

    if not args.input:
        parser.print_help()
        return

    # Load lead(s)
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File not found: {args.input}")
        sys.exit(1)

    with open(input_path) as f:
        data = json.load(f)

    leads = [data] if isinstance(data, dict) else data

    # Audit each lead
    results = []
    for i, lead in enumerate(leads):
        result = audit_lead(
            lead,
            duplicate_mode=args.mode,
            run_network_checks=not args.skip_network
        )
        results.append({
            "index": i,
            "email": lead.get("email", "unknown"),
            "passed": result.passed,
            "blocking_errors": result.blocking_errors,
            "warnings": result.warnings,
            "duplicate_status": result.duplicate_status,
            "score_preview": result.score_preview
        })

    # Output
    output = json.dumps(results, indent=2)
    if args.output:
        Path(args.output).write_text(output)
        if not args.quiet:
            print(f"Results written to {args.output}")
    else:
        print(output)

    # Summary
    if not args.quiet:
        passed = sum(1 for r in results if r["passed"])
        print(f"\n{'='*50}")
        print(f"AUDIT SUMMARY: {passed}/{len(results)} leads passed")
        print(f"{'='*50}")

        for r in results:
            status = "PASS" if r["passed"] else "FAIL"
            email = r.get("email", "unknown")[:30]
            print(f"\n[{status}] {email}")

            if r["blocking_errors"]:
                print("  Blocking errors:")
                for err in r["blocking_errors"]:
                    print(f"    - {err}")

            if r["warnings"]:
                print("  Warnings:")
                for warn in r["warnings"]:
                    print(f"    ! {warn}")

            if r["score_preview"].get("icp_match"):
                print(f"  ICP Match: {r['score_preview']['icp_name']} (+{r['score_preview']['icp_bonus']})")

            adj = r["score_preview"].get("estimated_adjustment", 0)
            if adj != 0:
                print(f"  Score Adjustment: {adj:+d}")


if __name__ == "__main__":
    main()
