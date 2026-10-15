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

    async function requestJson(path, { method = 'GET', body, auth = false, retry } = {}) {
      // Retries are only safe for GET by default. POSTs use client_request_id
      // idempotency on the server, but we still avoid blind retries unless asked.
      const shouldRetry = retry == null ? method === 'GET' : retry;
      const maxAttempts = shouldRetry ? retries : 0;
      let attempts = 0;
      let lastError;
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
        return { ok: true, inventory: data.inventory || [], history: data.history || [], warranty: data.warranty || [], sop_layout: data.sop_layout || [], sop_locations: data.sop_locations || [] };
      },
      async loadFurnitureRooms() {
        const data = await requestJson('/furniture/rooms', { method: 'GET', auth: true });
        if (data && data.ok === false) return data;
        return { ok: true, floors: data.floors || [], rooms: data.rooms || [], types: data.types || [] };
      },
      async furnitureRoomCreate(p) {
        return requestJson('/furniture/rooms', { method: 'POST', auth: true, body: p, retry: false });
      },
      async furnitureRoomUpdate(id, p) {
        return requestJson(`/furniture/rooms/${encodeURIComponent(id)}`, { method: 'PATCH', auth: true, body: p, retry: false });
      },
      async furnitureRoomDelete(id) {
        return requestJson(`/furniture/rooms/${encodeURIComponent(id)}`, { method: 'DELETE', auth: true, retry: false });
      },
      async furnitureRoomAdd(id, p) {
        return requestJson(`/furniture/rooms/${encodeURIComponent(id)}/items`, { method: 'POST', auth: true, body: p, retry: false });
      },
      async furnitureRoomSet(id, typeId, p) {
        return requestJson(`/furniture/rooms/${encodeURIComponent(id)}/items/${encodeURIComponent(typeId)}`, { method: 'PATCH', auth: true, body: p, retry: false });
      },
      async furnitureRoomRemove(id, typeId, p) {
        return requestJson(`/furniture/rooms/${encodeURIComponent(id)}/items/${encodeURIComponent(typeId)}/remove`, { method: 'POST', auth: true, body: p, retry: false });
      },
      async furnitureRoomMove(p) {
        return requestJson('/furniture/rooms/move', { method: 'POST', auth: true, body: p, retry: false });
      },
      async furnitureRoomTypeUpdate(typeId, p) {
        return requestJson(`/furniture/room-types/${encodeURIComponent(typeId)}`, { method: 'PATCH', auth: true, body: p, retry: false });
      },
      async furnitureCreateItem(p) {
        return requestJson('/furniture/items', { method: 'POST', auth: true, body: p, retry: false });
      },
      async furnitureUpdateItem(id, p) {
        return requestJson(`/furniture/items/${encodeURIComponent(id)}`, { method: 'PATCH', auth: true, body: p, retry: false });
      },
      async furnitureAdjustStock(id, p) {
        return requestJson(`/furniture/items/${encodeURIComponent(id)}/stock`, { method: 'PATCH', auth: true, body: p, retry: false });
      },
      async furnitureDeleteItem(id) {
        return requestJson(`/furniture/items/${encodeURIComponent(id)}`, { method: 'DELETE', auth: true, retry: false });
      },
      async loadFurniture() {
        const data = await requestJson('/furniture', { method: 'GET', auth: true });
        if (data && data.ok === false) return data;
        return { ok: true, locations: data.locations || [], categories: data.categories || [], items: data.items || [] };
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
          retry: false,
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
          retry: false,
        });
      },
      async receive(payload) {
        return requestJson('/receive', {
          method: 'POST',
          auth: true,
          body: {
            client_request_id: payload.client_request_id || newRequestId(),
            item: payload.item,
            qty: payload.qty,
            reason: payload.reason || 'restock',
            category: payload.category || null,
            sop_status: payload.sop_status || null,
            shelf_location: payload.shelf_location || null,
            reorder_min: payload.reorder_min != null ? payload.reorder_min : null,
          },
          retry: false,
        });
      },
      async updateItem(p) {
        return requestJson('/inventory/update', {
          method: 'POST', auth: true, retry: false,
          body: {
            client_request_id: p.client_request_id || newRequestId(), item: p.item,
            new_name: p.new_name != null ? p.new_name : null, qty: p.qty != null ? p.qty : null,
            expected_qty: p.expected_qty != null ? p.expected_qty : null, reorder_min: p.reorder_min != null ? p.reorder_min : null,
            sop_status: p.sop_status || null, shelf_location: p.shelf_location || null, reason: p.reason || null,
          },
        });
      },
      async deleteItem(p) {
        return requestJson('/inventory/delete', {
          method: 'POST', auth: true, retry: false,
          body: { client_request_id: p.client_request_id || newRequestId(), item: p.item },
        });
      },
      async warranty(payload) {
        return requestJson('/warranty', {
          method: 'POST',
          auth: true,
          body: {
            client_request_id: payload.client_request_id || newRequestId(),
            part_name: payload.part_name,
            serial_number: payload.serial_number,
            issue: payload.issue,
          },
          retry: false,
        });
      },
      async postAction(payload) {
        if (payload.action === 'takeBatch') return this.takeBatch(payload);
        if (payload.action === 'returnBatch') return this.returnBatch(payload);
        if (payload.action === 'restock' || payload.action === 'receive') {
          return this.receive({
            item: payload.item,
            qty: payload.qty,
            reason: payload.reason || 'restock',
            category: payload.category,
            sop_status: payload.sop_status,
            shelf_location: payload.shelf_location,
          });
        }
        return { ok: false, error: 'Unsupported action', code: 'UNSUPPORTED' };
      },
      mapError,
    };
  }

  window.SupplyApi = { createApiClient };
})();
