// Flush at ten clicks or 500 ms after the batch's first click, whichever comes first.
export function createClickBatcher(send: (count: number) => void) {
  let clicks = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let disposed = false;
  let waiting: { action: () => Promise<void>; resolve: () => void; reject: (reason: unknown) => void }[] = [];
  function flush() {
    clearTimeout(timer);
    timer = undefined;
    const count = clicks;
    clicks = 0;
    if (count > 0) send(count);
    const actions = waiting;
    waiting = [];
    for (const task of actions) task.action().then(task.resolve, task.reject);
  }
  function clear() {
    clearTimeout(timer);
    timer = undefined;
    clicks = 0;
    waiting.forEach(task => task.resolve());
    waiting = [];
  }
  return {
    add() {
      if (disposed) return;
      clicks++;
      if (clicks === 10) flush();
      else if (timer === undefined) timer = setTimeout(flush, 500);
    },
    afterFlush(action: () => Promise<void>): Promise<void> {
      if (disposed) return Promise.resolve();
      if (!clicks) return action();
      return new Promise((resolve, reject) => waiting.push({ action, resolve, reject }));
    },
    clear,
    dispose() { disposed = true; clear(); },
  };
}
