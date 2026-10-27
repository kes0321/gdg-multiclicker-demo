import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/click-batcher.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { createClickBatcher } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('flushes at ten clicks or 500ms without losing clicks, orders upgrades and cleans up', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const events = [];
  const batcher = createClickBatcher(count => events.push(['batch', count]));
  t.after(() => batcher.dispose());

  t.mock.timers.tick(500);
  assert.deepEqual(events, []); // No empty requests.
  for (let i = 0; i < 3; i++) batcher.add();
  t.mock.timers.tick(499);
  assert.deepEqual(events, []);
  t.mock.timers.tick(1);
  assert.deepEqual(events, [['batch', 3]]);

  for (let i = 0; i < 25; i++) batcher.add();
  assert.deepEqual(events.slice(1), [['batch', 10], ['batch', 10]]);
  t.mock.timers.tick(499);
  assert.equal(events.length, 3);
  t.mock.timers.tick(1);
  assert.deepEqual(events.at(-1), ['batch', 5]);
  assert.equal(events.reduce((sum, event) => sum + event[1], 0), 28);
  t.mock.timers.tick(500);
  assert.equal(events.length, 4); // No empty or duplicate batches.

  // An early flush cancels the old deadline; the next click starts a fresh timer.
  batcher.add();
  t.mock.timers.tick(200);
  for (let i = 0; i < 9; i++) batcher.add();
  assert.deepEqual(events.at(-1), ['batch', 10]);
  batcher.add();
  t.mock.timers.tick(300);
  assert.equal(events.length, 5);
  t.mock.timers.tick(199);
  assert.equal(events.length, 5);
  t.mock.timers.tick(1);
  assert.deepEqual(events.at(-1), ['batch', 1]);

  batcher.add();
  const upgraded = batcher.afterFlush(async () => { events.push(['upgrade']); });
  assert.equal(events.length, 6);
  t.mock.timers.tick(500);
  await upgraded;
  assert.deepEqual(events.slice(-2), [['batch', 1], ['upgrade']]);

  batcher.add();
  const cancelled = batcher.afterFlush(async () => { events.push(['unexpected upgrade']); });
  batcher.clear();
  await cancelled;
  const before = events.length;
  t.mock.timers.tick(500);
  assert.equal(events.length, before);
  batcher.add();
  batcher.dispose();
  batcher.add();
  t.mock.timers.tick(1000);
  assert.equal(events.length, before);
});
