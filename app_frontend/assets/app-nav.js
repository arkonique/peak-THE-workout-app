import { APP_SECTIONS, normalizeAppPath } from "/app/assets/app-pages.js";

const TABS = [
  {
    key: "dashboard",
    label: "Dashboard",
    href: "/app/dashboard",
    colors: ["#7779ff", "#fa2a97"],
    icon: '<path d="M4.5 11.2 12 5l7.5 6.2"/><path d="M6.3 10.4v7.3h4.1v-4.4h3.2v4.4h4.1v-7.3"/><path d="M9.2 7.1V5h2"/>'
  },
  {
    key: "workout",
    label: "Workout",
    href: "/app/workout",
    colors: ["#ff9900", "#f14c7d"],
    icon: '<path d="M4.3 12h15.4"/><path d="M7.1 8.4v7.2"/><path d="M10 9.6v4.8"/><path d="M14 9.6v4.8"/><path d="M16.9 8.4v7.2"/><path d="M3.1 10.2v3.6"/><path d="M20.9 10.2v3.6"/>'
  },
  {
    key: "meals",
    label: "Meals",
    href: "/app/meals",
    colors: ["#61e37a", "#12b5cb"],
    icon: '<path d="M6.6 4.5v6.4a2.5 2.5 0 0 0 2.5 2.5v6.1"/><path d="M4.4 4.5v4.9"/><path d="M8.8 4.5v4.9"/><path d="M15.3 4.6c2.1.7 3.6 2.7 3.6 5.1v1.8a3.8 3.8 0 0 1-2.2 3.4v4.6"/><path d="M15.3 4.6v10.3"/>'
  },
  {
    key: "charts",
    label: "Charts",
    href: "/app/charts",
    colors: ["#638cff", "#dff34e"],
    icon: '<path d="M5 19V5"/><path d="M5 19h14"/><path d="M8.2 15.7v-3"/><path d="M12 15.7V8.8"/><path d="M15.8 15.7v-5.1"/><path d="m8.2 9.9 3.8-3 3.8 1.5"/>'
  },
  {
    key: "plan",
    label: "Plan",
    href: "/app/plan",
    colors: ["#b673ff", "#fa2a97"],
    icon: '<path d="M12 4.5v2"/><path d="M7.2 6.6A4.7 4.7 0 0 0 5 10.6v2.9A4.5 4.5 0 0 0 9.5 18h5a4.5 4.5 0 0 0 4.5-4.5v-2.9a4.7 4.7 0 0 0-2.2-4"/><path d="M9.2 11.3h.1"/><path d="M14.7 11.3h.1"/><path d="M9.6 14.5h4.8"/><path d="M6 19.5h12"/>'
  },
  {
    key: "friends",
    label: "Friends",
    href: "/app/friends",
    colors: ["#f7815e", "#ffcf4a"],
    icon: '<path d="M9.2 10.9a2.9 2.9 0 1 0 0-5.8 2.9 2.9 0 0 0 0 5.8Z"/><path d="M3.9 18.7a5.3 5.3 0 0 1 10.6 0"/><path d="M16.2 10.8a2.4 2.4 0 1 0 0-4.8"/><path d="M16.4 14.1a4.4 4.4 0 0 1 3.7 4.4"/>'
  },
  {
    key: "leagues",
    label: "Leagues",
    href: "/app/leagues",
    colors: ["#dff34e", "#61e37a"],
    icon: '<path d="M8.1 5.2h7.8v2.5a3.9 3.9 0 0 1-7.8 0V5.2Z"/><path d="M8.1 6.7H5.3v1.5a3 3 0 0 0 3.2 3"/><path d="M15.9 6.7h2.8v1.5a3 3 0 0 1-3.2 3"/><path d="M12 11.6v3.5"/><path d="M9.3 18.8h5.4"/><path d="M10.1 15.1h3.8v3.7h-3.8z"/>'
  }
];

export function initializeAppNav({ onNavigate } = {}) {
  const nav = document.querySelector("#app-bottom-nav");
  if (!nav) return;
  let currentPath = normalizeAppPath(window.location.pathname);
  let currentTab = TABS.find(tab => tab.href === currentPath) || TABS[0];

  nav.innerHTML = '<span class="bottom-nav__indicator" aria-hidden="true"></span>' + TABS.map(tab => {
    const active = currentPath === tab.href;
    const [from, to] = tab.colors;
    return `
      <a class="bottom-nav__item${active ? " is-active" : ""}" href="${tab.href}" aria-label="${tab.label}" data-nav-key="${tab.key}" style="--tab-from:${from};--tab-to:${to};"${active ? ' aria-current="page"' : ""}>
        <span class="bottom-nav__icon">
          <svg viewBox="0 0 24 24" aria-hidden="true">${tab.icon}</svg>
        </span>
      </a>
    `;
  }).join("");

  const items = new Map([...nav.querySelectorAll(".bottom-nav__item")].map(item => [item.dataset.navKey, item]));
  const tabsByPath = new Map(TABS.map(tab => [tab.href, tab]));

  function placeIndicator(key) {
    const item = items.get(key);
    if (!item) return;
    const navRect = nav.getBoundingClientRect();
    const itemRect = item.getBoundingClientRect();
    const tab = TABS.find(entry => entry.key === key) || currentTab;
    nav.style.setProperty("--indicator-x", `${itemRect.left - navRect.left + itemRect.width / 2}px`);
    nav.style.setProperty("--indicator-y", `${itemRect.top - navRect.top + itemRect.height / 2}px`);
    nav.style.setProperty("--indicator-color", tab.colors[0]);
  }

  function updateActiveClasses(nextTab) {
    items.forEach(item => {
      const active = item.dataset.navKey === nextTab.key;
      item.classList.toggle("is-active", active);
      if (active) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
  }

  function moveTo(path, { animate = true, render = true, history = true } = {}) {
    const normalizedPath = normalizeAppPath(path);
    if (!APP_SECTIONS[normalizedPath]) return false;
    const nextTab = tabsByPath.get(normalizedPath) || TABS[0];
    if (normalizedPath === currentPath && render) return true;

    placeIndicator(currentTab.key);
    updateActiveClasses(nextTab);
    currentPath = normalizedPath;
    currentTab = nextTab;

    if (history) window.history.pushState({ peakRoute: normalizedPath }, "", normalizedPath);
    if (render) {
      onNavigate?.({ path: normalizedPath, tab: nextTab });
      window.dispatchEvent(new CustomEvent("peak:navigate", { detail: { path: normalizedPath, tab: nextTab } }));
    }

    if (animate) {
      nav.classList.remove("is-moving");
      void nav.offsetWidth;
      nav.classList.add("is-moving");
      placeIndicator(nextTab.key);
      window.setTimeout(() => nav.classList.remove("is-moving"), 430);
    } else {
      placeIndicator(nextTab.key);
    }
    return true;
  }

  updateActiveClasses(currentTab);
  requestAnimationFrame(() => placeIndicator(currentTab.key));

  nav.addEventListener("click", event => {
    const link = event.target.closest(".bottom-nav__item");
    if (!link) return;
    const destination = new URL(link.href, window.location.href);
    if (destination.origin !== window.location.origin) return;
    if (!APP_SECTIONS[normalizeAppPath(destination.pathname)]) return;
    event.preventDefault();
    moveTo(destination.pathname);
  });

  window.addEventListener("popstate", () => {
    moveTo(window.location.pathname, { history: false, render: true });
  });

  window.addEventListener("resize", () => placeIndicator(currentTab.key), { passive: true });
}
