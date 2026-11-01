import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/game-service.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { getGlobalLeaderboard } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('global HTTP rankings preserve server order and stay independent of online rankings', async t => {
  const original = globalThis.fetch;
  t.after(() => { globalThis.fetch = original; });
  const controller = new AbortController();
  let request;
  globalThis.fetch = async (url, options) => {
    request = { url, options };
    return { ok: true, json: async () => ({ players: [
      { rank: 1, player_id: 'offline', nickname: '오프라인 유저', score: 900 },
      { rank: 2, player_id: 'me', nickname: '나', score: 900 },
    ] }) };
  };
  assert.deepEqual(await getGlobalLeaderboard(controller.signal), [
    { rank: 1, id: 'offline', name: '오프라인 유저', gold: 900 },
    { rank: 2, id: 'me', name: '나', gold: 900 },
  ]);
  assert.equal(request.url, '/api/leaderboard?limit=25');
  assert.equal(request.options.method, 'GET');
  assert.equal(request.options.credentials, 'include');
  controller.abort();
  assert.equal(request.options.signal.aborted, true);

  globalThis.fetch = async () => ({ ok: true, json: async () => ({ players: [] }) });
  assert.deepEqual(await getGlobalLeaderboard(), []);
  globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => ({ detail: '랭킹 조회 실패' }) });
  await assert.rejects(getGlobalLeaderboard(), /랭킹 조회 실패/);
});
