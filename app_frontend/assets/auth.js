const views = {
  login: {
    form: document.querySelector("#login-form"),
    title: "Welcome back",
    description: "Sign in to continue building your record."
  },
  signup: {
    form: document.querySelector("#signup-form"),
    title: "Build your account",
    description: "Create one secure identity for your complete health record."
  }
};

const tabs = [...document.querySelectorAll("[data-view]")];
const tabList = document.querySelector(".tabs");
const formTitle = document.querySelector("#form-title");
const formDescription = document.querySelector("#form-description");
const feedback = document.querySelector("#feedback");
const feedbackMessage = document.querySelector("#feedback-message");
const passkeyButton = document.querySelector("#passkey-login");
const passkeySetup = document.querySelector("#passkey-setup");
const registerPasskeyButton = document.querySelector("#register-passkey");
const skipPasskeySetupButton = document.querySelector("#skip-passkey-setup");
const signupPassword = document.querySelector("#signup-password");
const strengthMeter = document.querySelector("#password-strength");
const strengthDetail = document.querySelector("#strength-detail");
let activeView = "login";
let passkeyClient;
let pendingPasswordLogin;
const requestedView = window.location.pathname.endsWith("/signup") ? "signup" : "login";

function setFeedback(message = "", isError = false) {
  feedback.hidden = !message;
  feedback.classList.toggle("is-error", isError);
  feedbackMessage.textContent = message;
}

function setLoading(form, loading) {
  const button = form.querySelector(".submit-button");
  button.disabled = loading;
  button.classList.toggle("is-loading", loading);
  form.setAttribute("aria-busy", String(loading));
}

function setPasskeyLoading(loading) {
  passkeyButton.disabled = loading;
  passkeyButton.classList.toggle("is-loading", loading);
  passkeyButton.setAttribute("aria-busy", String(loading));
}

function setPasskeySetupLoading(loading) {
  registerPasskeyButton.disabled = loading;
  skipPasskeySetupButton.disabled = loading;
  registerPasskeyButton.classList.toggle("is-loading", loading);
  registerPasskeyButton.setAttribute("aria-busy", String(loading));
}

function updatePasswordStrength(password) {
  let score = 0;
  let label = "Not entered";
  if (password) {
    if (password.length < 15) {
      score = 1;
      label = `${15 - password.length} more character${15 - password.length === 1 ? "" : "s"} needed`;
    } else {
      const characterGroups = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/]
        .filter(pattern => pattern.test(password)).length;
      score = 2;
      label = "Good";
      if (password.length >= 20 || characterGroups >= 3) {
        score = 3;
        label = "Strong";
      }
      if (password.length >= 20 && characterGroups >= 3) {
        score = 4;
        label = "Very strong";
      }
    }
  }
  strengthMeter.dataset.score = String(score);
  strengthMeter.setAttribute("aria-valuenow", String(score));
  strengthMeter.setAttribute("aria-valuetext", label);
  strengthDetail.textContent = label;
}

function setView(name, { focus = true } = {}) {
  if (!views[name]) return;
  activeView = name;
  tabList.dataset.view = name;
  setFeedback();
  for (const [viewName, view] of Object.entries(views)) view.form.hidden = viewName !== name;
  for (const tab of tabs) {
    const selected = tab.dataset.view === name;
    tab.classList.toggle("is-active", selected);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  }
  formTitle.textContent = views[name].title;
  formDescription.textContent = views[name].description;
  document.title = `${name === "login" ? "Sign in" : "Create account"} | Peak`;
  if (focus) views[name].form.querySelector("input")?.focus();
}

function fieldError(field, message) {
  field.setAttribute("aria-invalid", "true");
  field.focus();
  setFeedback(message, true);
}

function clearFieldErrors(form) {
  form.querySelectorAll("[aria-invalid='true']").forEach(field => field.removeAttribute("aria-invalid"));
}

function continueIntoApp(profile) {
  window.location.replace(profile?.onboarding_completed === true ? "/app/dashboard" : "/app/onboarding");
}

function passkeySetupKey(email) {
  return `peak:passkey-setup-prompted:${email.trim().toLowerCase()}`;
}

function hasSeenPasskeySetup(email) {
  try {
    return localStorage.getItem(passkeySetupKey(email)) === "1";
  } catch {
    return false;
  }
}

function markPasskeySetupSeen(email) {
  try {
    localStorage.setItem(passkeySetupKey(email), "1");
  } catch {
    // Private browsing or locked-down storage should not block account setup.
  }
}

function shouldOfferPasskeySetup(result) {
  return Boolean(
    result?.email &&
    window.PublicKeyCredential &&
    result.profile?.onboarding_completed !== true &&
    !hasSeenPasskeySetup(result.email)
  );
}

function showPasskeySetup(result, password) {
  pendingPasswordLogin = {
    email: result.email,
    password,
    profile: result.profile
  };
  tabList.hidden = true;
  for (const view of Object.values(views)) view.form.hidden = true;
  passkeySetup.hidden = false;
  formTitle.textContent = "Secure future sign-ins";
  formDescription.textContent = "Add a passkey before you finish setting up Peak.";
  document.title = "Set up passkey | Peak";
  setFeedback();
  registerPasskeyButton.focus();
}

