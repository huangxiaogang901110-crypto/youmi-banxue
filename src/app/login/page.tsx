"use client"

import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { authApi, getToken, setToken } from "@/lib/api"

export default function LoginPage() {
  const router = useRouter()
  const [phone, setPhone] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (getToken()) {
      router.push("/")
    }
  }, [router])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)

    try {
      const res = await authApi.login(phone, password)
      if (res.ok && res.data) {
        setToken(res.data.token)
        router.push("/")
      } else {
        setError(res.message || "登录失败，请重试")
      }
    } catch {
      setError("网络异常，请稍后重试")
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm rounded-xl border bg-card p-6 shadow-sm">
        <h1 className="text-center text-2xl font-semibold text-foreground">
          登录
        </h1>

        <form onSubmit={handleSubmit} className="mt-6 space-y-4">
          {error && (
            <p className="rounded-md bg-destructive/10 px-3 py-2 text-center text-sm text-destructive">
              {error}
            </p>
          )}

          <input
            type="tel"
            placeholder="手机号"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            required
            disabled={loading}
            className="w-full rounded-md border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-50"
          />

          <input
            type="password"
            placeholder="密码"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            disabled={loading}
            className="w-full rounded-md border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-50"
          />

          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? "登录中..." : "登录"}
          </Button>
        </form>

        <p className="mt-4 text-center text-sm text-muted-foreground">
          还没有账号？
          <button
            type="button"
            onClick={() => router.push("/register")}
            className="ml-1 underline text-primary hover:text-primary/80"
          >
            去注册
          </button>
        </p>
      </div>
    </main>
  )
}
