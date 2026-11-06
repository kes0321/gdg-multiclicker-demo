import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/game-service.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { createApiGameService, preparePlayer } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('entry confirmations only submit the chosen session action', async t => {
  const originalFetch = globalThis.fetch;
  const fixture = () => ({ player_id: 'previous-player', nickname: '기존 이름', level: 13, score: 3234, total_clicks: 17 });
  let player, failure;
  const requests = [], questions = [];
  globalThis.fetch = async (path, options) => {
    requests.push({ path, options });
    if (failure) return { ok: false, status: 409, json: async () => ({ detail: failure }) };
    if (path === '/api/players') {
      const body = JSON.parse(options.body);
      if (!player) player = { player_id: 'new', nickname: body.nickname, level: 1, score: 0, total_clicks: 0 };
      else if (body.action === 'rename') player.nickname = body.nickname;
      else if (body.action === 'restart') Object.assign(player, { nickname: body.nickname, level: 1, score: 0, total_clicks: 0 });
    }
    return { ok: true, json: async () => ({ player: player ? { ...player } : null }) };
  };
  t.after(() => { globalThis.fetch = originalFetch; });
  async function run(answers, nickname = '입력 이름') {
    requests.length = questions.length = 0;
    return preparePlayer(nickname, async message => {
      questions.push(message);
      assert.ok(answers.length, 'unexpected confirmation');
      return answers.shift();
    });
  }
  await t.test('new visitors start without confirmations', async () => {
    player = null;
    assert.equal(await run([]), '입력 이름');
    assert.equal(questions.length, 0);
    assert.deepEqual(JSON.parse(requests[1].options.body), { nickname: '입력 이름', action: 'resume' });
  });
  await t.test('continuing can rename without losing progress', async () => {
    player = fixture();
    assert.equal(await run([true, true]), '입력 이름');
    for (const detail of ['기존 이름', '13lv', '3,234G', '이어하시겠습니까']) assert.ok(questions[0].includes(detail));
    assert.ok(questions[1].includes('입력 이름'));
    assert.deepEqual(JSON.parse(requests[1].options.body), { nickname: '입력 이름', action: 'rename', expected_player_id: 'previous-player' });
    assert.equal(player.level, 13);
    assert.equal(player.score, 3234);
  });
  await t.test('declining rename keeps the existing nickname and progress', async () => {
    player = fixture();
    assert.equal(await run([true, false]), '기존 이름');
    assert.deepEqual(player, fixture());
    assert.equal(JSON.parse(requests[1].options.body).action, 'resume');
  });
  await t.test('same nickname needs no rename confirmation', async () => {
    player = fixture();
    assert.equal(await run([true], player.nickname), '기존 이름');
    assert.equal(questions.length, 1);
  });
  await t.test('declining the restart warning makes no mutation request', async () => {
    player = fixture();
    assert.equal(await run([false, false]), null);
    assert.equal(requests.length, 1);
    assert.ok(questions[1].includes('이전 데이터는 사라집니다'));
    assert.deepEqual(player, fixture());
  });
  await t.test('confirmed restart clears progress with the entered nickname', async () => {
    player = fixture();
    assert.equal(await run([false, true]), '입력 이름');
    assert.equal(JSON.parse(requests[1].options.body).action, 'restart');
    assert.deepEqual([player.level, player.score, player.total_clicks], [1, 0, 0]);
  });
  await t.test('session lookup errors stop before any confirmation or mutation', async () => {
    failure = '접속 기록 조회 실패';
    await assert.rejects(run([]), /접속 기록 조회 실패/);
    assert.equal(requests.length, 1);
    assert.equal(questions.length, 0);
    failure = undefined;
  });
  assert.ok(requests.every(({ options }) => options.credentials === 'include'));
});

