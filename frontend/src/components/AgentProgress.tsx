import { LoaderCircle, Search, ShieldCheck, Sparkles } from "lucide-react";
import type { AgentEvent } from "../lib/types";

function description(event: AgentEvent): string {
  const data = event.data as any;
  if (event.event === "status") return String(data || "Agent 正在处理");
  if (event.event === "state") return "已建立结构化任务状态";
  if (event.event === "plan") return `计划调用：${data?.tool || "未知工具"}`;
  if (event.event === "tool_start") return `执行工具：${data?.tool || "未知工具"}`;
  if (event.event === "tool_result") return `工具返回：${data?.tool || "未知工具"} · ${data?.ok ? "通过校验" : "校验失败"}`;
  if (event.event === "validation") return data?.ok ? "结果校验通过" : `结果校验失败：${(data?.errors || []).join("、")}`;
  if (event.event === "evidence") return `记录 ${data?.items?.length || 0} 条证据`;
  if (event.event === "replan") return `触发重规划：${(data?.reason || []).join("、")}`;
  if (event.event === "message") return String(data || "");
  return "";
}

export function AgentProgress({ events, running }: { events: AgentEvent[]; running: boolean }) {
  const visible = events.map(description).filter(Boolean).slice(-8);
  if (!visible.length && !running) return null;
  return (
    <section className="glass-panel p-5" role="status" aria-live="polite" aria-busy={running}>
      <div className="mb-4 flex items-center gap-3 text-[#e6b45e]">
        <LoaderCircle aria-hidden="true" className={running ? "h-5 w-5 animate-spin" : "h-5 w-5"} />
        <span className="font-semibold">DeepSeek Agent 正在工作</span>
      </div>
      <div className="space-y-2 text-sm text-[#a7b8ae]">
        {visible.map((line, index) => (
          <div key={`${line}-${index}`} className="flex items-center gap-2">
            {line.includes("官网") ? <ShieldCheck className="h-4 w-4 text-[#e6b45e]" /> : line.includes("高德") ? <Search className="h-4 w-4 text-[#8fc3db]" /> : <Sparkles className="h-4 w-4 text-[#72c9a1]" />}
            <span>{line}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
