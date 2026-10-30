import { FormEvent, useState } from "react";
import { Search } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { fetchJson } from "../lib/api";
import type { AttractionSummary, SearchResponse } from "../lib/types";

export function AttractionSearchBox({ province = "", defaultNationwide = false }: { province?: string; defaultNationwide?: boolean }) {
  const [query, setQuery] = useState("");
  const [nationwide, setNationwide] = useState(defaultNationwide);
  const [results, setResults] = useState<AttractionSummary[]>([]);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (!query.trim()) return;
    try {
      const params = new URLSearchParams({ q: query.trim(), province, nationwide: String(nationwide) });
      const data = await fetchJson<SearchResponse>(`/api/attractions/search?${params}`);
      if (data.direct_poi_id) {
        navigate(`/attraction/${data.direct_poi_id}`);
        return;
      }
      setResults(data.results);
      if (!data.results.length) setError("没有找到匹配景区");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "搜索失败");
    }
  }

  return (
    <div className="relative w-full max-w-xl">
      <form role="search" onSubmit={submit} className="flex items-center gap-2 rounded-full border border-[#f3ead8]/25 bg-[#061512]/62 p-2 shadow-[0_18px_46px_rgba(0,0,0,.26)] backdrop-blur-xl">
        <Search className="ml-2 h-5 w-5 text-[#d8b766]" aria-hidden="true" />
        <input aria-label="搜索景区" autoComplete="off" spellCheck={false} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={nationwide ? "搜索全国景区" : "搜索当前省份景区"} className="min-w-0 flex-1 bg-transparent px-2 py-2 text-[#f3ead8] outline-none placeholder:text-[#f3ead8]/40" />
        {province && <label className="hidden items-center gap-1 text-xs text-[#f3ead8]/55 md:flex"><input type="checkbox" checked={nationwide} onChange={(event) => setNationwide(event.target.checked)} /> 全国</label>}
        <button type="submit" className="rounded-full bg-[#d8b766] px-5 py-2 text-sm font-bold text-[#10231d] shadow-[0_8px_22px_rgba(216,183,102,.2)] transition-colors hover:bg-[#e5c97d]">搜索</button>
      </form>
      {(results.length > 0 || error) && (
        <div aria-live="polite" className="absolute left-0 right-0 top-full z-30 mt-2 overflow-hidden rounded-2xl border border-[#f3ead8]/18 bg-[#061512]/94 p-2 shadow-[0_22px_58px_rgba(0,0,0,.38)] backdrop-blur-xl">
          {error ? <p className="p-3 text-sm text-rose-300">{error}</p> : results.map((item) => (
            <button type="button" key={item.poi_id} onClick={() => navigate(`/attraction/${item.poi_id}`)} className="block w-full rounded-xl px-4 py-3 text-left transition-colors hover:bg-[#f3ead8]/8">
              <strong className="block text-sm text-[#f3ead8]">{item.name}</strong>
              <span className="mt-1 block text-xs text-[#f3ead8]/55">{item.province} · {item.city} · 高德评分 {item.rating || "暂无"}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
