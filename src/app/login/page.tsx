"use client"

import { useState, useEffect, useRef } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { authApi, getToken, setToken } from "@/lib/api"

type LoginTab = "password" | "code"

export default function LoginPage() {
  const router = useRouter()

  // ── 表单状态 ──
  const [tab, setTab] = useState<LoginTab>("password")
  const [phone, setPhone] = useState("")
  const [password, setPassword] = useState("")
  const [code, setCode] = useState("")
  const [agreed, setAgreed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // 验证码倒计时
  const [codeCountdown, setCodeCountdown] = useState(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const canSubmit =
    phone.trim().length >= 11 &&
    (tab === "password" ? password.length >= 6 : code.length >= 4) &&
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
    // TODO: 接入短信验证码 API
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

  // ── 登录 ──
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
        // TODO: 验证码登录接口
        setError("验证码登录暂未开放，请使用密码登录")
      }
    } catch {
      setError("网络异常，请稍后重试")
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-gradient-to-b from-primary-light/60 to-background px-4 safe-top safe-bottom">
      <div className="w-full max-w-sm">

        {/* ── 品牌区 ── */}
        <div className="flex flex-col items-center gap-2 mb-8">
          <img
            src="/logo.png"
            alt="悠米伴学"
            className="w-20 h-20 object-contain"
          />
          <p className="text-xs text-muted-foreground tracking-wide">
            AI家庭学习助手
          </p>
        </div>

        {/* ── 表单卡片 ── */}
        <div className="rounded-xl border bg-card p-6 shadow-sm">
          <h1 className="text-center text-xl font-semibold text-foreground mb-6">
            欢迎回来
          </h1>

          {/* Tab 切换 */}
          <div className="flex rounded-lg bg-muted p-1 mb-6">
            <button
              type="button"
              onClick={() => setTab("password")}
              className={`flex-1 rounded-md py-2 text-sm font-medium transition ${
                tab === "password"
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground"
              }`}
            >
              密码登录
            </button>
            <button
              type="button"
              onClick={() => setTab("code")}
              className={`flex-1 rounded-md py-2 text-sm font-medium transition ${
                tab === "code"
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground"
              }`}
            >
              验证码登录
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* 错误提示 */}
            {error && (
              <p className="rounded-md bg-destructive/10 px-3 py-2 text-center text-sm text-destructive">
                {error}
              </p>
            )}

            {/* 手机号 */}
            <div>
              <label className="block text-sm font-medium text-foreground mb-1.5">
                手机号
              </label>
              <div className="flex items-center rounded-xl border border-border bg-background px-4 py-3 focus-within:ring-2 focus-within:ring-primary focus-within:border-primary transition">
                <span className="text-sm text-muted-foreground mr-2">+86</span>
                <input
                  type="tel"
                  placeholder="请输入手机号"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  maxLength={11}
                  required
                  disabled={loading}
                  className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground outline-none disabled:opacity-50"
                />
              </div>
            </div>

            {/* 密码 / 验证码 */}
            {tab === "password" ? (
              <div>
                <label className="block text-sm font-medium text-foreground mb-1.5">
                  密码
                </label>
                <input
                  type="password"
                  placeholder="请输入密码"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  minLength={6}
                  required
                  disabled={loading}
                  className="w-full rounded-xl border border-border bg-background px-4 py-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary focus:border-primary transition disabled:opacity-50"
                />
              </div>
            ) : (
              <div>
                <label className="block text-sm font-medium text-foreground mb-1.5">
                  验证码
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    placeholder="请输入验证码"
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    maxLength={6}
                    required
                    disabled={loading}
                    className="flex-1 rounded-xl border border-border bg-background px-4 py-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary focus:border-primary transition disabled:opacity-50"
                  />
                  <button
                    type="button"
                    onClick={handleSendCode}
                    disabled={codeCountdown > 0 || phone.trim().length < 11 || loading}
                    className="shrink-0 rounded-xl border border-primary px-3 py-3 text-sm font-medium text-primary transition hover:bg-primary/5 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {codeCountdown > 0 ? `${codeCountdown}s` : "获取验证码"}
                  </button>
                </div>
              </div>
            )}

            {/* 协议勾选 */}
            <label className="flex items-start gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={agreed}
                onChange={(e) => setAgreed(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded border-border accent-primary"
              />
              <span className="text-xs text-muted-foreground leading-relaxed">
                已阅读并同意
                <span className="text-primary underline cursor-pointer">《用户协议》</span>
                和
                <span className="text-primary underline cursor-pointer">《隐私政策》</span>
              </span>
            </label>

            {/* 登录按钮 */}
            <Button
              type="submit"
              className="w-full py-6 text-base font-medium"
              disabled={!canSubmit}
            >
              {loading ? "登录中..." : "登  录"}
            </Button>
          </form>
        </div>

        {/* ── 社交登录占位 ── */}
        <div className="mt-8 flex flex-col items-center gap-4">
          <div className="flex items-center gap-3 w-full">
            <div className="flex-1 h-px bg-border" />
            <span className="text-xs text-muted-foreground shrink-0">
              其他登录方式
            </span>
            <div className="flex-1 h-px bg-border" />
          </div>

          <div className="flex gap-6">
            <button
              type="button"
              disabled
              className="flex flex-col items-center gap-1 opacity-50 cursor-not-allowed"
              title="微信登录（即将上线）"
            >
              <span className="w-10 h-10 rounded-full bg-muted flex items-center justify-center text-lg">
                💬
              </span>
              <span className="text-xs text-muted-foreground">微信</span>
            </button>

            <button
              type="button"
              disabled
              className="flex flex-col items-center gap-1 opacity-50 cursor-not-allowed"
              title="家长账号（即将上线）"
            >
              <span className="w-10 h-10 rounded-full bg-muted flex items-center justify-center text-lg">
                👨‍👧
              </span>
              <span className="text-xs text-muted-foreground">家长</span>
            </button>

            <button
              type="button"
              onClick={() => {
                setToken("guest-token")
                router.push("/")
              }}
              className="flex flex-col items-center gap-1 transition hover:opacity-80"
            >
              <span className="w-10 h-10 rounded-full bg-muted flex items-center justify-center text-lg">
                👤
              </span>
              <span className="text-xs text-muted-foreground">游客</span>
            </button>
          </div>
        </div>

        {/* ── 底部链接 ── */}
        <p className="mt-8 text-center text-sm text-muted-foreground">
          还没有账号？
          <button
            type="button"
            onClick={() => router.push("/register")}
            className="ml-1 underline text-primary hover:text-primary/80 transition"
          >
            去注册
          </button>
        </p>

      </div>
    </main>
  )
}
