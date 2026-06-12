# judge/frontend/responsive_checker.py

import re
import tinycss2


def _parse_media_blocks(css_code: str) -> list[dict]:
    """
    Returns list of { "query": str, "rules": {selector: {prop: value}} }
    for every @media block found.
    """
    blocks = []
    stylesheet = tinycss2.parse_stylesheet(css_code, skip_comments=True, skip_whitespace=True)

    for rule in stylesheet:
        if rule.type != "at-rule" or rule.lower_at_keyword != "media":
            continue

        query = tinycss2.serialize(rule.prelude).strip()

        inner_rules: dict[str, dict[str, str]] = {}
        inner = tinycss2.parse_stylesheet(
            tinycss2.serialize(rule.content),
            skip_comments=True,
            skip_whitespace=True,
        )
        for inner_rule in inner:
            if inner_rule.type != "qualified-rule":
                continue
            selector = tinycss2.serialize(inner_rule.prelude).strip()
            decls = tinycss2.parse_declaration_list(
                inner_rule.content, skip_comments=True, skip_whitespace=True
            )
            props: dict[str, str] = {}
            for d in decls:
                if d.type == "declaration":
                    props[d.lower_name] = tinycss2.serialize(d.value).strip()
            for sel in selector.split(","):
                sel = sel.strip()
                if sel:
                    inner_rules.setdefault(sel, {}).update(props)

        blocks.append({"query": query, "rules": inner_rules})

    return blocks


def _breakpoint_in_query(bp: str, query: str) -> bool:
    """True if the breakpoint value appears anywhere in the media query string."""
    return bp.replace(" ", "") in query.replace(" ", "")


def check_responsive(css_code: str, test_cases: list[dict]) -> dict:
    """
    Check that @media blocks contain expected property values at given breakpoints.

    Each test_case:
        { "breakpoint": "768px", "selector": "nav", "property": "flex-direction", "expected": "column", "points": 10 }

    selector is optional — if omitted, any rule in the matching media block is checked.

    Returns:
        { "score": int, "max_score": int, "results": [...] }
    """
    blocks = _parse_media_blocks(css_code)
    results = []
    score = 0
    max_score = 0

    for tc in test_cases:
        bp       = str(tc.get("breakpoint", "")).strip()
        selector = tc.get("selector", "").strip()
        prop     = tc.get("property", "").strip().lower()
        expected = str(tc.get("expected", "")).strip().lower()
        points   = int(tc.get("points", 0))
        max_score += points
        passed = False

        for block in blocks:
            if not _breakpoint_in_query(bp, block["query"]):
                continue

            # If selector specified, look it up directly; otherwise scan all rules
            candidates = (
                [block["rules"].get(selector, {})]
                if selector
                else block["rules"].values()
            )

            for rule_props in candidates:
                actual = rule_props.get(prop, "").strip().lower()
                if actual == expected:
                    passed = True
                    break

            if passed:
                break

        if passed:
            score += points

        results.append({
            "breakpoint": bp,
            "selector":   selector or "*",
            "property":   prop,
            "expected":   expected,
            "passed":     passed,
            "points":     points,
        })

    return {"score": score, "max_score": max(max_score, 1), "results": results}
