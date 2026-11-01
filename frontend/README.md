# Clicker frontend

React + TypeScript + Vite, connected to the backend HTTP and WebSocket APIs.

```sh
npm ci
npm run dev
```

Build verification: `npm run build`.

For the three-container Docker deployment (Nginx + backend + Redis), see the
[root deployment README](../README.md). Run `docker compose up -d --build` from
the repository root; the frontend image includes the Vite `dist/` output.

The online leaderboard's **전체 랭킹** button opens an independent HTTP snapshot of the top 25 players across all users (`GET /api/leaderboard?limit=25`), including offline players. Each opening and manual refresh fetches new data. The popup supports Escape, outside click, close, loading, empty, and retry states; closing cancels its request. It does not change the online ranking or local gold predictions.
API adapter and batching tests: `node --test tests/*.test.mjs`.

- `src/theme.ts`: GDGoC Clicker wordmark text and theme colors; independent of gameplay.
- `src/clicker-mark.tsx`: shared typographic click target and entry title, with four-color SVG brackets.
- `src/game-service.ts`: cookie session creation/restoration, WebSocket clicks and upgrades, player state and WebSocket leaderboard broadcasts.
- `src/main.tsx`: fullscreen game, top-25 leaderboard, gold, target, upgrade button, and measured game-request round-trip time.
- `src/styles.css`: viewport-sized layout without page or panel scrolling. Top three players have gold, silver, and bronze treatments.

Visual reference: https://gdgoc.yonsei.ac.kr/ko — charcoal stage, bold typography, four-color brackets, and restrained rounded controls. The game target is a code-native GDGoC Clicker wordmark rather than an image.

Start the backend and Redis, then run `npm run dev`. Vite forwards `/api` and `/ws` to `http://localhost:8000`. To use another backend address, set `BACKEND_URL` in `.env.local` before starting Vite. Production hosting must forward `/api` and `/ws` (including WebSocket upgrades) to the backend on the same origin; the Vite development proxy is not included in the production build.

Enter a nickname (1–20 characters) to create or restore a player through `POST /api/players`. The browser manages the HttpOnly session cookie. An existing session restores its original nickname and progress.

Click anywhere outside the controls to collect clicks; the target also supports keyboard activation. The first click starts a 500 ms timer. At ten clicks, the frontend immediately queues a WebSocket `click_batch` and cancels that timer; the next click starts a new batch and timer. If the timer expires first, the collected 1–9 clicks are sent. No clicks are discarded because of click speed, and empty batches send nothing. Speed detection and rate limits belong to the backend. Game operations are queued in order. Upgrades wait for collected clicks to flush, then send `levelup` using the server's `level × 100` cost. Click input pauses while an upgrade is pending. Scores and levels come from server responses. The frontend receives `leaderboard` WebSocket broadcasts (`players` with `player_id`, `nickname`, `score`, and `rank`) when online membership or scores change. It displays the first 25 online players in server order, highlighting your row by player ID. Initial state comes from `connected`; neither player state nor the leaderboard is polled over HTTP.

Gold updates immediately for each local click using the current gold per click. The client tracks all clicks awaiting acknowledgement, including clicks still buffered or queued for transmission. Each successful batch response replaces the confirmed balance and removes only that batch's predictions, preserving newer clicks. Predicted gold can enable an upgrade; its request still waits behind the relevant batches, and the server determines whether it succeeds. Your leaderboard row displays the same predicted gold as the main balance. Leaderboard ranks, row order, and other players' scores remain server values.

Failed requests show an error, discard outstanding predictions, and cancel queued inputs. The retry button restores the session and reconnects the WebSocket to receive fresh player state and leaderboard broadcasts. Requests without a response are not replayed, since the server may already have processed them. The latency display measures the latest click/upgrade response time and shows `—` until measured.
