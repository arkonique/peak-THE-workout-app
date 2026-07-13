const state = { username: "", sessionId: null, sending: false, retentionDays: 1 };
const sessionsElement = document.querySelector("#sessions");
const messagesElement = document.querySelector("#messages");
const statusElement = document.querySelector("#status");
const form = document.querySelector("#chat-form");
const promptElement = document.querySelector("#prompt");
const sendElement = document.querySelector("#send");
const retentionElement = document.querySelector("#retention-notice");

async function api(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", credentials: "same-origin", ...options });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request returned ${response.status}.`);
  return payload.data;
}

function setStatus(message, error = false) {
  statusElement.textContent = message;
  statusElement.classList.toggle("error", error);
}

function applyRetention(retention) {
  if (!retention?.days) return;
  state.retentionDays = retention.days;
  const duration = retention.days === 1 ? "1 day" : `${retention.days} days`;
  retentionElement.textContent = `Important: this chat is stored temporarily by Google and will be permanently deleted after ${duration}. Messages are not stored in Supabase.`;
}

function valueText(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function titleCase(value) {
  return String(value).replace(/_/g, " ").replace(/\b\w/g, letter => letter.toUpperCase());
}

function renderModelText(model, target) {
  const workout = model?.workout;
  if (workout) {
    const section = document.createElement("section");
    section.className = "model-section";
    const heading = document.createElement("h3");
    heading.textContent = workout.workout_name;
    section.append(heading);
    for (const exercise of workout.exercises || []) {
      const line = document.createElement("p");
      line.className = "model-line";
      line.textContent = `${exercise.exercise_name}: ${exercise.reasoning}`;
      section.append(line);
    }
    target.append(section);
  }
  for (const meal of model?.meals || []) {
    const section = document.createElement("section");
    section.className = "model-section";
    const heading = document.createElement("h3");
    heading.textContent = meal.meal_name;
    section.append(heading);
    for (const food of meal.foods || []) {
      const line = document.createElement("p");
      line.className = "model-line";
      line.textContent = `${food.quantity} ${food.name}: ${food.reasoning}`;
      section.append(line);
    }
    target.append(section);
  }
  if (!workout && !(model?.meals || []).length) target.textContent = "The model returned an empty plan.";
}

function exerciseCard(exercise) {
  const card = document.createElement("article");
  card.className = "card";
  const heading = document.createElement("h4");
  heading.textContent = exercise.name || `Exercise ${exercise.id}`;
  card.append(heading);
  const fields = document.createElement("dl");
  fields.className = "fields";
  for (const [key, value] of Object.entries(exercise)) {
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = titleCase(key);
    description.textContent = valueText(value);
    fields.append(term, description);
  }
  card.append(fields);
  return card;
}

function nutritionCard(detail) {
  const card = document.createElement("article");
  card.className = "card";
  const product = detail.product || {};
  const heading = document.createElement("h4");
  heading.textContent = product.product_name_en || product.product_name || detail.match?.name || detail.requested_name;
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = [detail.meal_name, detail.quantity, product.brands, detail.match?.code].filter(Boolean).join(" · ");
  card.append(heading, meta);
  if (detail.error) {
    const error = document.createElement("p");
    error.className = "card-error";
    error.textContent = detail.error;
    card.append(error);
    return card;
  }

  const nutrients = document.createElement("dl");
  nutrients.className = "nutrients";
  const values = product.nutriments || {};
  const entries = Object.entries(values)
    .filter(([key, value]) => key.endsWith("_100g") && !key.includes("prepared") && Number.isFinite(Number(value)))
    .sort(([a], [b]) => a.localeCompare(b));
  for (const [key, value] of entries) {
    const nutrient = key.slice(0, -5);
    const unit = values[`${nutrient}_unit`] || "";
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = titleCase(nutrient.replaceAll("-", " "));
    description.textContent = `${valueText(value)} ${unit}`.trim();
    nutrients.append(term, description);
  }
  card.append(nutrients);
  const ingredients = product.ingredients_text_en || product.ingredients_text || "Ingredient information unavailable.";
  const paragraph = document.createElement("p");
  paragraph.className = "ingredients";
  paragraph.textContent = `Ingredients: ${ingredients}`;
  card.append(paragraph);
  return card;
}

// Formatting is deliberately separate from the POST request so this renderer can be reused.
function renderStructured(structured, host) {
  if (!structured) return;
  const exercises = structured.exercise_details || [];
  const foods = structured.food_details || [];
  if (exercises.length) {
    const cards = document.createElement("section");
    cards.className = "cards";
    exercises.forEach(exercise => cards.append(exerciseCard(exercise)));
    host.append(cards);
  }
  if (foods.length) {
    const cards = document.createElement("section");
    cards.className = "cards";
    foods.forEach(food => cards.append(nutritionCard(food)));
    host.append(cards);
  }
}

function renderMessages(messages) {
  messagesElement.replaceChildren();
  if (!messages.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = `Start a new conversation. Google permanently deletes free-tier chat history after ${state.retentionDays} day${state.retentionDays === 1 ? "" : "s"}.`;
    messagesElement.append(empty);
    return;
  }
  for (const message of messages) {
    const wrapper = document.createElement("article");
    wrapper.className = `message ${message.role}`;
    const label = document.createElement("div");
    label.className = "message-label";
    label.textContent = message.role === "user" ? state.username : "peak ai";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (message.role === "assistant" && message.structured_data?.model_response) {
      renderModelText(message.structured_data.model_response, bubble);
    } else {
      bubble.textContent = message.content;
    }
    wrapper.append(label, bubble);
    messagesElement.append(wrapper);
    if (message.role === "assistant") renderStructured(message.structured_data, messagesElement);
  }
  messagesElement.scrollTop = messagesElement.scrollHeight;
}

function renderSessions(sessions) {
  sessionsElement.replaceChildren();
  for (const session of sessions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `session${session.id === state.sessionId ? " active" : ""}`;
    const title = document.createElement("span");
    title.className = "session-title";
    title.textContent = session.title;
    const time = document.createElement("span");
    time.className = "session-time";
    const expires = session.history_expires_at
      ? ` · expires ${new Date(session.history_expires_at).toLocaleString()}`
      : "";
    time.textContent = `${new Date(session.updated_at).toLocaleString()}${expires}`;
    button.append(title, time);
    button.addEventListener("click", () => selectSession(session.id));
    sessionsElement.append(button);
  }
}

async function loadSessions() {
  const data = await api("/api/ai/sessions");
  applyRetention(data.retention);
  renderSessions(data.sessions || []);
}

async function selectSession(id) {
  if (state.sending) return;
  try {
    setStatus("loading session…");
    const data = await api(`/api/ai/sessions/${encodeURIComponent(id)}`);
    applyRetention(data.retention);
    state.sessionId = data.session.id;
    renderMessages(data.messages || []);
    await loadSessions();
    setStatus(`${state.username} · ${data.session.title}`);
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function sendPrompt(prompt) {
  state.sending = true;
  promptElement.disabled = true;
  sendElement.disabled = true;
  setStatus("Gemini is generating; food enrichment may continue afterward…");
  try {
    const data = await api("/api/ai/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: state.username, session_id: state.sessionId, prompt })
    });
    applyRetention(data.retention);
    state.sessionId = data.session.id;
    renderMessages(data.messages || []);
    promptElement.value = "";
    await loadSessions();
    setStatus(`${state.username} · complete`);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    state.sending = false;
    promptElement.disabled = false;
    sendElement.disabled = false;
    promptElement.focus();
  }
}

form.addEventListener("submit", event => {
  event.preventDefault();
  const prompt = promptElement.value.trim();
  if (prompt && !state.sending) sendPrompt(prompt);
});

promptElement.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelector("#new-chat").addEventListener("click", () => {
  state.sessionId = null;
  renderMessages([]);
  loadSessions().catch(error => setStatus(error.message, true));
  setStatus(`${state.username} · new session`);
  promptElement.focus();
});

async function initialize() {
  try {
    const auth = await api("/api/auth/me");
    state.username = auth.profile?.username || "user";
    promptElement.disabled = false;
    sendElement.disabled = false;
    await loadSessions();
    setStatus(`${state.username} · ready`);
    promptElement.focus();
  } catch (error) {
    setStatus(`${error.message} Open /auth-test to log in.`, true);
  }
}

initialize();
