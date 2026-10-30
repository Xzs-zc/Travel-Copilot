import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { CalendarDays, Clock3, Compass, ExternalLink, MapPin, Phone, Ticket } from "lucide-react";
import { BackButton } from "../components/BackButton";
import { MountainArchiveLoader } from "../components/MountainArchiveLoader";
import { SourceList } from "../components/SourceList";
import { useAgentStream } from "../hooks/useAgentStream";
import { useTravelStore } from "../lib/store";
import type { AttractionDetail } from "../lib/types";

export function AttractionPage() {
  const { poiId = "" } = useParams();
  const navigate = useNavigate();
  const setAttraction = useTravelStore((state) => state.setAttraction);
  const { data, error } = useAgentStream<AttractionDetail>(`/api/attractions/${poiId}/stream`);

  useEffect(() => {
    if (!data) return;
    setAttraction({ poiId: data.poi_id, name: data.name, city: data.city, heroImage: data.hero_image });
    data.photos.slice(0, 6).forEach((url) => {
      const image = new Image();
      image.decoding = "async";
      image.src = url;
    });
  }, [data, setAttraction]);

  if (!data) {
    return (
      <main className="min-h-screen bg-[#07110f] px-5 py-8 text-[#f6f1e7]">
        <BackButton />
        <div className="mx-auto mt-28 max-w-3xl">
          <div className="flex min-h-[300px] flex-col items-center justify-center text-center">
            <MountainArchiveLoader label="正在翻阅山河档案" />
            {error && <p className="mt-8 rounded-2xl border border-rose-300/20 bg-rose-950/50 p-4 text-rose-200">{error}</p>}
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[#07110f] text-[#f6f1e7]">
      <section className="relative min-h-[72vh] overflow-hidden">
        {data.hero_image ? (
          <>
            <img src={data.hero_image} alt={`${data.name}风景`} width={2400} height={1600} loading="eager" fetchPriority="high" decoding="async" className="absolute inset-0 h-full w-full object-cover object-center" />
            <div className="absolute inset-0 bg-white/5" />
          </>
        ) : (
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_25%_30%,#9cc8d1,transparent_35%),radial-gradient(circle_at_78%_25%,#d8a184,transparent_32%),linear-gradient(135deg,#eef4f2,#f7f4ed)]" />
        )}
        <div className="absolute inset-0 bg-gradient-to-t from-[#07110f] via-[#07110f]/55 to-black/15" />
        <header className="relative z-10 flex items-center justify-between p-5 md:p-10">
          <BackButton />
          <span className={`rounded-full border px-4 py-2 text-xs font-bold backdrop-blur-xl ${data.open_status === "official" ? "border-white/40 bg-emerald-200/20 text-emerald-50" : "border-white/40 bg-white/20 text-white"}`}>
            {data.open_status === "official" ? "官网信息已核验" : "高德信息 · 待官方确认"}
          </span>
        </header>
        <div className="relative z-10 mx-auto flex min-h-[58vh] max-w-6xl items-end px-6 pb-12 md:px-10">
          <div>
            <p className="mb-3 text-xs font-semibold tracking-[0.12em] text-[#e6b45e]">{data.province} · {data.city}</p>
            <h1 className="font-serif text-5xl tracking-[0.05em] md:text-7xl">{data.name}</h1>
            <p className="mt-5 max-w-3xl text-base leading-8 text-white/70">{data.summary}</p>
          </div>
        </div>
      </section>

      <section className="mx-auto grid max-w-6xl gap-8 px-6 py-12 md:grid-cols-[1fr_340px] md:px-10">
        <div className="space-y-10">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <InfoCard icon={Clock3} label="开放时间" value={data.open_time || "待官方确认"} />
            <InfoCard icon={Ticket} label="参考票价" value={data.ticket_price || (data.reference_cost ? `约 ${data.reference_cost} 元` : "待官方确认")} />
            <InfoCard icon={MapPin} label="地址" value={data.address || "高德暂无地址"} />
            <InfoCard icon={Phone} label="电话" value={data.telephone || "暂无"} />
          </div>

          {!!data.photos.length && (
            <section>
              <div className="mb-5 flex items-end justify-between">
                <div><p className="text-xs font-semibold tracking-[0.12em] text-[#e6b45e]">景区影像</p><h2 className="mt-2 font-serif text-3xl">山河实景</h2></div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {data.photos.slice(0, 6).map((image, index) => (
                  <img key={`${image}-${index}`} src={image} alt="景区高清图片" width={1200} height={800} loading={index < 3 ? "eager" : "lazy"} fetchPriority={index === 0 ? "high" : "auto"} decoding="async" className="h-48 w-full rounded-2xl border border-white/10 object-cover shadow-[0_16px_36px_rgba(0,0,0,.22)]" />
                ))}
              </div>
            </section>
          )}

          <section>
            <div className="mb-5 rounded-2xl border border-[#e6b45e]/18 bg-[linear-gradient(135deg,rgba(230,180,94,.10),rgba(255,255,255,.035))] px-5 py-4 text-sm leading-7 text-[#d7ded8] shadow-[0_14px_32px_rgba(0,0,0,.14)] backdrop-blur-xl">
              <span className="mr-2 font-semibold text-[#e6b45e]">小提示</span>
              页面信息核验需要一丁点时间，小大人可以先去下方小红书、知乎或右边高德链接瞅瞅哦
            </div>
            <h2 className="font-serif text-3xl">游览提示</h2>
            <div className="mt-5 space-y-3">
              {data.travel_tips.map((tip) => <p key={tip} className="rounded-2xl border border-white/10 bg-white/[0.045] px-5 py-4 text-sm leading-7 text-[#a7b8ae] shadow-[0_14px_32px_rgba(0,0,0,.16)] backdrop-blur-xl">{tip}</p>)}
              {!data.travel_tips.length && <p className="text-sm text-[#78847f]">暂无额外提示，请以官网最新公告为准。</p>}
            </div>
          </section>

          <section>
            <h2 className="font-serif text-3xl">查看网友攻略</h2>
            <p className="mt-2 text-sm text-[#78847f]">只跳转到平台搜索页，不抓取社区正文。</p>
            <div className="mt-5 flex flex-wrap gap-3">
              {data.guide_links.map((link) => (
                <a key={link.platform} href={link.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 rounded-full border border-white/12 bg-white/[0.04] px-5 py-3 text-sm text-[#d7ded8] backdrop-blur-xl transition-colors hover:border-[#e6b45e]/50 hover:bg-white/10 hover:text-[#e6b45e]">
                  {link.platform}<ExternalLink className="h-4 w-4" />
                </a>
              ))}
            </div>
          </section>
        </div>

        <aside className="space-y-6">
          <div className="glass-panel sticky top-6 p-6">
            <Compass aria-hidden="true" className="h-7 w-7 text-[#e6b45e]" />
            <h2 className="mt-4 font-serif text-2xl">山河已定，何时出发？</h2>
            <p className="mt-2 text-sm leading-6 text-[#a7b8ae]">选择日期，DeepSeek 会调用实时交通工具，为你比较高铁、火车与驾车方案。</p>
            <button type="button" onClick={() => navigate(`/plan/${data.poi_id}`)} className="mt-6 flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#e6b45e] px-5 py-4 font-bold text-[#10231d] shadow-[0_12px_28px_rgba(230,180,94,.22)] transition-colors hover:bg-[#f0c875]">
              <CalendarDays aria-hidden="true" className="h-5 w-5" />确定行程日期
            </button>
            <div className="mt-5 grid grid-cols-2 gap-3 text-xs text-[#a7b8ae]">
              <span className="rounded-xl bg-white/5 p-3">高德评分 {data.rating || "暂无"}</span>
              <span className="rounded-xl bg-white/5 p-3">{data.city || "中国"}</span>
            </div>
          </div>
          <SourceList sources={data.sources} />
          {data.official_url && (
            <a href={data.official_url} target="_blank" rel="noreferrer" className="flex items-center justify-between rounded-2xl border border-[#72c9a1]/20 bg-[#72c9a1]/5 p-4 text-sm text-[#9ee0bf]">
              查看景区官方网站<ExternalLink className="h-4 w-4" />
            </a>
          )}
        </aside>
      </section>

    </main>
  );
}

function InfoCard({ icon: Icon, label, value }: { icon: typeof Clock3; label: string; value: string }) {
  return (
    <div className="border-t border-white/12 py-5">
      <Icon aria-hidden="true" className="h-5 w-5 text-[#e6b45e]" />
      <span className="mt-4 block text-xs text-[#78847f]">{label}</span>
      <strong className="mt-2 block text-sm leading-6 text-[#e8eee9]">{value}</strong>
    </div>
  );
}
