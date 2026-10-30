import { FormEvent, useEffect, useState } from "react";
import { KeyRound, LogOut, Save, Sparkles, UserRound } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { BackButton } from "../components/BackButton";
import { fetchCurrentUser, loginAccount, logoutAccount, registerAccount, updateProfileStyle } from "../lib/api";
import { useTravelStore } from "../lib/store";
import type { UserProfile } from "../lib/types";

const STYLES = [
  { value: "landscape", label: "山水自然", description: "优先山川、湖泊、森林与自然风景" },
  { value: "culture", label: "历史人文", description: "优先古城、寺庙、博物馆与文化遗产" },
  { value: "balanced", label: "均衡体验", description: "山水与人文兼顾" },
] as const;

type StyleValue = UserProfile["style_preference"];

export function AccountPage() {
  const navigate = useNavigate();
  const user = useTravelStore((state) => state.user);
  const setUser = useTravelStore((state) => state.setUser);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [style, setStyle] = useState<StyleValue>("balanced");
  const [savedStyle, setSavedStyle] = useState<StyleValue>(user?.style_preference || "balanced");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!user) {
      void fetchCurrentUser().then((result) => {
        if (result.user) {
          setUser(result.user);
          setSavedStyle(result.user.style_preference);
        }
      }).catch(() => undefined);
    }
  }, [setUser, user]);

  useEffect(() => {
    if (user) {
      setSavedStyle(user.style_preference);
      setStyle(user.style_preference);
    }
  }, [user]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setNotice("");
    setSaving(true);
    try {
      const result = mode === "register"
        ? await registerAccount({ username, password, invite_code: inviteCode, style_preference: style })
        : await loginAccount({ username, password });
      setUser(result.user);
      setSavedStyle(result.user.style_preference);
      setStyle(result.user.style_preference);
      setNotice(mode === "register" ? "账户已创建，欢迎同行。" : "登录成功。");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "操作失败");
    } finally {
      setSaving(false);
    }
  }

  async function saveProfile() {
    setError("");
    setNotice("");
    setSaving(true);
    try {
      const result = await updateProfileStyle(style);
      setUser(result.user);
      setSavedStyle(result.user.style_preference);
      setNotice("偏好已更新，省份热门景区会按新画像调整。");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function logout() {
    setSaving(true);
    try {
      await logoutAccount();
      setUser(null);
      setUsername("");
      setPassword("");
      setNotice("已退出登录。");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "退出失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="relative min-h-screen overflow-hidden bg-[#07110f] px-5 py-8 text-[#f6f1e7]">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_18%_18%,rgba(143,195,219,.18),transparent_34%),radial-gradient(circle_at_82%_76%,rgba(230,180,94,.12),transparent_32%)]" />
      <div className="relative z-10 mx-auto max-w-4xl">
        <BackButton />
        <header className="mt-12 text-center">
          <p className="flex items-center justify-center gap-2 text-xs font-semibold tracking-[0.16em] text-[#d8b766]"><Sparkles className="h-4 w-4" /> 山河行迹 · 旅人档案</p>
          <h1 className="mt-4 font-serif text-5xl md:text-6xl">{user ? "我的旅行画像" : "与山河相识"}</h1>
          <p className="mx-auto mt-4 max-w-2xl text-sm leading-7 text-[#a7b8ae]">账户只用来保存你的景点风格偏好。偏好会影响中国各省热门景区中的山水与人文比例，不会限制你的自由选择。</p>
        </header>

        {user ? (
          <section className="mt-12 grid gap-6 md:grid-cols-[.75fr_1.25fr]">
            <aside className="glass-panel h-fit p-7">
              <div className="grid h-20 w-20 place-items-center rounded-full border border-[#e6b45e]/30 bg-[#e6b45e]/10 text-[#e6b45e]"><UserRound className="h-9 w-9" /></div>
              <h2 className="mt-6 font-serif text-3xl">{user.username}</h2>
              <p className="mt-2 text-xs leading-6 text-[#a7b8ae]">账户已登录。偏好仅用于省份热门景区排序。</p>
              <button type="button" onClick={logout} disabled={saving} className="mt-8 inline-flex items-center gap-2 text-sm text-rose-200/78 hover:text-rose-100 disabled:opacity-50"><LogOut className="h-4 w-4" /> 退出登录</button>
            </aside>

            <div className="glass-panel p-7">
              <div className="flex items-center gap-3"><Sparkles className="h-5 w-5 text-[#e6b45e]" /><h2 className="font-serif text-3xl">景点风格偏好</h2></div>
              <div className="mt-6 grid gap-3">
                {STYLES.map((item) => (
                  <label key={item.value} className={`cursor-pointer rounded-2xl border p-4 transition ${style === item.value ? "border-[#e6b45e]/60 bg-[#e6b45e]/10" : "border-white/10 bg-white/[0.035] hover:border-white/20"}`}>
                    <input type="radio" name="style" value={item.value} checked={style === item.value} onChange={() => setStyle(item.value)} className="sr-only" />
                    <span className="font-semibold">{item.label}</span>
                    <span className="mt-1 block text-xs leading-6 text-[#a7b8ae]">{item.description}</span>
                  </label>
                ))}
              </div>
              <button type="button" onClick={saveProfile} disabled={saving || style === savedStyle} className="mt-6 inline-flex items-center gap-2 rounded-xl bg-[#e6b45e] px-5 py-3 text-sm font-bold text-[#10231d] transition hover:bg-[#f0c875] disabled:cursor-not-allowed disabled:opacity-45"><Save className="h-4 w-4" /> 保存偏好</button>
            </div>
          </section>
        ) : (
          <section className="glass-panel mx-auto mt-12 max-w-xl p-7 md:p-9">
            <div className="grid grid-cols-2 rounded-xl bg-black/20 p-1">
              <button type="button" onClick={() => setMode("login")} className={`rounded-lg px-4 py-2 text-sm ${mode === "login" ? "bg-[#e6b45e] font-bold text-[#10231d]" : "text-[#a7b8ae]"}`}>登录</button>
              <button type="button" onClick={() => setMode("register")} className={`rounded-lg px-4 py-2 text-sm ${mode === "register" ? "bg-[#e6b45e] font-bold text-[#10231d]" : "text-[#a7b8ae]"}`}>邀请码注册</button>
            </div>
            <form onSubmit={submit} className="mt-7 space-y-4">
              <label className="block text-xs text-[#a7b8ae]">用户名<input value={username} onChange={(event) => setUsername(event.target.value)} required autoComplete="username" className="mt-2 w-full rounded-xl border border-white/12 bg-black/20 px-4 py-3 text-[#f6f1e7] outline-none focus:border-[#e6b45e]/60" /></label>
              <label className="block text-xs text-[#a7b8ae]">密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required minLength={mode === "register" ? 8 : 1} autoComplete={mode === "register" ? "new-password" : "current-password"} className="mt-2 w-full rounded-xl border border-white/12 bg-black/20 px-4 py-3 text-[#f6f1e7] outline-none focus:border-[#e6b45e]/60" /></label>
              {mode === "register" && (
                <>
                  <label className="block text-xs text-[#a7b8ae]">邀请码<input value={inviteCode} onChange={(event) => setInviteCode(event.target.value)} required className="mt-2 w-full rounded-xl border border-white/12 bg-black/20 px-4 py-3 text-[#f6f1e7] outline-none focus:border-[#e6b45e]/60" /></label>
                  <div>
                    <p className="mb-3 text-xs text-[#a7b8ae]">你更喜欢哪一种风景？</p>
                    <div className="grid gap-2">
                      {STYLES.map((item) => (
                        <label key={item.value} className={`cursor-pointer rounded-xl border px-4 py-3 text-sm ${style === item.value ? "border-[#e6b45e]/55 bg-[#e6b45e]/10" : "border-white/10 bg-white/[0.035]"}`}>
                          <input type="radio" name="register-style" value={item.value} checked={style === item.value} onChange={() => setStyle(item.value)} className="sr-only" />
                          <span className="font-semibold">{item.label}</span>
                          <span className="ml-2 text-xs text-[#a7b8ae]">{item.description}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                </>
              )}
              <button type="submit" disabled={saving} className="flex w-full items-center justify-center gap-2 rounded-xl bg-[#e6b45e] px-5 py-3 font-bold text-[#10231d] transition hover:bg-[#f0c875] disabled:opacity-50"><KeyRound className="h-4 w-4" /> {saving ? "处理中..." : mode === "register" ? "创建账户" : "登录"}</button>
            </form>
          </section>
        )}
        {notice && <p className="mx-auto mt-6 max-w-xl rounded-xl border border-[#72c9a1]/20 bg-[#72c9a1]/8 px-4 py-3 text-center text-sm text-[#9ee0bf]">{notice}</p>}
        {error && <p className="mx-auto mt-6 max-w-xl rounded-xl border border-rose-300/20 bg-rose-950/50 px-4 py-3 text-center text-sm text-rose-200">{error}</p>}
        {user && <div className="mt-6 text-center"><button type="button" onClick={() => navigate("/")} className="text-xs text-[#8fc3db] hover:text-[#b8ddeb]">返回中国地图</button></div>}
      </div>
    </main>
  );
}
