from __future__ import annotations

from enum import Enum


class FailureCause(str, Enum):
    RETRIEVAL_MISS = "retrieval_miss"
    RETRIEVAL_NOISE = "retrieval_noise"
    GENERATION_HALLUCINATION = "generation_hallucination"
    CITATION_ERROR = "citation_error"
    ROUTING_ERROR = "routing_error"
    TOOL_SELECTION_ERROR = "tool_selection_error"
    TOOL_ARGUMENT_ERROR = "tool_argument_error"
    POLICY_VIOLATION = "policy_violation"
    VALIDATOR_GAP = "validator_gap"
    ENVIRONMENT_ERROR = "environment_error"
    DATASET_ERROR = "dataset_error"
    DECISION_RULE_ERROR = "decision_rule_error"


CATEGORY_DEFAULT_CAUSE = {
    "transport_policy": FailureCause.POLICY_VIOLATION,
    "attraction_evidence": FailureCause.VALIDATOR_GAP,
    "agent_contract": FailureCause.VALIDATOR_GAP,
    "support_routing": FailureCause.ROUTING_ERROR,
    "knowledge_retrieval": FailureCause.RETRIEVAL_MISS,
    "safety_policy": FailureCause.POLICY_VIOLATION,
    "trajectory": FailureCause.TOOL_SELECTION_ERROR,
}
