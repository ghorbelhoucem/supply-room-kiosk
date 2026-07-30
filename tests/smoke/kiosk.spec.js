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
        payload.txIds.includes(h.txId) ? { ...h, returnedAt: new Date().toISOString(), returnedBy: payload.returnedBy } : h
      );
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
  });
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
  const fileUrl = `file://${path.join(__dirname, '..', '..', 'index.html')}`;
  await page.goto(fileUrl);
});

test('sign-in flow works', async ({ page }) => {
  await page.getByText('Maintenance').click();
  await page.getByText('Marwan').click();
  await page.locator('#pinInput').fill('4827');
  await page.getByRole('button', { name: 'Sign In' }).click();
  await expect(page.getByText('Take Something')).toBeVisible();
});

test('take batch flow works', async ({ page }) => {
  await page.getByText('Maintenance').click();
  await page.getByText('Marwan').click();
  await page.locator('#pinInput').fill('4827');
  await page.getByRole('button', { name: 'Sign In' }).click();
  await page.getByText('Take Something').click();
  await page.getByRole('button', { name: 'Or choose manually' }).click();
  await page.getByText('Tools').first().click();
  await page.getByText('Keyboard').first().click();
  await page.getByRole('button', { name: 'Confirm' }).click();
  await page.getByRole('button', { name: /Confirm & Take/ }).click();
  await expect(page.getByText('item(s) recorded')).toBeVisible();
});

test('return batch flow works', async ({ page }) => {
  await page.getByText('Maintenance').click();
  await page.getByText('Marwan').click();
  await page.locator('#pinInput').fill('4827');
  await page.getByRole('button', { name: 'Sign In' }).click();
  await page.getByText('Take Something').click();
  await page.getByRole('button', { name: 'Or choose manually' }).click();
  await page.getByText('Tools').first().click();
  await page.getByText('Keyboard').first().click();
  await page.getByRole('button', { name: 'Confirm' }).click();
  await page.getByRole('button', { name: /Confirm & Take/ }).click();
  await page.getByRole('button', { name: 'Done' }).click();
  await page.getByText('Maintenance').click();
  await page.getByText('Marwan').click();
  await page.locator('#pinInput').fill('4827');
  await page.getByRole('button', { name: 'Sign In' }).click();
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
