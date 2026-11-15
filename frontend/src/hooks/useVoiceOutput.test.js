import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useVoiceOutput } from "./useVoiceOutput.js";

describe("useVoiceOutput", () => {
  let utterances;
  let mockSynth;
  const originalSynth = window.speechSynthesis;
  const originalUtterance = window.SpeechSynthesisUtterance;

  beforeEach(() => {
    window.localStorage.clear();
    utterances = [];
    mockSynth = {
      speak: vi.fn((utt) => {
        utterances.push(utt);
        utt.onstart?.();
      }),
      cancel: vi.fn(),
    };
    Object.defineProperty(window, "speechSynthesis", {
      value: mockSynth,
      configurable: true,
      writable: true,
    });
    window.SpeechSynthesisUtterance = function (text) {
      this.text = text;
      this.lang = "";
      this.onstart = null;
      this.onend = null;
      this.onerror = null;
    };
  });

  afterEach(() => {
    Object.defineProperty(window, "speechSynthesis", {
      value: originalSynth,
      configurable: true,
      writable: true,
    });
    window.SpeechSynthesisUtterance = originalUtterance;
  });

  it("speaks stripped markdown and tracks speakingMessageId", () => {
    const { result } = renderHook(() => useVoiceOutput());
    expect(result.current.isSupported).toBe(true);
    expect(result.current.isSpeaking).toBe(false);

    act(() => {
      result.current.speak("**Denver** had `42` snow days.", "msg-1");
    });

    expect(mockSynth.cancel).toHaveBeenCalledTimes(1);
    expect(mockSynth.speak).toHaveBeenCalledTimes(1);
    expect(utterances[0].text).toBe("Denver had 42 snow days.");
    expect(result.current.isSpeaking).toBe(true);
    expect(result.current.speakingMessageId).toBe("msg-1");

    act(() => {
      utterances[0].onend();
    });
    expect(result.current.isSpeaking).toBe(false);
    expect(result.current.speakingMessageId).toBeNull();
  });

  it("stops speech on demand and on unmount", () => {
    const { result, unmount } = renderHook(() => useVoiceOutput());

    act(() => {
      result.current.speak("Hello world", "msg-2");
    });
    expect(result.current.isSpeaking).toBe(true);

    act(() => {
      result.current.stop();
    });
    expect(result.current.isSpeaking).toBe(false);
    expect(result.current.speakingMessageId).toBeNull();

    unmount();
    expect(mockSynth.cancel).toHaveBeenCalled();
  });

  it("persists autoSpeak preference in localStorage", () => {
    const { result } = renderHook(() => useVoiceOutput());
    expect(result.current.autoSpeak).toBe(false);

    act(() => {
      result.current.toggleAutoSpeak();
    });
    expect(result.current.autoSpeak).toBe(true);
    expect(window.localStorage.getItem("wria_auto_speak")).toBe("true");

    act(() => {
      result.current.toggleAutoSpeak();
    });
    expect(result.current.autoSpeak).toBe(false);
    expect(window.localStorage.getItem("wria_auto_speak")).toBe("false");
  });
});
