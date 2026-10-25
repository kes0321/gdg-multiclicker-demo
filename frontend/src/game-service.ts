export interface Player { id: string; name: string; gold: number; avatar: string }
export interface GameState { gold: number; goldPerClick: number; upgradeCost: number; level: number; leaderboard: Player[]; ping: number }
export interface GameService { getState(): Promise<GameState>; click(): Promise<GameState>; upgrade(): Promise<GameState> }
// Replace this adapter with API calls. UI and theme are independent of the transport.
export function createMockGameService(playerName: string): GameService {
  const state = { gold: 1240, goldPerClick: 1, upgradeCost: 100, level: 1 };
  const names = ['버터의신','빵굽는고양이','소금빵러버','크루아상장인','오늘도빵긋','식빵이좋아','밀가루요정','바삭한하루','앙버터','새벽의베이커','호밀밭의파수꾼','달콤한반죽','몽블랑','치아바타','모닝커피','겉바속촉','빵지순례','작은오븐','슈크림','브리오슈','통밀주의자','단팥친구','베이글링','마지막한입'];
  const scores = [28450,24180,21930,18400,15260,12800,10420,8760,6530,4810,3620,2480,1860,1520,1320,1120,980,840,720,560,420,310,180,90];
  const entries = names.map((name,i) => ({ id: `sample-${i}`, name, gold: scores[i], avatar: ['🍞','🐱','🥨','🥐','🌻','🍯','🌿','☕'][i % 8] }));
  const snapshot = (): GameState => ({ ...state, leaderboard: [...entries, { id: 'me', name: playerName, gold: state.gold, avatar: '👩🏻‍🍳' }].sort((a,b) => b.gold-a.gold), ping: 24 });
  return {
    async getState() { return snapshot(); },
    async click() { state.gold += state.goldPerClick; return snapshot(); },
    async upgrade() {
      if (state.gold >= state.upgradeCost) { state.gold -= state.upgradeCost; state.goldPerClick++; state.level++; state.upgradeCost = Math.ceil(state.upgradeCost * 1.65); }
      return snapshot();
    },
  };
}
