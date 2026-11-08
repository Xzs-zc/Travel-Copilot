export type Route =
  | { page: "datasets" }
  | { page: "runs" }
  | { page: "run"; runId: string }
  | { page: "trace"; runId: string; caseKey: string }
  | { page: "compare" }
  | { page: "labeling" }
  | { page: "trends" };

export function parseRoute(): Route {
  const hash = window.location.hash.replace(/^#\/?/, "");
  const parts = hash.split("/").filter(Boolean);
  if (parts[0] === "runs") return { page: "runs" };
  if (parts[0] === "run" && parts[1]) return { page: "run", runId: parts[1] };
  if (parts[0] === "trace" && parts[1] && parts[2]) {
    return { page: "trace", runId: parts[1], caseKey: parts[2] };
  }
  if (parts[0] === "compare") return { page: "compare" };
  if (parts[0] === "labeling") return { page: "labeling" };
  if (parts[0] === "trends") return { page: "trends" };
  return { page: "datasets" };
}

export function go(path: string) {
  window.location.hash = path.startsWith("#")
    ? path
    : `#/${path.replace(/^\/+/, "")}`;
}
