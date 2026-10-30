from __future__ import annotations

import json
import re
import io
from PIL import Image
from pathlib import Path

from openai import AsyncOpenAI

from ..config import get_settings
from .images import image_data_url


class VisionUnavailable(RuntimeError):
    pass


async def assess_image_match(name: str, city: str, data: bytes, mime: str) -> dict[str, object]:
    settings = get_settings()
    if not settings.vision_api_key:
        raise VisionUnavailable("未配置 VISION_API_KEY，无法执行图片语义匹配")
    client = AsyncOpenAI(
        api_key=settings.vision_api_key,
        base_url=settings.vision_base_url,
        timeout=60,
        max_retries=1,
    )
    prompt = (
        "你是旅行图片质量评审。判断图片是否真实、合理地代表指定景点。"
        f"景点：{name}；城市：{city}。只返回 JSON："
        '{"score": 0-100, "match": true/false, "reason": "一句话理由"}。'
        "若为地图、Logo、无关人物或明显另一景点，score 必须低于 40。"
    )
    try:
        response = await client.chat.completions.create(
            model=settings.vision_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url(data, mime)}},
                ],
            }],
        )
        content = response.choices[0].message.content or "{}"
        usage = response.usage.model_dump() if response.usage else {}
    finally:
        await client.close()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.S)
        payload = json.loads(match.group(0)) if match else {"score": 0, "match": False, "reason": content[:200]}
    score = max(0, min(100, int(payload.get("score") or 0)))
    return {
        "score": score,
        "match": bool(payload.get("match")) and score >= 70,
        "reason": str(payload.get("reason") or ""),
        "usage": usage,
    }


async def review_gallery(name: str, city: str, images: list[tuple[bytes, str]]) -> dict[str, object]:
    settings = get_settings()
    if not settings.vision_api_key:
        raise VisionUnavailable("未配置 VISION_API_KEY，无法审核画册图片")
    if not images:
        return {"keep": [], "duplicate_groups": [], "reason": "no_images"}
    thumbnails: list[str] = []
    for data, mime in images[:8]:
        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
            image.thumbnail((512, 512))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=82)
        thumbnails.append(image_data_url(buffer.getvalue(), "image/jpeg"))
    prompt = (
        "你是旅行画廊审核员。检查这些候选图片是否真实、合理地代表指定景点，"
        "是否互相重复或高度相似，以及是否具有代表性。"
        f"景点：{name}；城市：{city}。"
        '只返回 JSON：{"keep": [0,1], "duplicate_groups": [[0,2]], "reason": "一句话理由"}。'
        "keep 是保留图片的原始索引，按推荐顺序排列，最多保留 6 张；不确定时优先保留。"
    )
    client = AsyncOpenAI(
        api_key=settings.vision_api_key,
        base_url=settings.vision_base_url,
        timeout=60,
        max_retries=1,
    )
    try:
        response = await client.chat.completions.create(
            model=settings.vision_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    *[{"type": "image_url", "image_url": {"url": url}} for url in thumbnails],
                ],
            }],
        )
        content = response.choices[0].message.content or "{}"
        usage = response.usage.model_dump() if response.usage else {}
    finally:
        await client.close()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.S)
        payload = json.loads(match.group(0)) if match else {}
    keep = []
    for index in payload.get("keep") or []:
        try:
            value = int(index)
        except (TypeError, ValueError):
            continue
        if 0 <= value < len(thumbnails) and value not in keep:
            keep.append(value)
    return {
        "keep": keep,
        "duplicate_groups": payload.get("duplicate_groups") or [],
        "reason": str(payload.get("reason") or ""),
        "usage": usage,
    }
