"""
Normalization helpers for enrichment pipeline.
"""

import re
from typing import Tuple
from urllib.parse import urlparse, unquote


# ============================================================================
# Valid employee count ranges (exact LinkedIn format)
# ============================================================================

# Numeric boundaries for mapping: (min, max, display_string)
EMPLOYEE_RANGE_MAP = [
    (0, 1, "0-1"),
    (2, 10, "2-10"),
    (11, 50, "11-50"),
    (51, 200, "51-200"),
    (201, 500, "201-500"),
    (501, 1000, "501-1,000"),
    (1001, 5000, "1,001-5,000"),
    (5001, 10000, "5,001-10,000"),
    (10001, float('inf'), "10,001+"),
]

# Derived from EMPLOYEE_RANGE_MAP for validation
VALID_EMPLOYEE_RANGES = [r[2] for r in EMPLOYEE_RANGE_MAP]


def normalize_employee_count(raw: str) -> str:
    """
    Normalize employee count to exact LinkedIn format.

    Handles formats like:
    - "50 employees"
    - "100-200"
    - "~500"
    - "1000+"
    - "1,500"

    Returns:
        Exact LinkedIn format like "51-200" or "" if cannot normalize.
    """
    if not raw or not isinstance(raw, str):
        return ""

    # If already in valid format, return as-is
    if raw in VALID_EMPLOYEE_RANGES:
        return raw

    # Clean up the string
    raw = raw.lower().strip()

    # Remove common words
    raw = re.sub(
        r'\b(employees?|people|staff|team members?|workers?|approximately|about|around|over|under|less than|more than)\b',
        '',
        raw,
        flags=re.IGNORECASE,
    )

    # Remove special characters except digits, commas, dashes, plus
    raw = re.sub(r'[~≈]', '', raw)

    # Extract numbers
    numbers = re.findall(r'[\d,]+', raw)

    if not numbers:
        return ""

    # Parse numbers (remove commas)
    parsed_nums = []
    for n in numbers:
        try:
            parsed_nums.append(int(n.replace(',', '')))
        except ValueError:
            continue

    if not parsed_nums:
        return ""

    # Check for plus sign (1000+)
    has_plus = '+' in raw

    # Use the appropriate number
    if len(parsed_nums) == 1:
        num = parsed_nums[0]
        if has_plus:
            # "1000+" means 1000 or more (bias upward to next range)
            if num >= 10000:
                num = max(num, 10001)
            elif num >= 1000:
                num = max(num, 1001)
    else:
        # Range like "100-200" - use midpoint or max
        num = max(parsed_nums)

    # Map to range
    for low, high, range_str in EMPLOYEE_RANGE_MAP:
        if low <= num <= high:
            return range_str

    return ""


def normalize_linkedin_url(url: str, url_type: str) -> str:
    """
    Normalize LinkedIn URL to gateway format.

    Args:
        url: Raw LinkedIn URL
        url_type: "company" or "profile"

    Returns:
        Normalized URL like "https://linkedin.com/company/{slug}" or ""
    """
    if not url or not isinstance(url, str):
        return ""

    url = url.strip()

    # Parse the URL
    try:
        # Add scheme if missing
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        parsed = urlparse(url)

        # Must be LinkedIn domain
        if 'linkedin.com' not in parsed.netloc.lower():
            return ""

        # Get the path and clean it (strip query params and fragments)
        path = unquote(parsed.path).strip('/')

        # Extract slug based on type
        if url_type == "company":
            # Match /company/{slug}
            match = re.match(r'^company/([^/?\s]+)', path)
            if match:
                slug = match.group(1).lower()
                return f"https://linkedin.com/company/{slug}"

        elif url_type == "profile":
            # Match /in/{slug}
            match = re.match(r'^in/([^/?\s]+)', path)
            if match:
                slug = match.group(1).lower()
                return f"https://linkedin.com/in/{slug}"

        return ""

    except Exception:
        return ""


# ============================================================================
# Sub-industry normalization map
# ============================================================================

