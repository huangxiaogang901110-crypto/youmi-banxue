import type { Metadata } from "next";
import "./globals.css";
import Providers from "@/components/layout/Providers";
import AppLayout from "@/components/layout/AppLayout";

export const metadata: Metadata = {
  title: "悠米伴学",
  description: "家庭学习工作台",
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
