import { useCallback, useEffect, useState } from "react";
import {
  isSpeechSynthesisSupported,
  stripMarkdownForSpeech,
} from "../utils/speech.js";

const AUTO_SPEAK_STORAGE_KEY = "wria_auto_speak";

function readStoredAutoSpeak() {
  try {
    return window.localStorage.getItem(AUTO_SPEAK_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

export function useVoiceOutput({ lang = "en-US" } = {}) {
  const isSupported = isSpeechSynthesisSupported();
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [speakingMessageId, setSpeakingMessageId] = useState(null);
  const [autoSpeak, setAutoSpeakState] = useState(readStoredAutoSpeak);

  const stop = useCallback(() => {
    if (isSupported) {
      try {
        window.speechSynthesis.cancel();
      } catch {
        // Ignore cancel errors.
      }
    }
    setIsSpeaking(false);
    setSpeakingMessageId(null);
  }, [isSupported]);

  const speak = useCallback(
    (text, messageId = null) => {
      if (!isSupported) {
        return;
      }

      const cleanText = stripMarkdownForSpeech(text);
      if (!cleanText) {
        return;
      }

      try {
        window.speechSynthesis.cancel();
      } catch {
        // Ignore cancel errors before starting new speech.
      }

      const utterance = new window.SpeechSynthesisUtterance(cleanText);
      utterance.lang = lang;

      utterance.onstart = () => {
        setIsSpeaking(true);
        setSpeakingMessageId(messageId);
      };

      utterance.onend = () => {
        setIsSpeaking(false);
        setSpeakingMessageId(null);
      };

      utterance.onerror = () => {
        setIsSpeaking(false);
        setSpeakingMessageId(null);
      };

      setIsSpeaking(true);
      setSpeakingMessageId(messageId);

      try {
        window.speechSynthesis.speak(utterance);
      } catch {
        setIsSpeaking(false);
        setSpeakingMessageId(null);
      }
    },
    [isSupported, lang]
  );

  const setAutoSpeak = useCallback(
    (enabled) => {
      const next = Boolean(enabled);
      setAutoSpeakState(next);
      try {
        window.localStorage.setItem(AUTO_SPEAK_STORAGE_KEY, String(next));
      } catch {
        // Ignore storage errors.
      }
      if (!next) {
        stop();
      }
    },
    [stop]
  );

  const toggleAutoSpeak = useCallback(() => {
    setAutoSpeak(!autoSpeak);
  }, [autoSpeak, setAutoSpeak]);

  useEffect(() => {
    return () => {
      if (isSupported) {
        try {
          window.speechSynthesis.cancel();
        } catch {
          // Ignore cleanup errors.
        }
      }
    };
  }, [isSupported]);

  return {
    isSupported,
    isSpeaking,
    speakingMessageId,
    autoSpeak,
    setAutoSpeak,
    toggleAutoSpeak,
    speak,
    stop,
  };
}
