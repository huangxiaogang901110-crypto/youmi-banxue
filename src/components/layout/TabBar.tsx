"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Home, Upload, Layout, BookOpen, User } from "lucide-react";

const tabs = [
  { href: "/", label: "首页", Icon: Home },
  { href: "/upload", label: "识别作业", Icon: Upload },
  { href: "/workspace", label: "工作台", Icon: Layout },
  { href: "/mistakes", label: "错题", Icon: BookOpen },
  { href: "/profile", label: "我的", Icon: User },
];

export default function TabBar() {
  const pathname = usePathname();

  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-50 flex items-center bg-white/90 backdrop-blur-md border-t border-gray-100"
      style={{
        height: "calc(3.5rem + env(safe-area-inset-bottom, 0px))",
        paddingBottom: "env(safe-area-inset-bottom, 0px)",
      }}
    >
      {tabs.map(({ href, label, Icon }) => {
        const active =
          href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            className={`flex flex-col items-center justify-center gap-0.5 w-full h-full transition-colors ${
              active ? "text-primary" : "text-muted-foreground"
            }`}
          >
            <Icon className="w-5 h-5" strokeWidth={active ? 2.5 : 2} />
            <span className="text-[11px] leading-none">{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
