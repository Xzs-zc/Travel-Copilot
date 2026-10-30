import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { CalendarDays, CarFront, Clock3, Cloud, CloudFog, CloudRain, CloudSun, MapPin, Plane, Snowflake, Sparkles, Sun, Ticket, Wind } from "lucide-react";
import { AgentProgress } from "../components/AgentProgress";
import { BackButton } from "../components/BackButton";
import { SourceList } from "../components/SourceList";
import { useAgentStream } from "../hooks/useAgentStream";
import { useAmap } from "../hooks/useAmap";
import { sseUrl } from "../lib/api";
import { useTravelStore } from "../lib/store";
import type { FlightPeriod, RouteMapInfo, TicketPeriod, TransportComparison, TransportPlan, TransportRecommendation } from "../lib/types";

export function PlanPage() {
  const { poiId = "" } = useParams();
  const attraction = useTravelStore((state) => state.selectedAttraction);
  const sessionId = useTravelStore((state) => state.sessionId);
  const setSessionId = useTravelStore((state) => state.setSessionId);
  const today = useMemo(() => new Date().toISOString().slice(0, 10), []);
  const defaultTravelDate = useMemo(() => {
    const date = new Date();
    date.setDate(date.getDate() + 1);
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  }, []);
  const [origin, setOrigin] = useState("");
  const [travelDate, setTravelDate] = useState(defaultTravelDate);
  const [days, setDays] = useState("");
  const [streamUrl, setStreamUrl] = useState<string | null>(null);
  const transport = useAgentStream<TransportPlan>(streamUrl);

  useEffect(() => {
    if (transport.data?.session_id) setSessionId(transport.data.session_id);
  }, [transport.data, setSessionId]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!attraction || !origin.trim()) return;
    setStreamUrl(sseUrl("/api/transport/stream", {
      origin,
      destination: attraction.city || attraction.name,
      travel_date: travelDate || undefined,
      poi_id: poiId,
      days: days || undefined,
      session_id: sessionId,
    }));
  }

  if (!attraction) {
    return (
      <main className="grid min-h-screen place-items-center bg-[#07110f] p-6 text-[#f6f1e7]">
        <div className="glass-panel max-w-lg p-8 text-center">
          <h1 className="font-serif text-3xl">请先从景区页进入</h1>
          <p className="mt-3 text-[#a7b8ae]">交通规划需要景区目的地信息。</p>
          <BackButton fallback="/" />
        </div>
      </main>
    );
  }

  const data = transport.data;
  const recommendation = data?.recommendation || ({ mode: "mixed", summary: "", reason: "" } satisfies TransportRecommendation);
  const ticketPeriods = data?.ticket_periods || [];
  const comparison = data?.transport_comparison || ({
    train_price: 0,
    flight_price: 0,
    train_duration_minutes: 0,
    flight_duration_minutes: 0,
    price_gap: 0,
    duration_gap_minutes: 0,
    flight_available: false,
  } satisfies TransportComparison);
  const flightPeriods = data?.flight_periods || [];
  const flightComparisonAllowed = Boolean(data?.flight_comparison_allowed);
  const weatherAdvice = data?.weather_advice || { summary: "", suggested_dates: [], best_date: "" };
  const caveats = data?.caveats || [];
  const showDrivingPlan = data?.show_driving_plan ?? true;
  const weather = data?.weather || {};
  const casts = (weather.casts || []).slice(0, data?.days || Number(days) || 3);
  const blessingText = data?.blessing?.text || "愿君此行，山河相迎。";

  return (
    <main className="min-h-screen bg-[#07110f] text-[#f6f1e7]">
      <header className="border-b border-white/10 bg-black/20 px-6 py-6 backdrop-blur-xl md:px-10">
        <div className="mx-auto flex max-w-6xl items-start justify-between gap-6">
          <div>
            <BackButton fallback={`/attraction/${poiId}`} />
            <p className="mt-6 text-xs font-semibold tracking-[0.12em] text-[#e6b45e]">出行方案</p>
            <h1 className="mt-2 font-serif text-4xl md:text-5xl">择一日，向{attraction.name}而去</h1>
          </div>
          <div className="hidden max-w-md border-l border-white/15 pl-5 text-xs leading-6 text-[#a7b8ae] md:block">
            由 Agent 根据距离、高铁耗时、航班价格和天气选择推荐方案；不展示具体车次、预订链接或付款入口。
          </div>
        </div>
      </header>

      <section className="mx-auto max-w-6xl px-6 py-10 md:px-10">
        <form onSubmit={submit} className="grid gap-5 border-b border-white/10 pb-8 md:grid-cols-4">
          <label className="text-xs text-[#a7b8ae]">出发地<input value={origin} onChange={(event) => setOrigin(event.target.value)} required autoComplete="address-level2" placeholder="请输入省、市、县，例如：上海市浦东新区" className="mt-2 w-full rounded-xl border border-white/12 bg-black/20 px-4 py-3 text-[#f6f1e7] outline-none backdrop-blur-xl placeholder:text-[#78847f] focus:border-[#e6b45e]/60" /></label>
          <label className="text-xs text-[#a7b8ae]">出发日期（选填）<input type="date" min={today} value={travelDate} onChange={(event) => setTravelDate(event.target.value)} autoComplete="off" className="mt-2 w-full rounded-xl border border-white/12 bg-black/20 px-4 py-3 text-[#f6f1e7] outline-none backdrop-blur-xl focus:border-[#e6b45e]/60" /></label>
          <label className="text-xs text-[#a7b8ae]">旅行天数（选填）<input type="number" min={1} max={14} inputMode="numeric" value={days} onChange={(event) => setDays(event.target.value)} placeholder="不填则默认按 3 天评估" className="mt-2 w-full rounded-xl border border-white/12 bg-black/20 px-4 py-3 text-[#f6f1e7] outline-none backdrop-blur-xl placeholder:text-[#78847f] focus:border-[#e6b45e]/60" /></label>
          <button type="submit" className="min-h-12 self-end rounded-xl bg-[#e6b45e] px-5 py-3 font-bold text-[#10231d] shadow-[0_12px_28px_rgba(230,180,94,.2)] transition-colors hover:bg-[#f0c875]">查询出行方案</button>
        </form>

        <div className="mt-8">
          <AgentProgress events={transport.events} running={transport.running} />
          {transport.error && <p className="mt-5 rounded-2xl border border-red-400/20 bg-red-950/40 p-4 text-red-200">{transport.error}</p>}
        </div>

        {data && (
          <section className="mt-10 space-y-10">
            <RecommendationCard
              recommendation={recommendation}
              dateIsRecommended={Boolean(data.date_is_recommended)}
              assumedDays={Boolean(data.assumed_days)}
              travelDate={data.travel_date}
              days={data.days || Number(days) || 3}
            />

            {data.route && (
              <div className={`grid gap-6 ${showDrivingPlan ? "lg:grid-cols-[1.25fr_.75fr]" : ""}`}>
                <RouteMapPanel route={data.route} showDrivingPath={showDrivingPlan} />
                {showDrivingPlan && <DrivingSummary route={data.route} recommended={Boolean(data.driving_recommended)} />}
              </div>
            )}

            <section>
              <div className="mb-5 flex items-end justify-between gap-6 border-b border-white/12 pb-4">
                <div>
                  <p className="text-xs font-semibold tracking-[0.12em] text-[#e6b45e]">12306 余票时段</p>
                  <h2 className="mt-2 font-serif text-3xl">哪些时间出发更从容</h2>
                </div>
                <p className="hidden max-w-md text-xs leading-5 text-[#a7b8ae] md:block">只展示当天有余票的时间段，不展示具体车次、余票数量或购票链接。</p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {ticketPeriods.map((period) => <TicketPeriodCard key={period.label} period={period} />)}
                {!ticketPeriods.length && <p className="py-8 text-sm text-[#78847f]">当天并无余票，可尝试调整日期或选择其他交通方式。</p>}
              </div>
            </section>

            <TransportComparisonSection
              comparison={comparison}
              flightPeriods={flightPeriods}
              flightComparisonAllowed={flightComparisonAllowed}
              recommendation={recommendation}
            />

            {!!caveats.length && (
              <div className="border-l-2 border-[#e6b45e]/60 pl-5 text-sm leading-7 text-[#e6b45e]/82">
                {caveats.map((item) => <p key={item}>{item}</p>)}
              </div>
            )}

            <section className="grid gap-6 lg:grid-cols-[1.15fr_.85fr]">
              <div className="overflow-hidden rounded-2xl border border-white/12 bg-white/[0.035] shadow-[0_18px_46px_rgba(0,0,0,.2)] backdrop-blur-xl">
                <div className="flex items-center justify-between border-b border-white/12 px-6 py-5">
                  <div><p className="text-xs font-semibold tracking-[0.12em] text-[#8fc3db]">旅行天气</p><h2 className="mt-2 font-serif text-2xl">旅行期间天气</h2></div>
                  <span className="text-xs text-[#78847f]">{weather.report_time || "高德实时天气"}</span>
                </div>
                <div className="grid gap-3 p-5 sm:grid-cols-2">
                  {casts.map((cast) => (
                    <article key={cast.date} className={`weather-card ${weatherTone(cast.dayweather || "")}`}>
                      {attraction.heroImage && <img src={attraction.heroImage} alt="" aria-hidden="true" width={800} height={600} loading="lazy" decoding="async" />}
                      <div className="weather-card-overlay" />
                      <WeatherIcon condition={cast.dayweather || ""} />
                      <div className="relative z-10 mt-3">
                        <strong>{cast.date?.slice(5)} · {cast.dayweather}</strong>
                        <p>{cast.nighttemp}° ~ {cast.daytemp}°</p>
                        <small>{cast.daywind || ""} {cast.daypower ? `${cast.daypower}级` : ""}</small>
                      </div>
                    </article>
                  ))}
                  {!casts.length && <p className="col-span-full py-8 text-center text-sm text-[#78847f]">高德暂未返回该日期范围的天气，请临近出发再次确认。</p>}
                </div>
                {weatherAdvice.summary && (
                  <div className="border-t border-white/12 px-6 py-5 text-sm leading-7 text-[#a7b8ae]">
                    <p>{weatherAdvice.summary}</p>
                    {!!weatherAdvice.suggested_dates.length && (
                      <p className="mt-2 text-xs text-[#8fc3db]/70">
                        建议日期：{weatherAdvice.suggested_dates.join("、")}
                      </p>
                    )}
                  </div>
                )}
              </div>

              <div className="relative overflow-hidden rounded-2xl border border-white/12 bg-[radial-gradient(circle_at_20%_20%,rgba(230,180,94,.17),transparent_38%),linear-gradient(135deg,rgba(16,37,31,.82),rgba(7,20,17,.72))] p-8 text-center shadow-[0_18px_46px_rgba(0,0,0,.24),inset_0_1px_0_rgba(255,255,255,.07)] backdrop-blur-xl md:p-10">
                <Sparkles aria-hidden="true" className="mx-auto h-7 w-7 text-[#e6b45e]" />
                <p className="mx-auto mt-6 font-serif text-2xl leading-[1.9] tracking-[0.08em] text-[#f6f1e7] md:text-3xl">{blessingText}</p>
              </div>
            </section>
          </section>
        )}
      </section>
    </main>
  );
}

