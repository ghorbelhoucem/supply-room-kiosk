(() => {
  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function newRequestId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  async function fetchWithTimeout(url, options, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
  }

  function mapError(err) {
    const raw = String((err && err.message) || err || 'Unknown error');
    if (raw.includes('AbortError')) return 'Request timed out. Please try again.';
    if (raw.includes('Failed to fetch')) return 'Network error. Check your connection and try again.';
    return 'Unexpected server error. Please retry.';
  }

  function createApiClient(baseUrl, config = {}) {
    const timeoutMs = config.timeoutMs || 9000;
    const retries = config.retries == null ? 2 : config.retries;
    const retryDelayMs = config.retryDelayMs || 350;
    let authToken = null;
    const root = String(baseUrl || '/api').replace(/\/$/, '');

    function setToken(token) {
      authToken = token || null;
    }

    function clearToken() {
      authToken = null;
    }

    async function requestJson(path, { method = 'GET', body, auth = false, retry = true } = {}) {
      let attempts = 0;
      let lastError;
      const maxAttempts = retry ? retries : 0;
      const url = `${root}${path.startsWith('/') ? path : `/${path}`}`;
      while (attempts <= maxAttempts) {
        try {
          const headers = { Accept: 'application/json', 'Content-Type': 'application/json' };
          if (auth && authToken) headers.Authorization = `Bearer ${authToken}`;
          const res = await fetchWithTimeout(
            url,
            {
              method,
              headers,
              body: body == null ? undefined : JSON.stringify(body),
              redirect: 'follow',
            },
            timeoutMs
          );
          const data = await res.json().catch(() => ({}));
          if (!res.ok) {
            return {
              ok: false,
              error: data.detail || data.error || `HTTP ${res.status}`,
              code: data.code || `HTTP_${res.status}`,
            };
          }
          return data;
        } catch (err) {
          lastError = err;
          if (attempts >= maxAttempts) break;
          await sleep(retryDelayMs * Math.pow(2, attempts));
          attempts += 1;
        }
      }
      return { ok: false, error: mapError(lastError), rawError: String(lastError || '') };
    }

    return {
      setToken,
      clearToken,
      newRequestId,
      async loadInventory() {
        const data = await requestJson('/inventory', { method: 'GET', auth: false });
        if (data && data.ok === false) return data;
        return { ok: true, inventory: data.inventory || [], history: data.history || [] };
      },
      async loginPin({ roleKey, pin, name }) {
        return requestJson('/auth/login/pin', {
          method: 'POST',
          body: { role_key: roleKey, pin, name: name || null },
          retry: false,
        });
      },
      async loginOperator({ roleKey, operatorId, password }) {
        return requestJson('/auth/login/operator', {
          method: 'POST',
          body: { role_key: roleKey, operator_id: operatorId, password },
          retry: false,
        });
      },
      async takeBatch(payload) {
        return requestJson('/take-batch', {
          method: 'POST',
          auth: true,
          body: {
            client_request_id: payload.client_request_id || newRequestId(),
            person: payload.person,
            role: payload.role,
            items: payload.items,
          },
        });
      },
      async returnBatch(payload) {
        return requestJson('/return-batch', {
          method: 'POST',
          auth: true,
          body: {
            client_request_id: payload.client_request_id || newRequestId(),
            txIds: payload.txIds,
            returnedBy: payload.returnedBy,
          },
        });
      },
      async postAction(payload) {
        // Back-compat shim for older call sites
        if (payload.action === 'takeBatch') return this.takeBatch(payload);
        if (payload.action === 'returnBatch') return this.returnBatch(payload);
        return { ok: false, error: 'Unsupported action', code: 'UNSUPPORTED' };
      },
      mapError,
    };
  }

  window.SupplyApi = { createApiClient };
})();
