import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUpRight, MessageCircle, RefreshCw, RotateCcw, Send, Sparkles, X } from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";
import { fetchSupportMessages, fetchSupportThreads, sseUrl } from "../lib/api";
import { useTravelStore } from "../lib/store";
import type { SupportAction, SupportMessage, SupportReply, SupportSource } from "../lib/types";
import { MountainArchiveLoader } from "./MountainArchiveLoader";

const AGENT_LABELS: Record<string, string> = {
  site_guide: "网站向导",
  troubleshooter: "故障助手",
  travel_concierge: "行程解释员",
  policy: "安全边界",
  clarification: "澄清问题",
};

function parsePayload(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function pageLabel(pageType: string, provinceName: string, attractionName: string) {
  if (pageType === "province") return provinceName || "省份地图";
  if (pageType === "attraction") return attractionName || "景区资料";
  if (pageType === "plan") return "交通规划";
  if (pageType === "account") return "账户与偏好";
  return "中国山河图";
}

export function FoxCompanion() {
  const location = useLocation();
  const navigate = useNavigate();
  const user = useTravelStore((state) => state.user);
  const selectedProvince = useTravelStore((state) => state.selectedProvince);
  const selectedAttraction = useTravelStore((state) => state.selectedAttraction);
  const sessionId = useTravelStore((state) => state.sessionId);
  const [open, setOpen] = useState(false);
  const [threadId, setThreadId] = useState("");
  const [messages, setMessages] = useState<SupportMessage[]>([]);
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [activity, setActivity] = useState("正在读取当前页面");
  const [pendingReply, setPendingReply] = useState("");
  const [error, setError] = useState("");
  const [hydrated, setHydrated] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);
  const finishedRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const routeSegments = location.pathname.split("/").filter(Boolean);
  const pageType = useMemo(() => {
    if (routeSegments[0] === "province") return "province";
    if (routeSegments[0] === "attraction") return "attraction";
    if (routeSegments[0] === "plan") return "plan";
    if (routeSegments[0] === "account") return "account";
    return "home";
  }, [location.pathname]);
  const poiId = routeSegments[0] === "attraction" || routeSegments[0] === "plan" ? routeSegments[1] || "" : "";
  const provinceAdcode = routeSegments[0] === "province" ? routeSegments[1] || "" : "";
  const attractionName = selectedAttraction?.poiId === poiId ? selectedAttraction.name : "";
  const provinceName = selectedProvince?.adcode === provinceAdcode ? selectedProvince.name : "";

  const context = useMemo(
    () => ({
      route: location.pathname,
      page_type: pageType,
      province_adcode: provinceAdcode,
      province_name: provinceName,
      poi_id: poiId,
      attraction_name: attractionName,
      plan_session_id: pageType === "plan" ? sessionId : "",
      origin: "",
      destination: pageType === "plan" ? attractionName : "",
      travel_date: "",
      ui_state: {
        viewport: typeof window !== "undefined" && window.innerWidth <= 640 ? "mobile" : "desktop",
      },
    }),
    [attractionName, location.pathname, pageType, poiId, provinceAdcode, provinceName, sessionId],
  );

  const quickPrompts = useMemo(() => {
    if (pageType === "province") return ["这些光点是什么？", "怎么选择想去的景区？"];
    if (pageType === "attraction") return ["这些图片来自哪里？", "开放时间和票价在哪里看？"];
    if (pageType === "plan") return ["为什么推荐这个交通方式？", "余票时段怎么看？"];
    if (pageType === "account") return ["景点偏好有什么作用？", "邀请码怎么注册？"];
    return ["这个地图怎么用？", "怎么搜索想去的景区？"];
  }, [pageType]);

  useEffect(() => {
    sourceRef.current?.close();
    setThreadId(user ? localStorage.getItem(`travel-fox-thread:${user.id}`) || "" : "");
    setMessages([]);
    setPendingReply("");
    setError("");
    setHydrated(false);
  }, [user?.id]);

  useEffect(() => {
    if (!open || !user || hydrated) return;
    let cancelled = false;
    const load = async () => {
      try {
        let activeThread = threadId;
        if (!activeThread) {
          const result = await fetchSupportThreads();
          activeThread = result.threads[0]?.id || "";
        }
        if (activeThread) {
          const result = await fetchSupportMessages(activeThread);
          if (cancelled) return;
          setThreadId(activeThread);
          localStorage.setItem(`travel-fox-thread:${user.id}`, activeThread);
          setMessages(result.messages);
        }
      } catch {
        if (!cancelled) setMessages([]);
      } finally {
        if (!cancelled) setHydrated(true);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [hydrated, open, threadId, user]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, pendingReply, running]);

  useEffect(() => () => sourceRef.current?.close(), []);

  const finishStream = () => {
    finishedRef.current = true;
    sourceRef.current?.close();
    sourceRef.current = null;
    setRunning(false);
    setActivity("正在读取当前页面");
  };

  const sendMessage = (raw: string) => {
    const message = raw.trim();
    if (!message || running || !user) return;
    setInput("");
    setError("");
    setPendingReply("");
    setRunning(true);
    setActivity("小狐狸正在读取当前页面");
    setMessages((current) => [...current, { role: "user", content: message }]);

    finishedRef.current = false;
    const source = new EventSource(
      sseUrl("/api/support/stream", {
        message,
        context: JSON.stringify(context),
        thread_id: threadId,
      }),
    );
    sourceRef.current = source;

    const listen = (name: string, handler: (payload: unknown) => void) => {
      source.addEventListener(name, (raw) => {
        if (!(raw instanceof MessageEvent)) return;
        handler(parsePayload((raw as MessageEvent<string>).data));
      });
    };

    listen("status", (payload) => {
      if (typeof payload === "string") setActivity(payload);
    });
    listen("tool_start", (payload) => {
      const tool = payload && typeof payload === "object" ? String((payload as { tool?: unknown }).tool || "") : "";
      if (tool) setActivity(`正在调用${tool === "search_support_knowledge" ? "客服知识库" : "页面诊断工具"}`);
    });
    listen("tool_result", () => setActivity("正在核对页面数据与来源"));
    listen("message", (payload) => {
      if (payload && typeof payload === "object") {
        setPendingReply(String((payload as { content?: unknown }).content || ""));
      }
    });
    listen("done", (payload) => {
      const result = payload as SupportReply;
      if (!result?.reply) return;
      setThreadId(result.thread_id);
      localStorage.setItem(`travel-fox-thread:${user.id}`, result.thread_id);
      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          content: result.reply,
          metadata: {
            agent: result.agent,
            sources: result.sources,
            actions: result.actions,
          },
        },
      ]);
      setPendingReply("");
      setHydrated(true);
      finishStream();
    });
    listen("error", (payload) => {
      const detail =
        payload && typeof payload === "object"
          ? String((payload as { detail?: unknown }).detail || (payload as { code?: unknown }).code || "客服暂时无法回答")
          : String(payload || "客服暂时无法回答");
      setError(detail);
      setPendingReply("");
      finishStream();
    });
    source.onerror = () => {
      if (finishedRef.current) return;
      setError("连接中断，请重新发送问题。");
      setPendingReply("");
      finishStream();
    };
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    sendMessage(input);
  };

  const runAction = (action: SupportAction) => {
    if (action.type === "reload") {
      window.location.reload();
      return;
    }
    if (action.href?.startsWith("/")) {
      setOpen(false);
      navigate(action.href);
    }
  };

  const startNewThread = () => {
    finishStream();
    setThreadId("");
    setMessages([]);
    setPendingReply("");
    setError("");
    setHydrated(true);
    if (user) localStorage.removeItem(`travel-fox-thread:${user.id}`);
  };

  return (
    <div className={`fox-support ${open ? "is-open" : ""}`}>
      {open && (
        <section className="fox-support-panel" aria-label="小狐狸旅行客服">
          <header className="fox-support-header">
            <div>
              <div className="fox-support-kicker"><Sparkles size={13} /> 山河客服</div>
              <h2>问问小狐狸</h2>
              <p>{pageLabel(pageType, provinceName, attractionName)}</p>
            </div>
            <div className="fox-support-header-actions">
              {user && (
                <button type="button" onClick={startNewThread} title="开始新对话" aria-label="开始新对话">
                  <RotateCcw size={15} />
                </button>
              )}
              <button type="button" onClick={() => setOpen(false)} title="收起客服" aria-label="收起客服">
                <X size={17} />
              </button>
            </div>
          </header>

          {!user ? (
            <div className="fox-support-login">
              <MessageCircle size={28} />
              <strong>登录后才能留下对话记忆</strong>
              <p>小狐狸会根据你的景点偏好和当前页面回答，不会读取密码或 API Key。</p>
              <button type="button" onClick={() => navigate("/account")}>去登录或注册</button>
            </div>
          ) : (
            <>
              <div ref={scrollRef} className="fox-support-messages">
                {messages.length === 0 && !pendingReply && (
                  <div className="fox-support-welcome">
                    <p>我在这里。你可以问页面怎么用、资料为什么在加载，或者让我解释当前推荐。</p>
                    <div>
                      {quickPrompts.map((prompt) => (
                        <button key={prompt} type="button" onClick={() => sendMessage(prompt)}>{prompt}</button>
                      ))}
                    </div>
                  </div>
                )}
                {messages.map((message, index) => (
                  <ChatMessage key={`${message.id || "message"}-${index}`} message={message} runAction={runAction} />
                ))}
                {pendingReply && (
                  <div className="fox-message is-assistant">
                    <div className="fox-message-avatar">狐</div>
                    <div className="fox-message-body">
                      <p>{pendingReply}</p>
                    </div>
                  </div>
                )}
                {running && (
                  <div className="fox-support-running">
                    <MountainArchiveLoader compact label={activity} />
                  </div>
                )}
                {error && <div className="fox-support-error">{error}</div>}
              </div>
              <form className="fox-support-input" onSubmit={submit}>
                <textarea
                  value={input}
                  onChange={(event) => setInput(event.target.value.slice(0, 1000))}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      sendMessage(input);
                    }
                  }}
                  placeholder="问问当前页面、景区图片或交通推荐…"
                  rows={2}
                  disabled={running}
                />
                <button type="submit" disabled={running || !input.trim()} aria-label="发送">
                  <Send size={17} />
                </button>
              </form>
            </>
          )}
        </section>
      )}

      <button
        type="button"
        className="fox-companion fox-support-trigger"
        aria-label={open ? "收起小狐狸客服" : "打开小狐狸客服"}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <svg viewBox="0 0 180 176" aria-hidden="true">
          <defs>
            <linearGradient id="fox-coat" x1=".2" y1="0" x2=".82" y2="1">
              <stop offset="0" stopColor="#e98a49" />
              <stop offset=".42" stopColor="#c85f31" />
              <stop offset=".78" stopColor="#8e3d24" />
              <stop offset="1" stopColor="#5f2b20" />
            </linearGradient>
            <linearGradient id="fox-tail" x1="0" y1=".1" x2="1" y2=".9">
              <stop offset="0" stopColor="#dd7740" />
              <stop offset=".55" stopColor="#a84a2b" />
              <stop offset="1" stopColor="#f1d4ad" />
            </linearGradient>
            <linearGradient id="fox-chest" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#fff0d6" />
              <stop offset="1" stopColor="#cfa77f" />
            </linearGradient>
            <filter id="fox-soft" x="-20%" y="-20%" width="140%" height="150%">
              <feGaussianBlur stdDeviation="5" />
            </filter>
          </defs>
          <ellipse cx="98" cy="159" rx="42" ry="7" fill="rgba(0,0,0,.35)" filter="url(#fox-soft)" />
          <g className="fox-tail">
            <path d="M113 126 C151 133 173 114 166 84 C161 61 138 55 124 70 C112 83 116 101 132 105 C145 108 155 99 153 88 C157 111 141 124 116 117 Z" fill="url(#fox-tail)" />
            <path d="M151 84 C164 84 169 74 165 66 C160 79 153 83 143 85 Z" fill="#f5ddb8" opacity=".9" />
            <path d="M128 115 C146 120 158 112 161 99" fill="none" stroke="#f0b37b" strokeWidth="1" opacity=".32" />
          </g>
          <path d="M54 139 C55 112 70 94 94 94 C119 94 134 112 134 139 Z" fill="url(#fox-coat)" />
          <path d="M73 107 C79 96 87 92 94 92 C104 92 113 99 117 111 C108 126 81 125 73 107 Z" fill="url(#fox-chest)" />
          <path d="M59 70 L63 27 L82 54 Z" fill="#a54a2b" />
          <path d="M130 70 L126 27 L106 54 Z" fill="#8d3b24" />
          <path d="M64 51 L66 35 L76 53 Z" fill="#3e2520" />
          <path d="M126 51 L124 35 L114 53 Z" fill="#3e2520" />
          <path d="M66 49 L68 40 L73 51 Z" fill="#b8684c" opacity=".55" />
          <path d="M124 49 L122 40 L117 51 Z" fill="#b8684c" opacity=".45" />
          <path d="M56 79 C54 55 72 42 94 42 C116 42 134 56 132 80 C130 103 112 115 94 115 C75 115 58 103 56 79 Z" fill="url(#fox-coat)" />
          <path d="M66 79 C72 68 82 65 94 65 C106 65 116 69 121 79 C116 91 106 97 94 97 C81 97 71 91 66 79 Z" fill="url(#fox-chest)" />
          <path className="fox-eye" d="M72 76 C77 73 82 73 87 77 C82 79 77 79 72 76 Z" fill="#28171a" />
          <path className="fox-eye" d="M101 77 C106 73 111 73 116 76 C111 79 106 79 101 77 Z" fill="#28171a" />
          <circle cx="81" cy="76" r=".9" fill="#f4d7a9" />
          <circle cx="109" cy="76" r=".9" fill="#f4d7a9" />
          <path d="M90 86 C93 84 97 84 100 86 L95 92 Z" fill="#29191b" />
          <path d="M87 97 C92 101 98 101 103 97" fill="none" stroke="#5a3022" strokeWidth="1.7" strokeLinecap="round" />
          <path d="M70 66 C77 61 83 60 88 62 M100 62 C107 60 113 62 119 67" fill="none" stroke="#f1b37d" strokeWidth="1.2" opacity=".34" strokeLinecap="round" />
          <path d="M60 82 C54 86 51 91 51 97 M129 82 C135 86 138 91 138 97" fill="none" stroke="#f5c28f" strokeWidth="1" opacity=".3" />
          <path d="M77 115 L94 138 L111 115" fill="#c99a45" opacity=".96" />
          <path d="M56 137 C72 147 87 151 94 151 C104 151 117 146 132 138" fill="none" stroke="#b98a3b" strokeWidth="3.5" strokeLinecap="round" />
          <circle cx="94" cy="138" r="6" fill="#e6b45e" />
          <path d="M94 132 L96 136 L94 140 L92 136 Z" fill="#fff1c8" opacity=".75" />
        </svg>
        <span>{open ? "收起" : "找小狐狸"}</span>
      </button>
    </div>
  );
}