function RecommendationCard({
  recommendation,
  dateIsRecommended,
  assumedDays,
  travelDate,
  days,
}: {
  recommendation: TransportRecommendation;
  dateIsRecommended: boolean;
  assumedDays: boolean;
  travelDate: string;
  days: number;
}) {
  const labels: Record<TransportRecommendation["mode"], string> = {
    drive: "驾车优先",
    high_speed_rail: "高铁优先",
    flight: "飞机优先",
    high_speed_rail_or_flight: "高铁 / 飞机比较",
    mixed: "综合方案",
  };
  return (
    <section className="rounded-2xl border border-[#e6b45e]/22 bg-[linear-gradient(150deg,rgba(230,180,94,.12),rgba(16,37,31,.7))] p-6 shadow-[0_20px_52px_rgba(0,0,0,.24),inset_0_1px_0_rgba(255,255,255,.07)] backdrop-blur-xl md:p-8">
      <p className="text-xs font-semibold tracking-[0.12em] text-[#e6b45e]">Agent 交通建议</p>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="font-serif text-3xl">{labels[recommendation.mode]}</h2>
          {recommendation.summary && <p className="mt-3 max-w-3xl text-sm leading-7 text-[#a7b8ae]">{recommendation.summary}</p>}
          {recommendation.reason && <p className="mt-2 max-w-3xl text-xs leading-6 text-[#e6b45e]/75">{recommendation.reason}</p>}
        </div>
        <div className="grid gap-2 text-xs text-[#a7b8ae]">
          <span>计划日期：{travelDate}{dateIsRecommended ? "（Agent 推荐）" : ""}</span>
          <span>旅行天数：{days} 天{assumedDays ? "（默认评估）" : ""}</span>
        </div>
      </div>
    </section>
  );
}

