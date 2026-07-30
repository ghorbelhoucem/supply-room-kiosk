const { defineConfig } = require('@playwright/test');
const path = require('path');

module.exports = defineConfig({
  testDir: path.join(__dirname, 'tests/smoke'),
  timeout: 30000,
  use: {
    headless: true,
  },
});
