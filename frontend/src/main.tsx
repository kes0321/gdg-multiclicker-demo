import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Crown, Signal } from 'lucide-react';
import { theme } from './theme';
import { createMockGameService, type GameState } from './game-service';
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
        <h1>GDGoC 클리커</h1>
        <span>김은성</span>
      </div>
      <input className="nickname-input" aria-label="닉네임" placeholder="닉네임" value={nickname} onChange={event => setNickname(event.target.value)} maxLength={20} autoComplete="nickname" autoFocus required/>
      <button className="start-button" type="submit" disabled={!nickname.trim()}>게임 시작</button>
    </form>
  </main>;
}

function Game({ playerName }: { playerName: string }) {
  const [service] = useState(() => createMockGameService(playerName));
  const [game, setGame] = useState<GameState>();
  const [bounce, setBounce] = useState(0);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [particles, setParticles] = useState<{ id: number; x: number; y: number; value: number }[]>([]);
  const nextParticleId = useRef(0);
  const particleTimers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const queue = useRef(Promise.resolve());

  useEffect(() => () => {
    particleTimers.current.forEach(clearTimeout);
    particleTimers.current.clear();
  }, []);

  useEffect(() => { service.getState().then(setGame).catch(() => setFailed(true)); }, [service]);

  function enqueue(action: () => Promise<GameState>) {
    queue.current = queue.current.then(async () => {
      setGame(await action());
      setFailed(false);
    }).catch(() => setFailed(true));
    return queue.current;
  }

  function reward(x: number, y: number) {
    if (!game) return;
    const id = nextParticleId.current++;
    setParticles(current => [...current.slice(-29), { id, x, y, value: game.goldPerClick }]);
    const timer = setTimeout(() => {
      setParticles(current => current.filter(particle => particle.id !== id));
      particleTimers.current.delete(timer);
    }, 800);
    particleTimers.current.add(timer);
    setBounce(value => value + 1);
    void enqueue(() => service.click());
  }

  function click(event: React.MouseEvent<HTMLElement>) {
    if ((event.target as HTMLElement).closest('button')) return;
    reward(event.clientX, event.clientY);
  }

  async function upgrade() {
    if (!game || busy || game.gold < game.upgradeCost) return;
    setBusy(true);
    try { await enqueue(() => service.upgrade()); }
    finally { setBusy(false); }
  }

  if (!game) return <div className="app" aria-label={failed ? '연결 실패' : '불러오는 중'} />;

  return <main className="app" onClick={click} style={{ '--accent': theme.colors.accent, '--background': theme.colors.background } as React.CSSProperties}>
    <div className={`ping ${failed ? 'disconnected' : ''}`} aria-label={failed ? '연결 오류' : '샘플 핑'} title="서버 연동 전 샘플 값">
      <Signal size={13}/><span>{failed ? '—' : game.ping} ms</span>
    </div>

    <section className="leaderboard" aria-label="리더보드">
      <div className="table-head"><span>순위</span><span>닉네임</span><span>골드</span></div>
      <ol className="rank-list">
        {game.leaderboard.map((player, index) => <li className={`rank-row ${index < 3 ? `podium podium-${index+1}` : ''} ${player.id === 'me' ? 'is-me' : ''}`} key={player.id} aria-current={player.id === 'me' ? 'true' : undefined}>
          <span className="rank-number">{index === 0 && <Crown className="first-crown" aria-hidden="true"/>}{index+1}</span>
          <span className="player-name" title={player.name}>{player.name}</span>
          <span className="player-gold">{number(player.gold)}<small> G</small></span>
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
      }} aria-label={`${theme.targetLabel}, ${game.goldPerClick} 골드 획득`}>
        <img key={bounce} className={`target-image ${bounce ? 'bounce' : ''}`} src={theme.targetImage} alt={theme.targetAlt} draggable={false}/>
      </button>
    </section>

    <button className="upgrade-button" disabled={busy || game.gold < game.upgradeCost} onClick={upgrade} aria-label="업그레이드" aria-busy={busy}>
      <span className="upgrade-top"><span>Lv. {game.level}</span><strong>{number(game.upgradeCost)} G</strong></span>
      <span className="upgrade-change"><span>{game.goldPerClick}</span><span className="upgrade-arrow">→</span><span>{game.goldPerClick+1}<small> G / 클릭</small></span></span>
    </button>
    {particles.map(particle => <span className="click-particle" key={particle.id} aria-hidden="true" style={{ left: particle.x, top: particle.y }}>+{number(particle.value)}<small> G</small></span>)}
  </main>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
