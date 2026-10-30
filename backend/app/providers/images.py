from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from ..config import get_settings


class ImageCache:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or get_settings().media_dir
        self.directory.mkdir(parents=True, exist_ok=True)

    async def cache(self, url: str) -> str:
        if not url:
            return ""
        suffix = Path(url.split("?", 1)[0]).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            suffix = ".jpg"
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        target = self.directory / f"{digest}{suffix}"
        public_url = f"/media/{target.name}"
        if target.exists() and target.stat().st_size > 0:
            return public_url
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                response = await client.get(url, headers={"User-Agent": "Mozilla/5.0 TravelCopilot/1.0"})
                response.raise_for_status()
                if len(response.content) > 8 * 1024 * 1024:
                    return ""
                target.write_bytes(response.content)
        except Exception:
            return ""
        return public_url

    async def cache_many(self, urls: list[str], limit: int = 8) -> list[str]:
        results = []
        for url in urls[:limit]:
            cached = await self.cache(url)
            if cached:
                results.append(cached)
        return results

    def center_crop(self, public_url: str, min_width: int, min_height: int, target_ratio: float = 4 / 3) -> str:
        """Create a center-cropped landscape derivative when a source image does not fit gallery bounds."""
        if not public_url.startswith("/media/"):
            return ""
        source = self.directory / public_url.rsplit("/", 1)[-1]
        if not source.is_file():
            return ""
        target = source.with_name(f"{source.stem}-crop{source.suffix}")
        if target.exists() and target.stat().st_size > 0:
            return f"/media/{target.name}"
        try:
            from PIL import Image

            with Image.open(source) as image:
                image = image.convert("RGB")
                width, height = image.size
                if width <= 0 or height <= 0:
                    return ""
                current_ratio = width / height
                if current_ratio > target_ratio:
                    crop_width = max(1, int(height * target_ratio))
                    left = max(0, (width - crop_width) // 2)
                    image = image.crop((left, 0, left + crop_width, height))
                elif current_ratio < target_ratio:
                    crop_height = max(1, int(width / target_ratio))
                    top = max(0, (height - crop_height) // 2)
                    image = image.crop((0, top, width, top + crop_height))
                if image.width < min_width or image.height < min_height:
                    scale = max(min_width / image.width, min_height / image.height)
                    image = image.resize(
                        (max(min_width, int(image.width * scale)), max(min_height, int(image.height * scale))),
                        Image.Resampling.LANCZOS,
                    )
                if target.suffix.lower() in {".jpg", ".jpeg"}:
                    image.save(target, quality=90)
                else:
                    image.save(target)
        except Exception:
            return ""
        return f"/media/{target.name}"
