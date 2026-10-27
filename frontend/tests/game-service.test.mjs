import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/game-service.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { createApiGameService } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('HTTP session and WebSocket game contract', async t => {
  const original = { fetch: globalThis.fetch, window: globalThis.window, WebSocket: globalThis.WebSocket };
  const player = { player_id: 'p-test', nickname: '복원된 닉네임', score: 150, level: 1, total_clicks: 0 };
  const requests = [];
  const messages = [];
  const sockets = [];
  let dropNext = false;
  let httpFailure = false;
  globalThis.window = { location: { href: 'https://game.example/' } };
  globalThis.fetch = async (path, options) => {
    requests.push({ path, options });
    if (httpFailure) return { ok: false, status: 503, json: async () => ({ detail: 'Redis 처리 실패' }) };
    let body;
    if (options.method === 'POST') body = { created: false, player: { ...player } };
    else if (path.startsWith('/api/players/')) body = { ...player };
    else if (path === '/api/leaderboard?limit=25') body = { players: [{ rank: 1, ...player }] };
    else throw new Error(`Unexpected request: ${path}`);
    return { ok: true, json: async () => body };
  };
  class Socket {
    static OPEN = 1;
    readyState = 1;
    constructor(url) {
      this.url = url;
      sockets.push(this);
      queueMicrotask(() => this.onmessage?.({ data: JSON.stringify({ type: 'connected', player: { ...player } }) }));
    }
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
        player.score += player.level * message.count;
        player.total_clicks += message.count;
        reply = { type: 'click_batch_result', success: true, accepted_clicks: message.count, ...player };
      } else if (player.score >= player.level * 100) {
        player.score -= player.level * 100;
        player.level++;
        reply = { type: 'levelup_result', success: true, score: player.score, level: player.level };
      } else reply = { type: 'levelup_result', success: false, code: 'INSUFFICIENT_GOLD', score: player.score };
      queueMicrotask(() => this.onmessage?.({ data: JSON.stringify(reply) }));
    }
    close() { this.readyState = 3; queueMicrotask(() => this.onclose?.()); }
  }
  globalThis.WebSocket = Socket;
  const service = createApiGameService('입력 닉네임');
  t.after(() => { service.dispose(); Object.assign(globalThis, original); });

  await t.test('restores cookie session and uses leaderboard nickname, rank and ID', async () => {
    const state = await service.getState();
    assert.equal(state.gold, 150);
    assert.equal(state.playerId, player.player_id);
    assert.equal(state.leaderboard[0].name, player.nickname);
    assert.equal(state.leaderboard[0].rank, 1);
    assert.equal(state.ping, null);
    assert.equal(sockets[0].url.href, 'wss://game.example/ws');
    assert.deepEqual(JSON.parse(requests[0].options.body), { nickname: '입력 닉네임' });
    assert.ok(requests.every(request => request.options.credentials === 'include'));
    assert.equal(requests.filter(request => request.path.startsWith('/api/players/')).length, 1);
  });
  await t.test('applies click and partial levelup responses with server upgrade cost', async () => {
    assert.equal((await service.click(1)).gold, 151);
    const upgraded = await service.upgrade();
    assert.equal(upgraded.gold, 51);
    assert.equal(upgraded.level, 2);
    assert.equal(upgraded.goldPerClick, 2);
    assert.equal(upgraded.upgradeCost, 200);
    assert.equal((await service.click(1)).gold, 53);
    assert.deepEqual(messages, [{ type: 'click_batch', count: 1 }, { type: 'levelup' }, { type: 'click_batch', count: 1 }]);
  });
  await t.test('reports insufficient gold and remains usable', async () => {
    await assert.rejects(service.upgrade(), /골드가 부족/);
    assert.equal((await service.click(1)).gold, 55);
  });
  await t.test('rejects interrupted input and reconnects without replaying it', async () => {
    dropNext = true;
    const before = messages.length;
    await assert.rejects(service.click(1), /연결이 끊겼/);
    const state = await service.getState();
    assert.equal(state.gold, 55);
    assert.equal(sockets.length, 2);
    assert.equal(messages.length, before + 1);
  });
  await t.test('surfaces HTTP error details', async () => {
    httpFailure = true;
    await assert.rejects(service.getState(), /Redis 처리 실패/);
    httpFailure = false;
  });
  await t.test('sends ten clicks in one request and rejects invalid batch counts locally', async () => {
    const before = messages.length;
    assert.equal((await service.click(10)).gold, 75);
    assert.equal(messages.length, before + 1);
    assert.deepEqual(messages.at(-1), { type: 'click_batch', count: 10 });
    for (const count of [0, 11, 1.5, '3', true, undefined]) {
      await assert.rejects(service.click(count), /1~10/);
    }
    assert.equal(messages.length, before + 1);
  });
  await t.test('disposes the socket and prevents further requests', async () => {
    service.dispose();
    await assert.rejects(service.getState(), /연결이 종료/);
    assert.equal(sockets.at(-1).readyState, 3);
  });
});
