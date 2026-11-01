"use client";

import { FormEvent, useState } from "react";
import { APIError, apiRequest, storeSessionToken } from "@/lib/media-api";

interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
}

interface Principal {
  email: string;
  role: string;
  tenant_id: string;
  is_platform_admin?: boolean;
}

export function SessionAccess({
  token,
  onToken,
  principal,
  onPrincipal
}: {
  token: string;
  onToken: (token: string) => void;
  principal: Principal | null;
  onPrincipal: (principal: Principal | null) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tenant, setTenant] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await apiRequest<TokenResponse>("/api/v1/auth/login", "", {
        method: "POST",
        body: JSON.stringify({ email, password, tenant_slug: tenant })
      });
      storeSessionToken(response.access_token);
      onToken(response.access_token);
      setPassword("");
      const current = await apiRequest<Principal>("/api/v1/auth/me", response.access_token);
      onPrincipal(current);
    } catch (caught) {
      const message = caught instanceof APIError ? caught.message : "Unable to establish a session";
      setError(message);
    } finally {
      setBusy(false);
    }
  }

  function logout() {
    storeSessionToken("");
    onToken("");
    onPrincipal(null);
  }

  if (token && principal) {
    return (
      <div className="session-chip">
        <span className="session-light" />
        <p><strong>{principal.email}</strong><small>{principal.role} · tenant session</small></p>
        <button type="button" onClick={logout}>End session</button>
      </div>
    );
  }

  return (
    <form className="session-form" onSubmit={login}>
      <div>
        <p className="card-kicker">TENANT SESSION</p>
        <h3>Authenticate to the control plane</h3>
        <p>Credentials go directly to the local API. The bearer token is retained only in this browser tab&apos;s session storage.</p>
      </div>
      <label>Email<input required type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="operator@example.com" /></label>
      <label>Tenant slug<input required minLength={2} value={tenant} onChange={(event) => setTenant(event.target.value)} placeholder="your-tenant" /></label>
      <label>Password<input required type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
      <button className="primary-button" disabled={busy} type="submit">{busy ? "Authenticating…" : "Start secure session"}</button>
      {error && <p className="form-error" role="alert">{error}</p>}
    </form>
  );
}
