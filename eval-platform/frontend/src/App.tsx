import { useEffect, useState } from "react";
import { Shell } from "./components/Shell";
import { parseRoute } from "./lib/router";
import { ComparePage } from "./pages/ComparePage";
import { DatasetsPage } from "./pages/DatasetsPage";
import { LabelingPage } from "./pages/LabelingPage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunsPage } from "./pages/RunsPage";
import { TraceViewerPage } from "./pages/TraceViewerPage";
import { TrendsPage } from "./pages/TrendsPage";

function useRoute() {
  const [route, setRoute] = useState(parseRoute);
  useEffect(() => {
    const onChange = () => setRoute(parseRoute());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export default function App() {
  const route = useRoute();
  return (
    <Shell route={route}>
      {route.page === "datasets" ? <DatasetsPage /> : null}
      {route.page === "runs" ? <RunsPage /> : null}
      {route.page === "run" ? <RunDetailPage runId={route.runId} /> : null}
      {route.page === "trace" ? (
        <TraceViewerPage runId={route.runId} caseKey={route.caseKey} />
      ) : null}
      {route.page === "compare" ? <ComparePage /> : null}
      {route.page === "labeling" ? <LabelingPage /> : null}
      {route.page === "trends" ? <TrendsPage /> : null}
    </Shell>
  );
}
