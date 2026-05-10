"use client";

import TabBar from "./TabBar";
import EntitlementBar from "@/components/entitlement/EntitlementBar";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative min-h-dvh bg-background text-foreground flex flex-col">
      {/* StatusBar */}
      <header
        className="sticky top-0 z-40 flex items-center justify-between px-4 py-2.5 bg-background/90 backdrop-blur-sm border-b border-border safe-top"
      >
        <span className="text-lg font-bold tracking-tight text-primary">
          悠米伴学
        </span>
        <EntitlementBar />
      </header>

      {/* Content */}
      <main className="flex-1 px-4 pt-4 pb-24 max-w-2xl mx-auto w-full">
        {children}
      </main>

      {/* TabBar */}
      <TabBar />
    </div>
  );
}
