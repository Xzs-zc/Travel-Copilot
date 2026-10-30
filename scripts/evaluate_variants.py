#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_DIR / "data" / "evaluation_variants"
PYTHON = sys.executable
VARIANTS = ("full", "no_llm", "no_vision", "no_replan", "no_cache")


def wait_health(base_url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.time() + 60
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"变体服务提前退出，返回码 {process.returncode}")
        try:
            response = httpx.get(f"{base_url}/api/health", timeout=2)
            if response.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("等待变体服务健康检查超时")


def run_variant(variant: str, *, trials: int, variant_suite: str, live_smoke: bool) -> list[Path]:
    port = 8910 + VARIANTS.index(variant)
    base_url = f"http://127.0.0.1:{port}"
    root = DATA_DIR / variant
    root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "ABLATION_VARIANT": variant,
        "DATABASE_URL": f"sqlite+aiosqlite:///{root / 'travel.db'}",
        "MEDIA_DIR": str(root / "media"),
        "MCP_TRANSPORT": "stdio",
    })
    if variant == "no_llm":
        env["DEEPSEEK_API_KEY"] = "no-llm-disabled"
        env["DEEPSEEK_BASE_URL"] = "http://127.0.0.1:1"
        env["DEEPSEEK_MODEL"] = "deterministic-no-llm"
    if variant == "no_vision":
        env["VISION_API_KEY"] = ""
        env["VISION_MODEL"] = "metadata-no-vision"
    log_path = root / "server.log"
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=PROJECT_DIR,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    paths: list[Path] = []
    try:
        wait_health(base_url, process)
        suites = ["all" if variant == "full" else variant_suite]
        if live_smoke and variant != "full" and "live-smoke" not in suites:
            suites.append("live-smoke")
        for suite in suites:
            suite_trials = trials if suite != "live-smoke" else 1
            command = [
                PYTHON,
                "scripts/evaluate_agent.py",
                "--suite", suite,
                "--base-url", base_url,
                "--output-dir", str(root / "reports"),
                "--trials", str(suite_trials),
                "--variant", variant,
                "--baseline", str(root / "baseline.json"),
            ]
            if variant == "full" or suite != "live-smoke":
                command.append("--save-baseline")
            subprocess.run(command, cwd=PROJECT_DIR, env=env, check=False)
            report_root = root / "reports"
            reports = sorted(report_root.glob("*/report.html"))
            if reports:
                paths.append(reports[-1])
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    manifest = {
        "variant": variant,
        "port": port,
        "base_url": base_url,
        "server_pid": process.pid,
        "database_url": env["DATABASE_URL"],
        "media_dir": env["MEDIA_DIR"],
        "agent_runtime": "deterministic" if variant == "no_llm" else "langgraph",
        "vision_disabled": variant == "no_vision",
        "reports": [str(path) for path in paths],
    }
    (root / "isolation.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Travel Copilot evaluation variants in isolated processes")
    parser.add_argument("--variants", default=",".join(VARIANTS), help="逗号分隔的变体列表")
    parser.add_argument("--trials", type=int, default=3, help="每个场景独立执行次数")
    parser.add_argument("--variant-suite", choices=("offline", "live-smoke", "all"), default="all")
    parser.add_argument("--live-smoke", action="store_true", help="为每个消融变体额外跑一次在线关键冒烟")
    args = parser.parse_args()
    variant_list = [item.strip() for item in args.variants.split(",") if item.strip()]
    invalid = [item for item in variant_list if item not in VARIANTS]
    if invalid:
        raise SystemExit(f"未知变体：{', '.join(invalid)}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for variant in variant_list:
        print(f"\n=== variant={variant} ===", flush=True)
        paths = run_variant(variant, trials=max(1, args.trials), variant_suite=args.variant_suite, live_smoke=args.live_smoke)
        for path in paths:
            print(f"report={path}", flush=True)


if __name__ == "__main__":
    main()
