# judge/frontend/watermark.py

import secrets
import io
from PIL import Image


def generate_token() -> str:
    """Returns an 8-char hex token. e.g. 'a3f9c2b1'"""
    return secrets.token_hex(4)


def _token_to_rgb(token: str) -> tuple[int, int, int]:
    """Deterministically derive an RGB colour from an 8-char hex token."""
    h = token.lower().replace(" ", "")[:8].ljust(8, "0")
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return r, g, b


def inject_watermark(html_code: str, token: str) -> str:
    """
    Injects a 1x1 fixed pixel into the HTML derived from the token.
    Returns the modified HTML string.
    """
    r, g, b = _token_to_rgb(token)
    wm = (
        f'<div id="__lx_wm" style="'
        f"position:fixed;bottom:0;right:0;"
        f"width:1px;height:1px;"
        f"background:rgb({r},{g},{b});"
        f'pointer-events:none;"></div>'
    )
    # Inject before </body> if present, otherwise append
    if "</body>" in html_code:
        return html_code.replace("</body>", f"{wm}</body>", 1)
    return html_code + wm


def verify_watermark(screenshot_bytes: bytes, token: str, tolerance: int = 5) -> bool:
    """
    Checks the bottom-right 1x1 pixel of the screenshot against the token colour.
    Returns True if within tolerance, False otherwise.
    """
    try:
        img = Image.open(io.BytesIO(screenshot_bytes)).convert("RGB")
        w, h = img.size
        actual_r, actual_g, actual_b = img.getpixel((w - 1, h - 1))
        exp_r, exp_g, exp_b = _token_to_rgb(token)
        return (
            abs(actual_r - exp_r) <= tolerance
            and abs(actual_g - exp_g) <= tolerance
            and abs(actual_b - exp_b) <= tolerance
        )
    except Exception:
        return False
