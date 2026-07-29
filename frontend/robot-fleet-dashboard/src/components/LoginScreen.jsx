import { useState } from "react";
import { login } from "../utils/auth";

/**
 * Sign-in gate. Rendered only when the backend reports
 * `login_required` — the hosted demo never sees this.
 */
export default function LoginScreen({ onSignedIn }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (submitting) return;

    setSubmitting(true);
    setError("");
    try {
      const session = await login(username, password);
      onSignedIn?.(session);
    } catch (err) {
      setError(err.message || "Sign-in failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="loginScreen">
      <form className="glass loginCard" onSubmit={handleSubmit} aria-label="Sign in">
        <h1 className="loginCard__title">Fleet Operations</h1>
        <p className="loginCard__subtitle">Sign in to access the console.</p>

        <label className="loginField">
          <span>Username</span>
          <input
            name="username"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </label>

        <label className="loginField">
          <span>Password</span>
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>

        {/* role="alert" so the failure is announced, not just coloured. */}
        {error && (
          <div className="loginError" role="alert">
            {error}
          </div>
        )}

        <button className="btn btn--primary" type="submit" disabled={submitting}>
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
