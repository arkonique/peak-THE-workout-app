import { canvasBlob, canvasFromFile, createGeometricAvatar } from "/app/assets/avatar.js";
import {
  hasRemoteProfilePicture,
  readBootstrappedProfile,
  readCachedProfile,
  writeCachedProfile
} from "/app/assets/profile-cache.js";

const form = document.querySelector("#profile-form");
const picture = document.querySelector("#profile-picture");
const avatarUsername = document.querySelector("#avatar-username");
const avatarDisplayName = document.querySelector("#avatar-display-name");
const fileInput = document.querySelector("#profile-file");
const uploadButton = document.querySelector("#profile-upload");
const regenerateButton = document.querySelector("#profile-regenerate");
const avatarStatus = document.querySelector("#avatar-status");
const formStatus = document.querySelector("#form-status");
const saveButton = form.querySelector(".save-button");
const bioCount = document.querySelector("#bio-count");
let replacingMissingPicture = false;
let generatedAvatarUpload = null;
let fallbackCanvas = null;
let currentProfile = {};
const customSelects = new Map();

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    credentials: "same-origin",
    ...options,
    headers: options.body && typeof options.body === "string"
      ? { "Content-Type": "application/json", ...options.headers }
      : options.headers
  });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401) {
    window.location.replace("/login");
    throw new Error("Authentication is required.");
  }
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  return payload.data;
}

function setStatus(element, message = "", isError = false) {
  element.textContent = message;
  element.classList.toggle("is-error", isError);
}

function setAvatarBusy(busy) {
  uploadButton.disabled = busy;
  regenerateButton.disabled = busy;
}

function setSaveBusy(busy) {
  saveButton.disabled = busy;
  saveButton.classList.toggle("is-loading", busy);
}

function commaList(value) {
  return value.split(",").map(item => item.trim()).filter(Boolean);
}

function closeCustomSelect(control) {
  control.root.classList.remove("is-open");
  control.trigger.setAttribute("aria-expanded", "false");
  control.menu.hidden = true;
}

function setCustomSelectValue(name, value) {
  const control = customSelects.get(name);
  if (!control) return;
  const option = control.options.find(item => item.dataset.value === value) || control.options[0];
  control.input.value = option.dataset.value;
  control.trigger.querySelector("span").textContent = option.textContent;
  control.options.forEach(item => item.setAttribute("aria-selected", String(item === option)));
}

function initializeCustomSelects() {
  document.querySelectorAll("[data-custom-select]").forEach(root => {
    const input = root.querySelector("input[type='hidden']");
    const trigger = root.querySelector(".custom-select__trigger");
    const menu = root.querySelector(".custom-select__menu");
    const options = [...menu.querySelectorAll("[role='option']")];
    const control = { root, input, trigger, menu, options };
    customSelects.set(root.dataset.customSelect, control);

    trigger.addEventListener("click", () => {
      const opening = menu.hidden;
      customSelects.forEach(other => closeCustomSelect(other));
      if (opening) {
        root.classList.add("is-open");
        trigger.setAttribute("aria-expanded", "true");
        menu.hidden = false;
        (options.find(option => option.dataset.value === input.value) || options[0]).focus();
      }
    });
    options.forEach(option => option.addEventListener("click", () => {
      setCustomSelectValue(root.dataset.customSelect, option.dataset.value);
      closeCustomSelect(control);
      trigger.focus();
    }));
    menu.addEventListener("keydown", event => {
      const current = options.indexOf(document.activeElement);
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        let next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : current;
        if (event.key === "ArrowDown") next = (current + 1) % options.length;
        if (event.key === "ArrowUp") next = (current - 1 + options.length) % options.length;
        options[next].focus();
      } else if (event.key === "Escape") {
        closeCustomSelect(control);
        trigger.focus();
      }
    });
  });
  document.addEventListener("click", event => {
    customSelects.forEach(control => {
      if (!control.root.contains(event.target)) closeCustomSelect(control);
    });
  });
}

function displayDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function showGeneratedProfilePicture() {
  if (!fallbackCanvas) fallbackCanvas = createGeometricAvatar();
  picture.src = fallbackCanvas.toDataURL("image/png");
  return fallbackCanvas;
}

function populate(profile) {
  form.elements.username.value = profile.username || "";
  form.elements.display_name.value = profile.display_name || "";
  form.elements.bio.value = profile.bio || "";
  setCustomSelectValue("workout_experience", profile.workout_experience || "");
  setCustomSelectValue("preferred_units", profile.preferred_units || "metric");
  setCustomSelectValue("pace_gender", profile.pace_gender || "female");
  form.elements.goals.value = (profile.goals || []).join(", ");
  form.elements.cuisine_preferences.value = (profile.cuisine_preferences || []).join(", ");
  form.elements.dietary_preferences.value = (profile.dietary_preferences || []).join(", ");
  avatarUsername.textContent = profile.username || "Profile";
  avatarDisplayName.textContent = profile.display_name || "";
  bioCount.textContent = String(form.elements.bio.value.length);
  document.querySelector("#profile-created").textContent = displayDate(profile.created_at);
  document.querySelector("#profile-updated").textContent = displayDate(profile.updated_at);
}