function TransportComparisonSection({
  comparison,
  flightPeriods,
  flightComparisonAllowed,
  recommendation,
}: {
  comparison: TransportComparison;
  flightPeriods: FlightPeriod[];
  flightComparisonAllowed: boolean;
  recommendation: TransportRecommendation;
}) {
  if (!flightPeriods.length && !comparison.train_duration_minutes) return null;
  return (
    <section>
      <div className="mb-5 border-b border-white/12 pb-4">
        <p className="text-xs font-semibold tracking-[0.12em] text-[#e6b45e]">交通对比</p>
        <h2 className="mt-2 font-serif text-3xl">高铁与飞机怎么选</h2>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <article className="rounded-2xl border border-[#72c9a1]/16 bg-[#72c9a1]/[0.05] p-6 backdrop-blur-xl">
          <Ticket className="h-6 w-6 text-[#72c9a1]" />
          <p className="mt-5 text-xs text-[#a7b8ae]">高铁参考</p>
          <strong className="mt-2 block font-serif text-2xl">
            {comparison.train_price ? `¥${Math.round(comparison.train_price)} 起` : "票价待确认"}
          </strong>
          <p className="mt-2 text-sm text-[#a7b8ae]">最短约 {formatMinutes(comparison.train_duration_minutes)}</p>
        </article>
        <article className="rounded-2xl border border-[#8fc3db]/16 bg-[#8fc3db]/[0.05] p-6 backdrop-blur-xl">
          <Plane className="h-6 w-6 text-[#8fc3db]" />
          <p className="mt-5 text-xs text-[#a7b8ae]">飞机参考</p>
          <strong className="mt-2 block font-serif text-2xl">
            {comparison.flight_price ? `¥${Math.round(comparison.flight_price)} 起` : "暂未取得航班报价"}
          </strong>
          <p className="mt-2 text-sm text-[#a7b8ae]">最短约 {formatMinutes(comparison.flight_duration_minutes)}</p>
        </article>
      </div>
      <div className="mt-4 rounded-2xl border border-white/10 bg-white/[0.035] p-5 text-sm leading-7 text-[#a7b8ae] backdrop-blur-xl">
        <p>{recommendation.summary}</p>
        {!!comparison.price_gap && <p>两者参考价差约 ¥{Math.round(comparison.price_gap)}。</p>}
        {!!comparison.duration_gap_minutes && <p>耗时差约 {formatMinutes(comparison.duration_gap_minutes)}。</p>}
      </div>
      {!!flightPeriods.length ? (
        <div className="mt-5 grid gap-3 lg:grid-cols-3">
          {flightPeriods.map((period) => (
            <article key={period.label} className="rounded-2xl border border-[#8fc3db]/16 bg-[#8fc3db]/[0.05] p-5 backdrop-blur-xl">
              <div className="flex items-center justify-between gap-3 text-xs text-[#8fc3db]">
                <span>有余票时段</span>
                <span>{period.stops ? `${period.stops} 次转机起` : "直飞可选"}</span>
              </div>
              <h3 className="mt-4 font-serif text-2xl">{period.label}</h3>
              <p className="mt-2 text-sm text-[#a7b8ae]">{period.summary}</p>
              <div className="mt-4 flex flex-wrap gap-3 text-xs text-[#78847f]">
                {!!period.min_price && <span>参考价 ¥{Math.round(period.min_price)} 起</span>}
                {!!period.duration_minutes && <span>最短约 {formatMinutes(period.duration_minutes)}</span>}
              </div>
            </article>
          ))}
        </div>
      ) : flightComparisonAllowed ? (
        <p className="mt-5 rounded-2xl border border-white/10 bg-white/[0.035] p-5 text-sm text-[#78847f] backdrop-blur-xl">
          当天暂未查到有余票的航班。
        </p>
      ) : null}
    </section>
  );
}

