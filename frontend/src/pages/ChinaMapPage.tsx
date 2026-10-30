import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Compass, MapPinned } from "lucide-react";
import { AttractionSearchBox } from "../components/AttractionSearchBox";
import { MountainArchiveLoader } from "../components/MountainArchiveLoader";
import { useAmap } from "../hooks/useAmap";
import { fetchJson } from "../lib/api";
import { getRandomMapBackground, rememberMapBackground } from "../lib/mapBackground";
import type { ProvinceSummary } from "../lib/types";

type ProvinceBoundary = ProvinceSummary & { ring: number[][] };


export function ChinaMapPage() {
  const container = useRef<HTMLDivElement>(null);
  const [mapReady, setMapReady] = useState(false);
  const landscapeImage = useMemo(() => getRandomMapBackground(), []);
  const navigate = useNavigate();
  const { AMap, error } = useAmap();
  useEffect(() => rememberMapBackground(landscapeImage), [landscapeImage]);

  const boundariesQuery = useQuery({
    queryKey: ["china-province-boundaries"],
    queryFn: () => fetchJson<ProvinceBoundary[]>("/data/china-provinces.json"),
    staleTime: 7 * 24 * 60 * 60 * 1000,
  });

  useEffect(() => {
    if (!AMap || !container.current || !boundariesQuery.data) return;
    const map = new AMap.Map(container.current, {
      zoom: 4.1,
      center: [104.5, 35.5],
      mapStyle: "amap://styles/normal",
      showLabel: false,
      features: [],
    });
    map.setLayers([]);
    map.addControl(new AMap.Scale());
    const polygons: any[] = [];
    const timers: number[] = [];
    const later = (callback: () => void, delay: number) => {
      const timer = window.setTimeout(callback, delay);
      timers.push(timer);
    };
    boundariesQuery.data.forEach((province, index) => {
      if (!province.ring?.length) return;
      const polygon = new AMap.Polygon({
        path: province.ring,
        strokeColor: "rgba(246,241,231,.82)",
        strokeWeight: 1.25,
        strokeOpacity: 0.96,
        fillColor: "#f6f1e7",
        fillOpacity: 0.01,
        cursor: "pointer",
        bubble: true,
      });
      polygon.on("mouseover", () => polygon.setOptions({ strokeColor: "#e6b45e", strokeWeight: 1.7, fillOpacity: 0.08 }));
      polygon.on("mouseout", () => polygon.setOptions({ strokeColor: "rgba(246,241,231,.82)", strokeWeight: 1.25, fillOpacity: 0.01 }));
      polygon.on("click", () => navigate(`/province/${province.adcode}`, { state: { name: province.name } }));
      map.add(polygon);
      polygons.push(polygon);
      if (province.center) {
        const point = province.center.split(",").map(Number);
        if (point.length === 2 && point.every(Number.isFinite)) {
          const shortName = province.name.replace(/省|市|壮族自治区|回族自治区|维吾尔自治区|特别行政区/g, "");
          const pointMarker = new AMap.Marker({
            position: point,
            anchor: "center",
            zIndex: 155,
            content: `<span class="map-point-wrap"><span class="map-point"></span><span class="map-point-label">${shortName}</span></span>`,
          });
          pointMarker.on("click", () => navigate(`/province/${province.adcode}`, { state: { name: province.name } }));
          map.add(pointMarker);
        }
      }
    });
    if (polygons.length) {
      map.setFitView(polygons, true, [45, 45, 85, 45], 5.2);
      later(() => {
        const baseZoom = map.getZoom();
        map.setZoom(Math.min(baseZoom + 0.3, 5.2));
        later(() => {
          const bounds = map.getBounds();
          const points = boundariesQuery.data.flatMap((province) => province.ring || []);
          if (!points.length) return;
          const lngs = points.map((point) => point[0]);
          const lats = points.map((point) => point[1]);
          const corners = [
            new AMap.LngLat(Math.min(...lngs), Math.max(...lats)),
            new AMap.LngLat(Math.max(...lngs), Math.min(...lats)),
          ];
          if (corners.some((point) => !bounds.contains(point))) map.setZoom(baseZoom);
        }, 220);
      }, 180);
    }
    later(() => setMapReady(true), 80);
    return () => {
      timers.forEach((timer) => window.clearTimeout(timer));
      map.destroy();
    };
  }, [AMap, boundariesQuery.data, navigate]);

  return (
    <main className="relative min-h-screen overflow-hidden bg-[#061512] text-[#f3ead8]">
      <div className="absolute inset-0 bg-cover bg-center" style={{ backgroundImage: `url("${landscapeImage}")` }} />
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(180deg,rgba(4,16,14,.30),rgba(4,16,14,.06)_42%,rgba(4,16,14,.38)),radial-gradient(circle_at_50%_44%,transparent_28%,rgba(3,12,11,.28)_100%)]" />
      <div className={`china-map-layer absolute inset-0 ${mapReady ? "is-ready" : ""}`}><div ref={container} className="h-full w-full" /></div>
      <header className="pointer-events-none relative z-10 flex items-start justify-between p-8 md:p-12">
        <div className="pointer-events-auto">
          <p className="mb-2 flex items-center gap-2 text-xs font-semibold tracking-[0.12em] text-[#d8b766]"><Compass aria-hidden="true" className="h-4 w-4" /> Travel Copilot</p>
          <h1 className="font-serif text-4xl tracking-[0.08em] md:text-6xl">山河行迹</h1>
          <p className="mt-4 max-w-xl text-sm leading-7 text-[#f3ead8]/58">循高德所识，向山河而行。点击一个省份，看看那里最值得抵达的七处风景。</p>
        </div>
      </header>
      {boundariesQuery.isLoading && <div className="pointer-events-none absolute left-1/2 top-1/2 z-20 -translate-x-1/2 -translate-y-1/2 rounded-full border border-[#f3ead8]/22 bg-[#061512]/70 px-5 py-3 shadow-[0_16px_36px_rgba(0,0,0,.28)] backdrop-blur-xl"><MountainArchiveLoader compact label="正在展开中国版图" /></div>}
      <section className="pointer-events-auto absolute bottom-10 left-1/2 z-20 flex w-full -translate-x-1/2 flex-col items-center px-6">
        <AttractionSearchBox defaultNationwide />
        <div className="mt-4 flex items-center gap-2 text-xs text-[#f3ead8]/48"><MapPinned className="h-4 w-4" /> 输入景区名称，可直接跨越省份进入景区页</div>
        {error && <p className="mt-4 rounded-xl bg-rose-950/70 px-4 py-2 text-sm text-rose-200">{error}</p>}
        {boundariesQuery.error && <p className="mt-4 rounded-xl bg-rose-950/70 px-4 py-2 text-sm text-rose-200">{boundariesQuery.error.message}</p>}
      </section>
    </main>
  );
}
