from __future__ import annotations

import base64
import io
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from PIL import Image, ImageStat


def load_image_bytes(source: str, *, base_url: str = "") -> tuple[bytes, str]:
    path = Path(source)
    if path.is_file():
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        return path.read_bytes(), mime
    url = urljoin(base_url + "/", source.lstrip("/")) if source.startswith("/") else source
    response = httpx.get(url, timeout=30, follow_redirects=True, headers={"User-Agent": "TravelCopilot-Evaluation/1.0"})
    response.raise_for_status()
    return response.content, response.headers.get("content-type", "image/jpeg").split(";", 1)[0]


def analyze_image(data: bytes) -> dict[str, Any]:
    image = Image.open(io.BytesIO(data)).convert("RGB")
    grayscale = image.convert("L")
    stat = ImageStat.Stat(grayscale)
    small = grayscale.resize((9, 8))
    pixels = list(small.get_flattened_data() if hasattr(small, "get_flattened_data") else small.getdata())
    bits = 0
    position = 0
    for row in range(8):
        for column in range(8):
            bits |= (pixels[row * 9 + column] > pixels[row * 9 + column + 1]) << position
            position += 1
    return {
        "width": image.width,
        "height": image.height,
        "brightness": round(float(stat.mean[0]), 2),
        "contrast": round(float(stat.stddev[0]), 2),
        "dhash": f"{bits:016x}",
    }


def image_data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def hamming_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def unique_hashes(hashes: list[str], threshold: int = 5) -> int:
    unique: list[str] = []
    for value in hashes:
        if all(hamming_distance(value, existing) > threshold for existing in unique):
            unique.append(value)
    return len(unique)


def is_black(metrics: dict[str, Any]) -> bool:
    return float(metrics.get("brightness", 0)) < 5 and float(metrics.get("contrast", 0)) < 4
