from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class SystemConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    description: str
    rag_mode: Literal["off", "lexical", "hybrid"]
    policy_gate: bool
    validators: Literal["off", "basic", "strict"]
    prompt_version: str
    model: str

    def as_record(self) -> dict[str, object]:
        return self.model_dump(mode="json")


BUILTIN_SYSTEM_CONFIGS: dict[str, SystemConfig] = {
    "baseline-no-rag": SystemConfig(
        id="baseline-no-rag",
        name="Baseline · 无 RAG",
        description="关闭知识检索，其余业务规则与当前配置一致。",
        rag_mode="off",
        policy_gate=True,
        validators="strict",
        prompt_version="traveling-current-v1",
        model="deepseek-chat",
    ),
    "baseline-basic-validators": SystemConfig(
        id="baseline-basic-validators",
        name="Baseline · 基础校验",
        description="仅检查输入与结果的基本结构，不执行严格契约校验。",
        rag_mode="hybrid",
        policy_gate=True,
        validators="basic",
        prompt_version="traveling-current-v1",
        model="deepseek-chat",
    ),
    "candidate-current": SystemConfig(
        id="candidate-current",
        name="Candidate · 当前配置",
        description="当前 Traveling 的严格校验、安全边界与混合检索配置。",
        rag_mode="hybrid",
        policy_gate=True,
        validators="strict",
        prompt_version="traveling-current-v1",
        model="deepseek-chat",
    ),
}

SYSTEM_CONFIG_IDS = tuple(BUILTIN_SYSTEM_CONFIGS)
