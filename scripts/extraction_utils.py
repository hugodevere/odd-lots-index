"""Small, deterministic validation helpers for the extraction notebook."""

import hashlib
import json
import re
from datetime import date

from rapidfuzz import fuzz


def request_hash(body):
    """Fingerprint every request field, independently of dictionary key order."""
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def require_stage_success(stage, errors):
    """A failed model job is unavailable evidence, never a rejected call."""
    if len(errors):
        raise RuntimeError(f"{stage} incomplete: {len(errors)} failed jobs; inspect the private stage log before exporting")


def validate_groups(groups, size):
    """A model grouping must be a complete, disjoint partition of input mentions."""
    seen = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("company"), str):
            raise ValueError("Invalid company group")
        members = group.get("items")
        if not group["company"].strip() or not isinstance(members, list) or not members:
            raise ValueError("Empty company group")
        if any(type(i) is not int or not 1 <= i <= size for i in members):
            raise ValueError("Invalid mention number")
        seen.extend(members)
    if sorted(seen) != list(range(1, size + 1)):
        raise ValueError("Matcher omitted or duplicated mentions")


def canonical_ticker(value):
    """Known share-class punctuation aliases only; do not collapse instruments."""
    symbol = (value or "").strip().upper()
    return {"BRK.A": "BRK-A", "BRK.B": "BRK-B", "BF.A": "BF-A", "BF.B": "BF-B"}.get(symbol, symbol)


_SUFFIXES = {
    "inc", "corp", "corporation", "co", "ltd", "plc", "company", "holdings",
    "holding", "group", "the", "llc", "sa", "ag", "nv", "public", "limited",
    "class", "common", "stock", "shares", "lp",
}
_ALIASES = {
    "google": "alphabet", "restoration hardware": "rh", "blue owl": "blue owl capital",
    "costco": "costco wholesale", "royal caribbean": "royal caribbean cruises",
    "seagate": "seagate technology",
    "ford": "ford motor", "meta": "meta platforms", "amazon": "amazon com",
}


def _company_name(value):
    # SEC state-of-incorporation and revision markers are not part of the name.
    value = re.sub(r"\s*/(?:NEW|[A-Z]{2})\s*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\bL\.P\.(?=\s|$)", "LP", value, flags=re.IGNORECASE)
    words = re.sub(r"\W+", " ", value.lower()).strip().split()
    name = " ".join(word for word in words if word not in _SUFFIXES)
    return _ALIASES.get(name, name)


def company_matches(candidate, registered, legal_name=""):
    """Require company identity as well as ticker existence; ambiguous names fail."""
    left, right = _company_name(candidate), _company_name(registered)
    matches = lambda a, b: bool(a and b and (a == b or fuzz.ratio(a, b) >= 90))
    if matches(left, right):
        return True
    # A model's legal name may bridge harmless wording differences, but cannot
    # establish identity by itself: it must agree with the named candidate too.
    legal = _company_name(legal_name)
    return matches(left, legal) and matches(legal, right)


def dated_ticker_override(company, episode_date, proposal):
    """Explicit issuer evidence, not a generic symbol-prefix or ADR equivalence."""
    try:
        published = date.fromisoformat(str(episode_date)[:10])
    except (ValueError, TypeError):
        return None
    if (_company_name(company) == "crh" and canonical_ticker(proposal) == "CRH.IR"
            and published >= date(2023, 9, 25)):
        return {
            "from": "CRH.IR", "to": "CRH", "effective_date": "2023-09-25",
            "source": "https://www.sec.gov/Archives/edgar/data/849395/000165495423012182/a4349n.htm",
        }
    return None
