export const APP_SECTIONS = {
  "/app/dashboard": {
    title: "Dashboard",
    kicker: "",
    description: "",
    dashboard: true
  },
  "/app/workout": {
    title: "Workout",
    kicker: "Training log",
    description: "Blank workout page. This will hold exercise logging later."
  },
  "/app/meals": {
    title: "Meals",
    kicker: "Nutrition log",
    description: "Blank meals page. This will hold food and nutrient logging later."
  },
  "/app/charts": {
    title: "Charts",
    kicker: "Progress views",
    description: "Blank charts page. This will hold metric visualizations later."
  },
  "/app/plan": {
    title: "Plan",
    kicker: "Pace AI",
    description: "Blank Plan page. This will hold AI planning later."
  },
  "/app/friends": {
    title: "Friends",
    kicker: "Social",
    description: "Blank friends page. This will hold friend requests and comparisons later."
  },
  "/app/leagues": {
    title: "Leagues",
    kicker: "Competition",
    description: "Blank leagues page. This will hold tournaments and rankings later."
  }
};

export function normalizeAppPath(pathname) {
  const route = pathname.replace(/\/+$/, "") || "/app/dashboard";
  if (route === "/" || route === "/app") return "/app/dashboard";
  if (route === "/dashboard") return "/app/dashboard";
  if (route.startsWith("/app/")) return APP_SECTIONS[route] ? route : "/app/dashboard";
  const appRoute = `/app${route}`;
  return APP_SECTIONS[appRoute] ? appRoute : "/app/dashboard";
}

export function renderAppRoute(pathname, { animate = false } = {}) {
  const main = document.querySelector(".dashboard");
  if (!main) return;
  const path = normalizeAppPath(pathname);
  const section = APP_SECTIONS[path] || APP_SECTIONS["/app/dashboard"];

  function commit() {
    document.title = `${section.title} | Peak`;
    if (section.dashboard) {
      main.className = "dashboard";
      main.setAttribute("aria-label", "Dashboard");
      main.innerHTML = "";
      return;
    }
    main.className = "dashboard section-page";
    main.setAttribute("aria-label", `${section.title} section`);
    main.innerHTML = `
      <section class="section-card">
        <p>${section.kicker}</p>
        <h1>${section.title}</h1>
        <span>${section.description}</span>
      </section>
    `;
  }

  if (!animate) {
    commit();
    return;
  }

  main.classList.add("is-route-switching");
  window.setTimeout(() => {
    commit();
    requestAnimationFrame(() => {
      requestAnimationFrame(() => main.classList.remove("is-route-switching"));
    });
  }, 90);
}
