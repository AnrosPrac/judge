# judge/frontend/html_checker.py

from bs4 import BeautifulSoup


def _soup(html_code: str) -> BeautifulSoup:
    return BeautifulSoup(html_code, "html.parser")


def check_html_structure(html_code: str, test_cases: list[dict]) -> dict:
    """
    Check HTML structure against test cases.

    test_case types:
        exists    — { "type": "exists",    "selector": "button",           "points": 5 }
        attribute — { "type": "attribute", "selector": "img",   "attribute": "alt", "expected": "logo", "points": 5 }
        count     — { "type": "count",     "selector": "li",   "min_count": 3,                          "points": 5 }
        nesting   — { "type": "nesting",   "selector": "ul li",                                         "points": 5 }

    Returns:
        { "score": int, "max_score": int, "results": [...] }
    """
    soup = _soup(html_code)
    results = []
    score = 0
    max_score = 0

    for tc in test_cases:
        kind     = tc.get("type", "exists")
        selector = tc.get("selector", "").strip()
        points   = int(tc.get("points", 0))
        max_score += points
        passed = False

        try:
            elements = soup.select(selector)

            if kind == "exists":
                passed = len(elements) > 0

            elif kind == "attribute":
                attr     = tc.get("attribute", "")
                expected = tc.get("expected")
                for el in elements:
                    actual = el.get(attr)
                    if actual is not None:
                        if expected is None or str(actual).strip() == str(expected).strip():
                            passed = True
                            break

            elif kind == "count":
                min_count = int(tc.get("min_count", 1))
                passed = len(elements) >= min_count

            elif kind == "nesting":
                # selector like "ul li" — just check at least one match exists
                passed = len(elements) > 0

        except Exception:
            passed = False

        if passed:
            score += points

        results.append({
            "type":     kind,
            "selector": selector,
            "passed":   passed,
            "points":   points,
        })

    return {"score": score, "max_score": max(max_score, 1), "results": results}