function continueAfterPasskeySetup({ rememberPrompt = false } = {}) {
  const login = pendingPasswordLogin;
  if (!login) return;
  if (rememberPrompt) markPasskeySetupSeen(login.email);
  const profile = login.profile;
  login.password = "";
  pendingPasswordLogin = undefined;
  continueIntoApp(profile);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    credentials: "same-origin",
    ...options,
    headers: options.body
      ? { "Content-Type": "application/json", ...options.headers }
      : options.headers
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const diagnostic = payload.reference ? ` Reference: ${payload.reference}.` : "";
    throw new Error(`${payload.error || "Authentication request failed."}${diagnostic}`);
  }
  return payload.data;
}

async function getPasskeyClient() {
  if (passkeyClient) return passkeyClient;
  const [{ createClient }, config] = await Promise.all([
    import("https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.106.2/+esm"),
    api("/api/auth/config")
  ]);
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

tabs.forEach(tab => {
  tab.addEventListener("click", () => setView(tab.dataset.view));
  tab.addEventListener("keydown", event => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    setView(activeView === "login" ? "signup" : "login");
  });
});

document.querySelectorAll("[data-password-toggle]").forEach(button => {
  button.addEventListener("click", () => {
    const input = document.querySelector(`#${button.dataset.passwordToggle}`);
    const shouldShow = input.type === "password";
    input.type = shouldShow ? "text" : "password";
    button.textContent = shouldShow ? "Hide" : "Show";
    button.setAttribute("aria-label", `${shouldShow ? "Hide" : "Show"} password`);
    button.setAttribute("aria-pressed", String(shouldShow));
  });
});

signupPassword.addEventListener("input", () => updatePasswordStrength(signupPassword.value));

passkeyButton.addEventListener("click", async () => {
  setFeedback();
  setPasskeyLoading(true);
  try {
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
    continueIntoApp(result.profile);
  } catch (error) {
    setFeedback(error.message || "Passkey sign-in failed.", true);
  } finally {
    setPasskeyLoading(false);
  }
});

views.login.form.addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  clearFieldErrors(form);
  setFeedback();
  if (!form.reportValidity()) return;
  setLoading(form, true);
  try {
    const password = form.elements.password.value.normalize("NFC");
    const result = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        identifier: form.elements.identifier.value.trim(),
        password
      })
    });
    form.reset();
    if (shouldOfferPasskeySetup(result)) {
      showPasskeySetup(result, password);
    } else {
      continueIntoApp(result.profile);
    }
  } catch (error) {
    setFeedback(error.message, true);
  } finally {
    setLoading(form, false);
  }
});

registerPasskeyButton.addEventListener("click", async () => {
  const login = pendingPasswordLogin;
  if (!login) return;
  setFeedback();
  setPasskeySetupLoading(true);
  let client;
  let signedIn = false;
  try {
    client = await getPasskeyClient();
    const { error: loginError } = await client.auth.signInWithPassword({
      email: login.email,
      password: login.password
    });
    if (loginError) throw loginError;
    signedIn = true;
    const { error } = await client.auth.registerPasskey();
    if (error) throw error;
    markPasskeySetupSeen(login.email);
    setFeedback("Passkey set up. Continuing into Peak.");
    continueAfterPasskeySetup();
  } catch (error) {
    setFeedback(error.message || "Passkey setup failed. You can continue without one.", true);
  } finally {
    if (signedIn && client) await client.auth.signOut({ scope: "local" }).catch(() => {});
    passkeyClient = undefined;
    setPasskeySetupLoading(false);
  }
});

skipPasskeySetupButton.addEventListener("click", () => {
  continueAfterPasskeySetup({ rememberPrompt: true });
});

views.signup.form.addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  clearFieldErrors(form);
  setFeedback();
  if (!form.reportValidity()) return;
  const password = form.elements.password;
  const confirmation = form.elements["password-confirm"];
  if (password.value !== confirmation.value) {
    fieldError(confirmation, "The passwords do not match.");
    return;
  }
  setLoading(form, true);
  try {
    const email = form.elements.email.value.trim();
    await api("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify({
        email,
        username: form.elements.username.value.trim().toLowerCase(),
        password: password.value
      })
    });
    form.reset();
    updatePasswordStrength("");
    setView("login", { focus: false });
    views.login.form.elements.identifier.value = email;
    setFeedback("Account created. Open the confirmation email, then return here to sign in.");
    views.login.form.elements.password.focus();
  } catch (error) {
    setFeedback(error.message, true);
  } finally {
    setLoading(form, false);
  }
});

async function restoreSession() {
  try {
    const result = await api("/api/auth/me");
    continueIntoApp(result.profile);
  } catch {
    setView(requestedView, { focus: false });
  }
}

restoreSession();
