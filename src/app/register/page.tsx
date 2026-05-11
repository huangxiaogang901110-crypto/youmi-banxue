"use client"

import { useState, useEffect, useRef } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { authApi, getToken, setToken } from "@/lib/api"
import { Eye, EyeOff } from "lucide-react"

export default function RegisterPage() {
  const router = useRouter()

  // ── 表单状态 ──
  const [phone, setPhone] = useState("")
  const [code, setCode] = useState("")
  const [password, setPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [showPwd, setShowPwd] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [agreed, setAgreed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // 验证码倒计时
  const [codeCountdown, setCodeCountdown] = useState(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const canSubmit =
    phone.trim().length >= 11 &&
    code.length >= 4 &&
    password.length >= 6 &&
    password === confirmPassword &&
    agreed &&
    !loading

  // ── 已有 token 直接跳转 ──
  useEffect(() => {
    if (getToken()) router.push("/")
  }, [router])

  // ── 倒计时清理 ──
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [])

  // ── 发送验证码（占位） ──
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

  // ── 注册 ──
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
    <main className="flex min-h-screen flex-col bg-gradient-to-b from-[#DAEEF9] via-[#F9FBFD] to-[#F2F0F4] safe-top safe-bottom">
      {/* ── ① Header：品牌 + 插画 ── */}
      <header className="relative flex flex-col items-center pt-14 pb-6 px-4">
        {/* 语言切换（右上角） */}
        <button
          type="button"
          disabled
          className="absolute top-6 right-4 flex items-center gap-1 rounded-full border border-white/60 bg-white/40 px-3 py-1.5 text-xs text-slate-600 backdrop-blur-sm opacity-60 cursor-not-allowed"
        >
          🌐 简体中文 ▾
        </button>

        {/* Logo */}
        <div className="w-16 h-16 rounded-2xl bg-[#29C7B5] flex items-center justify-center mb-3 shadow-lg shadow-[#29C7B5]/20">
          <span className="text-white text-2xl font-bold">U</span>
        </div>
        <h1 className="text-2xl font-bold text-slate-800 tracking-tight">悠米伴学</h1>
        <p className="text-xs text-slate-500 mt-1 tracking-wide">AI Family Learning Workspace</p>
        <p className="text-sm text-slate-600 mt-2">让学习更轻松，成长更快乐 ❤️</p>

        {/* 插画占位：兔子 */}
        <div className="mt-4 text-5xl select-none">🐰🎒⭐</div>
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

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* 错误提示 */}
            {error && (
              <p className="rounded-lg bg-red-50 px-3 py-2.5 text-center text-sm text-red-600">
                {error}
              </p>
            )}

            {/* ① 手机号 */}
            <div className="relative">
              <div className="flex items-center rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 focus-within:ring-2 focus-within:ring-[#29C7B5]/30 focus-within:border-[#29C7B5] transition">
                <span className="text-[#29C7B5] mr-2.5">📱</span>
                <input
                  type="tel"
                  placeholder="手机号"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  maxLength={11}
                  required
                  disabled={loading}
                  className="flex-1 bg-transparent text-sm text-slate-800 placeholder:text-slate-400 outline-none disabled:opacity-50"
                />
                <button
                  type="button"
                  onClick={handleSendCode}
                  disabled={codeCountdown > 0 || phone.trim().length < 11 || loading}
                  className="shrink-0 rounded-lg bg-[#29C7B5]/10 px-3 py-1.5 text-xs font-medium text-[#29C7B5] transition hover:bg-[#29C7B5]/20 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {codeCountdown > 0 ? `${codeCountdown}s` : "获取验证码"}
                </button>
              </div>
            </div>

            {/* ② 验证码 */}
            <div className="relative">
              <div className="flex items-center rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 focus-within:ring-2 focus-within:ring-[#29C7B5]/30 focus-within:border-[#29C7B5] transition">
                <span className="text-[#29C7B5] mr-2.5">🛡️</span>
                <input
                  type="text"
                  placeholder="短信验证码"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  maxLength={6}
                  required
                  disabled={loading}
                  className="flex-1 bg-transparent text-sm text-slate-800 placeholder:text-slate-400 outline-none disabled:opacity-50"
                />
              </div>
            </div>

            {/* ③ 密码 */}
            <div className="relative">
              <div className="flex items-center rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 focus-within:ring-2 focus-within:ring-[#29C7B5]/30 focus-within:border-[#29C7B5] transition">
                <span className="text-[#29C7B5] mr-2.5">🔒</span>
                <input
                  type={showPwd ? "text" : "password"}
                  placeholder="设置密码（6-20位，含字母或数字）"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  minLength={6}
                  maxLength={20}
                  required
                  disabled={loading}
                  className="flex-1 bg-transparent text-sm text-slate-800 placeholder:text-slate-400 outline-none disabled:opacity-50"
                />
                <button
                  type="button"
                  onClick={() => setShowPwd(!showPwd)}
                  className="ml-2 text-slate-400 hover:text-slate-600 transition shrink-0"
                  tabIndex={-1}
                >
                  {showPwd ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </div>

            {/* ④ 确认密码 */}
            <div className="relative">
              <div className="flex items-center rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 focus-within:ring-2 focus-within:ring-[#29C7B5]/30 focus-within:border-[#29C7B5] transition">
                <span className="text-[#29C7B5] mr-2.5">🔒</span>
                <input
                  type={showConfirm ? "text" : "password"}
                  placeholder="确认密码"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  minLength={6}
                  maxLength={20}
                  required
                  disabled={loading}
                  className="flex-1 bg-transparent text-sm text-slate-800 placeholder:text-slate-400 outline-none disabled:opacity-50"
                />
                <button
                  type="button"
                  onClick={() => setShowConfirm(!showConfirm)}
                  className="ml-2 text-slate-400 hover:text-slate-600 transition shrink-0"
                  tabIndex={-1}
                >
                  {showConfirm ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </div>

            {/* 协议勾选 */}
            <label className="flex items-start gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={agreed}
                onChange={(e) => setAgreed(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded border-slate-300 accent-[#29C7B5]"
              />
              <span className="text-xs text-slate-500 leading-relaxed">
                我已阅读并同意
                <span className="text-[#29C7B5] underline cursor-pointer">《用户协议》</span>
                和
                <span className="text-[#29C7B5] underline cursor-pointer">《隐私政策》</span>
              </span>
            </label>

            {/* 注册按钮 */}
            <Button
              type="submit"
              className="w-full py-6 text-base font-medium bg-[#29C7B5] hover:bg-[#22b0a0] text-white rounded-xl"
              disabled={!canSubmit}
            >
              {loading ? "注册中..." : "注册"}
            </Button>
          </form>

          {/* 第三方登录 */}
          <div className="mt-6 flex flex-col items-center gap-4">
            <div className="flex items-center gap-3 w-full">
              <div className="flex-1 h-px bg-slate-200" />
              <span className="text-xs text-slate-400 shrink-0">或使用以下方式注册</span>
              <div className="flex-1 h-px bg-slate-200" />
            </div>

            <div className="flex gap-8">
              {/* 微信 */}
              <button
                type="button"
                disabled
                className="flex flex-col items-center gap-1 opacity-60 cursor-not-allowed"
                title="微信注册（即将上线）"
              >
                <span className="w-11 h-11 rounded-full bg-green-50 flex items-center justify-center text-xl">
                  💬
                </span>
              </button>
              {/* QQ */}
              <button
                type="button"
                disabled
                className="flex flex-col items-center gap-1 opacity-60 cursor-not-allowed"
                title="QQ注册（即将上线）"
              >
                <span className="w-11 h-11 rounded-full bg-blue-50 flex items-center justify-center text-xl">
                  🐧
                </span>
              </button>
              {/* Apple */}
              <button
                type="button"
                disabled
                className="flex flex-col items-center gap-1 opacity-60 cursor-not-allowed"
                title="Apple ID注册（即将上线）"
              >
                <span className="w-11 h-11 rounded-full bg-slate-100 flex items-center justify-center text-xl">
                  🍎
                </span>
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* ── ③ Footer ── */}
      <footer className="relative flex flex-col items-center pb-10 pt-4 px-4">
        {/* 安全承诺 */}
        <div className="flex items-center gap-1.5 text-xs text-slate-400 mb-6">
          <span>🛡️</span>
          <span>我们将保护您的信息安全</span>
        </div>

        {/* 装饰 */}
        <div className="flex justify-between items-end w-full max-w-sm">
          <span className="text-2xl select-none">🌿🍃</span>
          <span className="text-2xl select-none">📚📖📕</span>
        </div>
      </footer>
    </main>
  )
}
