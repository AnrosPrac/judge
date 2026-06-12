# judge/frontend/css_checker.py

import re
import tinycss2
from typing import Any


# ─── Color normalisation ──────────────────────────────────────────────────────

def _hex_to_rgb(h: str) -> tuple[int, int, int] | None:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = h[0]*2 + h[1]*2 + h[2]*2
    if len(h) == 6:
        try:
            return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        except ValueError:
            return None
    return None


def _rgb_str_to_rgb(s: str) -> tuple[int, int, int] | None:
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", s.strip())
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def _normalise_color(value: str) -> tuple[int, int, int] | None:
    v = value.strip().lower()
    if v.startswith("#"):
        return _hex_to_rgb(v)
    if v.startswith("rgb"):
        return _rgb_str_to_rgb(v)
    # named colors — only the most common ones
    named = {
        "red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255),
        "white": (255, 255, 255), "black": (0, 0, 0), "yellow": (255, 255, 0),
        "orange": (255, 165, 0), "purple": (128, 0, 128), "pink": (255, 192, 203),
        "gray": (128, 128, 128), "grey": (128, 128, 128),
    }
    return named.get(v)


def _colors_match(expected: str, actual: str, tol: int = 10) -> bool:
    e = _normalise_color(expected)
    a = _normalise_color(actual)
    if e is None or a is None:
        # fall back to string match if we can't parse
        return expected.strip().lower() == actual.strip().lower()
    return all(abs(e[i] - a[i]) <= tol for i in range(3))


def _values_match(expected: str, actual: str, prop: str) -> bool:
    color_props = {
        "color", "background-color", "background", "border-color",
        "outline-color", "text-decoration-color",
    }
    if prop.lower() in color_props:
        return _colors_match(expected, actual)
    return expected.strip().lower() == actual.strip().lower()


# ─── CSS parser ───────────────────────────────────────────────────────────────

def _parse_rules(css_code: str) -> dict[str, dict[str, str]]:
    """
    Parse top-level qualified rules into {selector: {property: value}}.
    Ignores at-rules (handled separately in responsive_checker).
    """
    rules: dict[str, dict[str, str]] = {}
    stylesheet = tinycss2.parse_stylesheet(css_code, skip_comments=True, skip_whitespace=True)

    for rule in stylesheet:
        if rule.type != "qualified-rule":
            continue

        selector = tinycss2.serialize(rule.prelude).strip()
        declarations = tinycss2.parse_declaration_list(rule.content, skip_comments=True, skip_whitespace=True)

        props: dict[str, str] = {}
        for decl in declarations:
            if decl.type == "declaration":
                props[decl.lower_name] = tinycss2.serialize(decl.value).strip()

        # Multiple selectors in one rule (e.g. "h1, h2") — register each
        for sel in selector.split(","):
            sel = sel.strip()
            if sel:
                rules.setdefault(sel, {}).update(props)

    return rules


# ─── Public API ───────────────────────────────────────────────────────────────

def check_css(css_code: str, test_cases: list[dict]) -> dict:
    """
    Check CSS property values against expected values.

    Each test_case:
        { "selector": "button", "property": "background-color", "expected": "#ff0000", "points": 10 }

    Returns:
        { "score": int, "max_score": int, "results": [...] }
    """
    rules = _parse_rules(css_code)
    results = []
    score = 0
    max_score = 0

    for tc in test_cases:
        selector = tc.get("selector", "").strip()
        prop     = tc.get("property", "").strip().lower()
        expected = tc.get("expected", "").strip()
        points   = int(tc.get("points", 0))
        max_score += points

        actual = rules.get(selector, {}).get(prop)
        if actual is not None and _values_match(expected, actual, prop):
            passed = True
            score += points
        else:
            passed = False

        results.append({
            "selector": selector,
            "property": prop,
            "expected": expected,
            "actual":   actual,
            "passed":   passed,
            "points":   points,
        })

    return {"score": score, "max_score": max(max_score, 1), "results": results}
