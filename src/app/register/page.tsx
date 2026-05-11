"use client"

import { useState, useEffect, useRef } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
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
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [])

  function handleSendCode() {
    if (codeCountdown > 0 || phone.trim().length < 11) return
    setCodeCountdown(60)
    timerRef.current = setInterval(() => {
      setCodeCountdown((n) => {
        if (n <= 1) {
          if (timerRef.current) clearInterval(timerRef.current)
          return 0
        }
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

  return (
    <main className="flex min-h-screen flex-col bg-gradient-to-b from-[#DAEEF9] via-[#F9FBFD] to-[#F2F0F4] safe-top safe-bottom relative overflow-hidden">
      {/* ── ① Header：品牌 + 插画（兔子左上角） ── */}
      <header className="relative flex flex-col items-center pt-12 pb-4 px-4">
        {/* 语言切换（右上角） */}
        <button
          type="button"
          disabled
          className="absolute top-6 right-4 flex items-center gap-1 rounded-full border border-white/60 bg-white/40 px-3 py-1.5 text-xs text-slate-600 backdrop-blur-sm opacity-60 cursor-not-allowed z-10"
        >
          🌐 简体中文 ▾
        </button>

        {/* R1: 兔子 — 左上角，增强 CSS 版 */}
        <div className="absolute left-2 top-4 w-28 h-40 z-0">
          {/* 身体 */}
          <div className="absolute bottom-0 left-3 w-20 h-20 bg-white rounded-[40%_40%_45%_45%] shadow-[0_4px_12px_rgba(0,0,0,0.08)]">
            {/* 肚子高光 */}
            <div className="absolute bottom-2 left-3 w-10 h-8 bg-slate-50 rounded-full opacity-60" />
          </div>
          {/* 头 */}
          <div className="absolute top-6 left-6 w-16 h-14 bg-white rounded-full shadow-[0_2px_8px_rgba(0,0,0,0.06)]">
            {/* 左耳 */}
            <div className="absolute -top-7 left-1 w-4 h-10 bg-white rounded-full rotate-[-12deg] origin-bottom shadow-sm">
              <div className="absolute inset-x-1 top-2 bottom-1 bg-pink-200 rounded-full opacity-70" />
            </div>
            {/* 右耳 */}
            <div className="absolute -top-7 right-2 w-4 h-10 bg-white rounded-full rotate-[8deg] origin-bottom shadow-sm">
              <div className="absolute inset-x-1 top-2 bottom-1 bg-pink-200 rounded-full opacity-70" />
            </div>
            {/* 左眼 */}
            <div className="absolute top-3 left-3 w-2 h-2.5 bg-slate-800 rounded-full" />
            {/* 右眼 */}
            <div className="absolute top-3 right-4 w-2 h-2.5 bg-slate-800 rounded-full" />
            {/* 微笑 */}
            <div className="absolute top-5 left-1/2 -translate-x-1/2 w-3 h-1.5 border-b-2 border-slate-400 rounded-b-full" />
            {/* 腮红 */}
            <div className="absolute top-4.5 left-1.5 w-2 h-1.5 bg-pink-200 rounded-full opacity-50" />
            <div className="absolute top-4.5 right-2 w-2 h-1.5 bg-pink-200 rounded-full opacity-50" />
          </div>
          {/* 左手 */}
          <div className="absolute top-12 left-1 w-5 h-8 bg-white rounded-full rotate-[30deg] shadow-sm" />
          {/* 右手 */}
          <div className="absolute top-12 right-1 w-5 h-8 bg-white rounded-full rotate-[-20deg] shadow-sm" />
          {/* 绿色书包 */}
          <div className="absolute top-7 -right-1 w-8 h-10 bg-[#29C7B5] rounded-xl shadow-md">
            <div className="absolute top-2 left-1 w-3 h-2 bg-[#1fa89a] rounded-sm" />
            <div className="absolute top-5 left-1 w-6 h-1 bg-white/30 rounded-full" />
          </div>
          {/* 星星纸飞机装饰 */}
          <span className="absolute -top-2 -right-2 text-amber-400 text-base animate-pulse">✦</span>
          <span className="absolute top-1 -left-2 text-amber-300 text-xs">✧</span>
          <span className="absolute -top-4 left-10 text-sky-400 text-sm rotate-12">✈</span>
        </div>

        {/* Logo + 文字组 */}
        <div className="w-14 h-14 rounded-2xl bg-[#29C7B5] flex items-center justify-center mb-2 shadow-lg shadow-[#29C7B5]/20">
          <span className="text-white text-xl font-bold">U</span>
        </div>
        <h1 className="text-2xl font-bold text-slate-800 tracking-tight">悠米伴学</h1>
        <p className="text-xs text-slate-500 mt-0.5 tracking-wide">AI Family Learning Workspace</p>
        {/* R3: 心形紧贴句末 */}
        <p className="text-sm text-slate-600 mt-1.5">让学习更轻松，成长更快乐<span className="ml-0.5">❤️</span></p>
      </header>

      {/* ── ② 表单卡片 ── */}
      <div className="flex-1 px-5 pb-8">
        <div className="mx-auto w-full max-w-sm rounded-2xl bg-white shadow-sm border border-slate-100 p-6">
          {/* Tab 切换 */}
          <div className="flex items-center border-b border-slate-100 pb-3 mb-5">
            <span className="text-base font-semibold text-[#29C7B5] border-b-2 border-[#29C7B5] pb-3 -mb-[13px]">
              注册账号
            </span>
            <button
              type="button"
              onClick={() => router.push("/login")}
              className="ml-auto text-sm text-blue-500 hover:text-blue-600 transition"
            >
              已有账号？去登录
            </button>
          </div>

          {/* R2: space-y-5 增大输入框间距 */}
          <form onSubmit={handleSubmit} className="space-y-5">
            {error && (
              <p className="rounded-lg bg-red-50 px-3 py-2.5 text-center text-sm text-red-600">
                {error}
              </p>
            )}

            {/* ① 手机号 + 验证码嵌入右侧 */}
            <div className="flex items-center rounded-xl border border-slate-200 bg-slate-50 pl-4 pr-1.5 py-1.5 focus-within:ring-2 focus-within:ring-[#29C7B5]/30 focus-within:border-[#29C7B5] transition">
              <span className="text-[#29C7B5] mr-2.5 shrink-0">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="5" y="2" width="14" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>
              </span>
              <input
                type="tel" placeholder="手机号" value={phone}
                onChange={(e) => setPhone(e.target.value)}
                maxLength={11} required disabled={loading}
                className="flex-1 bg-transparent text-sm text-slate-800 placeholder:text-slate-400 outline-none disabled:opacity-50 min-w-0"
              />
              <button
                type="button" onClick={handleSendCode}
                disabled={codeCountdown > 0 || phone.trim().length < 11 || loading}
                className="shrink-0 rounded-lg bg-[#29C7B5]/10 px-3 py-2 text-xs font-medium text-[#29C7B5] transition hover:bg-[#29C7B5]/20 disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap"
              >
                {codeCountdown > 0 ? `${codeCountdown}s` : "获取验证码"}
              </button>
            </div>

            {/* ② 验证码 */}
            <div className="flex items-center rounded-xl border border-slate-200 bg-slate-50 px-4 py-3.5 focus-within:ring-2 focus-within:ring-[#29C7B5]/30 focus-within:border-[#29C7B5] transition">
              <span className="text-[#29C7B5] mr-2.5">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
              </span>
              <input
                type="text" placeholder="短信验证码" value={code}
                onChange={(e) => setCode(e.target.value)}
                maxLength={6} required disabled={loading}
                className="flex-1 bg-transparent text-sm text-slate-800 placeholder:text-slate-400 outline-none disabled:opacity-50"
              />
            </div>

            {/* ③ 密码 */}
            <div className="flex items-center rounded-xl border border-slate-200 bg-slate-50 pl-4 pr-3 py-3.5 focus-within:ring-2 focus-within:ring-[#29C7B5]/30 focus-within:border-[#29C7B5] transition">
              <span className="text-[#29C7B5] mr-2.5">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
              </span>
              <input
                type={showPwd ? "text" : "password"}
                placeholder="设置密码（6-20位，含字母或数字）"
                value={password} onChange={(e) => setPassword(e.target.value)}
                minLength={6} maxLength={20} required disabled={loading}
                className="flex-1 bg-transparent text-sm text-slate-800 placeholder:text-slate-400 outline-none disabled:opacity-50"
              />
              <button type="button" onClick={() => setShowPwd(!showPwd)}
                className="ml-2 text-slate-400 hover:text-slate-600 transition shrink-0" tabIndex={-1}>
                {showPwd ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>

            {/* ④ 确认密码 */}
            <div className="flex items-center rounded-xl border border-slate-200 bg-slate-50 pl-4 pr-3 py-3.5 focus-within:ring-2 focus-within:ring-[#29C7B5]/30 focus-within:border-[#29C7B5] transition">
              <span className="text-[#29C7B5] mr-2.5">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
              </span>
              <input
                type={showConfirm ? "text" : "password"}
                placeholder="确认密码" value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                minLength={6} maxLength={20} required disabled={loading}
                className="flex-1 bg-transparent text-sm text-slate-800 placeholder:text-slate-400 outline-none disabled:opacity-50"
              />
              <button type="button" onClick={() => setShowConfirm(!showConfirm)}
                className="ml-2 text-slate-400 hover:text-slate-600 transition shrink-0" tabIndex={-1}>
                {showConfirm ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>

            {/* 协议 */}
            <label className="flex items-start gap-2 cursor-pointer select-none">
              <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded border-slate-300 accent-[#29C7B5]" />
              <span className="text-xs text-slate-500 leading-relaxed">
                我已阅读并同意
                <span className="text-[#29C7B5] underline cursor-pointer">《用户协议》</span>和
                <span className="text-[#29C7B5] underline cursor-pointer">《隐私政策》</span>
              </span>
            </label>

            <Button type="submit"
              className="w-full py-6 text-base font-medium bg-[#29C7B5] hover:bg-[#22b0a0] text-white rounded-xl"
              disabled={!canSubmit}>
              {loading ? "注册中..." : "注册"}
            </Button>
          </form>

          {/* R4: 三方登录 gap-6 等间距 */}
          <div className="mt-6 flex flex-col items-center gap-4">
            <div className="flex items-center gap-3 w-full">
              <div className="flex-1 h-px bg-slate-200" />
              <span className="text-xs text-slate-400 shrink-0">或使用以下方式注册</span>
              <div className="flex-1 h-px bg-slate-200" />
            </div>
            <div className="flex gap-6">
              <button type="button" disabled className="flex flex-col items-center gap-1 opacity-60 cursor-not-allowed" title="微信注册（即将上线）">
                <span className="w-11 h-11 rounded-full bg-[#07C160] flex items-center justify-center">
                  <MessageCircle size={20} className="text-white" />
                </span>
              </button>
              <button type="button" disabled className="flex flex-col items-center gap-1 opacity-60 cursor-not-allowed" title="QQ注册（即将上线）">
                <span className="w-11 h-11 rounded-full bg-[#12B7F5] flex items-center justify-center text-white text-sm font-bold">QQ</span>
              </button>
              <button type="button" disabled className="flex flex-col items-center gap-1 opacity-60 cursor-not-allowed" title="Apple ID注册（即将上线）">
                <span className="w-11 h-11 rounded-full bg-black flex items-center justify-center">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="white"><path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.8-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z"/></svg>
                </span>
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* ── ③ Footer：安全承诺 + 装饰 ── */}
      <footer className="relative flex flex-col items-center pb-10 pt-2 px-4">
        <div className="flex items-center gap-1.5 text-xs text-slate-400 mb-6">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
          <span>我们将保护您的信息安全</span>
        </div>

        {/* R5: 右下彩色书籍堆叠 + 左下绿色叶子 */}
        <div className="flex justify-between items-end w-full max-w-sm">
          {/* 左下绿色叶子 */}
          <div className="flex gap-0.5 items-end">
            <span className="text-xl text-[#29C7B5]">🌿</span>
            <span className="text-lg text-[#4dd4c5] -ml-1">🍃</span>
          </div>
          {/* 右下彩色书籍堆叠 */}
          <div className="flex items-end gap-0.5">
            <span className="text-xl">📕</span>
            <span className="text-xl text-blue-400">📘</span>
            <span className="text-xl text-amber-400">📙</span>
            <span className="text-xl text-pink-300">📚</span>
          </div>
        </div>
      </footer>
    </main>
  )
}
