import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useVoiceInput } from "./useVoiceInput.js";

describe("useVoiceInput", () => {
  let instances;
  const originalSR = window.SpeechRecognition;
  const originalWebkitSR = window.webkitSpeechRecognition;

  beforeEach(() => {
    instances = [];
    class MockSpeechRecognition {
      constructor() {
        this.lang = "";
        this.interimResults = false;
        this.continuous = false;
        this.maxAlternatives = 1;
        this.start = vi.fn(() => {
          this.onstart?.();
        });
        this.stop = vi.fn(() => {
          this.onend?.();
        });
        this.abort = vi.fn();
        instances.push(this);
      }
    }
    window.SpeechRecognition = MockSpeechRecognition;
    delete window.webkitSpeechRecognition;
  });

  afterEach(() => {
    window.SpeechRecognition = originalSR;
    window.webkitSpeechRecognition = originalWebkitSR;
  });

  it("reports unsupported when SpeechRecognition is unavailable", () => {
    delete window.SpeechRecognition;
    const { result } = renderHook(() => useVoiceInput());
    expect(result.current.isSupported).toBe(false);

    act(() => {
      result.current.startListening();
    });
    expect(result.current.error).toMatch(/not supported/i);
  });

  it("starts, captures interim and final transcripts, and stops", () => {
    const onTranscript = vi.fn();
    const { result } = renderHook(() => useVoiceInput({ onTranscript }));

    expect(result.current.isSupported).toBe(true);
    expect(result.current.isListening).toBe(false);

    act(() => {
      result.current.startListening();
    });

    expect(result.current.isListening).toBe(true);
    const rec = instances[0];
    expect(rec.lang).toBe("en-US");
    expect(rec.interimResults).toBe(true);

    act(() => {
      rec.onresult({
        resultIndex: 0,
        results: [{ isFinal: false, 0: { transcript: "compare miami" } }],
      });
    });
    expect(result.current.interimTranscript).toBe("compare miami");
    expect(onTranscript).not.toHaveBeenCalled();

    act(() => {
      rec.onresult({
        resultIndex: 0,
        results: [{ isFinal: true, 0: { transcript: "Compare Miami and Houston" } }],
      });
    });
    expect(onTranscript).toHaveBeenCalledWith("Compare Miami and Houston");
    expect(result.current.interimTranscript).toBe("");

    act(() => {
      result.current.toggleListening();
    });
    expect(rec.stop).toHaveBeenCalledTimes(1);
    expect(result.current.isListening).toBe(false);
  });

  it("handles permission error and clears error", () => {
    const { result } = renderHook(() => useVoiceInput());

    act(() => {
      result.current.startListening();
    });
    act(() => {
      instances[0].onerror({ error: "not-allowed" });
    });

    expect(result.current.isListening).toBe(false);
    expect(result.current.error).toMatch(/permission was denied/i);

    act(() => {
      result.current.clearError();
    });
    expect(result.current.error).toBeNull();
  });

  it("aborts active recognition on unmount", () => {
    const { result, unmount } = renderHook(() => useVoiceInput());
    act(() => {
      result.current.startListening();
    });
    const rec = instances[0];
    unmount();
    expect(rec.abort).toHaveBeenCalledTimes(1);
  });
});
