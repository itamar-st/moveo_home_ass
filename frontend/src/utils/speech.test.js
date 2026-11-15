import { afterEach, describe, expect, it } from "vitest";
import {
  getSpeechRecognitionConstructor,
  isSpeechRecognitionSupported,
  isSpeechSynthesisSupported,
  mapSpeechRecognitionError,
  stripMarkdownForSpeech,
} from "./speech.js";

describe("speech utilities", () => {
  const originalSR = window.SpeechRecognition;
  const originalWebkitSR = window.webkitSpeechRecognition;
  const originalSynth = window.speechSynthesis;
  const originalUtterance = window.SpeechSynthesisUtterance;

  afterEach(() => {
    window.SpeechRecognition = originalSR;
    window.webkitSpeechRecognition = originalWebkitSR;
    Object.defineProperty(window, "speechSynthesis", {
      value: originalSynth,
      configurable: true,
      writable: true,
    });
    window.SpeechSynthesisUtterance = originalUtterance;
  });

  it("detects SpeechRecognition and webkitSpeechRecognition fallback", () => {
    delete window.SpeechRecognition;
    delete window.webkitSpeechRecognition;
    expect(isSpeechRecognitionSupported()).toBe(false);
    expect(getSpeechRecognitionConstructor()).toBeNull();

    const MockWebkit = function () {};
    window.webkitSpeechRecognition = MockWebkit;
    expect(isSpeechRecognitionSupported()).toBe(true);
    expect(getSpeechRecognitionConstructor()).toBe(MockWebkit);

    const MockStandard = function () {};
    window.SpeechRecognition = MockStandard;
    expect(getSpeechRecognitionConstructor()).toBe(MockStandard);
  });

  it("detects speechSynthesis support", () => {
    Object.defineProperty(window, "speechSynthesis", {
      value: { speak: () => {}, cancel: () => {} },
      configurable: true,
      writable: true,
    });
    window.SpeechSynthesisUtterance = function () {};
    expect(isSpeechSynthesisSupported()).toBe(true);

    window.SpeechSynthesisUtterance = undefined;
    expect(isSpeechSynthesisSupported()).toBe(false);
  });

  it("maps speech recognition errors to friendly messages", () => {
    expect(mapSpeechRecognitionError("not-allowed")).toMatch(/permission was denied/i);
    expect(mapSpeechRecognitionError("no-speech")).toMatch(/no speech/i);
    expect(mapSpeechRecognitionError("audio-capture")).toMatch(/no microphone/i);
    expect(mapSpeechRecognitionError("network")).toMatch(/network error/i);
    expect(mapSpeechRecognitionError("aborted")).toBeNull();
    expect(mapSpeechRecognitionError("unknown")).toMatch(/could not be completed/i);
  });

  it("strips markdown formatting for clean speech output", () => {
    const md = [
      "## Dallas Hub Risk",
      "",
      "**WDRI is 74.2** (`High` tier). See [FEMA NRI](https://example.com).",
      "- Severe storm score: 88",
      "- Flood score: _61_",
    ].join("\n");

    const spoken = stripMarkdownForSpeech(md);
    expect(spoken).toBe(
      "Dallas Hub Risk WDRI is 74.2 (High tier). See FEMA NRI. Severe storm score: 88 Flood score: 61"
    );
  });

  it("handles empty or non-string input in stripMarkdownForSpeech", () => {
    expect(stripMarkdownForSpeech("")).toBe("");
    expect(stripMarkdownForSpeech(null)).toBe("");
  });
});
