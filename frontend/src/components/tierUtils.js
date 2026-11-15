export function tierClass(tier) {
  switch ((tier || "").toLowerCase()) {
    case "severe":
      return "tier-severe";
    case "high":
      return "tier-high";
    case "moderate":
      return "tier-moderate";
    case "low":
      return "tier-low";
    default:
      return "tier-unknown";
  }
}

export function severityClass(severity) {
  switch ((severity || "").toLowerCase()) {
    case "extreme":
      return "tier-severe";
    case "severe":
      return "tier-high";
    case "moderate":
      return "tier-moderate";
    case "minor":
      return "tier-low";
    default:
      return "tier-unknown";
  }
}

export function formatHazardLabel(key) {
  const labels = {
    overall: "Overall WDRI",
    winter: "Winter",
    hurricane: "Hurricane",
    flood: "Flood",
    severe_storm: "Severe Storm",
    heat: "Extreme Heat",
  };
  return labels[key] || String(key || "").replaceAll("_", " ");
}

export function formatMetricLabel(key) {
  return String(key || "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
