const PROFILE_CACHE_KEY = "peak.profile.v1";

function safeStorage() {
  try {
    const storage = window.sessionStorage;
    const probe = "__peak_probe__";
    storage.setItem(probe, "1");
    storage.removeItem(probe);
    return storage;
  } catch {
    return null;
  }
}

function profileObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : null;
}

export function readBootstrappedProfile() {
  const element = document.querySelector("#peak-bootstrap");
  if (!element?.textContent) return null;
  try {
    const payload = JSON.parse(element.textContent);
    return profileObject(payload.profile);
  } catch {
    return null;
  }
}

export function readCachedProfile() {
  const storage = safeStorage();
  if (!storage) return null;
  try {
    const payload = JSON.parse(storage.getItem(PROFILE_CACHE_KEY) || "null");
    return profileObject(payload?.profile);
  } catch {
    storage.removeItem(PROFILE_CACHE_KEY);
    return null;
  }
}

export function writeCachedProfile(profile) {
  const storage = safeStorage();
  const cleaned = profileObject(profile);
  if (!storage || !cleaned) return;
  try {
    storage.setItem(
      PROFILE_CACHE_KEY,
      JSON.stringify({ profile: cleaned, cached_at: Date.now() })
    );
  } catch {
    storage.removeItem(PROFILE_CACHE_KEY);
  }
}

export function hasRemoteProfilePicture(profile) {
  const picture = profileObject(profile)?.profile_picture;
  return (
    typeof picture === "string" &&
    picture.length > 0 &&
    !picture.startsWith("/profile-pictures/")
  );
}
