# Frontend — Chirala Bay PMS

React + TypeScript + Vite + Tailwind app shell.

## Run

```bash
npm install
npm run dev     # http://localhost:5173  (proxies /api -> gateway :8000)
```

## Structure

- `src/components/` — `Sidebar`, `TopBar`, `AppLayout` (shell matching the mockups)
- `src/pages/` — `Dashboard` (mockup 001) + placeholders for the other sidebar sections
- `src/nav.ts` — sidebar navigation config
- `src/api.ts` — axios client (routes through the gateway, attaches bearer token)

The design tokens (teal palette) live in `tailwind.config.js`. Screens are built
incrementally from `../Chirala_Bay_Resort_PMS_120_Screens/`.
