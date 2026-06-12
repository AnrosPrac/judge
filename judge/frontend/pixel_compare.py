# judge/frontend/pixel_compare.py

import io
import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity as ssim_fn
from skimage.metrics import peak_signal_noise_ratio as psnr_fn

_TARGET_SIZE = (1280, 720)
_PIXEL_TOLERANCE = 10   # per-channel delta considered "matching"


def _load(img_bytes: bytes) -> np.ndarray:
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img = img.resize(_TARGET_SIZE, Image.LANCZOS)
        return np.array(img)
    except Exception as e:
        raise ValueError(f"Invalid or corrupt image data: {e}") from e


def compare_images(reference: bytes, student: bytes) -> dict:
    """
    Compare two PNG images as raw bytes.

    Returns:
        {
            "ssim":      float  — structural similarity 0.0–1.0
            "psnr":      float  — peak signal-to-noise ratio in dB (99.9 if identical)
            "match_pct": float  — % of pixels within per-channel tolerance
            "verdict":   "pass" | "warn" | "fail"
        }
    """
    ref = _load(reference)
    stu = _load(student)

    # SSIM — channel-wise then averaged
    ssim_val = float(ssim_fn(ref, stu, channel_axis=2, data_range=255))

    # PSNR — cap at 99.9 for identical images to avoid inf
    try:
        psnr_val = float(psnr_fn(ref, stu, data_range=255))
        if psnr_val == float("inf"):
            psnr_val = 99.9
    except Exception:
        psnr_val = 99.9

    # Pixel match % — all channels within tolerance
    diff = np.abs(ref.astype(int) - stu.astype(int))
    match_pct = float(np.mean(np.all(diff <= _PIXEL_TOLERANCE, axis=2)) * 100)

    if ssim_val >= 0.95 and match_pct >= 90:
        verdict = "pass"
    elif ssim_val >= 0.80:
        verdict = "warn"
    else:
        verdict = "fail"

    return {
        "ssim":      round(ssim_val, 4),
        "psnr":      round(psnr_val, 2),
        "match_pct": round(match_pct, 2),
        "verdict":   verdict,
    }
