# Evaluation Gate

- Run: `f4fbe94ac3a842b8`
- Baseline: `a3251b5640c84816`
- Result: **PASS**

| Rule | Status | Actual | Expected | Detail |
|---|---|---:|---:|---|
| dataset | pass | traveling-business-v2 | traveling-business-v2 |  |
| split | pass | dev | dev |  |
| overall_pass_rate | pass | 0.971 | 0.95 |  |
| critical_failures | pass | 0 | 0 |  |
| category:safety_policy | pass | 1.0 | 1.0 |  |
| category:agent_contract | pass | 1.0 | 0.98 |  |
| regression_vs_baseline | pass | 0 | 0 |  |
| latency_p95_ms | pass | 1 | 15000 |  |
| cost_usd_per_case | skip | 0.0 | None |  |
