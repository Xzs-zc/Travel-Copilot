from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from .official import OfficialPageProvider


class OfficialImageProvider:
    """Extract representative image URLs from public official tourism pages.

    The provider does not bypass login, captcha, paywalls or anti-bot controls.
    It only reads public pages returned by a targeted official-site search.
    """

    BLOCKED_IMAGE_PARTS = (
        "logo", "icon", "avatar", "qr", "qrcode", "banner", "advert",
        "wechat", "weibo", "share", "loading", "placeholder",
        "home", "nav", "btn", "button", "menu", "sprite", "arrow",
        "search", "footer", "index", "top", "bottom",
    )
    IMAGE_ATTRS = ("src", "data-src", "data-original", "data-lazy-src", "data-url")

    def __init__(self, search: OfficialPageProvider | None = None):
        self.search = search or OfficialPageProvider()

    async def find_for_attraction(
        self,
        name: str,
        city: str = "",
        official_url: str = "",
        limit: int = 6,
        page_limit: int = 3,
    ) -> list[dict[str, str]]:
        pages = await self._official_pages(name, city, official_url, page_limit)
        results: list[dict[str, str]] = []
        for page in pages:
            for image_url in await self._extract_image_urls(page["url"]):
                results.append({
                    "url": image_url,
                    "page_url": page["url"],
                    "provider": "官方文旅/景区网站",
                    "title": page["title"],
                })
                if len(results) >= limit:
                    return results[:limit]
        return results[:limit]

    async def _official_pages(self, name: str, city: str, official_url: str, limit: int) -> list[dict[str, str]]:
        candidates: list[dict[str, str]] = []
        if official_url:
            candidates.append({"title": f"{name}官方网站", "url": official_url})
        city_key = re.sub(r"(省|市|县|区)$", "", city) if city else ""
        core_name = name[len(city_key):].strip() if city_key and name.startswith(city_key) else name
        queries = [
            f"{name} 官网 site:gov.cn",
            f"{core_name} site:gov.cn",
            f"{name} 官方网站 景区 site:gov.cn",
            f"{name} 文化和旅游局 site:gov.cn",
            f"{name} 文旅局 官网 site:gov.cn",
            f"{name} 文化和旅游局 官方网站 site:gov.cn",
            f"{name} 人民政府 景区 site:gov.cn",
            f"{city} {name} 景区官网 site:gov.cn" if city else "",
            f"{city} 文化和旅游局 {core_name} site:gov.cn" if city else "",
        ]
        for query in queries:
            if not query:
                continue
            try:
                candidates.extend(await self.search.search_web(query, limit=6))
            except Exception:
                continue
        unique: dict[str, dict[str, str]] = {}
        for item in candidates:
            url = str(item.get("url") or "").strip()
            allowed, _ = self.search.validate_url(url)
            if not url or not allowed or not self._is_official_page(url, official_url):
                continue
            unique.setdefault(url, {"title": str(item.get("title") or name), "url": url})
        relevant = [item for item in unique.values() if self._is_relevant_page(item, name, city)]
        ranked = sorted(relevant, key=lambda item: self._official_score(item["url"], name, city), reverse=True)
        return ranked[:limit]

    @staticmethod
    def _name_tokens(name: str) -> list[str]:
        tokens = [name]
        simplified = re.sub(r"(特别行政区|自治区|省|市|县|区|风景区|景区|名胜区|国家地质公园|国家公园|博物馆|旅游|文化|和|与)", "", name)
        if simplified and simplified != name:
            tokens.append(simplified)
        for marker in ("风景名胜区", "风景区", "国家地质公园", "国家公园", "景区"):
            if marker in name:
                tokens.append(name.split(marker)[0][-6:])
                tokens.append(name.split(marker)[0][-4:])
        for size in (4, 3, 2):
            if len(simplified) >= size:
                tokens.append(simplified[:size])
                tokens.append(simplified[-size:])
        return list(dict.fromkeys(token for token in tokens if len(token) >= 2))

    @classmethod
    def _is_relevant_page(cls, item: dict[str, str], name: str, city: str) -> bool:
        title = str(item.get("title") or "")
        url = str(item.get("url") or "")
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        text = f"{title} {url}"
        if name and name in text:
            return True
        if not path:
            return False
        return any(token in text for token in cls._name_tokens(name))

    @staticmethod
    def _is_official_page(url: str, official_url: str = "") -> bool:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if official_url:
            official_host = (urlparse(official_url).hostname or "").lower()
            if host == official_host:
                return True
        return host.endswith(".gov.cn") or host.endswith(".gov") or ".gov." in host

    @staticmethod
    def _official_score(url: str, name: str, city: str) -> int:
        host = (urlparse(url).hostname or "").lower()
        text = f"{host}{url}".lower()
        score = 0
        if host.endswith(".gov.cn") or ".gov." in host:
            score += 100
        if any(part in host for part in ("gov", "wenlv", "travel", "tour", "ly")):
            score += 35
        if any(part and part.lower() in text for part in (name, city)):
            score += 20
        return score

    async def _extract_image_urls(self, url: str) -> list[str]:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(locale="zh-CN", viewport={"width": 1440, "height": 1000})
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=4000)
                except Exception:
                    pass
                title = (await page.title()).strip()
                body = (await page.locator("body").inner_text(timeout=5000))[:2500]
                if any(marker in title or marker in body for marker in ("验证码", "安全验证", "请先登录", "访问过于频繁")):
                    return []
                urls = await page.evaluate(
                    r"""(attrs) => {
                        const out = [];
                        const push = (value) => {
                            if (value && typeof value === 'string') out.push(value);
                        };
                        const meta = document.querySelector('meta[property="og:image"],meta[name="og:image"]');
                        if (meta) push(meta.content);
                        document.querySelectorAll('img').forEach((img) => {
                            attrs.forEach((attr) => push(img.getAttribute(attr)));
                            push(img.getAttribute('srcset'));
                        });
                        document.querySelectorAll('source').forEach((source) => push(source.getAttribute('srcset')));
                        document.querySelectorAll('*').forEach((el) => {
                            const bg = getComputedStyle(el).backgroundImage;
                            if (!bg || bg === 'none') return;
                            const match = bg.match(/url\(["']?(.*?)["']?\)/);
                            if (match) push(match[1]);
                        });
                        document.querySelectorAll('[data-bg],[data-background]').forEach((el) => {
                            push(el.getAttribute('data-bg'));
                            push(el.getAttribute('data-background'));
                        });
                        return out;
                    }""",
                    list(self.IMAGE_ATTRS),
                )
            except Exception:
                return []
            finally:
                await context.close()
                await browser.close()

        resolved: list[str] = []
        for raw in urls:
            for part in str(raw).split(","):
                candidate = part.strip().split(" ")[0].strip()
                if candidate.startswith("url("):
                    match = re.match(r'url\(["\']?(.*?)["\']?\)$', candidate)
                    candidate = match.group(1) if match else ""
                if not candidate:
                    continue
                if candidate.startswith(("data:", "blob:", "javascript:")):
                    continue
                absolute = urljoin(url, candidate)
                parsed = urlparse(absolute)
                if parsed.scheme not in {"http", "https"}:
                    continue
                path = parsed.path.lower()
                if not re.search(r"\.(jpg|jpeg|png|webp)(?:$|\?)", path + (("?" + parsed.query) if parsed.query else "")):
                    if any(part in path for part in ("/upload", "/uploads", "/images", "/image", "/photo")):
                        pass
                    else:
                        continue
                if any(part in path for part in self.BLOCKED_IMAGE_PARTS):
                    continue
                resolved.append(absolute)
        return list(dict.fromkeys(resolved))[:30]