test('HTTP session and WebSocket game contract', async t => {
  const original = { fetch: globalThis.fetch, window: globalThis.window, WebSocket: globalThis.WebSocket };
  const player = { player_id: 'p-test', nickname: '복원된 닉네임', score: 150, level: 1, total_clicks: 0, gold_per_click: 10, next_gold_per_click: 12, next_upgrade_cost: 100 };
  const requests = [];
  const messages = [];
  const sockets = [];
  let dropNext = false;
  let httpFailure = false;
  let holdReply = false;
  let heldReply;
  globalThis.window = { location: { href: 'https://game.example/' } };
  globalThis.fetch = async (path, options) => {
    requests.push({ path, options });
    if (httpFailure) return { ok: false, status: 503, json: async () => ({ detail: 'Redis 처리 실패' }) };
    let body;
    if (options.method === 'POST') body = { created: false, player: { ...player } };
    else throw new Error(`Unexpected request: ${path}`);
    return { ok: true, json: async () => body };
  };
  class Socket {
    static OPEN = 1;
    readyState = 1;
    constructor(url) {
      this.url = url;
      sockets.push(this);
      queueMicrotask(() => {
        this.emit({ type: 'connected', player: { ...player } });
        this.emit({ type: 'leaderboard', players: [{ rank: 1, ...player }] });
      });
    }
    emit(message) { this.onmessage?.({ data: JSON.stringify(message) }); }
    send(data) {
      const message = JSON.parse(data);
      messages.push(message);
      if (dropNext) {
        dropNext = false;
        this.close();
        return;
      }
      let reply;
      if (message.type === 'click_batch') {
        player.score += player.gold_per_click * message.count;
        player.total_clicks += message.count;
        reply = { type: 'click_batch_result', success: true, accepted_clicks: message.count, ...player };
      } else if (player.next_upgrade_cost === null) {
        reply = { type: 'levelup_result', success: false, code: 'MAX_LEVEL_REACHED', score: player.score,
          level: player.level, gold_per_click: player.gold_per_click, next_gold_per_click: null, next_upgrade_cost: null };
      } else if (player.score >= player.next_upgrade_cost) {
        player.score -= player.next_upgrade_cost;
        player.level++;
        const growth = player.level === 40
          ? { gold_per_click: 60185, next_gold_per_click: null, next_upgrade_cost: null }
          : player.level === 31
          ? { gold_per_click: 8077, next_gold_per_click: 10097, next_upgrade_cost: 132922800 }
          : { gold_per_click: 12, next_gold_per_click: 15, next_upgrade_cost: 160 };
        Object.assign(player, growth);
        reply = { type: 'levelup_result', success: true, score: player.score, level: player.level,
          gold_per_click: player.gold_per_click, next_gold_per_click: player.next_gold_per_click, next_upgrade_cost: player.next_upgrade_cost };
      } else reply = { type: 'levelup_result', success: false, code: 'INSUFFICIENT_GOLD', score: player.score };
      queueMicrotask(() => {
        this.emit({ type: 'leaderboard', players: [{ rank: 1, ...player }] });
        if (holdReply) heldReply = reply;
        else this.emit(reply);
      });
    }
    close() { this.readyState = 3; queueMicrotask(() => this.onclose?.()); }
  }
  globalThis.WebSocket = Socket;
  const service = createApiGameService('입력 닉네임');
  const broadcasts = [];
  const errors = [];
  const counts = [];
  const unsubscribe = service.subscribe((players, count) => { broadcasts.push(players); counts.push(count); }, error => errors.push(error));
  t.after(() => { service.dispose(); Object.assign(globalThis, original); });

  await t.test('restores cookie session and uses leaderboard nickname, rank and ID', async () => {
    const state = await service.getState();
    assert.equal(state.gold, 150);
    assert.equal(state.goldPerClick, 10);
    assert.equal(state.nextGoldPerClick, 12);
    assert.equal(state.upgradeCost, 100);
    assert.equal(state.playerId, player.player_id);
    assert.equal(state.leaderboard[0].name, player.nickname);
    assert.equal(state.leaderboard[0].rank, 1);
    assert.equal(state.ping, null);
    assert.equal(sockets[0].url.href, 'wss://game.example/ws');
    assert.deepEqual(JSON.parse(requests[0].options.body), { nickname: '입력 닉네임' });
    assert.ok(requests.every(request => request.options.credentials === 'include'));
    assert.equal(requests.length, 1);
    assert.equal(requests[0].path, '/api/players');
    assert.equal(broadcasts.length, 1);
  });
  await t.test('applies click and partial levelup responses with server upgrade cost', async () => {
    assert.equal((await service.click(1)).gold, 160);
    const upgraded = await service.upgrade();
    assert.equal(upgraded.gold, 60);
    assert.equal(upgraded.level, 2);
    assert.equal(upgraded.goldPerClick, 12);
    assert.equal(upgraded.nextGoldPerClick, 15);
    assert.equal(upgraded.upgradeCost, 160);
    assert.equal((await service.click(1)).gold, 72);
    assert.deepEqual(messages, [{ type: 'click_batch', count: 1 }, { type: 'levelup' }, { type: 'click_batch', count: 1 }]);
  });
  await t.test('reports insufficient gold and remains usable', async () => {
    await assert.rejects(service.upgrade(), /골드가 부족/);
    assert.equal((await service.click(1)).gold, 84);
  });
  await t.test('rejects interrupted input and reconnects without replaying it', async () => {
    dropNext = true;
    const before = messages.length;
    await assert.rejects(service.click(1), /연결이 끊겼/);
    const state = await service.getState();
    assert.equal(state.gold, 84);
    assert.equal(sockets.length, 2);
    assert.equal(messages.length, before + 1);
  });
  await t.test('surfaces HTTP error details', async () => {
    httpFailure = true;
    const failingService = createApiGameService('테스트');
    try { await assert.rejects(failingService.getState(), /Redis 처리 실패/); }
    finally { failingService.dispose(); httpFailure = false; }
  });
  await t.test('sends ten clicks in one request and rejects invalid batch counts locally', async () => {
    const before = messages.length;
    assert.equal((await service.click(10)).gold, 204);
    assert.equal(messages.length, before + 1);
    assert.deepEqual(messages.at(-1), { type: 'click_batch', count: 10 });
    for (const count of [0, 11, 1.5, '3', true, undefined]) {
      await assert.rejects(service.click(count), /1~10/);
    }
    assert.equal(messages.length, before + 1);
  });
  await t.test('broadcasts update immediately without resolving an in-flight batch or fetching HTTP', async () => {
    const before = requests.length;
    holdReply = true;
    let resolved = false;
    const operation = service.click(2).then(state => { resolved = true; return state; });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(resolved, false);
    assert.equal(broadcasts.at(-1)[0].gold, 228);
    sockets.at(-1).emit(heldReply);
    assert.equal((await operation).gold, 228);
    holdReply = false;
    sockets.at(-1).emit({ type: 'leaderboard', players: [] });
    assert.deepEqual(broadcasts.at(-1), []);
    assert.deepEqual((await service.getState()).leaderboard, []);
    assert.equal(requests.length, before);
    assert.ok(requests.every(request => request.options.method === 'POST' && request.path === '/api/players'));
  });
  await t.test('counts all online players before truncating the visible top 25', async () => {
    sockets.at(-1).emit({ type: 'leaderboard', players: Array.from({ length: 32 }, (_, index) => ({
      rank: index + 1, player_id: `online-${index}`, nickname: `유저${index}`, score: 100-index,
    })) });
    assert.equal(broadcasts.at(-1).length, 25);
    assert.equal(counts.at(-1), 32);
    sockets.at(-1).emit({ type: 'leaderboard', players: [] });
    assert.equal(counts.at(-1), 0);
    assert.deepEqual(broadcasts.at(-1), []);
  });
  await t.test('reports idle disconnects and stops delivering broadcasts after unsubscribe', async () => {
    const before = broadcasts.length;
    unsubscribe();
    sockets.at(-1).emit({ type: 'leaderboard', players: [{ rank: 1, ...player }] });
    assert.equal(broadcasts.length, before);
    const stop = service.subscribe(() => {}, error => errors.push(error));
    const errorCount = errors.length;
    sockets.at(-1).close();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(errors.length, errorCount + 1);
    stop();
  });
  await t.test('continues upgrading and showing server rewards beyond level 30', async () => {
    Object.assign(player, { level: 30, score: 83076750, gold_per_click: 6462, next_gold_per_click: 8077, next_upgrade_cost: 83076750 });
    const state = await service.getState();
    assert.equal(state.goldPerClick, 6462);
    assert.equal(state.upgradeCost, 83076750);
    assert.equal(state.nextGoldPerClick, 8077);
    const upgraded = await service.upgrade();
    assert.equal(upgraded.level, 31);
    assert.equal(upgraded.gold, 0);
    assert.equal(upgraded.goldPerClick, 8077);
    assert.equal(upgraded.nextGoldPerClick, 10097);
    assert.equal(upgraded.upgradeCost, 132922800);
    const before = player.score;
    const clicked = await service.click(1);
    assert.equal(clicked.gold, before + 8077);
    assert.equal(clicked.upgradeCost, 132922800);
    assert.equal(clicked.nextGoldPerClick, 10097);
    assert.equal(clicked.goldPerClick, 8077);
  });
  await t.test('reaches level 40 with null previews and keeps clicking after rejected upgrades', async () => {
    Object.assign(player, { level: 39, score: 5708990871, gold_per_click: 48148, next_gold_per_click: 60185, next_upgrade_cost: 5708990771 });
    sockets.at(-1).close();
    await new Promise(resolve => setImmediate(resolve));
    const state = await service.getState();
    assert.equal(state.level, 39);
    assert.equal(state.nextGoldPerClick, 60185);
    const upgraded = await service.upgrade();
    assert.equal(upgraded.level, 40);
    assert.equal(upgraded.gold, 100);
    assert.equal(upgraded.goldPerClick, 60185);
    assert.equal(upgraded.nextGoldPerClick, null);
    assert.equal(upgraded.upgradeCost, null);
    await assert.rejects(service.upgrade(), /최대 레벨/);
    assert.equal((await service.getState()).gold, 100);
    const clicked = await service.click(1);
    assert.equal(clicked.gold, 60285);
    assert.equal(clicked.goldPerClick, 60185);
    assert.equal(clicked.nextGoldPerClick, null);
    assert.equal(clicked.upgradeCost, null);
  });
  await t.test('disposes the socket and prevents further requests', async () => {
    service.dispose();
    await assert.rejects(service.getState(), /연결이 종료/);
    assert.equal(sockets.at(-1).readyState, 3);
  });
});
