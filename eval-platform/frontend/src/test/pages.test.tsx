import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ComparePage } from "../pages/ComparePage";
import { LabelingPage } from "../pages/LabelingPage";
import { TraceViewerPage } from "../pages/TraceViewerPage";

function response(payload: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    })
  );
}

function run(id: string, variant: string) {
  return {
    id,
    dataset_id: "traveling-business-v2",
    name: id,
    variant,
    system_config_id: variant,
    kind: "executed",
    seed: 42,
    trials: 1,
    deterministic: false,
    status: "completed",
    baseline_run_id: null,
    metrics: {
      pass_rate: variant === "candidate-current" ? 100 : 80,
      passed: 10,
      total: 10,
      critical_failures: 0,
      p95_duration_ms: 10,
      categories: {}
    },
    root_causes: {},
    config: {},
    started_at: "2026-11-07T00:00:00+00:00",
    duration_ms: 10,
    error: ""
  };
}

describe("evaluation pages", () => {
  it("renders significance in Compare", async () => {
    const base = run("base", "baseline-no-rag");
    const candidate = run("candidate", "candidate-current");
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/runs") return response({ items: [candidate, base] });
      return response({
        base,
        candidate,
        summary: {
          fixed: 2,
          regressions: 0,
          stable_pass: 8,
          stable_fail: 0,
          pass_rate_delta: 20,
          mcnemar_b: 0,
          mcnemar_c: 2,
          statistics: {
            sample_size: 10,
            warning: "样本量小于 30，统计结论仅作参考",
            available: true,
            reason: "",
            mcnemar_p_value: 0.5,
            ci_lower: 0.1,
            ci_upper: 0.3,
            significant: false
          }
        },
        cases: []
      });
    }));
    render(<ComparePage />);
    expect(await screen.findByText("不显著")).toBeInTheDocument();
    expect(screen.getByText("样本量小于 30，统计结论仅作参考")).toBeInTheDocument();
  });

  it("renders a real trace timeline", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      await response({
        case: {
          case_key: "CASE-1",
          name: "trace case",
          trace_id: "trace-1",
          status: "passed",
          duration_ms: 20
        },
        spans: [
          {
            span_id: "root",
            name: "root span",
            span_type: "agent",
            status: "passed",
            start_ms: 0,
            duration_ms: 20,
            input: {},
            output: {},
            metadata: {},
            error_text: "",
            tokens_in: 0,
            tokens_out: 0,
            cost_usd: null,
            model: null
          }
        ]
      })
    ));
    render(<TraceViewerPage runId="run" caseKey="CASE-1" />);
    expect((await screen.findAllByText("root span")).length).toBeGreaterThan(0);
  });

  it("submits a Labeling score", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      if (String(input).startsWith("/api/labeling/items")) {
        return response({
          items: [{
            run_id: "run",
            run_name: "run",
            case_key: "CASE-1",
            name: "case",
            status: "passed",
            question: "question",
            answer: "answer",
            citations: [],
            retrieved: [],
            gold_sections: [],
            failures: []
          }]
        });
      }
      return response({ ok: true });
    }));
    render(<LabelingPage />);
    await screen.findByText("question");
    await user.click(screen.getAllByRole("button", { name: "5" })[0]);
    await user.click(screen.getByRole("button", { name: "保存并继续" }));
    await waitFor(() => expect(screen.getByText("已保存，进入下一条")).toBeInTheDocument());
  });
});
