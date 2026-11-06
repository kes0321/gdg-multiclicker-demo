import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../../backend/app/admin/static/admin.js', import.meta.url), 'utf8');

class Element {
  children = [];
  listeners = {};
  disabled = false;
  textContent = '';
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  insertCell() { const cell = new Element(); this.append(cell); return cell; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  setAttribute() {}
  remove() { this.removed = true; }
}

async function setup(confirmed, deleteStatus = 200) {
  const elements = Object.fromEntries(['login', 'console', 'players', 'status', 'previous', 'next', 'refresh', 'logout'].map(id => [id, new Element()]));
  const player = { player_id: 'target-player', nickname: '<script>untrusted</script>', score: 100, level: 3 };
  const requests = [], confirmations = [];
  const context = vm.createContext({
    location: { pathname: '/control-ui-test/' }, AbortSignal,
    document: { getElementById: id => elements[id], createElement: () => new Element() },
    window: { addEventListener() {}, confirm(message) { confirmations.push(message); return confirmed; } },
    async fetch(url, options) {
      requests.push({ url, options });
      const status = options.method === 'DELETE' ? deleteStatus : 200;
      return { ok: status === 200, status, async json() {
        if (url.endsWith('/session')) return { role: 'superadmin', csrf_token: 'csrf-from-server' };
        if (options.method === 'DELETE') return status === 200 ? { ok: true, player_id: player.player_id } : { detail: 'Delete failed' };
        return { players: [player], next_cursor: '0' };
      } };
    },
  });
  vm.runInContext(source, context);
  await new Promise(setImmediate);
  const row = elements.players.children[0];
  const [save, remove] = row.children[3].children;
  return { elements, requests, confirmations, row, save, remove };
}

test('admin deletion cancellation sends no DELETE and keeps the row', async () => {
  const ui = await setup(false);
  await ui.remove.listeners.click();
  assert.equal(ui.requests.filter(request => request.options.method === 'DELETE').length, 0);
  assert.equal(ui.row.removed, undefined);
  assert.match(ui.confirmations[0], /target-player/);
  assert.match(ui.confirmations[0], /복구할 수 없습니다/);
});

test('admin deletion sends confirmed ID and CSRF, then removes the row', async () => {
  const ui = await setup(true);
  await ui.remove.listeners.click();
  const deletion = ui.requests.find(request => request.options.method === 'DELETE');
  assert.equal(deletion.url, '/control-ui-test/users/target-player');
  assert.deepEqual(JSON.parse(deletion.options.body), { confirm_player_id: 'target-player' });
  assert.equal(deletion.options.headers['X-CSRF-Token'], 'csrf-from-server');
  assert.equal(deletion.options.credentials, 'same-origin');
  assert.equal(ui.row.removed, true);
});

test('failed admin deletion keeps the row and enables retry', async () => {
  const ui = await setup(true, 503);
  await ui.remove.listeners.click();
  assert.equal(ui.row.removed, undefined);
  assert.equal(ui.elements.status.textContent, 'Delete failed');
  assert.equal(ui.remove.disabled, false);
  assert.equal(ui.save.disabled, false);
});
