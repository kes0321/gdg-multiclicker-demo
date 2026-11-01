import { useEffect, useRef, useState } from 'react';
import { Crown, RefreshCw, X } from 'lucide-react';
import { getGlobalLeaderboard, type Player } from './game-service';

export function GlobalLeaderboard({ playerId, onClose }: { playerId: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [players, setPlayers] = useState<Player[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => element.close();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    getGlobalLeaderboard(controller.signal).then(result => {
      if (!controller.signal.aborted) setPlayers(result);
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '랭킹 조회 실패');
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [revision]);

  return <dialog ref={dialog} className="global-ranking" aria-labelledby="global-ranking-title"
    onCancel={event => { event.preventDefault(); onClose(); }}
    onClick={event => {
      event.stopPropagation();
      if (event.target === event.currentTarget) {
        const bounds = event.currentTarget.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose();
      }
    }}>
    <div className="global-ranking-content">
      <header className="global-ranking-header">
        <h2 id="global-ranking-title">전체 랭킹</h2>
        <button className="ranking-icon-button" onClick={() => setRevision(value => value+1)} disabled={loading} aria-label="랭킹 새로고침"><RefreshCw size={16}/></button>
        <button className="ranking-icon-button" onClick={onClose} aria-label="닫기" autoFocus><X size={19}/></button>
      </header>
      <div className="table-head"><span>순위</span><span>닉네임</span><span>골드</span></div>
      {loading ? <div className="ranking-status" role="status">불러오는 중…</div>
        : error ? <div className="ranking-status" role="alert"><span>{error}</span><button onClick={() => setRevision(value => value+1)}>다시 시도</button></div>
        : players.length === 0 ? <div className="ranking-status" role="status">등록된 플레이어가 없습니다.</div>
        : <ol className="rank-list">
          {players.map(player => <li key={player.id} className={`rank-row ${player.rank <= 3 ? `podium podium-${player.rank}` : ''} ${player.id === playerId ? 'is-me' : ''}`} aria-current={player.id === playerId ? 'true' : undefined}>
            <span className="rank-number">{player.rank === 1 && <Crown className="first-crown" aria-hidden="true"/>}{player.rank}</span>
            <span className="player-identity"><span className="player-name" title={player.name}>{player.name}</span>{player.id === playerId && <span className="me-badge">나</span>}</span>
            <span className="player-gold">{player.gold.toLocaleString('ko-KR')}<small> G</small></span>
          </li>)}
        </ol>}
    </div>
  </dialog>;
}
