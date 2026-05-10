"use client";

import { useState, useEffect, useRef, useCallback } from "react";

interface UseTypewriterOptions {
  text: string;
  speed?: number;
  enabled?: boolean;
}

export function useTypewriter({ text, speed = 45, enabled = true }: UseTypewriterOptions) {
  const [displayed, setDisplayed] = useState("");
  const [isComplete, setIsComplete] = useState(false);
  const idxRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval>>(undefined);

  useEffect(() => {
    if (!enabled || !text) return;
    idxRef.current = 0;
    setDisplayed("");
    setIsComplete(false);

    timerRef.current = setInterval(() => {
      idxRef.current += 1;
      if (idxRef.current >= text.length) {
        setDisplayed(text);
        setIsComplete(true);
        clearInterval(timerRef.current);
      } else {
        setDisplayed(text.slice(0, idxRef.current));
      }
    }, 1000 / speed);

    return () => clearInterval(timerRef.current);
  }, [text, speed, enabled]);

  const skip = useCallback(() => {
    clearInterval(timerRef.current);
    setDisplayed(text);
    setIsComplete(true);
  }, [text]);

  return { displayed, isComplete, skip };
}
