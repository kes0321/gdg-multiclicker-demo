# Clicker frontend

React + TypeScript + Vite, connected to the backend HTTP and WebSocket APIs.

```sh
npm install
npm run dev
```

Build verification: `npm run build`.
API adapter and batching tests: `node --test tests/*.test.mjs`.

- `src/theme.ts`: target image, colors, and sample player name; independent of gameplay.
- `public/bread.svg`: target illustration.
- `src/game-service.ts`: cookie session creation/restoration, WebSocket clicks and upgrades, player state and leaderboard requests.
- `src/main.tsx`: fullscreen game, top-25 leaderboard, gold, target, upgrade button, and measured game-request round-trip time.
- `src/styles.css`: viewport-sized layout without page or panel scrolling. Top three players have gold, silver, and bronze treatments.

Start the backend and Redis, then run `npm run dev`. Vite forwards `/api` and `/ws` to `http://localhost:8000`. To use another backend address, set `BACKEND_URL` in `.env.local` before starting Vite. Production hosting must forward `/api` and `/ws` (including WebSocket upgrades) to the backend on the same origin; the Vite development proxy is not included in the production build.

Enter a nickname (1–20 characters) to create or restore a player through `POST /api/players`. The browser manages the HttpOnly session cookie. An existing session restores its original nickname and progress.

Click anywhere outside the controls to collect clicks; the target also supports keyboard activation. The first click starts a 500 ms timer. At ten clicks, the frontend immediately queues a WebSocket `click_batch` and cancels that timer; the next click starts a new batch and timer. If the timer expires first, the collected 1–9 clicks are sent. No clicks are discarded because of click speed, and empty batches send nothing. Speed detection and rate limits belong to the backend. Game operations are queued in order. Upgrades wait for collected clicks to flush, then send `levelup` using the server's `level × 100` cost. Click input pauses while an upgrade is pending. Scores and levels come from server responses. Every five seconds, the frontend retrieves the current player and `GET /api/leaderboard?limit=25`, using the returned `nickname` and `rank` directly. The leaderboard only contains players returned by the server; your row is highlighted by player ID when present.

Failed requests show an error and cancel queued inputs. Polling or the retry button restores the session and synchronizes state. Requests without a response are not replayed, since the server may already have processed them. The latency display measures the latest click/upgrade response time and shows `—` until measured.
