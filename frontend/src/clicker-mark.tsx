import { theme } from './theme';

export function ClickerMark({ animated = false }: { animated?: boolean }) {
  return <span className={`clicker-mark ${animated ? 'mark-bounce' : ''}`}>
    <svg className="mark-bracket" viewBox="0 0 56 90" aria-hidden="true"><path d="M43 16 13 39" stroke="#ea4335"/><path d="m13 51 30 23" stroke="#4285f4"/></svg>
    <span className="mark-words"><span>{theme.name}</span><span>{theme.subtitle}<span className="mark-period">.</span></span></span>
    <svg className="mark-bracket" viewBox="0 0 56 90" aria-hidden="true"><path d="m13 16 30 23" stroke="#34a853"/><path d="M43 51 13 74" stroke="#f9ab00"/></svg>
  </span>;
}
