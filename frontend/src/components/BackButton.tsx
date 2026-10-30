import { ArrowLeft } from "lucide-react";
import { useNavigate } from "react-router-dom";

export function BackButton({ fallback = "/", variant = "dark" }: { fallback?: string; variant?: "light" | "dark" }) {
  const navigate = useNavigate();
  const className = variant === "dark"
    ? "border-[#f3ead8]/25 bg-[#061512]/52 text-[#f3ead8]/78 hover:bg-[#061512]/72 hover:text-[#f3ead8]"
    : "border-white/75 bg-white/65 text-[#4f5d57] hover:bg-white/85 hover:text-[#1f2825]";
  return (
    <button type="button" aria-label="返回上一页" onClick={() => navigate(fallback)} className={`inline-flex min-h-11 items-center gap-2 rounded-full border px-4 py-2 text-sm shadow-[0_10px_28px_rgba(62,77,71,.1)] backdrop-blur-xl transition-colors ${className}`}>
      <ArrowLeft aria-hidden="true" className="h-4 w-4" />
      返回
    </button>
  );
}
