const steps = [...document.querySelectorAll(".step")];
const aiStepIndex = steps.findIndex(step => step.hasAttribute("data-ai-step"));
const privacyStepIndex = steps.findIndex(step => step.hasAttribute("data-privacy-step"));
const paceChoices = [...document.querySelectorAll(".pace-choice")];
const backButton = document.querySelector("#back-button");
const nextButton = document.querySelector("#next-button");
const progressBar = document.querySelector("#progress-bar");
const stepCount = document.querySelector("#step-count");
const stepLabel = document.querySelector("#step-label");
const username = document.querySelector("#onboarding-username");
const paceError = document.querySelector("#pace-error");
const status = document.querySelector("#onboarding-status");
const privacyOverlay = document.querySelector("#privacy-overlay");
const privacyRequest = document.querySelector("#privacy-request");
const aiPrompt = document.querySelector("#ai-prompt");
let currentStep = 0;
let paceGender = "";
let trackingPromptAnswered = false;
const aiPrompts = [
  "Build a short leg session and a high-protein breakfast.",
  "Plan a three-day strength routine for a beginner.",
  "Show me a dairy-free lunch with at least 30 grams of protein.",
  "Create a mobility session for the day after heavy squats.",
  "Help me turn today's workout into a repeatable plan."
];

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    credentials: "same-origin",
    ...options,
    headers: options.body ? { "Content-Type": "application/json", ...options.headers } : options.headers
  });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401) {
    window.location.replace("/login");
    throw new Error("Authentication is required.");
  }
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  return payload.data;
}

function commaList(value) {
  return value.split(",").map(item => item.trim()).filter(Boolean);
}

function setPaceGender(value) {
  paceGender = value;
  paceError.textContent = "";
  paceChoices.forEach(choice => {
    const selected = choice.dataset.gender === value;
    choice.classList.toggle("is-selected", selected);
    choice.setAttribute("aria-checked", String(selected));
  });
  document.querySelectorAll("[data-pace-pose]").forEach(image => {
    image.src = `/app/pace/pace_${value}_${image.dataset.pacePose}.png`;
  });
}

function setStep(index, { focus = true } = {}) {
  currentStep = Math.max(0, Math.min(index, steps.length - 1));
  steps.forEach((step, stepIndex) => {
    const active = stepIndex === currentStep;
    step.hidden = !active;
    step.classList.toggle("is-active", active);
  });
  stepCount.textContent = `${currentStep + 1} of ${steps.length}`;
  stepLabel.textContent = steps[currentStep].dataset.label;
  progressBar.style.width = `${((currentStep + 1) / steps.length) * 100}%`;
  backButton.disabled = currentStep === 0;
  nextButton.querySelector("span").textContent = currentStep === steps.length - 1 ? "Finish onboarding" : "Continue";
  status.textContent = "";
  if (focus) steps[currentStep].querySelector("h1,h2")?.focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function populate(profile) {
  username.textContent = profile.username ? `@${profile.username}` : "Your account";
  document.querySelector('[name="display_name"]').value = profile.display_name || "";
  document.querySelector('[name="bio"]').value = profile.bio || "";
  document.querySelector('[name="goals"]').value = (profile.goals || []).join(", ");
  document.querySelector('[name="cuisine_preferences"]').value = (profile.cuisine_preferences || []).join(", ");
  document.querySelector('[name="dietary_preferences"]').value = (profile.dietary_preferences || []).join(", ");
  const experience = document.querySelector(`[name="workout_experience"][value="${profile.workout_experience}"]`);
  if (experience) experience.checked = true;
  const units = document.querySelector(`[name="preferred_units"][value="${profile.preferred_units || "metric"}"]`);
  if (units) units.checked = true;
  if (profile.pace_gender) setPaceGender(profile.pace_gender);
}

function payload() {
  return {
    pace_gender: paceGender,
    display_name: document.querySelector('[name="display_name"]').value.trim() || null,
    bio: document.querySelector('[name="bio"]').value.trim() || null,
    workout_experience: document.querySelector('[name="workout_experience"]:checked')?.value || null,
    preferred_units: document.querySelector('[name="preferred_units"]:checked')?.value || "metric",
    goals: commaList(document.querySelector('[name="goals"]').value),
    cuisine_preferences: commaList(document.querySelector('[name="cuisine_preferences"]').value),
    dietary_preferences: commaList(document.querySelector('[name="dietary_preferences"]').value)
  };
}

function showPrivacyPrompt() {
  privacyOverlay.hidden = false;
  document.body.classList.add("is-modal-open");
  privacyRequest.querySelector("button")?.focus();
}

function continueToPrivacyPage() {
  trackingPromptAnswered = true;
  privacyOverlay.hidden = true;
  document.body.classList.remove("is-modal-open");
  setStep(privacyStepIndex);
}

async function finishOnboarding() {
  nextButton.disabled = true;
  nextButton.classList.add("is-loading");
  status.textContent = "Saving your preferences…";
  try {
    await api("/api/onboarding", { method: "PATCH", body: JSON.stringify(payload()) });
    status.textContent = "Onboarding complete.";
    window.location.replace("/app/dashboard");
  } catch (error) {
    status.textContent = error.message || "Onboarding could not be completed.";
    nextButton.disabled = false;
    nextButton.classList.remove("is-loading");
  }
}

const wait = milliseconds => new Promise(resolve => window.setTimeout(resolve, milliseconds));

async function runAiTypewriter() {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    aiPrompt.textContent = aiPrompts[0];
    return;
  }
  let promptIndex = 0;
  while (true) {
    if (currentStep !== aiStepIndex || document.hidden) {
      await wait(250);
      continue;
    }
    const prompt = aiPrompts[promptIndex];
    aiPrompt.textContent = "";
    for (const character of prompt) {
      if (currentStep !== aiStepIndex) break;
      aiPrompt.textContent += character;
      await wait(18 + Math.random() * 12);
    }
    if (currentStep === aiStepIndex) await wait(1500);
    for (let index = aiPrompt.textContent.length; index > 0 && currentStep === aiStepIndex; index -= 1) {
      aiPrompt.textContent = aiPrompt.textContent.slice(0, -1);
      await wait(8);
    }
    promptIndex = (promptIndex + 1) % aiPrompts.length;
  }
}

paceChoices.forEach(choice => choice.addEventListener("click", () => setPaceGender(choice.dataset.gender)));
backButton.addEventListener("click", () => setStep(currentStep - 1));
nextButton.addEventListener("click", () => {
  if (currentStep === 0 && !paceGender) {
    paceError.textContent = "Choose the Pace avatar you want to continue with.";
    paceChoices[0].focus();
    return;
  }
  if (currentStep === steps.length - 1) {
    finishOnboarding();
    return;
  }
  if (currentStep === aiStepIndex && !trackingPromptAnswered) {
    showPrivacyPrompt();
    return;
  }
  setStep(currentStep + 1);
});
document.querySelectorAll("[data-tracking-answer]").forEach(button => button.addEventListener("click", continueToPrivacyPage));

async function initialize() {
  try {
    const data = await api("/api/profile");
    if (data.profile.onboarding_completed === true) {
      window.location.replace("/app/dashboard");
      return;
    }
    populate(data.profile);
    setStep(0, { focus: false });
  } catch (error) {
    status.textContent = error.message || "Your profile could not be loaded.";
  }
}

initialize();
runAiTypewriter();
