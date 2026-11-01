import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/click-prediction.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { createClickPrediction } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const state = { playerId: 'me', gold: 100, goldPerClick: 12, level: 2, upgradeCost: 160, leaderboard: [], ping: null };

test('predicts every click and preserves later clicks across batch acknowledgements and polling', () => {
  const prediction = createClickPrediction();
  prediction.reconcile(state);
  assert.equal(prediction.add().gold, 112);
  for (let i = 1; i < 13; i++) prediction.add();
  assert.equal(prediction.reconcile({ ...state, gold: 220 }, 10).gold, 256);
  assert.equal(prediction.reconcile({ ...state, gold: 220 }).gold, 256);
  assert.equal(prediction.reconcile({ ...state, gold: 256 }, 3).gold, 256);
  assert.equal(state.gold, 100);
});

test('corrects server disagreements and discards uncertain predictions after errors', () => {
  const prediction = createClickPrediction();
  prediction.reconcile(state);
  for (let i = 0; i < 12; i++) prediction.add();
  // The server reports less gold than predicted for the completed batch.
  assert.equal(prediction.reconcile({ ...state, gold: 110 }, 10).gold, 134);
  assert.equal(prediction.discard().gold, 110);
  assert.equal(prediction.reconcile({ ...state, gold: 112 }).gold, 112);
});

test('uses authoritative upgrade results and resets pending clicks if session player changes', () => {
  const prediction = createClickPrediction();
  prediction.reconcile({ ...state, gold: 148 });
  assert.equal(prediction.add().gold, 160);
  assert.equal(prediction.reconcile({ ...state, gold: 160 }, 1).gold, 160);
  const upgraded = { ...state, gold: 0, level: 3, goldPerClick: 15, upgradeCost: 256 };
  assert.equal(prediction.reconcile(upgraded).gold, 0);
  assert.equal(prediction.add().gold, 15);
  assert.equal(prediction.reconcile({ ...state, playerId: 'new-player', gold: 0 }).gold, 0);
});

test('leaderboard broadcasts preserve pending predictions and do not acknowledge in-flight clicks', () => {
  const prediction = createClickPrediction();
  const ranking = [{ id: 'me', name: '나', rank: 1, gold: 120 }];
  // A broadcast can arrive before the initial state promise completes.
  assert.equal(prediction.updateLeaderboard(ranking), undefined);
  assert.deepEqual(prediction.reconcile(state).leaderboard, ranking);
  for (let i = 0; i < 12; i++) prediction.add();
  const nextRanking = [{ id: 'other', name: '다른 사람', rank: 1, gold: 150 }, ...ranking];
  assert.equal(prediction.updateLeaderboard(nextRanking).gold, 244);
  const acknowledged = prediction.reconcile({ ...state, gold: 120 }, 10);
  assert.equal(acknowledged.gold, 144);
  assert.deepEqual(acknowledged.leaderboard, nextRanking);
  assert.deepEqual(prediction.updateLeaderboard([]).leaderboard, []);
});
