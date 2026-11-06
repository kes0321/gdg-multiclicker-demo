'use strict';
const base = location.pathname.replace(/\/$/, '');
const byId = id => document.getElementById(id);
let csrf = '', cursor = '0', nextCursor = '0', history = [];

function showLogin() {
  csrf = '';
  byId('console').hidden = true;
  byId('login').hidden = false;
  byId('players').replaceChildren();
}

async function request(path, options = {}) {
  const response = await fetch(base + path, {
    ...options, credentials: 'same-origin', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
    signal: AbortSignal.timeout(10000),
  });
  const body = await response.json();
  if (response.status === 401) showLogin();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `요청 실패 (${response.status})`);
  return body;
}

function report(error) { byId('status').textContent = error.message || '요청에 실패했습니다.'; }

function render(players) {
  const rows = players.map(player => {
    const row = document.createElement('tr');
    const name = row.insertCell();
    name.textContent = player.nickname;
    const id = document.createElement('small');
    id.textContent = player.player_id;
    name.append(id);
    const gold = document.createElement('input');
    Object.assign(gold, { type: 'number', min: '0', max: String(Number.MAX_SAFE_INTEGER), step: '1', value: player.score ?? '' });
    gold.setAttribute('aria-label', `${player.nickname} 보유 골드`);
    row.insertCell().append(gold);
    const level = document.createElement('input');
    Object.assign(level, { type: 'number', min: '1', max: '40', step: '1', value: player.level ?? '', className: 'level' });
    level.setAttribute('aria-label', `${player.nickname} 레벨`);
    row.insertCell().append(level);
    const save = document.createElement('button');
    save.textContent = '저장';
    save.disabled = player.score === null || player.level === null;
    if (save.disabled) save.textContent = '데이터 오류';
    save.addEventListener('click', async () => {
      const score = gold.valueAsNumber, currentLevel = level.valueAsNumber;
      if (!gold.checkValidity() || !level.checkValidity() || !Number.isSafeInteger(score) || !Number.isSafeInteger(currentLevel)) {
        report(new Error('골드와 레벨의 정수 범위를 확인해 주세요.'));
        return;
      }
      save.disabled = true;
      try {
        await request('/users/' + encodeURIComponent(player.player_id), {
          method: 'PATCH', body: JSON.stringify({ score, level: currentLevel, expected_score: player.score, expected_level: player.level }),
        });
        await loadPage(cursor);
        byId('status').textContent = '저장했습니다.';
      } catch (error) { report(error); }
      finally { save.disabled = false; }
    });
    const remove = document.createElement('button');
    remove.textContent = '완전 삭제';
    remove.className = 'danger';
    remove.addEventListener('click', async () => {
      if (!window.confirm(`${player.nickname}\nID: ${player.player_id}\n\n유저 데이터와 모든 게임 세션을 완전히 삭제하시겠습니까?\n삭제 후에는 복구할 수 없습니다.`)) return;
      remove.disabled = save.disabled = true;
      try {
        await request('/users/' + encodeURIComponent(player.player_id), {
          method: 'DELETE', body: JSON.stringify({ confirm_player_id: player.player_id }),
        });
        row.remove();
        byId('status').textContent = '유저 데이터와 게임 세션을 완전히 삭제했습니다.';
      } catch (error) { report(error); }
      finally { remove.disabled = false; save.disabled = player.score === null || player.level === null; }
    });
    row.insertCell().append(save, remove);
    return row;
  });
  byId('players').replaceChildren(...rows);
}

async function loadPage(next) {
  const body = await request('/users?cursor=' + encodeURIComponent(next) + '&count=50');
  cursor = next;
  nextCursor = body.next_cursor;
  render(body.players);
  byId('previous').disabled = history.length === 0;
  byId('next').disabled = nextCursor === '0';
  byId('status').textContent = `${body.players.length}명의 유저를 표시합니다.`;
}

async function enter(session) {
  csrf = session.csrf_token;
  byId('login').hidden = true;
  byId('console').hidden = false;
  history = [];
  await loadPage('0');
}

byId('login').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button');
  button.disabled = true;
  const password = byId('password').value;
  byId('password').value = '';
  try { await enter(await request('/login', { method: 'POST', body: JSON.stringify({ password }) })); }
  catch (error) { report(error); }
  finally { button.disabled = false; }
});
byId('logout').addEventListener('click', async () => {
  try { await request('/logout', { method: 'POST' }); showLogin(); byId('status').textContent = '로그아웃했습니다.'; }
  catch (error) { report(error); }
});
byId('refresh').addEventListener('click', () => loadPage(cursor).catch(report));
byId('next').addEventListener('click', async () => {
  const old = cursor;
  history.push(old);
  try { await loadPage(nextCursor); } catch (error) { history.pop(); report(error); }
});
byId('previous').addEventListener('click', async () => {
  const previous = history.pop();
  if (previous === undefined) return;
  try { await loadPage(previous); } catch (error) { history.push(previous); report(error); }
});
async function checkSession() {
  try { await enter(await request('/session')); }
  catch (error) { showLogin(); report(error); }
}
window.addEventListener('pageshow', event => { if (event.persisted) checkSession(); });
checkSession();
