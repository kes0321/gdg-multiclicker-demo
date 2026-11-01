import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/game-service.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { createApiGameService } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('HTTP session and WebSocket game contract', async t => {
  const original = { fetch: globalThis.fetch, window: globalThis.window, WebSocket: globalThis.WebSocket };
  const player = { player_id: 'p-test', nickname: '복원된 닉네임', score: 150, level: 1, total_clicks: 0, gold_per_click: 10, next_upgrade_cost: 100 };
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
        reply = { type: 'levelup_result', success: false, code: 'MAX_LEVEL_REACHED',
          score: player.score, level: player.level, gold_per_click: player.gold_per_click, next_upgrade_cost: null };
      } else if (player.score >= player.next_upgrade_cost) {
        player.score -= player.next_upgrade_cost;
        player.level++;
        player.gold_per_click = 12;
        player.next_upgrade_cost = 160;
        reply = { type: 'levelup_result', success: true, score: player.score, level: player.level,
          gold_per_click: player.gold_per_click, next_upgrade_cost: player.next_upgrade_cost };
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
  await t.test('accepts null upgrade cost and keeps clicking after maximum level rejection', async () => {
    Object.assign(player, { level: 30, gold_per_click: 6462, next_upgrade_cost: null });
    const state = await service.getState();
    assert.equal(state.goldPerClick, 6462);
    assert.equal(state.upgradeCost, null);
    await assert.rejects(service.upgrade(), /최대 레벨/);
    const before = player.score;
    const clicked = await service.click(1);
    assert.equal(clicked.gold, before + 6462);
    assert.equal(clicked.upgradeCost, null);
    assert.equal(clicked.goldPerClick, 6462);
  });
  await t.test('disposes the socket and prevents further requests', async () => {
    service.dispose();
    await assert.rejects(service.getState(), /연결이 종료/);
    assert.equal(sockets.at(-1).readyState, 3);
  });
});
