from backend.app.observability import trace_value, usage_details


def test_trace_value_redacts_secrets_and_truncates():
    value = trace_value({"api_key": "secret", "text": "x" * 10, "items": list(range(50))})
    assert value["api_key"] == "<redacted>"
    assert len(value["text"]) < 20
    assert value["items"][-1] == "<truncated>"


def test_usage_details_maps_deepseek_tokens():
    value = usage_details({
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_cache_hit_tokens": 30,
    })
    assert value == {"input": 100, "output": 20, "total": 120, "input_cached": 30}
