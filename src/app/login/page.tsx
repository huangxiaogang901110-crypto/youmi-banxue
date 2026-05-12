"use client"

import { useState, useEffect, useRef } from "react"
import { useRouter } from "next/navigation"
import { authApi, getToken, setToken } from "@/lib/api"
import { Eye, EyeOff, MessageCircle, Camera, Bot, BarChart3, UserCheck, Users, User } from "lucide-react"

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

  const inputClass =
    "flex items-center rounded-lg border border-[#EBEEF2] bg-[#F7F8FA] pl-4 pr-3 h-12 focus-within:ring-2 focus-within:ring-[#2EC899]/20 focus-within:border-[#2EC899] transition"

  // 功能列表数据
  const features = [
    { icon: Camera, title: "拍照识别作业", desc: "一拍即识别，快速整理成清晰任务" },
    { icon: Bot, title: "AI 单题辅导", desc: "分步讲解，启发思路，个性化学习" },
    { icon: BarChart3, title: "错题沉淀与学习报告", desc: "错题自动归集，学习数据一目了然" },
  ]

  return (
    <main className="relative min-h-screen flex flex-col items-center justify-center safe-top safe-bottom px-4 py-8"
      style={{ background: "#F8F9FB" }}
    >
      {/* 背景星形装饰 */}
      <div className="absolute inset-0 pointer-events-none select-none overflow-hidden">
        <div className="absolute top-[10%] right-[15%] text-[#2EC899]/15 text-1xl opacity-50">✦</div>
        <div className="absolute top-[20%] left-[10%] text-[#2EC899]/10 text-sm opacity-40">✦</div>
        <div className="absolute bottom-[25%] right-[20%] text-[#2EC899]/10 text-lg opacity-30">✦</div>
        <div className="absolute bottom-[15%] left-[18%] text-[#2EC899]/12 text-base opacity-35">✦</div>
        <div className="absolute top-[35%] right-[8%] text-[#2EC899]/8 text-xs opacity-30">✧</div>
      </div>

      {/* ═══════════ 主卡片: 左右两栏 ═══════════ */}
      <div className="relative z-10 flex flex-col lg:flex-row w-full max-w-[960px] rounded-[20px] bg-white overflow-hidden"
        style={{ boxShadow: "0 8px 32px rgba(0,0,0,0.06), 0 2px 8px rgba(0,0,0,0.04)" }}
      >
        {/* ═══ 左侧: 品牌 + 功能介绍 ═══ */}
        <div className="flex-1 flex flex-col items-center justify-center px-8 py-10 lg:py-12"
          style={{ background: "linear-gradient(180deg, #F0FDF9 0%, #FFFFFF 100%)" }}
        >
          {/* 猫吉祥物 */}
          <div className="w-[100px] h-[100px] rounded-full bg-[#E6F7F2] flex items-center justify-center mb-5">
            <img src="/rabbit-hero.png" alt="悠米" className="w-[80px] h-[80px] object-contain" />
          </div>

          {/* 品牌 */}
          <h1 className="text-[28px] font-bold text-[#333]">悠米伴学</h1>
          <p className="text-base text-[#666] mt-1.5">AI 家庭学习助手</p>

          {/* 分隔线 */}
          <div className="w-10 h-0.5 bg-[#2EC899] mt-5 mb-5" />

          {/* 功能介绍 */}
          <p className="text-sm text-[#666] mb-6">把作业整理成清晰任务，让孩子更高效学习</p>

          <div className="space-y-6 w-full max-w-[280px]">
            {features.map((f, i) => (
              <div key={i} className="flex items-start gap-3">
                <div className="w-10 h-10 rounded-full bg-[#E6F7F2] flex items-center justify-center shrink-0 mt-0.5">
                  <f.icon size={18} className="text-[#2EC899]" strokeWidth={1.5} />
                </div>
                <div>
                  <p className="text-[15px] font-semibold text-[#333]">{f.title}</p>
                  <p className="text-[13px] text-[#666] mt-0.5">{f.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* ═══ 右侧: 登录表单 ═══ */}
        <div className="flex-1 flex flex-col px-8 py-10 lg:py-12">
          {/* 欢迎标题 */}
          <h2 className="text-[28px] font-bold text-[#333]">欢迎回来</h2>
          <p className="text-sm text-[#666] mt-1.5 mb-8">登录后继续孩子的学习之旅</p>

          {/* Tab 切换 */}
          <div className="flex gap-6 mb-6">
            <button type="button" onClick={() => setTab("code")}
              className={`relative pb-2 text-base font-semibold transition ${
                tab === "code" ? "text-[#2EC899]" : "text-[#999]"
              }`}
            >
              验证码登录
              {tab === "code" && <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-[#2EC899] rounded-full" />}
            </button>
            <button type="button" onClick={() => setTab("password")}
              className={`relative pb-2 text-base font-semibold transition ${
                tab === "password" ? "text-[#2EC899]" : "text-[#999]"
              }`}
            >
              密码登录
              {tab === "password" && <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-[#2EC899] rounded-full" />}
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <p className="rounded-xl bg-red-50 px-4 py-3 text-center text-sm text-red-600">{error}</p>
            )}

            {/* 手机号 */}
            <div className={inputClass}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#999" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mr-3">
                <rect x="5" y="2" width="14" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/>
              </svg>
              <input type="tel" placeholder="请输入手机号" value={phone}
                onChange={(e) => setPhone(e.target.value)}
                maxLength={11} required disabled={loading}
                className="flex-1 bg-transparent text-[15px] text-[#333] placeholder:text-[#999] outline-none disabled:opacity-50 min-w-0"
              />
              {tab === "code" && (
                <button type="button" onClick={handleSendCode}
                  disabled={codeCountdown > 0 || phone.trim().length < 11 || loading}
                  className="shrink-0 rounded-2xl bg-transparent px-3 py-1.5 text-[13px] font-semibold text-[#2EC899] transition hover:opacity-70 disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap ml-2"
                >
                  {codeCountdown > 0 ? `${codeCountdown}s` : "获取验证码"}
                </button>
              )}
            </div>

            {/* 密码 / 验证码 */}
            {tab === "password" ? (
              <div className={inputClass}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#999" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mr-3">
                  <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                </svg>
                <input type={showPwd ? "text" : "password"}
                  placeholder="请输入密码" value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  minLength={6} required disabled={loading}
                  className="flex-1 bg-transparent text-[15px] text-[#333] placeholder:text-[#999] outline-none disabled:opacity-50"
                />
                <button type="button" onClick={() => setShowPwd(!showPwd)}
                  className="ml-2 text-[#999] hover:text-[#666] transition shrink-0" tabIndex={-1}>
                  {showPwd ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            ) : (
              <div className={inputClass}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#999" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mr-3">
                  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
                </svg>
                <input type="text" placeholder="请输入验证码" value={code}
                  onChange={(e) => setCode(e.target.value)}
                  maxLength={6} required disabled={loading}
                  className="flex-1 bg-transparent text-[15px] text-[#333] placeholder:text-[#999] outline-none disabled:opacity-50"
                />
              </div>
            )}

            {/* 协议 */}
            <label className="flex items-start gap-2 cursor-pointer select-none mt-5">
              <div className="relative mt-0.5 shrink-0">
                <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} className="sr-only" />
                <div className={`w-4 h-4 rounded-sm border-2 flex items-center justify-center transition ${agreed ? "border-[#2EC899] bg-[#2EC899]" : "border-[#999]"}`}>
                  {agreed && <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3"><path d="M20 6 9 17l-5-5"/></svg>}
                </div>
              </div>
              <span className="text-[13px] text-[#666] leading-relaxed">
                我已阅读并同意
                <span className="text-[#2EC899] underline cursor-pointer">《用户协议》</span>
                <span className="text-[#2EC899] underline cursor-pointer">《隐私政策》</span>
              </span>
            </label>

            {/* 登录按钮 — 纯色 #2EC899，始终可点（后端校验） */}
            <button type="submit"
              className="w-full h-12 rounded-lg text-white text-base font-semibold transition-all duration-200 active:scale-[0.98] mt-5"
              style={{ background: "#2EC899" }}
            >
              {loading ? "登录中..." : "登录 / 进入工作台"}
            </button>
          </form>

          {/* 第三方登录 */}
          <div className="mt-8 flex flex-col items-center gap-4">
            <div className="flex items-center gap-3 w-full">
              <div className="flex-1 h-px bg-[#EBEEF2]" />
              <span className="text-[13px] text-[#999] shrink-0">其他登录方式</span>
              <div className="flex-1 h-px bg-[#EBEEF2]" />
            </div>
            <div className="flex gap-4">
              {/* 微信登录 */}
              <button type="button" disabled
                className="flex flex-col items-center gap-1.5 opacity-70 cursor-not-allowed">
                <div className="w-16 h-16 rounded-2xl bg-[#E6F7F2] flex items-center justify-center">
                  <MessageCircle size={22} className="text-[#07C160]" />
                </div>
                <span className="text-[13px] text-[#666]">微信登录</span>
              </button>
              {/* 家长账号 */}
              <button type="button" disabled
                className="flex flex-col items-center gap-1.5 opacity-70 cursor-not-allowed">
                <div className="w-16 h-16 rounded-2xl bg-[#FEEBEA] flex items-center justify-center">
                  <Users size={22} className="text-[#E87A6E]" />
                </div>
                <span className="text-[13px] text-[#666]">家长账号</span>
              </button>
              {/* 游客体验 */}
              <button type="button" disabled
                className="flex flex-col items-center gap-1.5 opacity-70 cursor-not-allowed">
                <div className="w-16 h-16 rounded-2xl bg-[#EBF3FE] flex items-center justify-center">
                  <User size={22} className="text-[#5B9BD5]" />
                </div>
                <span className="text-[13px] text-[#666]">游客体验</span>
              </button>
            </div>
          </div>

          {/* 底部引导 */}
          <p className="text-center text-[14px] text-[#666] mt-8">
            没有账号？
            <span className="text-[#2EC899] font-medium cursor-pointer"
              onClick={() => router.push("/register")}
            >去注册</span>
          </p>
        </div>
      </div>

      {/* ═══════════ 底部母子学习插画 ═══════════ */}
      <div className="relative z-10 w-full max-w-[960px] mt-6 hidden lg:block">
        <div className="absolute left-0 bottom-0 pointer-events-none select-none">
          <svg width="240" height="160" viewBox="0 0 240 160" fill="none" xmlns="http://www.w3.org/2000/svg" className="opacity-85">
            {/* 桌面 */}
            <rect x="20" y="100" width="200" height="8" rx="4" fill="#E8D5B0"/>
            <rect x="30" y="108" width="8" height="30" rx="2" fill="#E8D5B0"/>
            <rect x="200" y="108" width="8" height="30" rx="2" fill="#E8D5B0"/>
            {/* 盆栽 */}
            <rect x="28" y="88" width="14" height="12" rx="3" fill="#E8A87C"/>
            <circle cx="35" cy="80" r="10" fill="#7EC8A0"/>
            <circle cx="30" cy="76" r="7" fill="#6BB892"/>
            <circle cx="40" cy="78" r="6" fill="#8DD4AA"/>
            {/* 笔记本电脑 */}
            <rect x="75" y="82" width="52" height="4" rx="2" fill="#C0C0C0"/>
            <rect x="80" y="82" width="42" height="18" rx="3" fill="#E8E8E8"/>
            <rect x="84" y="84" width="34" height="12" rx="1" fill="#D0E8F0"/>
            {/* 书本叠 */}
            <rect x="140" y="88" width="22" height="3" rx="1" fill="#F0C8A0"/>
            <rect x="138" y="85" width="24" height="3" rx="1" fill="#A0C8F0"/>
            <rect x="136" y="82" width="26" height="3" rx="1" fill="#F08080"/>
            {/* 妈妈 - 左侧 */}
            <circle cx="65" cy="62" r="14" fill="#F5D0B0"/>
            <rect x="55" y="76" width="20" height="24" rx="8" fill="#5B9BD5"/>
            <circle cx="60" cy="58" r="3" fill="#333"/>
            <circle cx="70" cy="58" r="3" fill="#333"/>
            <path d="M58 67 Q65 72 72 67" stroke="#333" strokeWidth="1.5" fill="none"/>
            <ellipse cx="58" cy="54" rx="6" ry="4" fill="#E8C8A0" opacity="0.6"/>
            {/* 孩子 - 右侧 */}
            <circle cx="165" cy="70" r="10" fill="#F5D0B0"/>
            <rect x="158" y="80" width="14" height="20" rx="7" fill="#F0A0A0"/>
            <circle cx="161" cy="67" r="2.5" fill="#333"/>
            <circle cx="169" cy="67" r="2.5" fill="#333"/>
            <path d="M158 74 Q165 78 172 74" stroke="#333" strokeWidth="1.2" fill="none"/>
            <rect x="159" y="60" width="12" height="4" rx="2" fill="#E8C8A0"/>
            {/* 孩子手臂指向电脑 */}
            <path d="M160 82 Q140 86 110 84" stroke="#F5D0B0" strokeWidth="4" strokeLinecap="round"/>
            <circle cx="110" cy="84" r="2.5" fill="#F5D0B0"/>
          </svg>
        </div>
      </div>
    </main>
  )
}
