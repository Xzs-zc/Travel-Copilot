import { Mountain } from "lucide-react";

export function MountainArchiveLoader({ label = "山河档案", compact = false }: { label?: string; compact?: boolean }) {
  return (
    <div className={`archive-loader ${compact ? "archive-loader-compact" : ""}`} role="status" aria-live="polite">
      <div className="archive-loader-ring">
        <span className="archive-loader-sweep" />
        <span className="archive-loader-inner"><Mountain aria-hidden="true" /></span>
      </div>
      <span className="archive-loader-label">{label}</span>
    </div>
  );
}