SUB_INDUSTRY_NORMALIZATION = {
    # Automotive variations → "Automotive" (Transportation)
    "automotive parts manufacturing": ("Automotive", "Transportation"),
    "automotive parts": ("Automotive", "Transportation"),
    "automotive manufacturing": ("Automotive", "Transportation"),
    "auto parts": ("Automotive", "Transportation"),
    "auto manufacturing": ("Automotive", "Transportation"),
    "vehicle manufacturing": ("Automotive", "Transportation"),
    "car manufacturing": ("Automotive", "Transportation"),
    # Robotics variations → "Robotics" (Hardware)
    "robotics manufacturing": ("Robotics", "Hardware"),
    "industrial robotics": ("Robotics", "Hardware"),
    "automation robotics": ("Robotics", "Hardware"),
    "robotics": ("Robotics", "Hardware"),
    "robotic": ("Robotics", "Hardware"),
    "3d scanning": ("Robotics", "Hardware"),
    # Boat/Marine - no boat manufacturing in taxonomy, use generic Manufacturing
    "boat manufacturing": ("Manufacturing", "Manufacturing"),
    "marine manufacturing": ("Manufacturing", "Manufacturing"),
    "shipbuilding": ("Manufacturing", "Manufacturing"),
    # Machinery variations → "Machinery Manufacturing" (Manufacturing)
    "machinery manufacturing": ("Machinery Manufacturing", "Manufacturing"),
    "industrial machinery": ("Machinery Manufacturing", "Manufacturing"),
    "heavy machinery": ("Machinery Manufacturing", "Manufacturing"),
    "machinery": ("Machinery Manufacturing", "Manufacturing"),
    # Precision/CNC variations → "Machinery Manufacturing" (Manufacturing)
    "precision manufacturing": ("Machinery Manufacturing", "Manufacturing"),
    "precision machining": ("Machinery Manufacturing", "Manufacturing"),
    "cnc machining": ("Machinery Manufacturing", "Manufacturing"),
    "cnc manufacturing": ("Machinery Manufacturing", "Manufacturing"),
    "cnc machinery": ("Machinery Manufacturing", "Manufacturing"),
    "cnc": ("Machinery Manufacturing", "Manufacturing"),
    # Metal variations → "Machinery Manufacturing" (Manufacturing)
    "metal fabrication": ("Machinery Manufacturing", "Manufacturing"),
    "metal manufacturing": ("Machinery Manufacturing", "Manufacturing"),
    "metal components": ("Machinery Manufacturing", "Manufacturing"),
    "metal components manufacturing": ("Machinery Manufacturing", "Manufacturing"),
    "sheet metal": ("Machinery Manufacturing", "Manufacturing"),
    # Plastic variations → "Plastics and Rubber Manufacturing" (Manufacturing)
    "plastic manufacturing": ("Plastics and Rubber Manufacturing", "Manufacturing"),
    "plastic injection molding": ("Plastics and Rubber Manufacturing", "Manufacturing"),
    "injection molding": ("Plastics and Rubber Manufacturing", "Manufacturing"),
    "plastics": ("Plastics and Rubber Manufacturing", "Manufacturing"),
    # Electronics variations → "Electronics" (Hardware)
    "electronics manufacturing": ("Electronics", "Hardware"),
    "electronic manufacturing": ("Electronics", "Hardware"),
    "pcb manufacturing": ("Electronics", "Hardware"),
    # General manufacturing → "Manufacturing" (Manufacturing)
    "general manufacturing": ("Manufacturing", "Manufacturing"),
    "contract manufacturing": ("Manufacturing", "Manufacturing"),
    "custom manufacturing": ("Manufacturing", "Manufacturing"),
    "manufacturing": ("Manufacturing", "Manufacturing"),
}


def normalize_sub_industry(sub_industry: str, industry: str = None) -> Tuple[str, str]:
    """
    Normalize LLM-extracted sub_industry to match taxonomy values.

    Args:
        sub_industry: Raw sub_industry from extraction
        industry: Raw industry from extraction (optional)

    Returns:
        Tuple of (normalized_sub_industry, normalized_industry)
    """
    if not sub_industry:
        return (sub_industry, industry)

    # Check exact match in normalization map (case-insensitive)
    sub_lower = sub_industry.lower().strip()
    if sub_lower in SUB_INDUSTRY_NORMALIZATION:
        return SUB_INDUSTRY_NORMALIZATION[sub_lower]

    # Check if contains key phrases
    for phrase, normalized in SUB_INDUSTRY_NORMALIZATION.items():
        if phrase in sub_lower:
            return normalized

    return (sub_industry, industry)
