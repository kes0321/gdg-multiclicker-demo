import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Crown, Signal } from 'lucide-react';
import { theme } from './theme';
import { createApiGameService, type GameService, type GameState } from './game-service';
import { createClickBatcher } from './click-batcher';
import { createClickPrediction } from './click-prediction';
import { GlobalLeaderboard } from './global-leaderboard';
import { ClickerMark } from './clicker-mark';
import './styles.css';

const number = (value: number) => value.toLocaleString('ko-KR');

function App() {
  const [nickname, setNickname] = useState('');
  const [playerName, setPlayerName] = useState<string>();

  if (playerName) return <Game playerName={playerName}/>;

  return <main className="entry-screen" style={{ '--accent': theme.colors.accent, '--background': theme.colors.background } as React.CSSProperties}>
    <form className="entry-form" onSubmit={event => {
      event.preventDefault();
      const name = nickname.trim();
      if (name) setPlayerName(name);
    }}>
      <div className="entry-title">
        <h1 aria-label="GDGoC Clicker"><ClickerMark/></h1>
        <span>김은성</span>
      </div>
      <input className="nickname-input" aria-label="닉네임" placeholder="닉네임" value={nickname} onChange={event => setNickname(event.target.value)} maxLength={20} autoComplete="nickname" autoFocus required/>
      <button className="start-button" type="submit" disabled={!nickname.trim()}>게임 시작</button>
    </form>
  </main>;
}