function RouteMapPanel({ route, showDrivingPath }: { route: RouteMapInfo; showDrivingPath: boolean }) {
  const container = useRef<HTMLDivElement>(null);
  const { AMap, error } = useAmap();

  useEffect(() => {
    if (!AMap || !container.current) return;
    const map = new AMap.Map(container.current, { zoom: 7, mapStyle: "amap://styles/darkblue", showLabel: true });
    const origin = route.origin_location.split(",").map(Number);
    const destination = route.destination_location.split(",").map(Number);
    const overlays: any[] = [];
    const labelStyle = {
      "background-color": "transparent",
      border: "none",
      "border-radius": "0",
      "box-shadow": "none",
      color: "#f6f1e7",
      "font-size": "15px",
      "font-weight": "700",
      padding: "0",
      "text-shadow": "0 2px 8px rgba(0,0,0,.92)",
      "white-space": "nowrap",
    };
    if (origin.length === 2 && origin.every(Number.isFinite)) {
      overlays.push(new AMap.Text({
        text: `出发 ${placeName(route.origin_name, route.origin_city, route.origin_province, "出发地")}`,
        position: new AMap.LngLat(origin[0], origin[1]),
        anchor: "bottom-center",
        offset: new AMap.Pixel(0, -8),
        zIndex: 200,
        style: labelStyle,
      }));
    }
    if (destination.length === 2 && destination.every(Number.isFinite)) {
      overlays.push(new AMap.Text({
        text: `到达 ${placeName(route.destination_name, route.destination_city, route.destination_province, "目的地")}`,
        position: new AMap.LngLat(destination[0], destination[1]),
        anchor: "bottom-center",
        offset: new AMap.Pixel(0, -8),
        zIndex: 200,
        style: labelStyle,
      }));
    }
    if (showDrivingPath && route.path?.length > 1) overlays.push(new AMap.Polyline({ path: route.path, strokeColor: "#e6b45e", strokeWeight: 6, strokeOpacity: .9, lineJoin: "round" }));
    overlays.forEach((overlay) => map.add(overlay));
    const fitRoute = () => {
      if (overlays.length) map.setFitView(overlays, true, [80, 80, 80, 80], 10);
    };
    map.on("complete", fitRoute);
    fitRoute();
    return () => {
      map.off("complete", fitRoute);
      map.destroy();
    };
  }, [AMap, route, showDrivingPath]);

  return (
    <div className="relative min-h-[380px] overflow-hidden rounded-2xl border border-white/12 shadow-[0_20px_52px_rgba(0,0,0,.24)]">
      <div className="absolute inset-0"><div ref={container} className="h-full w-full" /></div>
      <div className="pointer-events-none absolute left-5 top-5 rounded-xl border border-white/12 bg-[#07110f]/78 px-4 py-3 shadow-[0_14px_34px_rgba(0,0,0,.22)] backdrop-blur-xl">
        <p className="text-xs text-[#a7b8ae]">地图距离</p>
        <strong className="mt-1 block font-serif text-2xl">{formatDistance(route.distance_m)}</strong>
      </div>
      {error && <p className="absolute bottom-4 left-4 right-4 rounded-xl bg-rose-950/80 p-3 text-sm text-rose-200 backdrop-blur-xl">{error}</p>}
    </div>
  );
}

