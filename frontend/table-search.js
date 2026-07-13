const IDENTIFIER_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;
const SEARCH_MODES = new Set(["as-you-type", "after-pause"]);

function resolveInput(input) {
  const element = typeof input === "string" ? document.querySelector(input) : input;
  if (!element || typeof element.addEventListener !== "function") {
    throw new TypeError("input must be an input element or a selector that resolves to one.");
  }
  return element;
}

async function readJson(response) {
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `search returned ${response.status}`);
  return payload;
}

export function createTableFuzzySearch({
  input,
  table,
  field = "name",
  searchMode = "as-you-type",
  pauseMs = 700,
  requestDelayMs = 40,
  minChars = 1,
  endpoint = "/api/search",
  fetchNames,
  fetchRow,
  onNames = () => {},
  onRow = () => {},
  onClear = () => {},
  onStatus = () => {},
  onError = () => {}
}) {
  if (!IDENTIFIER_PATTERN.test(table) || !IDENTIFIER_PATTERN.test(field)) {
    throw new TypeError("table and field must be valid SQL identifiers.");
  }
  if (!SEARCH_MODES.has(searchMode)) {
    throw new TypeError('searchMode must be "as-you-type" or "after-pause".');
  }

  const inputElement = resolveInput(input);
  let namesTimer;
  let rowTimer;
  let namesController;
  let rowController;
  let generation = 0;
  let activeMode = searchMode;
  let matchedQuery = "";
  let matches = [];
  let namesRequest;

  const requestNames = fetchNames || (async ({ query, signal }) => {
    const parameters = new URLSearchParams({ table, field, q: query, details: "false" });
    const response = await fetch(`${endpoint}?${parameters}`, { cache: "no-store", signal });
    const payload = await readJson(response);
    return payload.data.results;
  });

  const requestRow = fetchRow || (async ({ query, signal }) => {
    const parameters = new URLSearchParams({ table, field, q: query, details: "true" });
    const response = await fetch(`${endpoint}?${parameters}`, { cache: "no-store", signal });
    const payload = await readJson(response);
    return payload.data.results[0] ?? null;
  });

  function context(query, extra = {}) {
    return { query, table, field, mode: activeMode, ...extra };
  }

  async function loadNames(query, currentGeneration) {
    if (matchedQuery === query) return matches;
    if (namesRequest?.query === query && namesRequest.generation === currentGeneration) {
      return namesRequest.promise;
    }

    namesController?.abort();
    namesController = new AbortController();
    const signal = namesController.signal;
    onStatus({ phase: "loading-names", ...context(query) });

    const promise = (async () => {
      const results = await requestNames({ ...context(query), signal });
      if (currentGeneration !== generation) return [];
      matches = Array.isArray(results) ? results.slice(0, 5) : [];
      matchedQuery = query;
      onNames(matches, context(query));
      onStatus({ phase: "names-ready", ...context(query) });
      return matches;
    })();
    namesRequest = { query, generation: currentGeneration, promise };

    try {
      return await promise;
    } finally {
      if (namesRequest?.promise === promise) namesRequest = undefined;
    }
  }

  async function loadRow(query, currentGeneration) {
    try {
      const currentMatches = await loadNames(query, currentGeneration);
      if (currentGeneration !== generation) return;

      if (!currentMatches.length) {
        onRow(null, context(query, { matches: currentMatches, topMatch: null }));
        onStatus({ phase: "row-ready", ...context(query) });
        return;
      }

      rowController?.abort();
      rowController = new AbortController();
      const topMatch = currentMatches[0];
      onStatus({ phase: "loading-row", ...context(query) });
      const row = await requestRow({
        ...context(query),
        signal: rowController.signal,
        matches: currentMatches,
        topMatch
      });
      if (currentGeneration !== generation) return;
      onRow(row ?? null, context(query, { matches: currentMatches, topMatch }));
      onStatus({ phase: "row-ready", ...context(query) });
    } catch (error) {
      if (error.name !== "AbortError" && currentGeneration === generation) {
        onError(error, context(query));
        onStatus({ phase: "error", ...context(query) });
      }
    }
  }

  function search(rawQuery = inputElement.value) {
    const query = String(rawQuery).trim();
    generation += 1;
    const currentGeneration = generation;
    clearTimeout(namesTimer);
    clearTimeout(rowTimer);
    namesController?.abort();
    rowController?.abort();
    matchedQuery = "";
    matches = [];
    namesRequest = undefined;

    if (query.length < minChars) {
      onClear(context(query));
      onStatus({ phase: "idle", ...context(query) });
      return;
    }

    onStatus({ phase: "typing", ...context(query) });
    if (activeMode === "as-you-type") {
      namesTimer = setTimeout(() => {
        loadNames(query, currentGeneration).catch(error => {
          if (error.name !== "AbortError" && currentGeneration === generation) {
            onError(error, context(query));
            onStatus({ phase: "error", ...context(query) });
          }
        });
      }, requestDelayMs);
    }
    rowTimer = setTimeout(() => loadRow(query, currentGeneration), pauseMs);
  }

  function setMode(mode, { runSearch = true } = {}) {
    if (!SEARCH_MODES.has(mode)) {
      throw new TypeError('mode must be "as-you-type" or "after-pause".');
    }
    activeMode = mode;
    if (runSearch) search(inputElement.value);
  }

  function destroy() {
    inputElement.removeEventListener("input", handleInput);
    clearTimeout(namesTimer);
    clearTimeout(rowTimer);
    namesController?.abort();
    rowController?.abort();
  }

  function handleInput() {
    search(inputElement.value);
  }

  inputElement.addEventListener("input", handleInput);
  return { search, setMode, destroy };
}
