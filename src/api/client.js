(() => {
  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function fetchWithTimeout(url, options, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(url, { ...options, signal: controller.signal });
      return res;
    } finally {
      clearTimeout(timer);
    }
  }

  function mapError(err) {
    const raw = String((err && err.message) || err || "Unknown error");
    if (raw.includes("AbortError")) return "Request timed out. Please try again.";
    if (raw.includes("Failed to fetch")) return "Network error. Check your connection and try again.";
    return "Unexpected server error. Please retry.";
  }

  function createApiClient(baseUrl, config = {}) {
    const timeoutMs = config.timeoutMs || 9000;
    const retries = config.retries == null ? 2 : config.retries;
    const retryDelayMs = config.retryDelayMs || 350;

    async function requestJson(method, payload) {
      let attempts = 0;
      let lastError;
      while (attempts <= retries) {
        try {
          const options =
            method === "GET"
              ? { method: "GET" }
              : {
                  method,
                  headers: { "Content-Type": "text/plain;charset=utf-8" },
                  body: JSON.stringify(payload || {}),
                };
          const res = await fetchWithTimeout(baseUrl, options, timeoutMs);
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return await res.json();
        } catch (err) {
          lastError = err;
          if (attempts >= retries) break;
          await sleep(retryDelayMs * Math.pow(2, attempts));
          attempts += 1;
        }
      }
      return { ok: false, error: mapError(lastError), rawError: String(lastError || "") };
    }

    return {
      async loadInventory() {
        const data = await requestJson("GET");
        if (data && data.ok === false) return data;
        return { ok: true, inventory: data.inventory || [], history: data.history || [] };
      },
      async postAction(payload) {
        const result = await requestJson("POST", payload);
        if (result && typeof result.ok === "boolean") return result;
        return { ok: true, data: result };
      },
      mapError,
    };
  }

  window.SupplyApi = { createApiClient };
})();
