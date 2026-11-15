import { severityClass } from "./tierUtils.js";

export default function AlertToasts({ toasts, onDismiss }) {
  if (!toasts || toasts.length === 0) {
    return null;
  }

  return (
    <div className="alert-toast-stack" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.toastId} className="alert-toast">
          <div className="alert-toast-head">
            <span className="alert-toast-badge">💜 {toast.hub}</span>
            <span className={`tier-pill ${severityClass(toast.alert_type)}`}>
              {toast.alert_type?.replaceAll("_", " ")}
            </span>
            <button
              type="button"
              className="toast-close-btn"
              aria-label="Dismiss alert"
              onClick={() => onDismiss?.(toast.toastId)}
            >
              ✕
            </button>
          </div>
          <p className="alert-toast-msg">{toast.message}</p>
          <div className="alert-toast-score">
            {toast.old_score !== null && toast.old_score !== undefined
              ? `${Number(toast.old_score).toFixed(1)} → `
              : ""}
            {Number(toast.new_score).toFixed(1)}
          </div>
        </div>
      ))}
    </div>
  );
}
