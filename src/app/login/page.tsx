"use client"

import { useState, useEffect, useRef } from "react"
import { useRouter } from "next/navigation"
import { authApi, getToken, setToken } from "@/lib/api"
import { Eye, EyeOff, MessageCircle } from "lucide-react"

type LoginTab = "code" | "password"

export default function LoginPage() {
  const router = useRouter()

  const [tab, setTab] = useState<LoginTab>("code")
  const [phone, setPhone] = useState("")
  const [password, setPassword] = useState("")
  const [code, setCode] = useState("")
  const [showPwd, setShowPwd] = useState(false)
  const [agreed, setAgreed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const [codeCountdown, setCodeCountdown] = useState(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const canSubmit =
    phone.trim().length >= 11 &&
    (tab === "password" ? password.length >= 6 : code.length >= 4) &&
    agreed &&
    !loading

  useEffect(() => { if (getToken()) router.push("/") }, [router])
  useEffect(() => { return () => { if (timerRef.current) clearInterval(timerRef.current) } }, [])

  function handleSendCode() {
    if (codeCountdown > 0 || phone.trim().length < 11) return
    setCodeCountdown(60)
    timerRef.current = setInterval(() => {
      setCodeCountdown((n) => {
        if (n <= 1) { if (timerRef.current) clearInterval(timerRef.current); return 0 }
        return n - 1
      })
    }, 1000)
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmit) return
    setError(null); setLoading(true)
    try {
      if (tab === "password") {
        const res = await authApi.login(phone, password)
        if (res.ok && res.data) { setToken(res.data.token); router.push("/") }
        else setError(res.message || "登录失败，请重试")
      } else {
        setError("验证码登录暂未开放，请使用密码登录")
      }
    } catch { setError("网络异常，请稍后重试") }
    finally { setLoading(false) }
  }

  // ── 共享输入框样式 ──
  const fieldCls = "flex items-center h-[56px] rounded-[16px] border border-[#E7EEF0] bg-white pl-[18px] pr-[18px] focus-within:border-[#20B8A8] transition"
  const iconCls = "shrink-0 mr-3 text-[#25B8A8]"

  return (
    <main
      className="relative w-full overflow-y-auto safe-top"
      style={{
        minHeight: "100vh",
        background: "linear-gradient(180deg, #EAF8FF 0%, #F8FCFD 46%, #FFF5F7 100%)",
        paddingBottom: "calc(28px + env(safe-area-inset-bottom))",
      }}
    >
      {/* ═══════════ Hero 430px ═══════════ */}
      <div
        className="relative overflow-hidden w-full"
        style={{ height: "430px", background: "linear-gradient(135deg, #DDF4FF 0%, #EAF8FF 46%, #FFF5F7 100%)" }}
      >
        {/* 语言切换 */}
        <button type="button" disabled
          className="absolute flex items-center gap-1.5 text-[#374151] opacity-60 cursor-not-allowed z-20"
          style={{
            top: "18px", right: "28px", height: "42px", padding: "0 18px",
            borderRadius: "999px", background: "rgba(255,255,255,0.72)",
            border: "1px solid rgba(255,255,255,0.9)",
            boxShadow: "0 4px 14px rgba(31,45,61,0.05)", fontSize: "16px",
          }}
        >🌐 简体中文 ▾</button>

        {/* 兔子 — 使用项目正式 Logo 图片 */}
        <div className="absolute pointer-events-none select-none z-10"
          style={{ left: "22px", top: "28px", width: "210px", height: "250px" }}>
          <img
            src="/rabbit-hero.png"
            alt=""
            className="w-full h-full object-contain"
            style={{ filter: "drop-shadow(0 6px 16px rgba(0,0,0,0.06))" }}
          />
        </div>

        {/* 品牌块 — 绝对定位 top:128px left:44% translateX(-6%) */}
        <div className="absolute flex flex-col items-center z-20"
          style={{ top: "128px", left: "44%", transform: "translateX(-6%)" }}>
          {/* 品牌行：U标记 + 标题 */}
          <div className="flex items-center gap-[18px]">
            <div className="w-16 h-16 rounded-[18px] flex items-center justify-center shrink-0"
              style={{ background: "linear-gradient(135deg, #20B8A8, #27D3C1)" }}>
              <span className="text-white text-2xl font-bold">U</span>
            </div>
            <div className="flex flex-col">
              <h1 className="text-[30px] font-bold text-[#111827] tracking-[0.02em] leading-tight">悠米伴学</h1>
              <p className="text-[13px] text-[#4B5563] tracking-wider">AI Family Learning Workspace</p>
            </div>
          </div>
          {/* 标语 */}
          <p className="text-[22px] text-[#4B5563] tracking-[0.08em] mt-[26px]">
            让学习更轻松，成长更快乐 <span style={{ color: "#F39AA8" }}>❤️</span>
          </p>
        </div>
      </div>

      {/* ═══════════ 卡片 margin-top: -82px ═══════════ */}
      <div className="relative z-[2] mx-auto" style={{
        width: "calc(100% - 48px)", maxWidth: "430px", marginTop: "-82px",
        padding: "28px 28px 34px", background: "#FFFFFF", borderRadius: "32px",
        boxShadow: "0 8px 32px rgba(31,45,61,0.06)", border: "1px solid rgba(255,255,255,0.9)",
      }}>
        {/* Tab: 验证码登录 / 密码登录 */}
        <div className="flex items-center border-b border-[#EEF2F4] h-[58px] mb-6">
          <button type="button" onClick={() => setTab("code")}
            className="relative text-[22px] font-bold mr-10 transition"
            style={{ color: tab === "code" ? "#20B8A8" : "#8B97A3" }}>
            验证码登录
            {tab === "code" && <span className="absolute -bottom-[15px] left-1/2 -translate-x-1/2 w-[44px] h-[4px] rounded-full" style={{ background: "#20B8A8" }} />}
          </button>
          <button type="button" onClick={() => setTab("password")}
            className="relative text-[20px] font-medium transition"
            style={{ color: tab === "password" ? "#20B8A8" : "#8B97A3" }}>
            密码登录
            {tab === "password" && <span className="absolute -bottom-[15px] left-1/2 -translate-x-1/2 w-[44px] h-[4px] rounded-full" style={{ background: "#20B8A8" }} />}
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-5">
          {error && <p className="rounded-xl bg-red-50 px-4 py-3 text-center text-sm text-red-600">{error}</p>}

          {/* 手机号 */}
          <div className={fieldCls} style={{ boxShadow: "none" }}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#25B8A8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={iconCls}>
              <rect x="5" y="2" width="14" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/>
            </svg>
            <span className="text-[18px] text-[#718096] mr-2 font-medium">+86</span>
            <input type="tel" placeholder="手机号" value={phone}
              onChange={(e) => setPhone(e.target.value)} maxLength={11} required disabled={loading}
              className="flex-1 bg-transparent text-[18px] text-[#1F2D3D] placeholder:text-[#9AA7B2] outline-none disabled:opacity-50 min-w-0"
            />
            {tab === "code" && (
              <button type="button" onClick={handleSendCode}
                disabled={codeCountdown > 0 || phone.trim().length < 11 || loading}
                className="shrink-0 rounded-xl bg-[#ECFBF8] px-4 py-2 text-[13px] font-semibold text-[#20B8A8] transition hover:bg-[#D5F0F5] disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap ml-2">
                {codeCountdown > 0 ? `${codeCountdown}s` : "获取验证码"}
              </button>
            )}
          </div>

          {/* 密码 或 验证码 */}
          {tab === "password" ? (
            <div className={fieldCls}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#25B8A8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={iconCls}>
                <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
              </svg>
              <input type={showPwd ? "text" : "password"} placeholder="请输入密码" value={password}
                onChange={(e) => setPassword(e.target.value)} minLength={6} required disabled={loading}
                className="flex-1 bg-transparent text-[18px] text-[#1F2D3D] placeholder:text-[#9AA7B2] outline-none disabled:opacity-50"
              />
              <button type="button" onClick={() => setShowPwd(!showPwd)}
                className="ml-2 text-[#9AA7B2] hover:text-[#718096] transition shrink-0" tabIndex={-1}>
                {showPwd ? <EyeOff size={20} /> : <Eye size={20} />}
              </button>
            </div>
          ) : (
            <div className={fieldCls}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#25B8A8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={iconCls}>
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
              </svg>
              <input type="text" placeholder="短信验证码" value={code}
                onChange={(e) => setCode(e.target.value)} maxLength={6} required disabled={loading}
                className="flex-1 bg-transparent text-[18px] text-[#1F2D3D] placeholder:text-[#9AA7B2] outline-none disabled:opacity-50"
              />
            </div>
          )}

          {/* 协议 */}
          <label className="flex items-start gap-2 cursor-pointer select-none" style={{ marginTop: "20px" }}>
            <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)}
              className="mt-0.5 rounded border-[#CBD5E0] accent-[#20B8A8]"
              style={{ width: "22px", height: "22px" }} />
            <span className="text-[#718096] leading-relaxed" style={{ fontSize: "15px" }}>
              已阅读并同意
              <span className="text-[#20B8A8] underline cursor-pointer">《用户协议》</span>和
              <span className="text-[#20B8A8] underline cursor-pointer">《隐私政策》</span>
            </span>
          </label>

          {/* 登录按钮 */}
          <button type="submit" disabled={!canSubmit}
            className="w-full text-white font-semibold transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed active:scale-[0.98]"
            style={{
              height: "58px", marginTop: "24px", borderRadius: "999px", fontSize: "20px",
              background: canSubmit
                ? "linear-gradient(90deg, #20B8A8 0%, #27D3C1 100%)"
                : "linear-gradient(90deg, #CBD5E0 0%, #A0AEC0 100%)",
              boxShadow: canSubmit ? "0 8px 20px rgba(32,184,168,0.22)" : "none",
            }}>
            {loading ? "登录中..." : "登  录"}
          </button>
        </form>

        {/* ↓ 以下各部分 style 完全对齐 Spec ↓ */}
        {/* 分割线 + 社交登录 */}
        <div className="flex items-center gap-3 w-full" style={{ marginTop: "28px" }}>
          <div className="flex-1 h-px bg-[#EEF2F4]" />
          <span className="text-sm text-[#9AA7B2] shrink-0">其他登录方式</span>
          <div className="flex-1 h-px bg-[#EEF2F4]" />
        </div>
        <div className="flex justify-center gap-6 mt-5">
          <button type="button" disabled
            className="w-14 h-14 rounded-full flex items-center justify-center opacity-70 cursor-not-allowed shadow-md"
            style={{ backgroundColor: "#18C33F" }}>
            <MessageCircle size={26} className="text-white" />
          </button>
          <button type="button" disabled
            className="w-14 h-14 rounded-full flex items-center justify-center opacity-70 cursor-not-allowed shadow-md text-white text-base font-bold"
            style={{ backgroundColor: "#2CA7F8" }}>QQ</button>
          <button type="button" disabled
            className="w-14 h-14 rounded-full flex items-center justify-center opacity-70 cursor-not-allowed shadow-md"
            style={{ backgroundColor: "#111111" }}>
            <svg width="24" height="24" viewBox="0 0 24 24" fill="white"><path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.8-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z"/></svg>
          </button>
        </div>

        {/* 安全提示 */}
        <p className="flex items-center justify-center gap-1.5 text-[#9AA7B2] text-sm" style={{ marginTop: "30px" }}>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
          我们将保护您的信息安全
        </p>

        {/* 去注册 */}
        <p className="text-center text-[#718096] mt-5" style={{ fontSize: "16px" }}>
          还没有账号？
          <button type="button" onClick={() => router.push("/register")}
            className="ml-1 text-[#1D8DA8] font-semibold hover:underline transition">去注册</button>
        </p>
      </div>
    </main>
  )
}
