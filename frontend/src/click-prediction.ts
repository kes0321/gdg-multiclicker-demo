import type { GameState, Player } from './game-service';

// Keep unacknowledged clicks separate from the server's authoritative balance.
export function createClickPrediction() {
  let confirmed: GameState | undefined;
  let pendingClicks = 0;
  let leaderboard: Player[] | undefined;
  function snapshot(): GameState | undefined {
    if (!confirmed) return undefined;
    return { ...confirmed, leaderboard: leaderboard ?? confirmed.leaderboard,
      gold: confirmed.gold + pendingClicks * confirmed.goldPerClick };
  }
  return {
    add() {
      if (!confirmed) return undefined;
      pendingClicks++;
      return snapshot();
    },
    reconcile(state: GameState, completedClicks = 0) {
      if (confirmed && confirmed.playerId !== state.playerId) pendingClicks = 0;
      else pendingClicks = Math.max(0, pendingClicks - completedClicks);
      confirmed = state;
      return snapshot();
    },
    updateLeaderboard(players: Player[]) {
      leaderboard = players;
      return snapshot();
    },
    discard() {
      pendingClicks = 0;
      return snapshot();
    },
  };
}
