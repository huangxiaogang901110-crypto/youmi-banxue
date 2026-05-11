"use client"

import { useState, useEffect, useRef } from "react"
import { useRouter } from "next/navigation"
import { authApi, getToken, setToken } from "@/lib/api"
import { Eye, EyeOff, MessageCircle } from "lucide-react"

type LoginTab = "password" | "code"

export default function LoginPage() {
  const router = useRouter()

  const [tab, setTab] = useState<LoginTab>("password")
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
      if (tab === "password") {
        const res = await authApi.login(phone, password)
        if (res.ok && res.data) {
          setToken(res.data.token)
          router.push("/")
        } else {
          setError(res.message || "登录失败，请重试")
        }
      } else {
        setError("验证码登录暂未开放，请使用密码登录")
      }
    } catch {
      setError("网络异常，请稍后重试")
    } finally {
      setLoading(false)
    }
  }

  const inputClass = "flex items-center rounded-[16px] border border-[#E7EEF0] bg-[#F8FAFB] pl-4 pr-3 h-[56px] focus-within:ring-2 focus-within:ring-[#20B8A8]/20 focus-within:border-[#20B8A8] transition"

  return (
    <main className="relative min-h-screen bg-gradient-to-b from-[#EAF8FF] via-white to-[#FFF5F7] flex flex-col overflow-hidden safe-top safe-bottom">
      {/* ═══════════ ① Hero 区 ═══════════ */}
      <header className="relative flex flex-col items-center pt-10 pb-3 px-6">
        {/* 兔子 — 使用正式素材 */}
        <div className="absolute left-[-10px] top-[32px] h-[176px] w-[154px] z-0 pointer-events-none select-none object-contain drop-shadow-[0_8px_18px_rgba(31,45,61,0.08)] min-[414px]:left-[-6px] min-[414px]:top-[28px] min-[414px]:h-[190px] min-[414px]:w-[168px] min-[430px]:left-[4px] min-[430px]:top-[24px] min-[430px]:h-[202px] min-[430px]:w-[178px]">
          <img src="/rabbit-hero.png" alt="" className="w-full h-full" />
        </div>

        {/* Logo */}
        <img src="/logo.png" alt="悠米伴学" className="w-16 h-16 object-contain mb-2 z-10" />
        <h1 className="text-[26px] font-bold text-[#1F2D3D] tracking-tight z-10">悠米伴学</h1>
        <p className="text-xs text-[#718096] mt-0.5 tracking-wider z-10">AI Family Learning Workspace</p>
        <p className="text-sm text-[#718096] mt-2 z-10">
          让学习更轻松，成长更快乐<span className="ml-0.5">❤️</span>
        </p>
      </header>

      {/* ═══════════ ② 登录卡片 ═══════════ */}
      <div className="flex-1 px-6 pb-10">
        <div
          className="mx-auto w-full rounded-[32px] bg-white px-7 py-7 mt-1"
          style={{
            maxWidth: "430px",
            boxShadow: "0 8px 32px rgba(0,0,0,0.06), 0 2px 8px rgba(0,0,0,0.04)",
          }}
        >
          {/* Tab 切换 */}
          <div className="flex rounded-xl bg-[#F1F5F9] p-1 mb-6">
            <button type="button" onClick={() => setTab("password")}
              className={`flex-1 rounded-lg py-2.5 text-[15px] font-semibold transition ${
                tab === "password"
                  ? "bg-white text-[#1F2D3D] shadow-sm"
                  : "text-[#718096]"
              }`}
            >密码登录</button>
            <button type="button" onClick={() => setTab("code")}
              className={`flex-1 rounded-lg py-2.5 text-[15px] font-semibold transition ${
                tab === "code"
                  ? "bg-white text-[#1F2D3D] shadow-sm"
                  : "text-[#718096]"
              }`}
            >验证码登录</button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-5">
            {error && (
              <p className="rounded-xl bg-red-50 px-4 py-3 text-center text-sm text-red-600">{error}</p>
            )}

            {/* 手机号 */}
            <div className={inputClass}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#25B8A8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mr-3">
                <rect x="5" y="2" width="14" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/>
              </svg>
              <span className="text-[15px] text-[#718096] mr-2 font-medium">+86</span>
              <input type="tel" placeholder="请输入手机号" value={phone}
                onChange={(e) => setPhone(e.target.value)}
                maxLength={11} required disabled={loading}
                className="flex-1 bg-transparent text-[15px] text-[#1F2D3D] placeholder:text-[#9AA7B2] outline-none disabled:opacity-50 min-w-0"
              />
              {tab === "code" && (
                <button type="button" onClick={handleSendCode}
                  disabled={codeCountdown > 0 || phone.trim().length < 11 || loading}
                  className="shrink-0 rounded-xl bg-[#EAF8FF] px-4 py-2 text-[13px] font-semibold text-[#20B8A8] transition hover:bg-[#D5F0F5] disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap ml-2"
                >
                  {codeCountdown > 0 ? `${codeCountdown}s` : "获取验证码"}
                </button>
              )}
            </div>

            {/* 密码 / 验证码 */}
            {tab === "password" ? (
              <div className={inputClass}>
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#25B8A8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mr-3">
                  <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                </svg>
                <input type={showPwd ? "text" : "password"}
                  placeholder="请输入密码" value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  minLength={6} required disabled={loading}
                  className="flex-1 bg-transparent text-[15px] text-[#1F2D3D] placeholder:text-[#9AA7B2] outline-none disabled:opacity-50"
                />
                <button type="button" onClick={() => setShowPwd(!showPwd)}
                  className="ml-2 text-[#9AA7B2] hover:text-[#718096] transition shrink-0" tabIndex={-1}>
                  {showPwd ? <EyeOff size={20} /> : <Eye size={20} />}
                </button>
              </div>
            ) : (
              <div className={inputClass}>
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#25B8A8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mr-3">
                  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
                </svg>
                <input type="text" placeholder="请输入验证码" value={code}
                  onChange={(e) => setCode(e.target.value)}
                  maxLength={6} required disabled={loading}
                  className="flex-1 bg-transparent text-[15px] text-[#1F2D3D] placeholder:text-[#9AA7B2] outline-none disabled:opacity-50"
                />
              </div>
            )}

            {/* 协议 */}
            <label className="flex items-start gap-2 cursor-pointer select-none">
              <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded border-[#CBD5E0] accent-[#20B8A8]" />
              <span className="text-[13px] text-[#718096] leading-relaxed">
                已阅读并同意
                <span className="text-[#20B8A8] underline cursor-pointer">《用户协议》</span>和
                <span className="text-[#20B8A8] underline cursor-pointer">《隐私政策》</span>
              </span>
            </label>

            {/* 登录按钮 */}
            <button type="submit" disabled={!canSubmit}
              className="w-full h-[58px] rounded-full text-white text-[18px] font-semibold transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed active:scale-[0.98]"
              style={{
                background: canSubmit
                  ? "linear-gradient(135deg, #20B8A8 0%, #27D3C1 100%)"
                  : "linear-gradient(135deg, #CBD5E0 0%, #A0AEC0 100%)",
                boxShadow: canSubmit ? "0 4px 16px rgba(32,184,168,0.3)" : "none",
              }}
            >
              {loading ? "登录中..." : "登  录"}
            </button>
          </form>

          {/* 第三方登录 */}
          <div className="mt-7 flex flex-col items-center gap-4">
            <div className="flex items-center gap-3 w-full">
              <div className="flex-1 h-px bg-[#E7EEF0]" />
              <span className="text-[13px] text-[#9AA7B2] shrink-0">其他登录方式</span>
              <div className="flex-1 h-px bg-[#E7EEF0]" />
            </div>
            <div className="flex gap-7">
              <button type="button" disabled
                className="w-[52px] h-[52px] rounded-full bg-[#07C160] flex items-center justify-center shadow-[0_2px_8px_rgba(7,193,96,0.2)] opacity-70 cursor-not-allowed"
                title="微信登录（即将上线）">
                <MessageCircle size={24} className="text-white" />
              </button>
              <button type="button" disabled
                className="w-[52px] h-[52px] rounded-full bg-[#12B7F5] flex items-center justify-center shadow-[0_2px_8px_rgba(18,183,245,0.2)] opacity-70 cursor-not-allowed text-white text-sm font-bold"
                title="QQ登录（即将上线）">QQ</button>
              <button type="button" disabled
                className="w-[52px] h-[52px] rounded-full bg-black flex items-center justify-center shadow-[0_2px_8px_rgba(0,0,0,0.15)] opacity-70 cursor-not-allowed"
                title="Apple ID登录（即将上线）">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="white"><path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.8-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z"/></svg>
              </button>
            </div>
          </div>
        </div>

        {/* 底部去注册 */}
        <p className="text-center text-[15px] text-[#718096] mt-6">
          还没有账号？
          <button type="button" onClick={() => router.push("/register")}
            className="ml-1 text-[#20B8A8] font-semibold hover:text-[#0E8F83] transition">
            去注册
          </button>
        </p>

        {/* 安全承诺 */}
        <p className="text-center text-[13px] text-[#9AA7B2] mt-4 flex items-center justify-center gap-1.5">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
          我们将保护您的信息安全
        </p>
      </div>
    </main>
  )
}
