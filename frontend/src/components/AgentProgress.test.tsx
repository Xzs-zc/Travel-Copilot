import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentProgress } from "./AgentProgress";

describe("AgentProgress", () => {
  it("renders status and tool events", () => {
    render(
      <AgentProgress
        running
        events={[
          { event: "status", data: "DeepSeek 正在分析任务", step: 0, run_id: "1" },
          { event: "tool_start", data: { tool: "query_train" }, step: 1, run_id: "1" },
        ]}
      />,
    );
    expect(screen.getByText("DeepSeek 正在分析任务")).toBeInTheDocument();
    expect(screen.getByText("执行工具：query_train")).toBeInTheDocument();
  });
});
