"use client"

import { useState, useEffect, useRef } from "react"
import { useRouter } from "next/navigation"
import { authApi, getToken, setToken } from "@/lib/api"
import { Eye, EyeOff, MessageCircle } from "lucide-react"

export default function RegisterPage() {
  const router = useRouter()

  const [phone, setPhone] = useState("")
  const [code, setCode] = useState("")
  const [password, setPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [showPwd, setShowPwd] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [agreed, setAgreed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const [codeCountdown, setCodeCountdown] = useState(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const canSubmit =
    phone.trim().length >= 11 &&
    code.length >= 4 &&
    password.length >= 6 &&
    password === confirmPassword &&
    agreed &&
    !loading

  useEffect(() => {
    if (getToken()) router.push("/")
  }, [router])

  useEffect(() => {
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [])

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
    setError(null)
    setLoading(true)
    try {
      const res = await authApi.register(phone, password, phone)
      if (res.ok && res.data) {
        setToken(res.data.token)
        router.push("/")
      } else {
        setError(res.message || "注册失败，请重试")
      }
    } catch {
      setError("网络异常，请稍后重试")
    } finally {
      setLoading(false)
    }
  }

  // 统一的输入框样式 — 基准: h-12, rounded-lg, bg-white, border-[#E3E3E3]
  const inputClass =
    "flex items-center rounded-lg border border-[#E3E3E3] bg-white pl-4 pr-3 h-12 focus-within:ring-2 focus-within:ring-[#28C1A8]/20 focus-within:border-[#28C1A8] transition"

  return (
    <main className="relative min-h-screen flex flex-col overflow-hidden safe-top safe-bottom"
      style={{ background: "linear-gradient(180deg, #D8EEFB 0%, #F0FAFE 100%)" }}
    >
      {/* ═══════════ 背景装饰 ═══════════ */}
      <div className="absolute inset-0 pointer-events-none select-none overflow-hidden">
        {/* 云朵 */}
        <div className="absolute top-8 left-6 opacity-40">
          <svg width="60" height="30" viewBox="0 0 60 30" fill="white"><ellipse cx="20" cy="20" rx="18" ry="10"/><ellipse cx="35" cy="15" rx="14" ry="12"/><ellipse cx="48" cy="20" rx="12" ry="8"/></svg>
        </div>
        <div className="absolute top-16 right-10 opacity-30">
          <svg width="45" height="22" viewBox="0 0 45 22" fill="white"><ellipse cx="15" cy="15" rx="13" ry="8"/><ellipse cx="26" cy="11" rx="10" ry="9"/><ellipse cx="36" cy="15" rx="9" ry="6"/></svg>
        </div>
        {/* 星星 */}
        <div className="absolute top-20 right-20 text-white/50 text-lg">✦</div>
        <div className="absolute top-32 left-10 text-white/40 text-sm">✦</div>
        <div className="absolute top-44 right-32 text-white/30 text-xs">✧</div>
        {/* 纸飞机 */}
        <div className="absolute top-28 right-6 text-white/35 text-2xl rotate-12">✈</div>
      </div>

      {/* ═══════════ ① Hero 区 ═══════════ */}
      <header className="relative flex flex-col items-center pt-[120px] pb-4 px-6">
        {/* 语言切换 */}
        <button type="button" disabled
          className="absolute top-6 right-5 flex items-center gap-1 rounded-[18px] border border-[#E0E0E0] bg-white px-3 py-1.5 text-xs text-[#666] opacity-60 cursor-not-allowed z-10 h-9"
        >
          🌐 简体中文 ▾
        </button>

        {/* 兔子 — 品牌素材 */}
        <div className="absolute left-0 top-[72px] w-32 h-44 z-0 pointer-events-none select-none">
          <img src="/rabbit-hero.png" alt="" className="w-full h-full object-contain"
            style={{ filter: "drop-shadow(0 4px 12px rgba(0,0,0,0.06))" }} />
        </div>

        {/* Logo 品牌区 */}
        <img src="/logo.png" alt="悠米伴学" className="w-12 h-12 object-contain mb-2 z-10" />
        <h1 className="text-[28px] font-bold text-[#333] tracking-tight z-10">悠米伴学</h1>
        <p className="text-xs text-[#333] mt-0.5 tracking-wider z-10">AI Family Learning Workspace</p>
        <p className="text-base text-[#333] mt-2 z-10">
          让学习更轻松，成长更快乐<span className="ml-0.5">❤️</span>
        </p>
      </header>

      {/* ═══════════ ② 注册卡片 ═══════════ */}
      <div className="flex-1 px-6 pb-10">
        <div className="mx-auto w-full rounded-[20px] bg-white px-6 py-6"
          style={{ maxWidth: "430px", boxShadow: "0 8px 32px rgba(0,0,0,0.06), 0 2px 8px rgba(0,0,0,0.04)" }}
        >
          {/* 标题区 */}
          <div className="flex items-center pb-3 mb-6">
            <span className="text-base font-semibold text-[#28C1A8]">注册账号</span>
            <button type="button" onClick={() => router.push("/login")}
              className="ml-auto text-sm text-[#28C1A8] hover:opacity-80 transition font-medium"
            >
              已有账号？去登录
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <p className="rounded-xl bg-red-50 px-4 py-3 text-center text-sm text-red-600">{error}</p>
            )}

            {/* 手机号 + 验证码嵌入 */}
            <div className={inputClass}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#28C1A8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mr-3">
                <rect x="5" y="2" width="14" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/>
              </svg>
              <input type="tel" placeholder="手机号" value={phone}
                onChange={(e) => setPhone(e.target.value)}
                maxLength={11} required disabled={loading}
                className="flex-1 bg-transparent text-[15px] text-[#333] placeholder:text-[#B0B0B0] outline-none disabled:opacity-50 min-w-0"
              />
              <button type="button" onClick={handleSendCode}
                disabled={codeCountdown > 0 || phone.trim().length < 11 || loading}
                className="shrink-0 rounded-2xl bg-[#EAFBFA] px-4 py-1.5 text-[13px] font-semibold text-[#28C1A8] transition hover:bg-[#D5F0F5] disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap ml-2"
              >
                {codeCountdown > 0 ? `${codeCountdown}s` : "获取验证码"}
              </button>
            </div>

            {/* 短信验证码 */}
            <div className={inputClass}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#28C1A8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mr-3">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>
              </svg>
              <input type="text" placeholder="短信验证码" value={code}
                onChange={(e) => setCode(e.target.value)}
                maxLength={6} required disabled={loading}
                className="flex-1 bg-transparent text-[15px] text-[#333] placeholder:text-[#B0B0B0] outline-none disabled:opacity-50"
              />
            </div>

            {/* 设置密码 */}
            <div className={inputClass}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#28C1A8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mr-3">
                <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
              </svg>
              <input type={showPwd ? "text" : "password"}
                placeholder="设置密码 (6-20位, 含字母或数字)"
                value={password} onChange={(e) => setPassword(e.target.value)}
                minLength={6} maxLength={20} required disabled={loading}
                className="flex-1 bg-transparent text-[15px] text-[#333] placeholder:text-[#B0B0B0] outline-none disabled:opacity-50"
              />
              <button type="button" onClick={() => setShowPwd(!showPwd)}
                className="ml-2 text-[#B0B0B0] hover:text-[#666] transition shrink-0" tabIndex={-1}>
                {showPwd ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>

            {/* 确认密码 */}
            <div className={inputClass}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#28C1A8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mr-3">
                <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
              </svg>
              <input type={showConfirm ? "text" : "password"}
                placeholder="确认密码" value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                minLength={6} maxLength={20} required disabled={loading}
                className="flex-1 bg-transparent text-[15px] text-[#333] placeholder:text-[#B0B0B0] outline-none disabled:opacity-50"
              />
              <button type="button" onClick={() => setShowConfirm(!showConfirm)}
                className="ml-2 text-[#B0B0B0] hover:text-[#666] transition shrink-0" tabIndex={-1}>
                {showConfirm ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>

            {/* 协议 — 圆形 checkbox */}
            <label className="flex items-start gap-2 cursor-pointer select-none mt-5">
              <div className="relative mt-0.5 shrink-0">
                <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)}
                  className="sr-only" />
                <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center transition ${agreed ? "border-[#28C1A8] bg-[#28C1A8]" : "border-[#B0B0B0]"}`}>
                  {agreed && <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3"><path d="M20 6 9 17l-5-5"/></svg>}
                </div>
              </div>
              <span className="text-[13px] text-[#666] leading-relaxed">
                我已阅读并同意
                <span className="text-[#28C1A8] underline cursor-pointer">《用户协议》</span>和
                <span className="text-[#28C1A8] underline cursor-pointer">《隐私政策》</span>
              </span>
            </label>

            {/* 注册按钮 — 纯色 #28C1A8，无渐变，无阴影 */}
            <button type="submit" disabled={!canSubmit}
              className="w-full h-[52px] rounded-[26px] text-white text-[18px] font-semibold transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed active:scale-[0.98] mt-6"
              style={{ background: canSubmit ? "#28C1A8" : "#CBD5E0" }}
            >
              {loading ? "注册中..." : "注册"}
            </button>
          </form>

          {/* 第三方登录 */}
          <div className="mt-6 flex flex-col items-center gap-4">
            <div className="flex items-center gap-3 w-full">
              <div className="flex-1 h-px bg-[#E3E3E3]" />
              <span className="text-[13px] text-[#999] shrink-0">或使用以下方式注册</span>
              <div className="flex-1 h-px bg-[#E3E3E3]" />
            </div>
            <div className="flex gap-6">
              {/* 微信 — 白色圆形背景+阴影 */}
              <button type="button" disabled className="flex flex-col items-center gap-1.5 opacity-70 cursor-not-allowed">
                <div className="w-[52px] h-[52px] rounded-full bg-white flex items-center justify-center" style={{ boxShadow: "0 2px 8px rgba(0,0,0,0.08)" }}>
                  <MessageCircle size={22} className="text-[#07C160]" />
                </div>
                <span className="text-[11px] text-[#999]">微信</span>
              </button>
              <button type="button" disabled className="flex flex-col items-center gap-1.5 opacity-70 cursor-not-allowed">
                <div className="w-[52px] h-[52px] rounded-full bg-white flex items-center justify-center" style={{ boxShadow: "0 2px 8px rgba(0,0,0,0.08)" }}>
                  <span className="text-[#12B7F5] text-sm font-bold">QQ</span>
                </div>
                <span className="text-[11px] text-[#999]">QQ</span>
              </button>
              <button type="button" disabled className="flex flex-col items-center gap-1.5 opacity-70 cursor-not-allowed">
                <div className="w-[52px] h-[52px] rounded-full bg-white flex items-center justify-center" style={{ boxShadow: "0 2px 8px rgba(0,0,0,0.08)" }}>
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="black"><path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.8-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z"/></svg>
                </div>
                <span className="text-[11px] text-[#999]">Apple</span>
              </button>
            </div>
          </div>
        </div>

        {/* 安全承诺 */}
        <p className="text-center text-[13px] text-[#666] mt-12 flex items-center justify-center gap-1.5">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#28C1A8" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>
          我们将保护您的信息安全
        </p>

        {/* 底部装饰 */}
        <div className="flex justify-between items-end w-full max-w-[430px] mx-auto mt-4 px-2 select-none pointer-events-none">
          <span className="text-2xl opacity-60">🌿🍃</span>
          <span className="flex gap-0.5 text-2xl opacity-60">📕<span className="text-blue-400">📘</span><span className="text-amber-400">📙</span>📚</span>
        </div>
      </div>
    </main>
  )
}