function ChatMessage({
  message,
  runAction,
}: {
  message: SupportMessage;
  runAction: (action: SupportAction) => void;
}) {
  const isAssistant = message.role === "assistant";
  const agent = message.metadata?.agent ? AGENT_LABELS[message.metadata.agent] || "小狐狸" : "小狐狸";
  const sources = message.metadata?.sources || [];
  const actions = message.metadata?.actions || [];

  return (
    <div className={`fox-message ${isAssistant ? "is-assistant" : "is-user"}`}>
      <div className="fox-message-avatar">{isAssistant ? "狐" : "我"}</div>
      <div className="fox-message-body">
        {isAssistant && <span className="fox-message-agent">{agent}</span>}
        <p>{message.content}</p>
        {sources.length > 0 && <SupportSources sources={sources} />}
        {actions.length > 0 && (
          <div className="fox-message-actions">
            {actions.map((action) => (
              <button key={`${action.type}-${action.label}`} type="button" onClick={() => runAction(action)}>
                {action.type === "reload" ? <RefreshCw size={13} /> : <ArrowUpRight size={13} />}
                {action.label}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SupportSources({ sources }: { sources: SupportSource[] }) {
  return (
    <div className="fox-message-sources">
      {sources.slice(0, 3).map((source, index) =>
        source.url ? (
          <a key={`${source.title}-${index}`} href={source.url} target="_blank" rel="noreferrer">
            {source.title} · {source.provider}
          </a>
        ) : (
          <span key={`${source.title}-${index}`}>{source.title} · {source.provider}</span>
        ),
      )}
    </div>
  );
}
