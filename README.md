# Supply Room — Check-Out Station

A touchscreen kiosk for tracking tool and station-part checkouts in a robotics / operations environment. Staff tap their role and name, enter a group PIN, then check items in or out. All data lives in a Google Sheet via a Google Apps Script web app — no separate server needed.

![CI](https://github.com/ghorbelhoucem/supply-room-kiosk/actions/workflows/ci.yml/badge.svg)

---

## Features

- **Role-based sign-in** — Maintenance, Management, Supervisor, Tele-operator
- **On-screen numpad** for PIN and operator-ID entry (no physical keyboard required)
- **Barcode / QR scanner support** — plug in a USB HID scanner and it auto-detects fast keystrokes
- **Take flow** — browse or search tools and station parts, build a basket, confirm in one tap
- **Return flow** — pick items from the open-checkout list, optional QR-code scan to verify
- **Manager report** — open/overdue checkouts, full history, and live inventory counts
- **Offline-tolerant** — shows a connection banner when the backend is unreachable
- **Responsive layout** — fluid grid adapts from small tablets to large kiosk monitors

---

## Stack

| Layer | Technology |
|---|---|
| UI | Vanilla HTML/CSS/JS — single `index.html`, no framework |
| JS modules | `src/` — domain logic, state machine, API client, scanner, renderer |
| Backend | Google Apps Script Web App |
| Data store | Google Sheets |
| Tests | Playwright (smoke) |
| CI | GitHub Actions |

---

## Quick start (local)

```bash
git clone https://github.com/ghorbelhoucem/supply-room-kiosk.git
cd supply-room-kiosk

# Serve with any static file server — no build step needed
python3 -m http.server 8080
# or: npx serve .
```

Open `http://localhost:8080` in a browser. The app shows a connection banner until a real backend URL is configured (see below).

---

## Configuration

All config lives in the `<script>` block at the bottom of `index.html`.

### 1. Backend URL

```js
const WEBAPP_URL = 'https://script.google.com/macros/s/YOUR_SCRIPT_ID/exec';
```

Deploy your Google Apps Script as a web app and paste the URL here. The script must handle `GET` (return `{ inventory, history }`) and `POST` (handle `take`, `takeBatch`, `returnBatch` actions).

### 2. Roles and names

```js
const ROLES = {
  maintenance: { label: 'Maintenance', icon: '🔧', kind: 'names', names: ['Alice', 'Bob'] },
  management:  { label: 'Management',  icon: '📋', kind: 'names', names: ['Carol'] },
  supervisor:  { label: 'Supervisor',  icon: '🧭', kind: 'operatorId' },
  teleoperator:{ label: 'Tele-operator', icon: '🎮', kind: 'operatorId' }
};
```

`kind: 'names'` — user picks their name then enters a group PIN.  
`kind: 'operatorId'` — user types their numeric operator ID on the numpad.

### 3. Group PINs

```js
const GROUP_PINS = {
  groupA:     '1234',
  management: '5678'
};

const NAME_TO_GROUP = {
  'Alice': 'groupA',
  'Bob':   'groupA',
  'Carol': 'management'
};
```

Everyone in the same group shares one 4-digit PIN. Change these codes to whatever you want.

---

## Deployment

The app is a static site — deploy anywhere that serves files.

**GitHub Pages**

1. Go to *Settings → Pages* in your repo.
2. Set source to *Deploy from a branch*, branch `main`, folder `/`.
3. The kiosk is live at `https://<your-org>.github.io/supply-room-kiosk/`.

**Local network kiosk**

```bash
python3 -m http.server 80
```

Point the kiosk browser to `http://localhost` in full-screen / kiosk mode.

---

## Item images

Place product photos in the `images/` folder and register them in the `PHOTO_MAP` object inside `index.html`:

```js
const PHOTO_MAP = {
  'Ethernet Cable 1M': 'images/ethernet_cable.jpg',
  'HDMI Cable':        'images/hdmi_cable.jpg',
  // ...
};
```

Items without a matching entry fall back to an emoji icon automatically.

---

## Development

```bash
npm install               # installs Playwright + linting tools

npm run lint              # ESLint
npm run format            # Prettier
npm test                  # Playwright smoke tests (headless Chromium)
```

Smoke tests live in `tests/smoke/kiosk.spec.js` and cover sign-in, take, return, and the manager report.

---

## Folder structure

```
supply-room-kiosk/
├── index.html                      # app shell, styles, and bootstrap logic
├── images/                         # product photos (jpg)
├── src/
│   ├── api/client.js               # fetch wrapper with retries + timeout
│   ├── domain/inventory.js         # pure business logic (availability, overdue)
│   ├── scanner/keyboardScanner.js  # HID barcode scanner detection
│   ├── state/machine.js            # allowed screen transitions
│   ├── state/store.js              # in-memory state store
│   └── ui/renderer.js              # DOM utility helpers
├── tests/smoke/kiosk.spec.js       # Playwright end-to-end smoke tests
├── .github/workflows/ci.yml        # GitHub Actions CI
├── package.json
└── playwright.config.js
```