async function uploadCanvas(canvas) {
  picture.src = canvas.toDataURL("image/png");
  const data = await api("/api/profile-picture", {
    method: "POST",
    headers: { "Content-Type": "image/png" },
    body: await canvasBlob(canvas)
  });
  currentProfile = { ...currentProfile, profile_picture: data.profile_picture };
  writeCachedProfile(currentProfile);
  picture.src = data.profile_picture;
}

function queueGeneratedProfilePictureUpload(canvas) {
  if (generatedAvatarUpload) return generatedAvatarUpload;
  generatedAvatarUpload = uploadCanvas(canvas)
    .catch(error => {
      setStatus(avatarStatus, error.message || "Profile picture could not be saved.", true);
    })
    .finally(() => {
      generatedAvatarUpload = null;
    });
  return generatedAvatarUpload;
}

function applyProfilePicture(profile, { uploadMissingPicture = true } = {}) {
  if (hasRemoteProfilePicture(profile)) {
    fallbackCanvas = null;
    if (picture.src !== profile.profile_picture) {
      picture.src = profile.profile_picture;
    }
    return;
  }
  const canvas = showGeneratedProfilePicture();
  if (uploadMissingPicture) queueGeneratedProfilePictureUpload(canvas);
}

function applyProfile(profile, options = {}) {
  if (!profile || typeof profile !== "object") return;
  currentProfile = profile;
  writeCachedProfile(profile);
  populate(profile);
  applyProfilePicture(profile, options);
}

async function generateAvatar() {
  fallbackCanvas = createGeometricAvatar();
  return uploadCanvas(fallbackCanvas);
}

async function initialize() {
  const initialProfile = readBootstrappedProfile() || readCachedProfile();
  if (initialProfile) {
    applyProfile(initialProfile, { uploadMissingPicture: false });
  } else {
    avatarUsername.textContent = "Profile";
    showGeneratedProfilePicture();
  }

  try {
    const data = await api("/api/profile");
    applyProfile(data.profile);
  } catch (error) {
    setStatus(formStatus, error.message || "Profile could not be loaded.", true);
  }
}

form.elements.bio.addEventListener("input", () => {
  bioCount.textContent = String(form.elements.bio.value.length);
});
form.elements.username.addEventListener("input", () => {
  form.elements.username.value = form.elements.username.value.toLowerCase().replace(/[^a-z0-9_]/g, "");
});

uploadButton.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", async () => {
  const [file] = fileInput.files;
  if (!file) return;
  setAvatarBusy(true);
  setStatus(avatarStatus, "Preparing and uploading your picture…");
  try {
    await uploadCanvas(await canvasFromFile(file));
    setStatus(avatarStatus, "Profile picture updated.");
  } catch (error) {
    setStatus(avatarStatus, error.message || "Profile picture could not be updated.", true);
  } finally {
    fileInput.value = "";
    setAvatarBusy(false);
  }
});

regenerateButton.addEventListener("click", async () => {
  setAvatarBusy(true);
  setStatus(avatarStatus, "Generating and uploading a new design…");
  try {
    await generateAvatar();
    setStatus(avatarStatus, "New profile picture saved.");
  } catch (error) {
    setStatus(avatarStatus, error.message || "A new profile picture could not be saved.", true);
  } finally {
    setAvatarBusy(false);
  }
});

picture.addEventListener("error", async () => {
  if (replacingMissingPicture) return;
  replacingMissingPicture = true;
  try { await queueGeneratedProfilePictureUpload(showGeneratedProfilePicture()); }
  catch (error) { setStatus(avatarStatus, error.message, true); }
  finally { replacingMissingPicture = false; }
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  setStatus(formStatus);
  if (!form.reportValidity()) return;
  setSaveBusy(true);
  try {
    const payload = {
      username: form.elements.username.value.trim(),
      display_name: form.elements.display_name.value.trim() || null,
      bio: form.elements.bio.value.trim() || null,
      workout_experience: form.elements.workout_experience.value || null,
      preferred_units: form.elements.preferred_units.value,
      pace_gender: form.elements.pace_gender.value,
      goals: commaList(form.elements.goals.value),
      cuisine_preferences: commaList(form.elements.cuisine_preferences.value),
      dietary_preferences: commaList(form.elements.dietary_preferences.value)
    };
    const data = await api("/api/profile", { method: "PATCH", body: JSON.stringify(payload) });
    applyProfile(data.profile);
    setStatus(formStatus, "Profile saved.");
  } catch (error) {
    setStatus(formStatus, error.message || "Profile could not be saved.", true);
  } finally {
    setSaveBusy(false);
  }
});

initializeCustomSelects();
initialize();
