import { createClient } from "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.106.2/+esm";

const output = document.querySelector("#output");
const status = document.querySelector("#status");
const signupForm = document.querySelector("#signup-form");
const loginForm = document.querySelector("#login-form");
const signupPassword = document.querySelector("#signup-password");
const loginPassword = document.querySelector("#login-password");
let passkeyClient;

function write(message, data, isError = false) {
  output.classList.toggle("error", isError);
  output.textContent = [
    `[${new Date().toLocaleTimeString()}] ${message}`,
    data ? `\n${JSON.stringify(data, null, 2)}` : ""
  ].join("");
  status.textContent = isError ? "error" : "ready";
}

async function api(path, options = {}) {
  status.textContent = "working";
  const response = await fetch(path, {
    cache: "no-store",
    credentials: "same-origin",
    ...options,
    headers: options.body ? { "Content-Type": "application/json", ...options.headers } : options.headers
  });
  const payload = await response.json();
  if (!response.ok) {
    const hiddenCodes = new Set(["email_exists", "user_already_exists", "user_not_found"]);
    const diagnostics = [];
    if (payload.code && !hiddenCodes.has(payload.code)) diagnostics.push(`code=${payload.code}`);
    if (payload.reference) diagnostics.push(`reference=${payload.reference}`);
    const suffix = diagnostics.length ? `\n[diagnostic] ${diagnostics.join(" ")}` : "";
    throw new Error(`${payload.error || `Request failed (${response.status}).`}${suffix}`);
  }
  return payload.data;
}

async function getPasskeyClient() {
  if (passkeyClient) return passkeyClient;
  const config = await api("/api/auth/config");
  passkeyClient = createClient(config.url, config.publishable_key, {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
      experimental: { passkey: true }
    }
  });
  return passkeyClient;
}

async function run(action) {
  try {
    await action();
  } catch (error) {
    write(error.message || "Authentication request failed.", null, true);
  }
}

signupForm.addEventListener("submit", event => {
  event.preventDefault();
  run(async () => {
    if (signupPassword.value !== document.querySelector("#signup-password-confirm").value) {
      throw new Error("The signup passwords do not match.");
    }
    const data = await api("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify({
        email: document.querySelector("#signup-email").value,
        username: document.querySelector("#signup-username").value,
        password: signupPassword.value
      })
    });
    write(data.message, data);
  });
});

loginForm.addEventListener("submit", event => {
  event.preventDefault();
  run(async () => {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        identifier: document.querySelector("#login-identifier").value,
        password: loginPassword.value
      })
    });
    write(data.message, data);
  });
});

document.querySelector("#session").addEventListener("click", () => run(async () => {
  const data = await api("/api/auth/me");
  write(data.message, data);
}));

document.querySelector("#logout").addEventListener("click", () => run(async () => {
  const data = await api("/api/auth/logout", { method: "POST", body: "{}" });
  passkeyClient = undefined;
  write(data.message, data);
}));

document.querySelector("#register-passkey").addEventListener("click", () => run(async () => {
  const identifier = document.querySelector("#login-identifier").value;
  const password = loginPassword.value.normalize("NFC");
  if (!identifier || !password) throw new Error("Enter the confirmed account's username/email and password in Login first.");

  const authenticated = await api("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ identifier, password })
  });

  const client = await getPasskeyClient();
  const { error: loginError } = await client.auth.signInWithPassword({
    email: authenticated.email,
    password
  });
  if (loginError) throw loginError;
  const { data, error } = await client.auth.registerPasskey();
  if (error) throw error;
  await client.auth.signOut({ scope: "local" });
  passkeyClient = undefined;
  write("Passkey registered with Supabase Auth.");
}));

document.querySelector("#passkey-login").addEventListener("click", () => run(async () => {
  const client = await getPasskeyClient();
  const { data, error } = await client.auth.signInWithPasskey();
  if (error) throw error;
  if (!data.session) throw new Error("Supabase did not return a passkey session.");
  const result = await api("/api/auth/session", {
    method: "POST",
    body: JSON.stringify({
      access_token: data.session.access_token,
      refresh_token: data.session.refresh_token
    })
  });
  passkeyClient = undefined;
  write(result.message, result);
}));

document.querySelectorAll("[data-password-toggle]").forEach(toggle => {
  toggle.addEventListener("change", () => {
    const password = document.querySelector(toggle.dataset.passwordToggle);
    password.type = toggle.checked ? "text" : "password";
  });
});
