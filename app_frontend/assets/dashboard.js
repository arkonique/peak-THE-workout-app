import { canvasBlob, createGeometricAvatar } from "/app/assets/avatar.js";
import { initializeAppNav } from "/app/assets/app-nav.js";
import { renderAppRoute } from "/app/assets/app-pages.js";
import {
  hasRemoteProfilePicture,
  readBootstrappedProfile,
  readCachedProfile,
  writeCachedProfile
} from "/app/assets/profile-cache.js";

const profileButton = document.querySelector("#profile-button");
const profilePicture = document.querySelector("#profile-picture");
const profileUsername = document.querySelector("#profile-username");
const logoutButton = document.querySelector("#logout-button");
const dashboardDateTime = document.querySelector("#dashboard-date-time");
const dashboardWeekday = document.querySelector("#dashboard-weekday");
const dashboardDate = document.querySelector("#dashboard-date");
const datePrev = document.querySelector("#date-prev");
const dateNext = document.querySelector("#date-next");
const MIN_YEAR = 1871;
const MAX_YEAR = 2171;
const MIN_DATE = new Date(MIN_YEAR, 0, 1);
const MAX_DATE = new Date(MAX_YEAR, 11, 31);
const today = startOfDay(new Date());
let selectedDate = clampDate(today);
let calendarElement;
let calendarView = "day";
let calendarYear = selectedDate.getFullYear();
let calendarMonth = selectedDate.getMonth();
let activeDecade = Math.floor(calendarYear / 10) * 10;
let replacingMissingPicture = false;
let generatedAvatarUpload = null;
let fallbackCanvas = null;
let currentProfile = {};

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

function showGeneratedProfilePicture() {
  if (!fallbackCanvas) fallbackCanvas = createGeometricAvatar();
  profilePicture.src = fallbackCanvas.toDataURL("image/png");
  return fallbackCanvas;
}

async function uploadGeneratedProfilePicture(canvas) {
  const data = await api("/api/profile-picture", {
    method: "POST",
    headers: { "Content-Type": "image/png" },
    body: await canvasBlob(canvas)
  });
  currentProfile = { ...currentProfile, profile_picture: data.profile_picture };
  writeCachedProfile(currentProfile);
  profilePicture.src = data.profile_picture;
}

function queueGeneratedProfilePictureUpload(canvas) {
  if (generatedAvatarUpload) return generatedAvatarUpload;
  generatedAvatarUpload = uploadGeneratedProfilePicture(canvas)
    .catch(error => {
      profileButton.title = error.message;
    })
    .finally(() => {
      generatedAvatarUpload = null;
    });
  return generatedAvatarUpload;
}

function applyProfile(profile, { cache = true, uploadMissingPicture = true } = {}) {
  if (!profile || typeof profile !== "object") return;
  currentProfile = profile;
  if (cache) writeCachedProfile(profile);

  const username = profile.username || "Profile";
  profileUsername.textContent = username;
  profileButton.setAttribute("aria-label", `Open ${username} profile`);

  if (hasRemoteProfilePicture(profile)) {
    fallbackCanvas = null;
    if (profilePicture.src !== profile.profile_picture) {
      profilePicture.src = profile.profile_picture;
    }
  } else {
    const canvas = showGeneratedProfilePicture();
    if (uploadMissingPicture) queueGeneratedProfilePictureUpload(canvas);
  }
  profileButton.classList.remove("is-loading");
}

function renderFallbackShell() {
  profileUsername.textContent = "Profile";
  profileButton.setAttribute("aria-label", "Open profile");
  showGeneratedProfilePicture();
  profileButton.classList.remove("is-loading");
}

async function refreshProfile() {
  const auth = await api("/api/auth/me");
  applyProfile(auth.profile || {});
}

async function createAndSaveProfilePicture() {
  await queueGeneratedProfilePictureUpload(showGeneratedProfilePicture());
}

