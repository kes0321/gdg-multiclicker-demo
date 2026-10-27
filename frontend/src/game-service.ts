export interface Player { id: string; name: string; gold: number; rank: number }
export interface GameState { playerId: string; gold: number; goldPerClick: number; upgradeCost: number; level: number; leaderboard: Player[]; ping: number | null }
export interface GameService { getState(): Promise<GameState>; click(count: number): Promise<GameState>; upgrade(): Promise<GameState>; dispose(): void }

interface ApiPlayer { player_id: string; nickname: string; score: number; level: number; total_clicks: number }
interface Ranking { rank: number; player_id: string; nickname: string; score: number }
interface ServerMessage {
  type: string; player?: ApiPlayer; score?: number; level?: number; total_clicks?: number;
  success?: boolean; code?: string; message?: string;
}

export function createApiGameService(nickname: string): GameService {
  let player: ApiPlayer | undefined;
  let leaderboard: Player[] = [];
  let ping: number | null = null;
  let socket: WebSocket | undefined;
  let connecting: Promise<void> | undefined;
  let disposed = false;
  const controller = new AbortController();
  let pending: { type: string; started: number; resolve: () => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> } | undefined;
  let cancelConnection: ((error: Error) => void) | undefined;

  async function request<T>(path: string, options?: RequestInit): Promise<T> {
    const response = await fetch(path, { ...options, credentials: 'include',
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]) });
    const body = await response.json();
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `요청 실패 (${response.status})`);
    return body as T;
  }

  function snapshot(): GameState {
    if (!player) throw new Error('플레이어를 불러오는 중입니다.');
    return { playerId: player.player_id, gold: player.score, goldPerClick: player.level,
      upgradeCost: player.level * 100, level: player.level, leaderboard: [...leaderboard], ping };
  }

  function rejectPending(error: Error) {
    if (!pending) return;
    const operation = pending;
    pending = undefined;
    clearTimeout(operation.timer);
    operation.reject(error);
  }

  async function connect(): Promise<void> {
    if (disposed) throw new Error('연결이 종료되었습니다.');
    if (connecting) return connecting;
    if (socket?.readyState === WebSocket.OPEN) return;
    connecting = (async () => {
      const result = await request<{ player: ApiPlayer }>('/api/players', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ nickname }),
      });
      if (disposed) throw new Error('연결이 종료되었습니다.');
      player = result.player;
      const url = new URL('/ws', window.location.href);
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
      await new Promise<void>((resolve, reject) => {
        const ws = new WebSocket(url);
        socket = ws;
        let ready = false;
        const fail = (error: Error) => {
          if (socket !== ws) return;
          clearTimeout(timer);
          cancelConnection = undefined;
          if (!ready) reject(error);
          rejectPending(error);
          if (socket === ws) socket = undefined;
          ws.onclose = null;
          ws.onerror = null;
          ws.close();
        };
        const timer = setTimeout(() => fail(new Error('서버 연결 시간이 초과되었습니다.')), 10000);
        cancelConnection = fail;
        ws.onmessage = event => {
          if (socket !== ws || disposed) return;
          let message: ServerMessage;
          try { message = JSON.parse(event.data); }
          catch { fail(new Error('서버 응답을 읽을 수 없습니다.')); return; }
          if (message.type === 'connected' && message.player) {
            player = message.player;
            ready = true;
            clearTimeout(timer);
            cancelConnection = undefined;
            resolve();
            return;
          }
          if (message.type === 'error') {
            rejectPending(new Error(message.message ?? message.code ?? '서버 요청 오류'));
            return;
          }
          if (!pending || message.type !== pending.type || !player) return;
          if (message.score !== undefined) player.score = message.score;
          if (message.level !== undefined) player.level = message.level;
          if (message.total_clicks !== undefined) player.total_clicks = message.total_clicks;
          ping = Math.round(performance.now() - pending.started);
          const operation = pending;
          pending = undefined;
          clearTimeout(operation.timer);
          if (message.success === false) operation.reject(new Error(message.code === 'INSUFFICIENT_GOLD' ? '골드가 부족합니다.' : message.message ?? message.code ?? '요청 실패'));
          else operation.resolve();
        };
        ws.onerror = () => fail(new Error('서버에 연결할 수 없습니다.'));
        ws.onclose = () => fail(new Error('서버 연결이 끊겼습니다. 다시 연결해 주세요.'));
      });
    })();
    try { await connecting; }
    finally { connecting = undefined; }
  }

  async function action(type: 'click_batch' | 'levelup', count?: number): Promise<GameState> {
    await connect();
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        rejectPending(new Error('응답 시간이 초과되었습니다. 상태를 다시 확인해 주세요.'));
        socket?.close();
        socket = undefined;
      }, 10000);
      pending = { type: `${type}_result`, started: performance.now(), resolve, reject, timer };
      try { socket!.send(JSON.stringify({ type, ...(count === undefined ? {} : { count }) })); }
      catch { rejectPending(new Error('요청을 전송할 수 없습니다.')); }
    });
    return snapshot();
  }

  return {
    async getState() {
      await connect();
      const [current, ranking] = await Promise.all([
        request<ApiPlayer>(`/api/players/${encodeURIComponent(player!.player_id)}`),
        request<{ players: Ranking[] }>('/api/leaderboard?limit=25'),
      ]);
      player = current;
      leaderboard = ranking.players.map(entry => ({ id: entry.player_id, name: entry.nickname,
        gold: entry.score, rank: entry.rank }));
      return snapshot();
    },
    click(count) {
      if (!Number.isInteger(count) || count < 1 || count > 10) return Promise.reject(new Error('클릭 배치는 1~10회의 정수여야 합니다.'));
      return action('click_batch', count);
    },
    upgrade: () => action('levelup'),
    dispose() {
      disposed = true;
      controller.abort();
      cancelConnection?.(new Error('연결이 종료되었습니다.'));
      rejectPending(new Error('연결이 종료되었습니다.'));
      socket?.close();
      socket = undefined;
    },
  };
}
