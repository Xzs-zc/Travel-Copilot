import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { MapPin, Sparkles } from "lucide-react";
import { AttractionSearchBox } from "../components/AttractionSearchBox";
import { BackButton } from "../components/BackButton";
import { MountainArchiveLoader } from "../components/MountainArchiveLoader";
import { useAmap } from "../hooks/useAmap";
import { fetchJson, prepareAttraction } from "../lib/api";
import { useTravelStore } from "../lib/store";
import { getInitialMapBackground, preloadMapBackground, rememberMapBackground } from "../lib/mapBackground";
import type { AttractionSummary, ProvinceSummary } from "../lib/types";


const LABEL_OFFSETS = [
  [0, -42], [62, -34], [78, -8], [62, 20],
  [0, 34], [-62, 20], [-78, -8], [-62, -34],
  [0, -72], [88, -58], [108, -10], [88, 38],
  [0, 60], [-88, 38], [-108, -10], [-88, -58],
];

function overlaps(a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }) {
  return Math.abs(a.x - b.x) < (a.w + b.w) / 2 + 6 && Math.abs(a.y - b.y) < (a.h + b.h) / 2 + 6;
}

export function ProvincePage() {
  const { adcode = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const mapRef = useRef<HTMLDivElement>(null);
  const user = useTravelStore((state) => state.user);
  const [hoveredAttraction, setHoveredAttraction] = useState<AttractionSummary | null>(null);
  const [backgroundLoading, setBackgroundLoading] = useState(false);
  const [mapReady, setMapReady] = useState(false);
  const [backgroundImage, setBackgroundImage] = useState(() => getInitialMapBackground());
  const [provinceBackground, setProvinceBackground] = useState("");
  const { AMap, error: mapError } = useAmap();
  const routeName = (location.state as { name?: string } | null)?.name || "";
  const provincesQuery = useQuery({
    queryKey: ["provinces"],
    queryFn: () => fetchJson<{ provinces: ProvinceSummary[] }>("/api/map/provinces"),
    staleTime: 24 * 60 * 60 * 1000,
  });
  const provinceName = routeName || provincesQuery.data?.provinces.find((item) => item.adcode === adcode)?.name || adcode;
  const attractionsQuery = useQuery({
    queryKey: ["province-attractions", adcode, provinceName, user?.style_preference || "guest"],
    queryFn: () => fetchJson<{ attractions: AttractionSummary[] }>(`/api/provinces/${adcode}/attractions?name=${encodeURIComponent(provinceName)}`),
    enabled: Boolean(adcode) && provinceName !== adcode,
    staleTime: 30 * 60 * 1000,
    retry: false,
  });
  const attractions = attractionsQuery.data?.attractions || [];

  useEffect(() => {
    setHoveredAttraction(null);
    setMapReady(false);
    setProvinceBackground("");
  }, [adcode]);

  useEffect(() => {
    const candidates = attractions.flatMap((attraction) =>
      (attraction.photos || []).map((photo) => photo),
    );
    if (!candidates.length) return;
    const nextBackground = candidates[Math.floor(Math.random() * candidates.length)];
    let cancelled = false;
    preloadMapBackground(nextBackground)
      .then(() => {
        if (cancelled) return;
        setProvinceBackground(nextBackground);
      })
      .catch(() => {
        // Keep the previous landscape visible when the next image is unavailable.
      });
    return () => {
      cancelled = true;
    };
  }, [adcode, attractionsQuery.data]);

  const backgroundTarget = hoveredAttraction?.photos?.[0] || provinceBackground;

  useEffect(() => {
    if (!backgroundTarget || backgroundTarget === backgroundImage) {
      setBackgroundLoading(false);
      return;
    }
    let cancelled = false;
    setBackgroundLoading(true);
    preloadMapBackground(backgroundTarget)
      .then(() => {
        if (cancelled) return;
        setBackgroundImage(backgroundTarget);
        rememberMapBackground(backgroundTarget);
      })
      .catch(() => {
        // Keep the current background instead of dropping to a dark placeholder.
      })
      .finally(() => {
        if (!cancelled) setBackgroundLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [backgroundImage, backgroundTarget]);

  useEffect(() => {
    if (!AMap || !mapRef.current || !adcode || !attractionsQuery.isFetched) return;
    const map = new AMap.Map(mapRef.current, {
      zoom: 6,
      mapStyle: "amap://styles/normal",
      showLabel: false,
      features: [],
    });
    map.setLayers([]);
    map.addControl(new AMap.Scale());
    const polygonOverlays: any[] = [];
    const markerRecords: Array<{ attraction: AttractionSummary; position: any; displayPosition: any; marker: any; element: HTMLButtonElement; label?: any }> = [];
    let labelTimer = 0;
    const labelBaseStyle = {
      "background-color": "transparent",
      border: "none",
      color: "rgba(246,241,231,.78)",
      "font-weight": "500",
      "text-shadow": "0 2px 8px rgba(0,0,0,.75)",
    };
    const labelActiveStyle = {
      "background-color": "transparent",
      border: "none",
      color: "#ffffff",
      "font-weight": "800",
      "text-shadow": "0 2px 10px rgba(0,0,0,.85)",
    };
    const applyLabelStyles = (active?: (typeof markerRecords)[number]) => {
      markerRecords.forEach((entry) => {
        entry.label?.setStyle(entry === active ? labelActiveStyle : labelBaseStyle);
      });
    };
    const setRecordHover = (record: (typeof markerRecords)[number], active: boolean) => {
      setHoveredAttraction(active ? record.attraction : null);
      applyLabelStyles(active ? record : undefined);
      record.element.classList.toggle("is-hovered", active);
    };
    const timers: number[] = [];
    const later = (callback: () => void, delay: number) => {
      const timer = window.setTimeout(callback, delay);
      timers.push(timer);
    };

    const search = new AMap.DistrictSearch({ extensions: "all", subdistrict: 0 });
    search.search(adcode, (status: string, result: any) => {
      if (status !== "complete") return;
      const district = result.districtList?.[0];
      if (!district) return;
      const rings = (district.boundaries || []).map((ring: any[]) => ring.map((point: any) => Array.isArray(point) ? point : [point.lng, point.lat]));
      const mainRing = [...rings].sort((a: number[][], b: number[][]) => b.length - a.length)[0];
      if (!mainRing?.length) return;
      const polygon = new AMap.Polygon({
        path: mainRing,
        strokeColor: "rgba(246,241,231,.84)",
        strokeWeight: 1.45,
        fillColor: "#f6f1e7",
        fillOpacity: 0.025,
      });
      map.add(polygon);
      polygonOverlays.push(polygon);
    });

    attractions.forEach((attraction) => {
      const [lng, lat] = attraction.location.split(",").map(Number);
      if (!Number.isFinite(lng) || !Number.isFinite(lat)) return;
      const position = new AMap.LngLat(lng, lat);
      const markerContent = document.createElement("button");
      const record = { attraction, position, displayPosition: position, marker: null as any, element: markerContent, label: undefined as any };
      markerContent.className = "province-marker";
      markerContent.setAttribute("aria-label", attraction.name);
      let preparationStarted = false;
      const startPreparation = () => {
        if (preparationStarted) return;
        preparationStarted = true;
        void prepareAttraction(attraction.poi_id).catch(() => undefined);
      };

      markerContent.addEventListener("mouseenter", () => {
        startPreparation();
        setRecordHover(record, true);
      });
      markerContent.addEventListener("mouseleave", () => setRecordHover(record, false));
      markerContent.addEventListener("click", () => {
        startPreparation();
        navigate(`/attraction/${attraction.poi_id}`);
      });

      const marker = new AMap.Marker({ position, content: markerContent, anchor: "center", zIndex: 180 });
      record.marker = marker;
      map.add(marker);
      markerRecords.push(record);
    });

    function clearLabels() {
      markerRecords.forEach((record) => {
        if (record.label) {
          map.remove(record.label);
          record.label = undefined;
        }
      });
    }

    function fanOutMarkers() {
      const pixels = markerRecords.map((record) => map.lngLatToContainer(record.position));
      const parent = markerRecords.map((_, index) => index);
      const find = (value: number): number => parent[value] === value ? value : (parent[value] = find(parent[value]));
      const union = (left: number, right: number) => {
        const a = find(left); const b = find(right); if (a !== b) parent[b] = a;
      };
      for (let i = 0; i < pixels.length; i += 1) {
        for (let j = i + 1; j < pixels.length; j += 1) {
          if (Math.hypot(pixels[i].x - pixels[j].x, pixels[i].y - pixels[j].y) < 34) union(i, j);
        }
      }
      const groups = new Map<number, number[]>();
      markerRecords.forEach((_, index) => {
        const root = find(index);
        groups.set(root, [...(groups.get(root) || []), index]);
      });
      markerRecords.forEach((record) => {
        record.marker.setOffset(new AMap.Pixel(0, 0));
        record.displayPosition = record.position;
      });
      groups.forEach((indexes) => {
        if (indexes.length < 2) return;
        const radius = indexes.length > 5 ? 44 : 36;
        indexes.forEach((recordIndex, order) => {
          const angle = -Math.PI / 2 + (Math.PI * 2 * order) / indexes.length;
          const dx = Math.cos(angle) * radius;
          const dy = Math.sin(angle) * radius;
          const record = markerRecords[recordIndex];
          record.marker.setOffset(new AMap.Pixel(dx, dy));
          const pixel = pixels[recordIndex];
          record.displayPosition = map.containerToLngLat(new AMap.Pixel(pixel.x + dx, pixel.y + dy));
        });
      });
    }

    function placeLabels() {
      if (!markerRecords.length) return;
      clearLabels();
      fanOutMarkers();
      const placed: Array<{ x: number; y: number; w: number; h: number }> = markerRecords.map((record) => {
        const pixel = map.lngLatToContainer(record.displayPosition);
        return { x: pixel.x, y: pixel.y, w: 24, h: 24 };
      });
      markerRecords.forEach((record) => {
        const pixel = map.lngLatToContainer(record.displayPosition);
        const width = Math.min(230, 32 + record.attraction.name.length * 13);
        const height = 30;
        let selected = LABEL_OFFSETS[LABEL_OFFSETS.length - 1];
        for (const offset of LABEL_OFFSETS) {
          const candidate = { x: pixel.x + offset[0], y: pixel.y + offset[1], w: width, h: height };
          if (!placed.some((rect) => overlaps(candidate, rect))) {
            selected = offset;
            placed.push(candidate);
            break;
          }
        }
        const label = new AMap.Text({
          text: record.attraction.name,
          position: record.displayPosition,
          anchor: "center",
          offset: new AMap.Pixel(selected[0], selected[1]),
          zIndex: 170,
          style: {
            "background-color": "transparent",
            border: "none",
            borderRadius: "0",
            color: labelBaseStyle.color,
            "font-size": "12px",
            "font-weight": "500",
            padding: "6px 10px",
            "white-space": "nowrap",
            cursor: "pointer",
          },
        });
        label.on("mouseover", () => setRecordHover(record, true));
        label.on("mouseout", () => setRecordHover(record, false));
        label.on("click", () => navigate(`/attraction/${record.attraction.poi_id}`));
        map.add(label);
        record.label = label;
      });
    }

    function refit() {
      const overlays = [...polygonOverlays, ...markerRecords.map((record) => record.marker)];
      if (overlays.length) map.setFitView(overlays, true, [150, 150, 160, 150], 10);
      later(() => {
        const baseZoom = map.getZoom();
        map.setZoom(Math.min(baseZoom + 0.2, 10));
        later(() => {
          const bounds = map.getBounds();
          if (markerRecords.some((record) => !bounds.contains(record.position))) map.setZoom(baseZoom);
          placeLabels();
        }, 180);
      }, 180);
    }

    function scheduleLabels() {
      window.clearTimeout(labelTimer);
      labelTimer = window.setTimeout(placeLabels, 120);
    }

    map.on("zoomend", scheduleLabels);
    map.on("moveend", scheduleLabels);
    later(refit, 350);
    later(() => setMapReady(true), 80);
    return () => {
      window.clearTimeout(labelTimer);
      timers.forEach((timer) => window.clearTimeout(timer));
      map.off("zoomend", scheduleLabels);
      map.off("moveend", scheduleLabels);
      map.destroy();
    };
  }, [AMap, adcode, attractions, attractionsQuery.isFetched, navigate]);

  return (
    <main className="relative min-h-screen bg-[#061512] text-[#f3ead8]">
      <div
        className={`province-hover-bg ${backgroundImage ? "is-visible" : ""}`}
        style={{ backgroundImage: `url("${backgroundImage}")` }}
      />
      <div className={`province-map-layer absolute inset-0 ${mapReady ? "is-ready" : ""} ${hoveredAttraction ? "province-map-blurred" : ""}`}><div ref={mapRef} className="h-full w-full" /></div>
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-[#03100e]/18 via-transparent to-[#03100e]/48" />
      <header className="relative z-10 flex items-start justify-between p-5 md:p-8">
        <div className="space-y-4">
          <BackButton variant="dark" />
          <div className="pointer-events-auto">
            <p className="flex items-center gap-2 text-xs font-semibold tracking-[0.12em] text-[#d8b766]"><Sparkles aria-hidden="true" className="h-4 w-4" /> DeepSeek 热门景区</p>
            <h1 className="mt-2 font-serif text-4xl md:text-6xl">{provinceName}</h1>
          </div>
        </div>
      </header>

      {attractionsQuery.isLoading && <div className="pointer-events-none absolute left-1/2 top-1/2 z-20 -translate-x-1/2 -translate-y-1/2 rounded-full border border-[#f6f1e7]/18 bg-[#07110f]/66 px-5 py-3 backdrop-blur-xl"><MountainArchiveLoader compact label="正在寻找热门景区" /></div>}
      {hoveredAttraction && backgroundLoading && <div className="pointer-events-none absolute left-1/2 top-5 z-20 -translate-x-1/2 rounded-full border border-[#f6f1e7]/18 bg-[#07110f]/62 px-4 py-2 shadow-[0_12px_30px_rgba(0,0,0,.2)] backdrop-blur-xl"><MountainArchiveLoader compact label="正在展开景区画卷" /></div>}
      <section className="absolute bottom-8 left-1/2 z-20 flex w-full -translate-x-1/2 flex-col items-center px-6">
        <AttractionSearchBox province={provinceName} />
        <div className="mt-3 flex items-center gap-2 text-xs text-[#f3ead8]/48"><MapPin aria-hidden="true" className="h-4 w-4" /> 鼠标靠近点位查看高清图片，点击进入景区</div>
        {mapError && <p className="mt-3 rounded-xl bg-rose-950/70 px-4 py-2 text-sm text-rose-200">{mapError}</p>}
        {attractionsQuery.error && <p className="mt-3 max-w-xl rounded-xl bg-rose-950/70 px-4 py-3 text-center text-sm text-rose-200">{attractionsQuery.error.message}</p>}
      </section>
    </main>
  );
}
