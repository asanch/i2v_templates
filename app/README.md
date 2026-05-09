# i2v_templates — web app

Next.js front-end for the i2v_templates pipeline. Lives alongside the Python backend in this same repo. The app/ directory is a self-contained Next.js project; the Python bits in the parent directory are not visible to it.

This is intentionally bare-bones — one hello-world page and an `/api/health` route — so we can iterate on UX and build the real surface once the backend wrappers are in place.

---

## Setup

```bash
cd app
pnpm install     # or npm install / yarn install
cp .env.local.example .env.local   # if you want NEXT_PUBLIC_BACKEND_URL set
pnpm dev
```

Open http://localhost:3000 — you should see the home page and a "Next.js is alive at <timestamp>" line confirming the client+server are both working.

---

## Layout

```
app/
├── package.json
├── next.config.ts
├── tsconfig.json
├── postcss.config.mjs
├── .env.local.example
├── app/                     # Next.js App Router
│   ├── layout.tsx
│   ├── page.tsx             # home (hello world + health check)
│   ├── globals.css          # Tailwind 4 import
│   ├── _components/
│   │   └── HealthCheck.tsx  # client component pinging /api/health
│   └── api/
│       └── health/
│           └── route.ts     # GET → { ok: true, ts }
└── README.md
```

The leading underscore on `_components/` makes it a private folder in the App Router (not routable). Real pages will be added at `app/<route>/page.tsx` once UX is decided.

---

## What's deliberately not here yet

- No component library (shadcn, Radix, etc.) — wait for UX decisions
- No state management — wait
- No backend client abstraction — wait until FastAPI exists
- No authentication — never needed for hackathon
- No testing setup — wait

When we want any of these, add them deliberately. Don't reach for them out of habit.

---

## Calling the Python backend (future)

When the FastAPI wrapper exists at `:8000`, the pattern will be:

```ts
// In a server component or API route
const url = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";
const res = await fetch(`${url}/run-slot`, { method: "POST", body: ... });
```

For browser-only fetches, the `NEXT_PUBLIC_` prefix exposes the env var to the client. For server-side fetches, drop the prefix and use a different env var so the URL stays out of the browser bundle.