function DrivingSummary({ route, recommended }: { route: RouteMapInfo; recommended: boolean }) {
  return (
    <section className="rounded-2xl border border-white/12 bg-white/[0.035] p-6 shadow-[0_18px_46px_rgba(0,0,0,.18)] backdrop-blur-xl">
      <CarFront aria-hidden="true" className="h-8 w-8 text-[#e6b45e]" />
      <p className="mt-6 text-xs font-semibold tracking-[0.12em] text-[#a7b8ae]">{recommended ? "推荐驾车方案" : "驾车备选方案"}</p>
      <h2 className="mt-2 font-serif text-3xl">{route.origin_name || "出发地"} → {route.destination_name || "目的地"}</h2>
      {(route.origin_province || route.origin_city || route.origin_district) && (
        <p className="mt-3 text-xs leading-6 text-[#a7b8ae]">识别出发地：{[route.origin_province, route.origin_city, route.origin_district].filter(Boolean).join(" · ")}</p>
      )}
      {(route.destination_province || route.destination_city || route.destination_district) && (
        <p className="mt-1 text-xs leading-6 text-[#a7b8ae]">识别目的地：{[route.destination_province, route.destination_city, route.destination_district].filter(Boolean).join(" · ")}</p>
      )}
      <dl className="mt-6 grid grid-cols-2 gap-4">
        <div className="border-t border-white/12 pt-4"><dt className="text-xs text-[#a7b8ae]">距离</dt><dd className="mt-2 text-2xl font-semibold">{formatDistance(route.distance_m)}</dd></div>
        <div className="border-t border-white/12 pt-4"><dt className="text-xs text-[#a7b8ae]">预计耗时</dt><dd className="mt-2 text-2xl font-semibold">{formatDuration(route.duration_s)}</dd></div>
        <div className="border-t border-white/12 pt-4"><dt className="text-xs text-[#a7b8ae]">预估过路费</dt><dd className="mt-2 text-2xl font-semibold">{route.tolls ? `${Math.round(route.tolls)} 元` : "暂无"}</dd></div>
      </dl>
      <div className="mt-7"><SourceList sources={[route.source]} /></div>
    </section>
  );
}

