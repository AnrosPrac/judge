# judge/frontend/a11y_checker.py

from bs4 import BeautifulSoup, Tag


_MAX_SCORE = 15   # 4 checks, weighted below


def check_accessibility(html_code: str) -> dict:
    """
    Static accessibility checks using BeautifulSoup only (no browser).

    Checks (and point weights):
        img alt attributes       — 4 pts
        input label association  — 4 pts
        non-empty buttons        — 3 pts
        heading hierarchy        — 4 pts

    Returns:
        { "score": int, "max_score": 15, "issues": ["..."] }
    """
    soup = BeautifulSoup(html_code, "html.parser")
    issues: list[str] = []
    score = 0

    # ── 1. <img> alt attributes (4 pts) ──────────────────────────────────────
    imgs = soup.find_all("img")
    img_issues = []
    for img in imgs:
        if not img.get("alt") and img.get("alt") != "":
            # alt="" is valid (decorative), missing entirely is not
            src = img.get("src", "?")
            img_issues.append(f"<img src='{src}'> is missing an alt attribute")
    if not img_issues:
        score += 4
    else:
        issues.extend(img_issues)

    # ── 2. <input> label association (4 pts) ──────────────────────────────────
    inputs = soup.find_all("input", type=lambda t: t not in ("hidden", "submit", "button", "reset"))
    input_issues = []
    labeled_ids = {label.get("for") for label in soup.find_all("label") if label.get("for")}

    for inp in inputs:
        inp_id   = inp.get("id")
        has_label = (
            (inp_id and inp_id in labeled_ids)
            or inp.get("aria-label")
            or inp.get("aria-labelledby")
            or inp.find_parent("label") is not None
        )
        if not has_label:
            inp_type = inp.get("type", "text")
            input_issues.append(f"<input type='{inp_type}'> has no associated label or aria-label")
    if not input_issues:
        score += 4
    else:
        issues.extend(input_issues)

    # ── 3. Non-empty <button> elements (3 pts) ────────────────────────────────
    buttons = soup.find_all("button")
    button_issues = []
    for btn in buttons:
        text = btn.get_text(strip=True)
        has_aria = btn.get("aria-label") or btn.get("aria-labelledby")
        has_img  = btn.find("img") is not None
        if not text and not has_aria and not has_img:
            button_issues.append("<button> element is empty (no text, aria-label, or img)")
    if not button_issues:
        score += 3
    else:
        issues.extend(button_issues)

    # ── 4. Heading hierarchy (4 pts) ─────────────────────────────────────────
    headings = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
    heading_issues = []
    if headings:
        levels = [int(h.name[1]) for h in headings]
        prev = levels[0]
        for lvl in levels[1:]:
            if lvl > prev + 1:
                heading_issues.append(
                    f"Heading level skipped: h{prev} followed by h{lvl} (expected h{prev + 1})"
                )
            prev = lvl
    if not heading_issues:
        score += 4
    else:
        issues.extend(heading_issues)

    return {"score": score, "max_score": _MAX_SCORE, "issues": issues}
