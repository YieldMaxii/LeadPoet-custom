"""
Lead scoring and ranking helpers.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List


# Industry keywords for heuristic scoring
INDUSTRY_KEYWORDS = {
    "manufacturing": ["manufacturing", "fabrication", "machining", "production", "assembly", "industrial", "cnc", "metal", "plastic"],
    "technology": ["tech", "software", "saas", "cloud", "ai", "ml", "data", "cyber", "digital", "platform", "app"],
    "healthcare": ["health", "medical", "clinical", "pharma", "biotech", "hospital", "patient", "therapeutic", "diagnostic"],
    "finance": ["finance", "fintech", "bank", "investment", "insurance", "capital", "fund", "trading", "wealth"],
    "retail": ["retail", "ecommerce", "e-commerce", "store", "shop", "consumer", "brand", "product", "merchandise"],
}

# Role keywords for scoring
ROLE_SCORE_KEYWORDS = {
    "executive": ["ceo", "president", "owner", "founder", "co-founder", "principal", "managing director"],
    "c_suite": ["cto", "cfo", "coo", "cmo", "cio", "cso", "cro", "chief"],
    "vp_director": ["vp", "vice president", "director", "head of"],
    "manager": ["manager", "lead", "senior"],
}

# LLM Batch Scoring Prompt - loaded from external file for maintainability
_LEAD_SCORING_PROMPT_PATH = Path(__file__).parent.parent / "config" / "prompts" / "lead_scoring.txt"


def _get_lead_scoring_prompt() -> str:
    """Load the lead scoring prompt from external file."""
    if _LEAD_SCORING_PROMPT_PATH.exists():
        return _LEAD_SCORING_PROMPT_PATH.read_text().strip()
    # Fallback if file not found
    return "You are a B2B lead qualification specialist. Score leads 0.0-1.0 based on ICP fit. Return JSON: [{\"lead_index\": 0, \"score\": 0.5}, ...]"


def _normalize_text(txt: str) -> str:
    """Normalize text for matching."""
    return re.sub(r"[^a-z0-9 ]+", " ", (txt or "").lower()).strip()


def _heuristic_score(lead: Dict[str, Any], icp_config: Dict[str, Any]) -> float:
    """
    Fast heuristic scoring when LLM is unavailable.

    Scores based on:
    - Industry keyword matching
    - Role priority from ICP config
    - Employee count (smaller = better for SMB targeting)
    """
    score = 0.3  # Base score

    # Get ICP text keywords
    icp_text = _normalize_text(icp_config.get("icp_text", ""))
    icp_words = set(icp_text.split())

    # Industry matching
    lead_industry = _normalize_text(lead.get("industry", ""))
    lead_sub = _normalize_text(lead.get("sub_industry", ""))
    lead_desc = _normalize_text(lead.get("description", ""))

    lead_text = f"{lead_industry} {lead_sub} {lead_desc}"
    lead_words = set(lead_text.split())

    # Check overlap with ICP
    overlap = icp_words & lead_words
    if overlap:
        score += min(len(overlap) * 0.1, 0.3)

    # Check industry keywords from config
    industry_keywords = icp_config.get("validation_config", {}).get("industry_keywords", [])
    for kw in industry_keywords:
        if kw.lower() in lead_text:
            score += 0.05
            if score >= 0.9:
                break

    # Role priority scoring
    role = _normalize_text(lead.get("role", ""))
    role_priority = icp_config.get("role_priority", {})

    # Check against role priority map
    best_role_score = 0
    for role_key, priority in role_priority.items():
        if role_key.lower() in role:
            if priority == 1:
                best_role_score = 0.3
            elif priority == 2:
                best_role_score = max(best_role_score, 0.2)
            elif priority == 3:
                best_role_score = max(best_role_score, 0.1)
            break

    score += best_role_score

    # Employee count bonus (prefer smaller companies for SMB targeting)
    emp_count = lead.get("employee_count", "")
    if emp_count in ["2-10", "11-50"]:
        score += 0.1
    elif emp_count in ["51-200"]:
        score += 0.05

    return min(score, 1.0)


async def _llm_batch_score(leads: List[Dict[str, Any]], icp_config: Dict[str, Any]) -> List[float]:
    """
    Score leads using LLM batch processing.
    """
    import httpx

    openrouter_key = os.getenv("OPENROUTER_KEY")
    if not openrouter_key or not leads:
        return [_heuristic_score(lead, icp_config) for lead in leads]

    icp_description = icp_config.get("icp_text", "")
    if not icp_description:
        return [_heuristic_score(lead, icp_config) for lead in leads]

    # Build lead descriptions
    prompt_parts = [f"BUYER'S IDEAL CUSTOMER PROFILE (ICP):\n{icp_description}\n\nLEADS TO EVALUATE ({len(leads)} total):\n"]
    for i, lead in enumerate(leads):
        prompt_parts.append(
            f"\nLEAD #{i}:\n"
            f"  Company: {lead.get('business', 'Unknown')}\n"
            f"  Industry: {lead.get('industry', 'Unknown')}\n"
            f"  Sub-industry: {lead.get('sub_industry', 'Unknown')}\n"
            f"  Contact Role: {lead.get('role', 'Unknown')}\n"
            f"  Employee Count: {lead.get('employee_count', 'Unknown')}\n"
            f"  Location: {lead.get('city', '')}, {lead.get('state', '')}, {lead.get('country', '')}\n"
            f"  Description: {lead.get('description', '')[:150]}\n"
        )

    prompt = "\n".join(prompt_parts)

    print(f"\n🎯 LEAD SCORING: Scoring {len(leads)} leads via LLM...")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://leadpoet.ai",
                },
                json={
                    "model": "anthropic/claude-3-haiku",
                    "messages": [
                        {"role": "system", "content": _get_lead_scoring_prompt()},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 1000
                }
            )

            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

                # Parse JSON from response
                try:
                    # Find JSON array in response
                    json_match = re.search(r'\[[\s\S]*\]', content)
                    if json_match:
                        json_str = json_match.group(0)
                        data = json.loads(json_str)

                        scores = [_heuristic_score(lead, icp_config) for lead in leads]  # Default
                        for item in data:
                            idx = item.get("lead_index", -1)
                            score = item.get("score", None)
                            if 0 <= idx < len(leads) and score is not None:
                                scores[idx] = float(score)
                        return scores

                except Exception:
                    return [_heuristic_score(lead, icp_config) for lead in leads]

    except Exception:
        return [_heuristic_score(lead, icp_config) for lead in leads]

    return [_heuristic_score(lead, icp_config) for lead in leads]


async def rank_leads(
    leads: List[Dict[str, Any]],
    icp_config: Dict[str, Any],
    use_llm: bool = True
) -> List[Dict[str, Any]]:
    """
    Score and rank leads by ICP fit.
    """
    if not leads:
        return []

    print(f"\n🎯 Lead Scoring & Ranking")
    print(f"   Leads to rank: {len(leads)}")

    try:
        if use_llm:
            scores = await _llm_batch_score(leads, icp_config)
        else:
            scores = [_heuristic_score(lead, icp_config) for lead in leads]
    except Exception:
        scores = [_heuristic_score(lead, icp_config) for lead in leads]

    # Add scores to leads
    for lead, score in zip(leads, scores):
        lead["lead_score"] = round(score, 3)

    # Sort by score
    sorted_leads = sorted(leads, key=lambda x: x.get("lead_score", 0), reverse=True)

    # Print top leads
    print(f"\n🏆 Top ranked leads:")
    for i, lead in enumerate(sorted_leads[:10], 1):
        company = lead.get('business', 'Unknown')[:35]
        role = lead.get('role', 'Unknown')[:20]
        score = lead.get('lead_score', 0)
        print(f"   {i}. {company:35} | {role:20} | Score: {score}")

    if len(sorted_leads) > 10:
        print(f"   ... and {len(sorted_leads) - 10} more leads")

    # Show score distribution
    high_score = sum(1 for l in sorted_leads if l.get("lead_score", 0) >= 0.7)
    mid_score = sum(1 for l in sorted_leads if 0.4 <= l.get("lead_score", 0) < 0.7)
    low_score = sum(1 for l in sorted_leads if l.get("lead_score", 0) < 0.4)

    print(f"\n📊 Score Distribution:")
    print(f"   High (0.7+):  {high_score} leads")
    print(f"   Medium (0.4-0.7): {mid_score} leads")
    print(f"   Low (<0.4):   {low_score} leads")

    return sorted_leads
