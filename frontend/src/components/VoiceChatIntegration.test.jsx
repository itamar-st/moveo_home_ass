import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChatWindow from "./ChatWindow.jsx";

describe("Voice input/output UI integration", () => {
  let recognitionInstances;
  let utterances;
  let mockSynth;
  const originalSR = window.SpeechRecognition;
  const originalWebkitSR = window.webkitSpeechRecognition;
  const originalSynth = window.speechSynthesis;
  const originalUtterance = window.SpeechSynthesisUtterance;

  beforeEach(() => {
    window.localStorage.clear();
    recognitionInstances = [];
    utterances = [];

    class MockSpeechRecognition {
      constructor() {
        this.start = vi.fn(() => {
          this.onstart?.();
        });
        this.stop = vi.fn(() => {
          this.onend?.();
        });
        this.abort = vi.fn();
        recognitionInstances.push(this);
      }
    }

    window.SpeechRecognition = MockSpeechRecognition;
    delete window.webkitSpeechRecognition;

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
    window.SpeechRecognition = originalSR;
    window.webkitSpeechRecognition = originalWebkitSR;
    Object.defineProperty(window, "speechSynthesis", {
      value: originalSynth,
      configurable: true,
      writable: true,
    });
    window.SpeechSynthesisUtterance = originalUtterance;
  });

  const baseProps = {
    sessionId: "sess-1234",
    messages: [],
    loadingHistory: false,
    isStreaming: false,
    statusStage: null,
    liveTrace: [],
    errorState: null,
    onClearError: vi.fn(),
    sendBurstKey: 0,
    availableModels: [],
    selectedModel: "",
    onSelectModel: vi.fn(),
    onSendMessage: vi.fn(),
    onNewChat: vi.fn(),
    onOpenMethodology: vi.fn(),
  };

  it("populates composer textarea via microphone dictation and submits", () => {
    const onSendMessage = vi.fn();
    render(<ChatWindow {...baseProps} onSendMessage={onSendMessage} />);

    const micBtn = screen.getByRole("button", { name: /start voice input/i });
    fireEvent.click(micBtn);

    expect(recognitionInstances).toHaveLength(1);
    expect(screen.getByRole("button", { name: /stop voice input/i })).toHaveAttribute(
      "aria-pressed",
      "true"
    );

    act(() => {
      recognitionInstances[0].onresult({
        resultIndex: 0,
        results: [{ isFinal: true, 0: { transcript: "Why is Dallas high risk?" } }],
      });
    });

    const textarea = screen.getByRole("textbox");
    expect(textarea).toHaveValue("Why is Dallas high risk?");

    fireEvent.submit(textarea.closest("form"));
    expect(onSendMessage).toHaveBeenCalledWith("Why is Dallas high risk?");
  });

  it("reads an assistant message aloud when clicking Listen and stops on Stop", () => {
    const messages = [
      {
        id: "msg-a1",
        role: "assistant",
        answer: {
          answer_markdown: "**Miami** has high hurricane exposure.",
          confidence: "high",
        },
        artifacts: [],
        toolTrace: [],
      },
    ];

    render(<ChatWindow {...baseProps} messages={messages} />);

    const listenBtn = screen.getByRole("button", { name: /read response aloud/i });
    fireEvent.click(listenBtn);

    expect(mockSynth.speak).toHaveBeenCalledTimes(1);
    expect(utterances[0].text).toBe("Miami has high hurricane exposure.");

    const stopBtn = screen.getByRole("button", { name: /stop reading response/i });
    fireEvent.click(stopBtn);
    expect(mockSynth.cancel).toHaveBeenCalled();
  });

  it("auto-reads new assistant responses when Auto-Read is toggled on", () => {
    const { rerender } = render(
      <ChatWindow {...baseProps} isStreaming={true} messages={[]} />
    );

    const autoReadBtn = screen.getByRole("button", { name: /auto-read off/i });
    fireEvent.click(autoReadBtn);
    expect(screen.getByRole("button", { name: /auto-read on/i })).toHaveAttribute(
      "aria-pressed",
      "true"
    );

    rerender(
      <ChatWindow
        {...baseProps}
        isStreaming={false}
        messages={[
          {
            id: "msg-a2",
            role: "assistant",
            answer: { answer_markdown: "Denver had 18% snow days.", confidence: "high" },
            artifacts: [],
            toolTrace: [],
          },
        ]}
      />
    );

    expect(mockSynth.speak).toHaveBeenCalledTimes(1);
    expect(utterances[0].text).toBe("Denver had 18% snow days.");
  });

  it("disables voice controls gracefully when Web Speech API is unsupported", () => {
    delete window.SpeechRecognition;
    window.SpeechSynthesisUtterance = undefined;

    render(<ChatWindow {...baseProps} />);

    expect(screen.getByRole("button", { name: /start voice input/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /auto-read off/i })).toBeDisabled();
  });
});
