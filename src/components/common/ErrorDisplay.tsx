"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";

interface ErrorDisplayProps {
  message: string;
  action?: string;
  onRetry?: () => void;
}

export default function ErrorDisplay({ message, action, onRetry }: ErrorDisplayProps) {
  return (
    <div className="bg-destructive/5 border border-destructive/20 rounded-2xl p-6 text-center space-y-3">
      <AlertTriangle className="w-10 h-10 text-destructive/60 mx-auto" strokeWidth={1.5} />
      <p className="text-sm text-foreground">{message}</p>
      {action && (
        <p className="text-xs text-muted-foreground">{action}</p>
      )}
      {onRetry && (
        <button
          onClick={onRetry}
          className="inline-flex items-center gap-1.5 rounded-xl border border-border px-4 py-2 text-sm text-foreground hover:bg-muted transition"
        >
          <RefreshCw className="w-3.5 h-3.5" /> 重试
        </button>
      )}
    </div>
  );
}