function logout(event) {
  event.preventDefault();
  sessionStorage.removeItem("peak.profile.v1");
  sessionStorage.removeItem("peak.nav.previous");
  window.location.assign("/logout");
}

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function dateKey(date) {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function clampDate(date) {
  const cleanDate = startOfDay(date);
  if (cleanDate < MIN_DATE) return new Date(MIN_DATE);
  if (cleanDate > MAX_DATE) return new Date(MAX_DATE);
  return cleanDate;
}

function sameDay(left, right) {
  return dateKey(left) === dateKey(right);
}

function addDays(date, amount) {
  return clampDate(new Date(date.getFullYear(), date.getMonth(), date.getDate() + amount));
}

function addMonths(year, month, amount) {
  const next = new Date(year, month + amount, 1);
  if (next < new Date(MIN_YEAR, 0, 1)) return { year: MIN_YEAR, month: 0 };
  if (next > new Date(MAX_YEAR, 11, 1)) return { year: MAX_YEAR, month: 11 };
  return { year: next.getFullYear(), month: next.getMonth() };
}

function monthName(month, format = "long") {
  return new Intl.DateTimeFormat(undefined, { month: format }).format(new Date(2024, month, 1));
}

function renderSelectedDate() {
  const key = dateKey(selectedDate);
  dashboardDateTime.dataset.date = key;
  dashboardDateTime.setAttribute("aria-label", `Choose date, currently ${selectedDate.toLocaleDateString()}`);
  dashboardWeekday.textContent = new Intl.DateTimeFormat(undefined, {
    weekday: "long"
  }).format(selectedDate);
  dashboardDate.textContent = new Intl.DateTimeFormat(undefined, {
    month: "long",
    day: "numeric",
    year: "numeric"
  }).format(selectedDate);
  datePrev.disabled = sameDay(selectedDate, MIN_DATE);
  dateNext.disabled = sameDay(selectedDate, MAX_DATE);
}

function setSelectedDate(date) {
  selectedDate = clampDate(date);
  calendarYear = selectedDate.getFullYear();
  calendarMonth = selectedDate.getMonth();
  activeDecade = Math.floor(calendarYear / 10) * 10;
  renderSelectedDate();
}

function createCalendar() {
  const element = document.createElement("div");
  element.className = "calendar-popover";
  element.hidden = true;
  element.innerHTML = `
    <div class="calendar-panel" role="dialog" aria-modal="false" aria-label="Select date">
      <header class="calendar-header">
        <button class="calendar-arrow" type="button" data-calendar-action="previous-month" aria-label="Previous month">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 6-6 6 6 6"/></svg>
        </button>
        <div class="calendar-title" id="calendar-title"></div>
        <button class="calendar-arrow" type="button" data-calendar-action="next-month" aria-label="Next month">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>
        </button>
      </header>
      <div class="calendar-body" id="calendar-body"></div>
    </div>
  `;
  document.body.append(element);
  element.addEventListener("click", event => {
    if (event.target === element) closeCalendar();
  });
  element.addEventListener("click", handleCalendarClick);
  return element;
}

function calendarTitle() {
  const title = calendarElement.querySelector("#calendar-title");
  if (calendarView === "day") {
    title.innerHTML = `
      <button type="button" data-calendar-action="show-months">${monthName(calendarMonth)}</button>
      <button type="button" data-calendar-action="show-years">${calendarYear}</button>
    `;
    return;
  }
  if (calendarView === "month") {
    title.innerHTML = `<button type="button" data-calendar-action="show-decades">${calendarYear}</button>`;
    return;
  }
  if (calendarView === "year") {
    title.innerHTML = `<button type="button" data-calendar-action="show-decades">${activeDecade}–${activeDecade + 9}</button>`;
    return;
  }
  title.textContent = `${MIN_YEAR}–${MAX_YEAR}`;
}

function renderDayView(body) {
  const firstOfMonth = new Date(calendarYear, calendarMonth, 1);
  const firstGridDay = new Date(calendarYear, calendarMonth, 1 - firstOfMonth.getDay());
  const weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  body.innerHTML = `
    <div class="calendar-weekdays">${weekdays.map(day => `<span>${day}</span>`).join("")}</div>
    <div class="calendar-days"></div>
  `;
  const days = body.querySelector(".calendar-days");
  for (let index = 0; index < 42; index += 1) {
    const date = new Date(firstGridDay.getFullYear(), firstGridDay.getMonth(), firstGridDay.getDate() + index);
    const disabled = date < MIN_DATE || date > MAX_DATE;
    const classes = [
      "calendar-day",
      date.getMonth() !== calendarMonth ? "is-muted" : "",
      sameDay(date, today) ? "is-today" : "",
      sameDay(date, selectedDate) ? "is-selected" : "",
    ].filter(Boolean).join(" ");
    days.insertAdjacentHTML(
      "beforeend",
      `<button class="${classes}" type="button" data-calendar-date="${dateKey(date)}"${disabled ? " disabled" : ""}>${date.getDate()}</button>`
    );
  }
}

function renderMonthView(body) {
  body.innerHTML = `<div class="calendar-months">${
    Array.from({ length: 12 }, (_, month) => {
      const disabled = calendarYear === MIN_YEAR && month < 0 || calendarYear === MAX_YEAR && month > 11;
      const selected = calendarYear === selectedDate.getFullYear() && month === selectedDate.getMonth();
      return `<button class="${selected ? "is-selected" : ""}" type="button" data-calendar-month="${month}"${disabled ? " disabled" : ""}>${monthName(month, "short")}</button>`;
    }).join("")
  }</div>`;
}

function renderYearView(body) {
  const years = [];
  for (let year = activeDecade; year <= activeDecade + 9; year += 1) {
    if (year >= MIN_YEAR && year <= MAX_YEAR) years.push(year);
  }
  body.innerHTML = `<div class="calendar-years">${
    years.map(year => `<button class="${year === calendarYear ? "is-selected" : ""}" type="button" data-calendar-year="${year}">${year}</button>`).join("")
  }</div>`;
}

function renderDecadeView(body) {
  const decades = [];
  for (let decade = Math.floor(MIN_YEAR / 10) * 10; decade <= Math.floor(MAX_YEAR / 10) * 10; decade += 10) {
    const start = Math.max(decade, MIN_YEAR);
    const end = Math.min(decade + 9, MAX_YEAR);
    decades.push({ decade, label: `${start}–${end}` });
  }
  body.innerHTML = `<div class="calendar-decades">${
    decades.map(({ decade, label }) => `<button class="${decade === activeDecade ? "is-selected" : ""}" type="button" data-calendar-decade="${decade}">${label}</button>`).join("")
  }</div>`;
}

function renderCalendar() {
  if (!calendarElement) calendarElement = createCalendar();
  calendarTitle();
  const body = calendarElement.querySelector("#calendar-body");
  calendarElement.querySelector('[data-calendar-action="previous-month"]').hidden = calendarView !== "day";
  calendarElement.querySelector('[data-calendar-action="next-month"]').hidden = calendarView !== "day";
  if (calendarView === "day") renderDayView(body);
  else if (calendarView === "month") renderMonthView(body);
  else if (calendarView === "year") renderYearView(body);
  else renderDecadeView(body);
}

function openCalendar() {
  if (!calendarElement) calendarElement = createCalendar();
  calendarView = "day";
  calendarYear = selectedDate.getFullYear();
  calendarMonth = selectedDate.getMonth();
  activeDecade = Math.floor(calendarYear / 10) * 10;
  calendarElement.hidden = false;
  dashboardDateTime.setAttribute("aria-expanded", "true");
  renderCalendar();
}

function closeCalendar() {
  if (!calendarElement) return;
  calendarElement.hidden = true;
  dashboardDateTime.setAttribute("aria-expanded", "false");
}

function handleCalendarClick(event) {
  const action = event.target.closest("[data-calendar-action]")?.dataset.calendarAction;
  if (action === "previous-month") {
    const next = addMonths(calendarYear, calendarMonth, -1);
    calendarYear = next.year;
    calendarMonth = next.month;
    renderCalendar();
    return;
  }
  if (action === "next-month") {
    const next = addMonths(calendarYear, calendarMonth, 1);
    calendarYear = next.year;
    calendarMonth = next.month;
    renderCalendar();
    return;
  }
  if (action === "show-months") {
    calendarView = "month";
    renderCalendar();
    return;
  }
  if (action === "show-years") {
    calendarView = "year";
    activeDecade = Math.floor(calendarYear / 10) * 10;
    renderCalendar();
    return;
  }
  if (action === "show-decades") {
    calendarView = "decade";
    renderCalendar();
    return;
  }

  const dateButton = event.target.closest("[data-calendar-date]");
  if (dateButton) {
    const [year, month, day] = dateButton.dataset.calendarDate.split("-").map(Number);
    setSelectedDate(new Date(year, month - 1, day));
    closeCalendar();
    return;
  }

  const monthButton = event.target.closest("[data-calendar-month]");
  if (monthButton) {
    calendarMonth = Number(monthButton.dataset.calendarMonth);
    calendarView = "day";
    renderCalendar();
    return;
  }

  const yearButton = event.target.closest("[data-calendar-year]");
  if (yearButton) {
    calendarYear = Number(yearButton.dataset.calendarYear);
    calendarView = "month";
    renderCalendar();
    return;
  }

  const decadeButton = event.target.closest("[data-calendar-decade]");
  if (decadeButton) {
    activeDecade = Number(decadeButton.dataset.calendarDecade);
    calendarView = "year";
    renderCalendar();
  }
}

async function initializeProfile() {
  const initialProfile = readBootstrappedProfile() || readCachedProfile();
  if (initialProfile) applyProfile(initialProfile, { uploadMissingPicture: false });
  else renderFallbackShell();

  try {
    await refreshProfile();
  } catch (error) {
    profileUsername.textContent = "Profile unavailable";
    profileButton.title = error.message;
  } finally {
    profileButton.classList.remove("is-loading");
  }
}

profilePicture.addEventListener("error", async () => {
  if (replacingMissingPicture) return;
  replacingMissingPicture = true;
  try {
    await createAndSaveProfilePicture();
  } catch (error) {
    profileButton.title = error.message;
  } finally {
    replacingMissingPicture = false;
  }
});

renderAppRoute(window.location.pathname);
renderSelectedDate();
datePrev.addEventListener("click", () => setSelectedDate(addDays(selectedDate, -1)));
dateNext.addEventListener("click", () => setSelectedDate(addDays(selectedDate, 1)));
dashboardDateTime.addEventListener("click", openCalendar);
logoutButton.addEventListener("click", logout);
document.addEventListener("keydown", event => {
  if (event.key === "Escape") closeCalendar();
});
initializeAppNav({
  onNavigate: ({ path }) => renderAppRoute(path, { animate: true })
});
initializeProfile();
