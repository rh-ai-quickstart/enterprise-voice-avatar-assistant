import { LoginForm, LoginPage } from "@patternfly/react-core";
import { useState, type FormEvent } from "react";
import { ApiError, api } from "../api";
import type { Me } from "../types";

// The name is remembered for the next sign-in; storage can be unavailable (private windows)
const remembered = {
  get: () => {
    try {
      return localStorage.getItem("admin-name") ?? "";
    } catch {
      return "";
    }
  },
  set: (name: string) => {
    try {
      localStorage.setItem("admin-name", name);
    } catch {
      /* not remembered */
    }
  },
};

const MESSAGES: Record<number, string> = {
  401: "Wrong password.",
  503: "The admin portal is not configured: ADMIN_PASSWORD is empty in the assistant-admin secret.",
};

/** The shared admin password, and the name decisions are attributed to ("approved by Dana"). */
export function Login({ onSignedIn }: { onSignedIn: (me: Me) => void }) {
  const [name, setName] = useState(remembered.get);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const me = await api.login(name.trim(), password);
      remembered.set(me.name);
      onSignedIn(me);
    } catch (err) {
      setError(err instanceof ApiError ? (MESSAGES[err.status] ?? err.message) : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <LoginPage
      loginTitle="Assistant admin portal"
      loginSubtitle="Approvals, tickets and what the assistant has been doing"
      textContent="Everyone signs in with the shared admin password. Your name is shown on every decision you take, to the requester and in the audit log."
    >
      <LoginForm
        usernameLabel="Your name"
        usernameValue={name}
        onChangeUsername={(_e, v) => setName(v)}
        isValidUsername={!error || !name.trim()}
        passwordLabel="Admin password"
        passwordValue={password}
        onChangePassword={(_e, v) => setPassword(v)}
        showHelperText={Boolean(error)}
        helperText={error}
        loginButtonLabel="Sign in"
        isLoginButtonDisabled={busy || name.trim().length < 2 || !password}
        onLoginButtonClick={submit}
      />
    </LoginPage>
  );
}
