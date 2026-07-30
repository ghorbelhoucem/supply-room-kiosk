const { test, expect } = require('@playwright/test');
const path = require('path');

function mockApi(page) {
  const state = {
    inventory: [
      { reference: 'Tools', item: 'Keyboard', quantity: 3, availability: '3/3', barcode: 'Keyboard', reorder_min: 1 },
      { reference: 'Station Parts', item: 'HDMI Cable', quantity: 8, availability: '8 left', barcode: 'HDMI Cable', reorder_min: 3 },
    ],
    history: [],
  };

  return page.route('**/api/**', async (route, request) => {
    const url = new URL(request.url());
    const pathname = url.pathname;
    const method = request.method();

    if (pathname.endsWith('/inventory') && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true, ...state }),
      });
      return;
    }

    if (pathname.endsWith('/auth/login/pin') && method === 'POST') {
      const payload = JSON.parse(request.postData() || '{}');
      if (payload.pin === '4708') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            ok: true,
            token: 'test-token',
            person: { name: 'Houcem', role: 'Maintenance', code: 'Houcem/Maintenance' },
          }),
        });
        return;
      }
      if (payload.pin === '4685') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            ok: true,
            token: 'mgr-token',
            person: { name: 'Rosa', role: 'Management', code: 'Rosa/Management' },
          }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: false, error: 'Incorrect PIN, try again.' }),
      });
      return;
    }

    if (pathname.endsWith('/take-batch') && method === 'POST') {
      const payload = JSON.parse(request.postData() || '{}');
      const item = payload.items[0].item;
      state.history.push({
        timestamp: new Date().toISOString(),
        personRole: `${payload.person}/${payload.role}`,
        item,
        expectedReturn: payload.items[0].expectedReturn || 'None',
        returnedAt: 'Not returned',
        returnedBy: '',
        txId: 'tx-1',
      });
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true, txIds: ['tx-1'] }),
      });
      return;
    }

    if (pathname.endsWith('/return-batch') && method === 'POST') {
      const payload = JSON.parse(request.postData() || '{}');
      state.history = state.history.map((h) =>
        payload.txIds.includes(h.txId)
          ? { ...h, returnedAt: new Date().toISOString(), returnedBy: payload.returnedBy }
          : h
      );
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true, txIds: payload.txIds }),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ok: true }),
    });
  });
}

async function signInMaintenance(page) {
  await page.getByText('Maintenance').click();
  for (const d of ['4', '7', '0', '8']) {
    await page.locator(`.numpad-key[data-digit="${d}"]`).click();
  }
  await page.getByRole('button', { name: 'Sign In' }).click();
  await expect(page.getByText('Take Something')).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
  const fileUrl = `file://${path.join(__dirname, '..', '..', 'index.html')}`;
  await page.goto(fileUrl);
});

test('sign-in flow works', async ({ page }) => {
  await signInMaintenance(page);
});

test('take batch flow works', async ({ page }) => {
  await signInMaintenance(page);
  await page.getByText('Take Something').click();
  await page.getByRole('button', { name: 'Or choose manually' }).click();
  await page.getByText('Tools').first().click();
  await page.getByText('Keyboard').first().click();
  await page.getByRole('button', { name: 'Confirm' }).click();
  await page.getByRole('button', { name: /Confirm & Take/ }).click();
  await expect(page.getByText('item(s) recorded')).toBeVisible();
});

test('return batch flow works', async ({ page }) => {
  await signInMaintenance(page);
  await page.getByText('Take Something').click();
  await page.getByRole('button', { name: 'Or choose manually' }).click();
  await page.getByText('Tools').first().click();
  await page.getByText('Keyboard').first().click();
  await page.getByRole('button', { name: 'Confirm' }).click();
  await page.getByRole('button', { name: /Confirm & Take/ }).click();
  await page.getByRole('button', { name: 'Done' }).click();
  await signInMaintenance(page);
  await page.getByText('Return a Tool').click();
  await page.getByText('Keyboard').first().click();
  await page.getByRole('button', { name: 'Return without QR code' }).click();
  await page.getByRole('button', { name: /Review & Confirm/ }).click();
  await page.getByRole('button', { name: /Confirm & Return/ }).click();
  await expect(page.getByText('item(s) returned')).toBeVisible();
});

test('report filters render for managers', async ({ page }) => {
  await page.getByText('Management').click();
  for (const d of ['4', '6', '8', '5']) {
    await page.locator(`.numpad-key[data-digit="${d}"]`).click();
  }
  await page.getByRole('button', { name: 'Sign In' }).click();
  await page.getByRole('button', { name: 'Manager Report' }).click();
  await expect(page.getByRole('button', { name: /Open/ })).toBeVisible();
  await page.getByRole('button', { name: 'Inventory Status' }).click();
  await expect(page.getByText('Station Parts')).toBeVisible();
});
