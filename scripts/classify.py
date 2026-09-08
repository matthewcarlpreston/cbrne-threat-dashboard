"""
classify.py

Tags an already-public news headline with one or more CBRN-E / novel-weapons
domain categories using keyword/regex matching against the headline text only.

This module does NOT interpret, expand, or reason about weapons technical
content -- it matches plain-English news-reporting vocabulary (e.g. "sarin",
"dirty bomb", "drone swarm") against headlines that are already public. See
README.md "Scope guardrail".

MVP approach: keyword/regex matching. Documented here as an intentional
starting point -- an embeddings/LLM-based classifier could replace or
augment this later without changing the output schema (an item's
"categories" list).
"""

from __future__ import annotations

import re
from typing import Any

# Category -> compiled regex (case-insensitive, word-boundary where sensible).
# Ordered roughly by the taxonomy in the project brief.
_TAXONOMY_PATTERNS: dict[str, list[str]] = {
    "chemical": [
        r"chemical weapon", r"chemical attack", r"chemical warfare",
        r"nerve agent", r"\bsarin\b", r"\bvx\b", r"novichok", r"mustard gas",
        r"chlorine gas", r"toxic gas attack", r"\bopcw\b",
        r"chemical weapons convention", r"poison gas",
    ],
    "biological": [
        r"biological weapon", r"bioweapon", r"bioterror", r"biosecurity",
        r"\banthrax\b", r"biosafety level", r"pathogen release", r"\bricin\b",
        r"bacillus anthracis", r"smallpox weapon", r"plague weapon",
        r"biological warfare", r"biological weapons convention", r"\bbwc\b",
    ],
    "radiological_nuclear": [
        r"nuclear weapon", r"dirty bomb", r"radiological (device|material|threat|dispersal)",
        r"uranium enrichment", r"\bplutonium\b", r"\biaea\b", r"nuclear proliferation",
        r"nuclear test", r"radioactive material", r"nuclear facility",
        r"\bwarhead", r"fissile material", r"nuclear smuggling",
        r"radioactive source", r"nuclear safeguards", r"enriched uranium",
    ],
    "explosives": [
        r"\bied\b", r"improvised explosive device", r"bomb plot",
        r"explosives? seizure", r"weapons? cache", r"bomb disposal", r"\beod\b",
        r"explosive precursor", r"ammonium nitrate", r"\btatp\b",
        r"vehicle[- ]borne", r"suicide bomb", r"unexploded ordnance",
        r"\blandmine", r"car bomb", r"pipe bomb", r"explosive device",
    ],
    "autonomous_weapons": [
        r"autonomous weapon", r"drone swarm", r"loitering munition",
        r"killer robot", r"lethal autonomous", r"autonomous drone strike",
        r"unmanned combat (aerial|air) vehicle", r"\bslaughterbot",
        r"ai-powered weapon", r"ai-enabled weapon",
    ],
    "directed_energy": [
        r"directed energy weapon", r"laser weapon system", r"microwave weapon",
        r"high[- ]power microwave", r"\blaser weapon\b", r"\bdew system\b",
        r"directed-energy",
    ],
}

_COMPILED: dict[str, re.Pattern] = {
    category: re.compile("|".join(patterns), re.IGNORECASE)
    for category, patterns in _TAXONOMY_PATTERNS.items()
}

# Some taxonomy terms collide with unrelated pop-culture usage (the band
# "Anthrax", the video game/show "Plague Inc.", etc). If a headline matching
# a category also matches its negative-context pattern, that category is
# suppressed for that headline (other categories can still match normally).
_NEGATIVE_CONTEXT: dict[str, re.Pattern] = {
    "biological": re.compile(
        r"\b(band|album|concert|tour dates?|setlist|guitarist|drummer|"
        r"frontman|rock and roll hall of fame|music festival|new music|"
        r"mastodon|metallica|megadeth|slayer)\b",
        re.IGNORECASE,
    ),
}

CATEGORY_LABELS: dict[str, str] = {
    "chemical": "Chemical",
    "biological": "Biological",
    "radiological_nuclear": "Radiological/Nuclear",
    "explosives": "Explosives",
    "autonomous_weapons": "Autonomous Weapons",
    "directed_energy": "Directed Energy Weapons",
}


def classify_text(text: str) -> list[str]:
    """Return the list of category keys whose patterns match `text`."""
    if not text:
        return []
    matches = []
    for category, pattern in _COMPILED.items():
        if not pattern.search(text):
            continue
        negative = _NEGATIVE_CONTEXT.get(category)
        if negative and negative.search(text):
            continue
        matches.append(category)
    return matches


def classify_item(item: dict[str, Any]) -> list[str]:
    """Classify a normalized article dict using its title (and RSS summary if present)."""
    haystack = item.get("title", "")
    if item.get("summary"):
        haystack = f"{haystack} {item['summary']}"
    return classify_text(haystack)


def annotate_items(items: list[dict[str, Any]], drop_unmatched: bool = True) -> list[dict[str, Any]]:
    """Add a 'categories' field to each item; optionally drop items with no category match.

    drop_unmatched=True is the default because upstream sources (GDELT's WMD
    theme in particular) are intentionally broad/noisy -- this keyword pass
    is what keeps the published dashboard on-topic.
    """
    annotated = []
    for item in items:
        categories = classify_item(item)
        item["categories"] = categories
        if categories or not drop_unmatched:
            annotated.append(item)
    return annotated
