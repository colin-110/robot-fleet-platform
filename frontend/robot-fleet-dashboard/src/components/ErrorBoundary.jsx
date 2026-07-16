import { Component } from "react";

/**
 * React error boundary — catches rendering crashes and shows a
 * user-friendly fallback instead of a blank white screen.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error("ErrorBoundary caught:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          style={{
            minHeight: "100vh",
            display: "grid",
            placeItems: "center",
            padding: 32,
            color: "var(--text, #e2e8f0)",
          }}
        >
          <div
            style={{
              maxWidth: 480,
              textAlign: "center",
              display: "grid",
              gap: 16,
            }}
          >
            <div style={{ fontSize: 48 }}>⚠️</div>
            <h1 style={{ margin: 0, fontSize: 22 }}>Something went wrong</h1>
            <p style={{ color: "var(--muted, #94a3b8)", margin: 0 }}>
              The dashboard encountered an unexpected error. Try refreshing the
              page.
            </p>
            <button
              className="btn"
              onClick={() => window.location.reload()}
              style={{ justifySelf: "center", padding: "10px 24px" }}
            >
              Reload
            </button>
            {this.state.error && (
              <pre
                style={{
                  fontSize: 12,
                  color: "var(--muted, #94a3b8)",
                  textAlign: "left",
                  overflow: "auto",
                  maxHeight: 120,
                  padding: 12,
                  borderRadius: 8,
                  background: "rgba(0,0,0,0.3)",
                }}
              >
                {this.state.error.toString()}
              </pre>
            )}
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
