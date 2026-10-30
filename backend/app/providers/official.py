from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx

from .amap import ProviderError


class OfficialPageProvider:
    BLOCKED_DOMAINS = (
        "ctrip.com", "trip.com", "qunar.com", "meituan.com", "dianping.com",
        "fliggy.com", "taobao.com", "booking.com", "agoda.com", "expedia.com",
        "baike.baidu.com", "zhihu.com", "sohu.com", "sina.com.cn", "weibo.com",
        "xiaohongshu.com", "douyin.com", "toutiao.com", "163.com", "ifeng.com",
        "mafengwo.cn", "cncn.com",
    )
    CAPTCHA_MARKERS = ("验证码", "安全验证", "访问过于频繁", "请先登录", "登录后查看")
    DEFAULT_KEYWORDS = (
        "开放时间", "营业时间", "门票", "票价", "预约", "闭园", "限流",
        "地址", "电话", "参观须知", "购票须知",
    )

    def __init__(self) -> None:
        self._search_cache: dict[str, list[dict[str, str]]] = {}

    def validate_url(self, url: str) -> tuple[bool, str]:
        parsed = urlparse((url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False, "请输入有效的 http/https 官方网址"
        hostname = parsed.hostname.lower()
        if any(hostname == domain or hostname.endswith(f".{domain}") for domain in self.BLOCKED_DOMAINS):
            return False, "OTA 平台不能作为官方信息来源"
        return True, ""

    async def search_web(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        cache_key = f"{query}|{limit}"
        if cache_key in self._search_cache:
            return self._search_cache[cache_key]

        results = await self._search_bing_rss(query, limit)
        if results:
            self._search_cache[cache_key] = results
            return results

        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(locale="zh-CN")
            page = await context.new_page()
            try:
                await page.goto(f"https://cn.bing.com/search?q={quote_plus(query)}", wait_until="domcontentloaded", timeout=25000)
                items = await page.locator("li.b_algo h2 a").all()
                for item in items[:limit]:
                    title = (await item.inner_text()).strip()
                    href = await item.get_attribute("href")
                    if title and href:
                        results.append({"title": title, "url": href})
            finally:
                await context.close()
                await browser.close()
        self._search_cache[cache_key] = results
        return results

    @staticmethod
    async def _search_bing_rss(query: str, limit: int) -> list[dict[str, str]]:
        url = f"https://cn.bing.com/search?q={quote_plus(query)}&format=rss"
        try:
            async with httpx.AsyncClient(
                timeout=20,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
            root = ET.fromstring(response.text)
        except Exception:
            return []
        rows: list[dict[str, str]] = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if title and link:
                rows.append({"title": title, "url": link})
            if len(rows) >= limit:
                break
        return rows

    async def verify_many(self, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not targets:
            return []
        from playwright.async_api import async_playwright

        results: list[dict[str, Any]] = []
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(locale="zh-CN", viewport={"width": 1440, "height": 1000})
            await context.route("**/*", self._route_request)
            for target in targets[:6]:
                results.append(await self._extract_one(context, target))
            await context.close()
            await browser.close()
        return results

    @staticmethod
    async def _route_request(route) -> None:
        if route.request.resource_type in {"image", "font", "media"}:
            await route.abort()
        else:
            await route.continue_()

    async def _extract_one(self, context, target: dict[str, Any]) -> dict[str, Any]:
        name = str(target.get("name") or "")
        url = str(target.get("url") or "")
        keywords = tuple(target.get("keywords") or self.DEFAULT_KEYWORDS)
        allowed, reason = self.validate_url(url)
        if not allowed:
            return self._result(name, url, "pending", "", [], reason)
        page = await context.new_page()
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            if response and response.status >= 400:
                return self._result(name, page.url, "pending", await page.title(), [], f"官方页面返回 HTTP {response.status}")
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            await page.wait_for_timeout(700)
            title = await page.title()
            try:
                text = await page.locator("body").inner_text(timeout=7000)
            except Exception:
                text = await page.content()
            if any(marker in title or marker in text[:3000] for marker in self.CAPTCHA_MARKERS):
                return self._result(name, page.url, "pending", title, [], "页面要求登录或安全验证，已停止抓取")
            lines = self._relevant_lines(text, keywords)
            normalized_name = re.sub(r"[\s（）()·\-]", "", name).lower()
            page_text = re.sub(r"\s+", "", f"{title}\n{text}").lower()
            if normalized_name and normalized_name not in page_text:
                return self._result(name, page.url, "pending", title, lines, "页面名称与景区不一致，请人工确认")
            return self._result(name, page.url, "official" if lines else "pending", title, lines, "" if lines else "未提取到开放时间或票价信息")
        except Exception as exc:
            return self._result(name, url, "pending", "", [], f"官方页面抓取失败：{exc}")
        finally:
            await page.close()

    @staticmethod
    def _relevant_lines(text: str, keywords: tuple[str, ...]) -> list[str]:
        lines = []
        seen = set()
        for raw in re.split(r"[\r\n]+", text):
            line = re.sub(r"[ \t]+", " ", raw).strip()
            if 2 <= len(line) <= 260 and any(keyword in line for keyword in keywords) and line not in seen:
                seen.add(line)
                lines.append(line)
            if len(lines) >= 20:
                break
        return lines

    @staticmethod
    def _result(name: str, url: str, status: str, title: str, lines: list[str], error: str) -> dict[str, Any]:
        return {
            "name": name,
            "url": url,
            "status": status,
            "title": title[:200],
            "matches": lines,
            "error": error,
        }