function TicketPeriodCard({ period }: { period: TicketPeriod }) {
  return (
    <article className="rounded-2xl border border-[#e6b45e]/22 bg-[#e6b45e]/[0.07] p-5 text-[#f0d28d]">
      <div className="flex items-center justify-between gap-4">
        <Ticket aria-hidden="true" className="h-5 w-5" />
        <span className="rounded-full border border-current/25 px-3 py-1 text-xs font-bold">有余票</span>
      </div>
      <h3 className="mt-5 font-serif text-2xl">{period.label}</h3>
      <p className="mt-3 text-sm leading-6 opacity-80">该时段有余票</p>
      <div className="mt-4 flex flex-wrap gap-3 text-xs opacity-70">
        {!!period.min_price && <span>最低参考价 ¥{Math.round(period.min_price)}</span>}
        {!!period.duration_minutes && <span>最短约 {formatMinutes(period.duration_minutes)}</span>}
      </div>
    </article>
  );
}

function formatDistance(distanceM: number): string {
  return distanceM ? `${(distanceM / 1000).toFixed(1)} 公里` : "暂无";
}

function placeName(value: string | undefined, city: string | undefined, province: string | undefined, fallback: string): string {
  const text = String(value || "").trim();
  if (text && !/^-?\d+(?:\.\d+)?,\s*-?\d+(?:\.\d+)?$/.test(text)) return text;
  return city || province || fallback;
}

function formatDuration(seconds: number): string {
  if (!seconds) return "暂无";
  const minutes = Math.round(seconds / 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分` : `${minutes} 分钟`;
}

function formatMinutes(minutes: number): string {
  if (!minutes) return "暂无";
  return minutes >= 60 ? `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分` : `${minutes} 分钟`;
}

function weatherTone(condition: string): string {
  if (condition.includes("雨")) return "from-sky-950/90 to-blue-900/50";
  if (condition.includes("雪")) return "from-slate-800/90 to-cyan-950/60";
  if (condition.includes("雾") || condition.includes("霾")) return "from-stone-900/90 to-slate-700/50";
  if (condition.includes("风")) return "from-teal-950/90 to-emerald-900/50";
  return "from-amber-950/80 to-emerald-950/70";
}

function WeatherIcon({ condition }: { condition: string }) {
  const className = "relative z-10 h-8 w-8 text-[#e6b45e]";
  const props = { "aria-hidden": true as const };
  if (condition.includes("雨")) return <CloudRain {...props} className={className} />;
  if (condition.includes("雪")) return <Snowflake {...props} className={className} />;
  if (condition.includes("雾") || condition.includes("霾")) return <CloudFog {...props} className={className} />;
  if (condition.includes("风")) return <Wind {...props} className={className} />;
  if (condition.includes("云")) return <CloudSun {...props} className={className} />;
  if (condition.includes("阴")) return <Cloud {...props} className={className} />;
  return <Sun {...props} className={className} />;
}
