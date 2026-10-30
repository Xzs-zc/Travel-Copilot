export type SourceRef = {
  title: string;
  url: string;
  provider: string;
  fetched_at: string;
  excerpt: string;
};

export type EvidenceItem = {
  id: string;
  field: string;
  value: unknown;
  source: SourceRef | null;
  confidence: number;
  status: "verified" | "partial" | "unknown" | "conflict";
  note: string;
};

export type AgentRunMeta = {
  run_id: string;
  status: string;
  tool_calls: number;
  replan_count: number;
  missing_fields: string[];
  token_usage: Record<string, number>;
  warnings: string[];
  errors: string[];
};

export type ProvinceSummary = {
  name: string;
  adcode: string;
  citycode: string;
  center: string;
  level: string;
  supported: boolean;
  unsupported_reason: string;
};

export type SearchResponse = {
  query: string;
  results: AttractionSummary[];
  direct_poi_id: string | null;
};

export type AttractionSummary = {
  poi_id: string;
  name: string;
  province: string;
  city: string;
  district: string;
  address: string;
  location: string;
  rating: number;
  cost: number;
  type: string;
  photos: string[];
  source: SourceRef | null;
  rank_reason: string;
};

export type SubSpot = {
  name: string;
  location: string;
  image: string;
  image_source: "exact" | "contextual" | "placeholder";
  description: string;
  open_time: string;
  cost: number;
};

export type AttractionDetail = {
  poi_id: string;
  name: string;
  province: string;
  city: string;
  district: string;
  address: string;
  location: string;
  rating: number;
  reference_cost: number;
  ticket_price: string;
  open_time: string;
  telephone: string;
  open_status: "official" | "amap_only" | "pending";
  official_url: string;
  official_excerpts: string[];
  summary: string;
  travel_tips: string[];
  hero_image: string;
  photos: string[];
  image_sources?: { image: string; provider: string; source_url: string }[];
  sub_spots: SubSpot[];
  sources: SourceRef[];
  guide_links: { platform: string; url: string }[];
  weather: Record<string, unknown>;
  evidence?: EvidenceItem[];
  agent?: AgentRunMeta;
  generated_at: string;
};

export type RouteMapInfo = {
  origin_name: string;
  destination_name: string;
  origin_location: string;
  destination_location: string;
  origin_province?: string;
  origin_city?: string;
  origin_district?: string;
  destination_province?: string;
  destination_city?: string;
  destination_district?: string;
  distance_m: number;
  duration_s: number;
  tolls: number;
  path: number[][];
  source: SourceRef;
};

export type TicketPeriod = {
  label: string;
  start_time: string;
  end_time: string;
  train_count: number;
  available_count: number;
  status: "ample" | "available" | "tight";
  seat_summary: string[];
  summary: string;
  min_price?: number;
  duration_minutes?: number;
  recommended?: boolean;
};

export type FlightOption = {
  airline: string;
  flight_number: string;
  price: number;
  currency: string;
  duration_minutes: number;
  departure_time: string;
  arrival_time: string;
  departure_airport: string;
  arrival_airport: string;
  stops: number;
  source?: SourceRef | null;
};

export type FlightPeriod = {
  label: string;
  start_time: string;
  end_time: string;
  flight_count: number;
  min_price?: number;
  duration_minutes?: number;
  stops?: number;
  summary: string;
};

export type TransportRecommendation = {
  mode: "drive" | "high_speed_rail" | "flight" | "high_speed_rail_or_flight" | "mixed";
  summary: string;
  reason: string;
};

export type TransportComparison = {
  train_price: number;
  flight_price: number;
  train_duration_minutes: number;
  flight_duration_minutes: number;
  price_gap: number;
  duration_gap_minutes: number;
  flight_available: boolean;
};

export type WeatherAdvice = {
  summary: string;
  suggested_dates: string[];
  best_date: string;
};

export type TransportPlan = {
  origin: string;
  destination: string;
  travel_date: string;
  route: RouteMapInfo;
  ticket_periods: TicketPeriod[];
  blessing: { text: string; style: string; generated_at: string };
  days: number;
  weather: {
    city?: string;
    report_time?: string;
    casts?: Array<Record<string, string>>;
  };
  caveats: string[];
  recommendation: TransportRecommendation;
  flight_options: FlightOption[];
  flight_periods: FlightPeriod[];
  flight_comparison_allowed?: boolean;
  transport_comparison: TransportComparison;
  weather_advice: WeatherAdvice;
  date_is_recommended: boolean;
  assumed_days: boolean;
  driving_recommended: boolean;
  show_driving_plan: boolean;
  evidence?: EvidenceItem[];
  agent?: AgentRunMeta;
  session_id?: string;
  cache_hit?: boolean;
  replan?: {
    changed_fields: string[];
    invalidated_evidence: string[];
    reused_evidence: string[];
  };
  preloaded_tools?: string[];
  generated_at: string;
};

export type AgentEvent = {
  event:
    | "status"
    | "plan"
    | "state"
    | "tool_start"
    | "tool_result"
    | "validation"
    | "evidence"
    | "replan"
    | "message"
    | "sources"
    | "partial"
    | "done"
    | "error";
  data: unknown;
  step: number;
  run_id: string;
  duration_ms?: number;
  tokens?: Record<string, number>;
  meta?: Record<string, unknown>;
};

export type FrontendConfig = {
  amap_js_key: string;
  amap_js_security_code: string;
  configured: boolean;
  missing_required: string[];
};


export type UserProfile = {
  id: string;
  username: string;
  style_preference: "landscape" | "culture" | "balanced";
};

export type SupportAction = {
  type: "navigate" | "reload";
  label: string;
  href?: string;
};

export type SupportSource = {
  title: string;
  provider: string;
  url?: string;
  excerpt?: string;
};

export type SupportReply = {
  thread_id: string;
  reply: string;
  agent: "site_guide" | "troubleshooter" | "travel_concierge" | "policy" | "clarification";
  intent: string;
  confidence: number;
  sources: SupportSource[];
  actions: SupportAction[];
  created_at: string;
};

export type SupportMessage = {
  id?: number;
  thread_id?: string;
  role: "user" | "assistant";
  content: string;
  metadata?: {
    agent?: string;
    sources?: SupportSource[];
    actions?: SupportAction[];
  };
  created_at?: string;
};

export type SupportThread = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
};
