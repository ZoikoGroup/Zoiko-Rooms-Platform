"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const getSpeechRecognition = (): SpeechRecognition | null => {
  if (typeof window === "undefined") return null;
  const w = window as Window & { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown };
  const SR = (w.SpeechRecognition || w.webkitSpeechRecognition) as
    | { new (): SpeechRecognition }
    | undefined;
  return SR ? new SR() : null;
};

export function useSpeechRecognition() {
  const [isListening, setIsListening] = useState(false);
  const [isSupported, setIsSupported] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recognitionRef = useRef<SpeechRecognition | null>(null);

  useEffect(() => {
    const recognition = getSpeechRecognition();
    setIsSupported(!!recognition);
    recognitionRef.current = recognition;
  }, []);

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop();
    setIsListening(false);
  }, []);

  const startListening = useCallback(
    (onResult: (transcript: string) => void) => {
      const recognition = recognitionRef.current;
      if (!recognition) {
        setError("Speech recognition is not supported in this browser.");
        return;
      }

      setError(null);

      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.lang = "en-US";

      let finalTranscript = "";

      recognition.onresult = (event: SpeechRecognitionEvent) => {
        let interimTranscript = "";
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i];
          const transcript = result[0].transcript;
          if (result.isFinal) {
            finalTranscript += transcript;
          } else {
            interimTranscript += transcript;
          }
        }
        onResult((finalTranscript + interimTranscript).trim());
      };

      recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
        if (event.error === "not-allowed") {
          setError("Microphone permission was denied. Please allow mic access in your browser settings.");
        } else if (event.error === "no-speech") {
          setError("No speech detected. Please try again.");
        } else {
          setError(`Speech recognition error: ${event.error}`);
        }
        setIsListening(false);
      };

      recognition.onend = () => {
        setIsListening(false);
      };

      try {
        recognition.start();
        setIsListening(true);
      } catch {
        setError("Failed to start speech recognition. Please try again.");
      }
    },
    [],
  );

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      recognitionRef.current?.stop();
    };
  }, []);

  return { isListening, isSupported, error, startListening, stopListening, clearError: () => setError(null) };
}
