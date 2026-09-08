"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export function useSpeechSynthesis() {
  const [isSupported, setIsSupported] = useState(false);
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);

  useEffect(() => {
    setIsSupported(typeof window !== "undefined" && "speechSynthesis" in window);
  }, []);

  const stop = useCallback(() => {
    window.speechSynthesis.cancel();
    utteranceRef.current = null;
    setSpeakingId(null);
  }, []);

  const speak = useCallback(
    (id: string, text: string) => {
      if (!isSupported) return;

      // If already speaking this message, stop it (toggle behavior)
      if (speakingId === id) {
        stop();
        return;
      }

      // Stop any ongoing speech first
      stop();

      // Strip markdown/HTML formatting for clean speech
      const cleanText = text
        .replace(/\*\*(.*?)\*\*/g, "$1")   // bold
        .replace(/\*(.*?)\*/g, "$1")         // italic
        .replace(/__(.*?)__/g, "$1")         // bold underscore
        .replace(/_(.*?)_/g, "$1")           // italic underscore
        .replace(/`{3}[\s\S]*?`{3}/g, (m) => m.replace(/`{3}\w*\n?/g, "").replace(/`{3}/g, "")) // code blocks
        .replace(/`(.*?)`/g, "$1")           // inline code
        .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1") // links
        .replace(/^#{1,6}\s+/gm, "")         // headings
        .replace(/^[-*+]\s+/gm, "")          // unordered list markers
        .replace(/^\d+\.\s+/gm, "")          // ordered list markers
        .replace(/^>\s+/gm, "")              // blockquotes
        .replace(/~~(.*?)~~/g, "$1")         // strikethrough
        .replace(/\|/g, " ")                 // table pipes
        .replace(/[-]{3,}/g, "")             // horizontal rules
        .trim();

      if (!cleanText) return;

      const utterance = new SpeechSynthesisUtterance(cleanText);
      utterance.lang = "en-US";
      utterance.rate = 1;

      utterance.onend = () => {
        setSpeakingId(null);
        utteranceRef.current = null;
      };

      utterance.onerror = () => {
        setSpeakingId(null);
        utteranceRef.current = null;
      };

      utteranceRef.current = utterance;
      setSpeakingId(id);
      window.speechSynthesis.speak(utterance);
    },
    [isSupported, speakingId, stop],
  );

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      window.speechSynthesis.cancel();
    };
  }, []);

  return { isSupported, speakingId, speak, stop };
}
