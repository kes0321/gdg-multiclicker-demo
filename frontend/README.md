# Clicker frontend

React + TypeScript + Vite. No backend calls are made.

```sh
npm install
npm run dev
```

Build verification: `npm run build`.

- `src/theme.ts`: target image, colors, and sample player name; independent of gameplay.
- `public/bread.svg`: target illustration.
- `src/game-service.ts`: typed adapter with local demo data, 24 other players plus you. Replace its methods with server calls.
- `src/main.tsx`: fullscreen game, 25-player leaderboard, gold, target, upgrade button, and sample ping.
- `src/styles.css`: viewport-sized layout without page or panel scrolling. Top three players have gold, silver, and bronze treatments.

Click anywhere outside the upgrade control to gain gold. The target also supports keyboard activation. Game operations are queued in order. The upgrade button displays level, cost, and the change in gold per click. Competitor scores and ping are sample values. Progress resets on refresh.
