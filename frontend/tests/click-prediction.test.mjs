import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/click-prediction.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { createClickPrediction } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const state = { playerId: 'me', gold: 100, goldPerClick: 2, level: 2, upgradeCost: 200, leaderboard: [], ping: null };

test('predicts every click and preserves later clicks across batch acknowledgements and polling', () => {
  const prediction = createClickPrediction();
  prediction.reconcile(state);
  assert.equal(prediction.add().gold, 102);
  for (let i = 1; i < 13; i++) prediction.add();
  assert.equal(prediction.reconcile({ ...state, gold: 120 }, 10).gold, 126);
  assert.equal(prediction.reconcile({ ...state, gold: 120 }).gold, 126);
  assert.equal(prediction.reconcile({ ...state, gold: 126 }, 3).gold, 126);
  assert.equal(state.gold, 100);
});

test('corrects server disagreements and discards uncertain predictions after errors', () => {
  const prediction = createClickPrediction();
  prediction.reconcile(state);
  for (let i = 0; i < 12; i++) prediction.add();
  // The server reports less gold than predicted for the completed batch.
  assert.equal(prediction.reconcile({ ...state, gold: 110 }, 10).gold, 114);
  assert.equal(prediction.discard().gold, 110);
  assert.equal(prediction.reconcile({ ...state, gold: 112 }).gold, 112);
});

test('uses authoritative upgrade results and resets pending clicks if session player changes', () => {
  const prediction = createClickPrediction();
  prediction.reconcile({ ...state, gold: 198 });
  assert.equal(prediction.add().gold, 200);
  assert.equal(prediction.reconcile({ ...state, gold: 200 }, 1).gold, 200);
  const upgraded = { ...state, gold: 0, level: 3, goldPerClick: 3, upgradeCost: 300 };
  assert.equal(prediction.reconcile(upgraded).gold, 0);
  assert.equal(prediction.add().gold, 3);
  assert.equal(prediction.reconcile({ ...state, playerId: 'new-player', gold: 0 }).gold, 0);
});
