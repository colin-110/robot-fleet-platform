import { useState } from "react";
import { login } from "../utils/auth";

/**
 * Sign-in gate. Rendered only when the backend reports `login_required`.
 * When it also offers a demo account (`demo`: { username, password }), both
 * fields start filled in and the credentials are shown, so a visitor signs
 * in with one click.
 */
export default function LoginScreen({ onSignedIn, demo = null }) {
  const [username, setUsername] = useState(demo?.username ?? "");
  const [password, setPassword] = useState(demo?.password ?? "");
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
        <h1 className="loginCard__title">FleetOps</h1>
        <p className="loginCard__subtitle">Sign in to access the fleet console.</p>

        {demo && (
          <p className="loginDemo">
            Demo account, filled in for you: <strong>{demo.username}</strong> /{" "}
            <strong>{demo.password}</strong>
          </p>
        )}

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
