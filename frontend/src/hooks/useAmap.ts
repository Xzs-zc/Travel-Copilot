import { useEffect, useState } from "react";
import AMapLoader from "@amap/amap-jsapi-loader";
import { useQuery } from "@tanstack/react-query";
import { fetchJson } from "../lib/api";
import type { FrontendConfig } from "../lib/types";

declare global {
  interface Window {
    _AMapSecurityConfig?: { securityJsCode: string };
  }
}

let amapPromise: Promise<any> | null = null;

export function useAmap() {
  const configQuery = useQuery({
    queryKey: ["frontend-config"],
    queryFn: () => fetchJson<FrontendConfig>("/api/config/frontend"),
  });
  const [AMap, setAMap] = useState<any>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!configQuery.data) return;
    const config = configQuery.data;
    if (!config.amap_js_key || !config.amap_js_security_code) {
      setError(`缺少前端地图配置：${config.missing_required.join("、")}`);
      return;
    }
    window._AMapSecurityConfig = { securityJsCode: config.amap_js_security_code };
    if (!amapPromise) {
      amapPromise = AMapLoader.load({
        key: config.amap_js_key,
        version: "2.0",
        plugins: ["AMap.DistrictSearch", "AMap.Scale", "AMap.ToolBar"],
      });
    }
    amapPromise.then(setAMap).catch((reason) => {
      amapPromise = null;
      setError(String(reason));
    });
  }, [configQuery.data]);

  return { AMap, error, config: configQuery.data, loading: configQuery.isLoading };
}