function Game({ playerName }: { playerName: string }) {
  const service = useRef<GameService | null>(null);
  const [game, setGame] = useState<GameState>();
  const [onlineCount, setOnlineCount] = useState<number | null>(null);
  const [bounce, setBounce] = useState(0);
  const [busy, setBusy] = useState(false);
  const [rankingOpen, setRankingOpen] = useState(false);
  const [error, setError] = useState('');
  const [particles, setParticles] = useState<{ id: number; x: number; y: number; value: number }[]>([]);
  const nextParticleId = useRef(0);
  const particleTimers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const queue = useRef(Promise.resolve());
  const queueVersion = useRef(0);
  const batcher = useRef<ReturnType<typeof createClickBatcher> | null>(null);
  const prediction = useRef(createClickPrediction());

  useEffect(() => () => {
    particleTimers.current.forEach(clearTimeout);
    particleTimers.current.clear();
  }, []);

  useEffect(() => {
    const api = createApiGameService(playerName);
    setOnlineCount(null);
    service.current = api;
    prediction.current = createClickPrediction();
    queue.current = Promise.resolve();
    const unsubscribe = api.subscribe(
      (players, count) => {
        setOnlineCount(count);
        setGame(prediction.current.updateLeaderboard(players));
      },
      handleFailure,
    );
    void enqueue(() => api.getState());
    const clickBatcher = createClickBatcher(count => { void enqueue(() => api.click(count), count); });
    batcher.current = clickBatcher;
    return () => {
      unsubscribe();
      clickBatcher.dispose();
      batcher.current = null;
      service.current = null;
      queueVersion.current++;
      api.dispose();
    };
  }, [playerName]);

  function handleFailure(reason: unknown) {
    setOnlineCount(null);
    queueVersion.current++;
    batcher.current?.clear();
    setGame(prediction.current.discard());
    setError(reason instanceof Error ? reason.message : '서버 요청에 실패했습니다.');
  }

  function enqueue(action: () => Promise<GameState>, completedClicks = 0) {
    const version = queueVersion.current;
    queue.current = queue.current.then(async () => {
      if (version !== queueVersion.current) return;
      const state = await action();
      if (version !== queueVersion.current) return;
      setGame(prediction.current.reconcile(state, completedClicks));
      setError('');
    }).catch((reason: unknown) => {
      if (version !== queueVersion.current) return;
      handleFailure(reason);
    });
    return queue.current;
  }

  function reward(x: number, y: number) {
    if (!game || error || busy || rankingOpen) return;
    const id = nextParticleId.current++;
    setParticles(current => [...current.slice(-29), { id, x, y, value: game.goldPerClick }]);
    const timer = setTimeout(() => {
      setParticles(current => current.filter(particle => particle.id !== id));
      particleTimers.current.delete(timer);
    }, 800);
    particleTimers.current.add(timer);
    setBounce(value => value + 1);
    setGame(prediction.current.add());
    batcher.current?.add();
  }

  function click(event: React.MouseEvent<HTMLElement>) {
    if ((event.target as HTMLElement).closest('button')) return;
    reward(event.clientX, event.clientY);
  }

  async function upgrade() {
    if (!game || busy || game.upgradeCost === null || game.gold < game.upgradeCost) return;
    setBusy(true);
    try { await batcher.current?.afterFlush(() => enqueue(() => service.current!.upgrade())); }
    finally { setBusy(false); }
  }

  function retry() { void enqueue(() => service.current!.getState()); }

  if (!game) return <main className="entry-screen"><div className="entry-form" role="status">
    <p>{error || '서버에 연결하는 중입니다…'}</p>
    {error && <button className="start-button" onClick={retry}>다시 연결</button>}
  </div></main>;

  return <main className="app" onClick={click} style={{ '--accent': theme.colors.accent, '--background': theme.colors.background } as React.CSSProperties}>
    <div className={`ping ${error ? 'disconnected' : ''}`} aria-label={error ? '연결 오류' : '서버 응답 시간'} title={error || '최근 게임 요청의 왕복 시간'}>
      <Signal size={13}/><span>{error || game.ping === null ? '—' : game.ping} ms</span>
    </div>
    {error && <div className="connection-error" role="alert">{error} <button onClick={retry}>다시 연결</button></div>}

    <section className="leaderboard" aria-label="리더보드">
      <div className="leaderboard-toolbar">
        <span className="online-count" aria-label="현재 접속자 수">접속 <strong>{onlineCount === null ? '—' : number(onlineCount)}</strong>명</span>
        <button className="global-ranking-trigger" onClick={() => setRankingOpen(true)} aria-haspopup="dialog">전체 랭킹</button>
      </div>
      <div className="table-head"><span>순위</span><span>닉네임</span><span>골드</span></div>
      <ol className="rank-list">
        {game.leaderboard.map(player => <li className={`rank-row ${player.rank <= 3 ? `podium podium-${player.rank}` : ''} ${player.id === game.playerId ? 'is-me' : ''}`} key={player.id} aria-current={player.id === game.playerId ? 'true' : undefined}>
          <span className="rank-number">{player.rank === 1 && <Crown className="first-crown" aria-hidden="true"/>}{player.rank}</span>
          <span className="player-identity">
            <span className="player-name" title={player.name}>{player.name}</span>
            {player.id === game.playerId && <span className="me-badge">나</span>}
          </span>
          <span className="player-gold">{number(player.id === game.playerId ? game.gold : player.gold)}<small> G</small></span>
        </li>)}
      </ol>
    </section>

    <section className="play-area" aria-label="클릭 게임">
      <div className="gold-block">
        <h1 aria-label="현재 골드">{number(game.gold)}<span> G</span></h1>
        <div className="per-click" aria-label="클릭당 골드">{number(game.goldPerClick)} G / 클릭</div>
      </div>
      <button className="target-button" onClick={event => {
        const rect = event.currentTarget.getBoundingClientRect();
        reward(event.detail === 0 ? rect.x + rect.width / 2 : event.clientX, event.detail === 0 ? rect.y + rect.height / 2 : event.clientY);
      }} disabled={!!error} aria-label={`${theme.targetLabel}, ${game.goldPerClick} 골드 획득`}>
        <ClickerMark key={bounce} animated={bounce > 0}/>
      </button>
    </section>

    <button className="upgrade-button" disabled={!!error || busy || game.upgradeCost === null || game.gold < game.upgradeCost} onClick={upgrade} aria-label="업그레이드" aria-busy={busy}>
      <span className="upgrade-top"><span>Lv. {game.level}</span><strong>{game.upgradeCost === null ? 'MAX' : `${number(game.upgradeCost)} G`}</strong></span>
      <span className="upgrade-change upgrade-preview">{game.nextGoldPerClick === null ? '최대 레벨 도달' : <><span>레벨업</span><small>({number(game.goldPerClick)}G/클릭 → {number(game.nextGoldPerClick)}G/클릭)</small></>}</span>
    </button>
    {particles.map(particle => <span className="click-particle" key={particle.id} aria-hidden="true" style={{ left: particle.x, top: particle.y }}>+{number(particle.value)}<small> G</small></span>)}
    {rankingOpen && <GlobalLeaderboard playerId={game.playerId} onClose={() => setRankingOpen(false)}/>}
  </main>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
