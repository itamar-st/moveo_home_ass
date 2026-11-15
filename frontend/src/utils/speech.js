export function getSpeechRecognitionConstructor() {
  if (typeof window === "undefined") {
    return null;
  }
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

export function isSpeechRecognitionSupported() {
  return Boolean(getSpeechRecognitionConstructor());
}

export function isSpeechSynthesisSupported() {
  return (
    typeof window !== "undefined" &&
    "speechSynthesis" in window &&
    typeof window.SpeechSynthesisUtterance !== "undefined"
  );
}

export function mapSpeechRecognitionError(errorCode) {
  switch (errorCode) {
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone permission was denied. Please allow mic access in your browser.";
    case "no-speech":
      return "No speech was detected. Please try speaking again.";
    case "audio-capture":
      return "No microphone was found on this device.";
    case "network":
      return "Speech recognition network error. Please check your connection.";
    case "aborted":
      return null;
    default:
      return "Voice input could not be completed. Please try again.";
  }
}

export function stripMarkdownForSpeech(markdown) {
  if (!markdown || typeof markdown !== "string") {
    return "";
  }

  return markdown
    // Remove fenced code blocks' backticks while keeping content
    .replace(/```[\w-]*\n?([\s\S]*?)```/g, "$1")
    // Convert markdown links [label](url) -> label
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    // Remove images ![alt](url) -> alt
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
    // Remove heading markers
    .replace(/^#{1,6}\s+/gm, "")
    // Remove blockquote markers
    .replace(/^\s*>\s?/gm, "")
    // Remove bullet / numbered list markers
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+\.\s+/gm, "")
    // Remove inline code, bold, italic, strikethrough markers
    .replace(/`([^`]+)`/g, "$1")
    .replace(/(\*\*|__)(.*?)\1/g, "$2")
    .replace(/(\*|_)(.*?)\1/g, "$2")
    .replace(/~~(.*?)~~/g, "$1")
    // Replace markdown table dividers/pipes with pauses
    .replace(/\|/g, ", ")
    .replace(/^\s*[-:,\s]+\s*$/gm, " ")
    // Collapse whitespace
    .replace(/\s+/g, " ")
    .trim();
}
