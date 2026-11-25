import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ApiError, errorMessage } from "../api/client";
import { auth } from "../api/endpoints";
import { useAuth } from "../auth/AuthContext";
import { Icon } from "../components/Icon";
import { Field } from "../components/ui";
import { COMPANION_NAME } from "../lib/format";

function AuthFrame({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <div className="auth">
      <div className="auth-side" aria-hidden="true">
        <div className="brand">
          <span className="brand-mark">{COMPANION_NAME.charAt(0)}</span>
          <span className="brand-name">{COMPANION_NAME}</span>
        </div>
        <div className="auth-side-copy">
          <h2>A voice assistant that keeps context across every conversation.</h2>
          <p>Talk or type. Memory, reminders and tasks stay in one place, under your control.</p>
          <ul className="auth-features">
            <li>
              <Icon name="brain" size={18} />
              <span>
                <strong>Persistent memory</strong>
                Review, edit or delete anything it has stored.
              </span>
            </li>
            <li>
              <Icon name="checkSquare" size={18} />
              <span>
                <strong>Tasks and reminders</strong>
                Created from conversation, managed from one list.
              </span>
            </li>
            <li>
              <Icon name="activity" size={18} />
              <span>
                <strong>Approval for every action</strong>
                Changes wait for your confirmation and are fully logged.
              </span>
            </li>
          </ul>
        </div>
        <div className="auth-side-foot">© {new Date().getFullYear()} {COMPANION_NAME}</div>
      </div>
      <div className="auth-main">
        <div className="auth-card">
          <div className="brand brand-lg">
            <span className="brand-mark">{COMPANION_NAME.charAt(0)}</span>
            <span className="brand-name">{COMPANION_NAME}</span>
          </div>
          <h1>{title}</h1>
          <p className="muted">{subtitle}</p>
          {children}
        </div>
      </div>
    </div>
  );
}

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401 ? "That email and password don't match." : errorMessage(err),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthFrame title="Sign in" subtitle="Enter your email and password to continue.">
      <form className="form" onSubmit={submit}>
        <Field label="Email">
          <input className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        </Field>
        <Field label="Password">
          <input className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="btn btn-primary btn-block" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <p className="auth-switch muted">
        Don't have an account? <Link to="/register">Create an account</Link>
      </p>
    </AuthFrame>
  );
}

export function RegisterPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [k]: e.target.value });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setErrors({});
    try {
      await auth.register({
        email: form.email.trim(),
        password: form.password,
        display_name: form.name.trim() || undefined,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      });
      await login(form.email.trim(), form.password);
      navigate("/", { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) setErrors(err.fieldErrors());
      else if (err instanceof ApiError && err.status === 409) setError("An account with that email already exists.");
      else setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthFrame title="Create an account" subtitle="Set up your workspace in under a minute.">
      <form className="form" onSubmit={submit}>
        <Field label="Display name" error={errors.display_name}>
          <input className="input" autoComplete="given-name" maxLength={100} value={form.name} onChange={set("name")} placeholder="Optional" autoFocus />
        </Field>
        <Field label="Email" error={errors.email}>
          <input className="input" type="email" autoComplete="email" required value={form.email} onChange={set("email")} />
        </Field>
        <Field label="Password" error={errors.password} hint="At least 8 characters">
          <input className="input" type="password" autoComplete="new-password" required minLength={8} maxLength={128} value={form.password} onChange={set("password")} />
        </Field>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="btn btn-primary btn-block" disabled={busy}>
          {busy ? "Creating account…" : "Create account"}
        </button>
      </form>
      <p className="auth-switch muted">
        Already have an account? <Link to="/login">Sign in</Link>
      </p>
    </AuthFrame>
  );
}
