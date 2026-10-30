import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Route, Routes, useLocation } from "react-router-dom";
import { AccountButton } from "./components/AccountButton";
import { ConfigGate } from "./components/ConfigGate";
import { FoxCompanion } from "./components/FoxCompanion";
import { MountainArchiveLoader } from "./components/MountainArchiveLoader";
import { AttractionPage } from "./pages/AttractionPage";
import { ChinaMapPage } from "./pages/ChinaMapPage";
import { PlanPage } from "./pages/PlanPage";
import { ProvincePage } from "./pages/ProvincePage";
import { AccountPage } from "./pages/AccountPage";
import { fetchCurrentUser, fetchJson } from "./lib/api";
import { useTravelStore } from "./lib/store";
import type { FrontendConfig } from "./lib/types";

export default function App() {
  const location = useLocation();
  const setUser = useTravelStore((state) => state.setUser);
  const config = useQuery({
    queryKey: ["frontend-config"],
    queryFn: () => fetchJson<FrontendConfig>("/api/config/frontend"),
  });

  useEffect(() => {
    void fetchCurrentUser()
      .then((result) => setUser(result.user))
      .catch(() => undefined);
  }, [setUser]);

  if (config.isLoading) return <div className="grid min-h-screen place-items-center bg-[#07110f]"><MountainArchiveLoader label="正在加载山河图" /></div>;
  if (config.error) return <div className="grid min-h-screen place-items-center bg-[#07110f] text-rose-300">{config.error.message}</div>;
  if (!config.data?.configured) return <ConfigGate config={config.data!} />;

  return (
    <>
      <div key={location.pathname} className="page-transition">
        <Routes location={location}>
          <Route path="/" element={<ChinaMapPage />} />
          <Route path="/account" element={<AccountPage />} />
          <Route path="/province/:adcode" element={<ProvincePage />} />
          <Route path="/attraction/:poiId" element={<AttractionPage />} />
          <Route path="/plan/:poiId" element={<PlanPage />} />
          <Route path="*" element={<ChinaMapPage />} />
        </Routes>
      </div>
      <AccountButton />
      <FoxCompanion />
    </>
  );
}
