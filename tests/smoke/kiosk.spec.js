const { test, expect } = require('@playwright/test');
const path = require('path');

function mockApi(page) {
  const state = {
    inventory: [
      { reference: 'Tools', item: 'Keyboard', quantity: 3, availability: '3/3' },
      { reference: 'Station Parts', item: 'HDMI Cable', quantity: 8, availability: '8 left' },
    ],
    history: [],
  };

  return page.route('**/exec', async (route, request) => {
    if (request.method() === 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(state) });
      return;
    }
    const body = request.postData() || '{}';
    const payload = JSON.parse(body);
    if (payload.action === 'takeBatch') {
      state.history.push({
        timestamp: new Date().toISOString(),
        personRole: `${payload.person}/${payload.role}`,
        item: payload.items[0].item,
        expectedReturn: payload.items[0].expectedReturn || 'None',
        returnedAt: 'Not returned',
        returnedBy: '',
        txId: 'tx-1',
      });
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
      return;
    }
    if (payload.action === 'returnBatch') {
      state.history = state.history.map((h) =>
        payload.txIds.includes(h.txId)
          ? { ...h, returnedAt: new Date().toISOString(), returnedBy: payload.returnedBy }
          : h
      );
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
  });
}

// Enter a PIN using the on-screen numpad (replaced the old #pinInput text field).
async function enterPin(page, pin) {
  for (const digit of pin) {
    await page.locator(`[data-digit="${digit}"]`).click();
  }
  await page.getByRole('button', { name: 'Sign In' }).click();
}

// Full sign-in sequence: role card → name card → PIN numpad.
async function signIn(page, { role = 'Maintenance', name = 'Marwan', pin = '4827' } = {}) {
  await page.getByText(role).click();
  await page.getByText(name).click();
  await enterPin(page, pin);
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
  const fileUrl = `file://${path.join(__dirname, '..', '..', 'index.html')}`;
  await page.goto(fileUrl);
});

test('sign-in flow works', async ({ page }) => {
  await signIn(page);
  await expect(page.getByText('Take Something')).toBeVisible();
});

test('take batch flow works', async ({ page }) => {
  await signIn(page);
  await page.getByText('Take Something').click();
  await page.getByRole('button', { name: 'Or choose manually' }).click();
  await page.getByText('Tools').first().click();
  await page.getByText('Keyboard').first().click();
  await page.getByRole('button', { name: 'Confirm' }).click();
  await page.getByRole('button', { name: /Confirm & Take/ }).click();
  await expect(page.getByText('item(s) recorded')).toBeVisible();
});

test('return batch flow works', async ({ page }) => {
  // First: take a tool
  await signIn(page);
  await page.getByText('Take Something').click();
  await page.getByRole('button', { name: 'Or choose manually' }).click();
  await page.getByText('Tools').first().click();
  await page.getByText('Keyboard').first().click();
  await page.getByRole('button', { name: 'Confirm' }).click();
  await page.getByRole('button', { name: /Confirm & Take/ }).click();
  await page.getByRole('button', { name: 'Done' }).click();

  // Then: sign back in and return it
  await signIn(page);
  await page.getByText('Return a Tool').click();
  await page.getByText('Keyboard').first().click();
  await page.getByRole('button', { name: 'Return without QR code' }).click();
  await page.getByRole('button', { name: /Review & Confirm/ }).click();
  await page.getByRole('button', { name: /Confirm & Return/ }).click();
  await expect(page.getByText('item(s) returned')).toBeVisible();
});

test('report filters render', async ({ page }) => {
  await page.getByRole('button', { name: 'Manager Report' }).click();
  await expect(page.getByRole('button', { name: /Open/ })).toBeVisible();
  await page.getByRole('button', { name: 'Inventory Status' }).click();
  await expect(page.getByText('Station Parts')).toBeVisible();
});

test('report back-link returns to home', async ({ page }) => {
  await page.getByRole('button', { name: 'Manager Report' }).click();
  await expect(page.locator('#reportBack')).toBeVisible();
  await page.locator('#reportBack').click();
  await expect(page.getByText("Who's checking in?", { exact: false })).toBeVisible();
});

test('report preserves session when opened from menu', async ({ page }) => {
  await signIn(page);
  await page.getByRole('button', { name: 'Manager Report' }).click();
  await expect(page.locator('#reportBack')).toContainText('Back to menu');
  await page.locator('#reportBack').click();
  await expect(page.getByText('Take Something')).toBeVisible();
});
