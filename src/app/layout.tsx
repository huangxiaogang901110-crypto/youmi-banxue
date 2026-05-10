import type { Metadata, Viewport } from "next";
import "./globals.css";
import Providers from "@/components/layout/Providers";
import AppLayout from "@/components/layout/AppLayout";

export const metadata: Metadata = {
  title: "悠米伴学",
  description: "家庭学习工作台 — 拍照识题、AI 辅导、错题管理",
  manifest: "/manifest.json",
  appleWebApp: {
    capable: true,
    title: "悠米伴学",
    statusBarStyle: "default",
  },
};

export const viewport: Viewport = {
  themeColor: "#4DBBAA",
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full bg-background text-foreground">
        <Providers>
          <AppLayout>{children}</AppLayout>
        </Providers>
      </body>
    </html>
  );
}
