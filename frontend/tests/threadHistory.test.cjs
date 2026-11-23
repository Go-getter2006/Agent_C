const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const { outputText } = ts.transpileModule(fs.readFileSync('src/lib/threadHistory.ts', 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
});
const subject = { exports: {} };
new Function('exports', 'module', outputText)(subject.exports, subject);
const { threadHistory } = subject.exports;

test('首次切题区分未知和确认为空，两道空会话共用同一空态', () => {
  assert.equal(threadHistory(null).messages, null);
  assert.deepEqual(threadHistory(undefined, 0).messages, []);
  assert.equal(threadHistory([], 0).messages, threadHistory(null, 0).messages);
});

test('已读新消息与未读提醒都优先于缓存里的空会话', () => {
  assert.equal(threadHistory([], 18).messages, null);
  assert.equal(threadHistory([], 0, 19).messages, null);
  assert.equal(threadHistory(null, 18).behind, true);
});

test('缓存有正文时后台补消息，较老的列表结果也不能清掉正文', () => {
  const cached = [{ id: 19 }];
  assert.equal(threadHistory(cached, 20).messages, cached);
  assert.equal(threadHistory(cached, 19).behind, false);
  assert.equal(threadHistory(cached, 0).messages, cached);
});
