import { ExternalLink } from "lucide-react";
import type { SourceRef } from "../lib/types";

export function SourceList({ sources }: { sources: SourceRef[] }) {
  if (!sources.length) return null;
  return (
    <div className="space-y-3">
      {sources.map((source) => (
        <a
          key={`${source.provider}-${source.url}-${source.title}`}
          href={source.url || "#"}
          target="_blank"
          rel="noreferrer"
          className="flex items-start justify-between gap-4 border-b border-[#f6f1e7]/10 py-4 transition-colors hover:text-[#e6b45e]"
        >
          <span>
            <strong className="block text-sm text-[#f6f1e7]">{source.title}</strong>
            <small className="mt-1 block text-xs text-[#a7b8ae]">{source.provider} · {source.excerpt || "查看来源"}</small>
          </span>
          <ExternalLink aria-hidden="true" className="mt-1 h-4 w-4 shrink-0 text-[#e6b45e]" />
        </a>
      ))}
    </div>
  );
}
