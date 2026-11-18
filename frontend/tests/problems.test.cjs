const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const output = ts.transpileModule(fs.readFileSync('src/lib/problems.ts', 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const subject = { exports: {} };
new Function('exports', 'module', output)(subject.exports, subject);
const { sortProblems } = subject.exports;
const row = (id, passed = false, time = null, submitted = false) => ({ key: `p1-${id}`, passed,
  last: submitted ? {time} : null });

test('未通过优先，其次未提交，最后已通过；时间倒序、题号升序', () => {
  const rows = [row(40,true,'2026-11-18T09:00:00+08:00',true), row(12),
    row(7,false,'2026-11-18T02:00:00Z',true), row(2),
    row(8,false,'2026-11-18T09:59:59+08:00',true), row(30,true,'2026-11-18T10:00:00+08:00',true)];
  const before = [...rows];
  assert.deepEqual(sortProblems(rows).map(p=>p.key), ['p1-7','p1-8','p1-2','p1-12','p1-30','p1-40']);
  assert.deepEqual(rows, before);
});

test('时间缺失仍属已提交，稳定排在同组有时间的提交后；相同时间按编号', () => {
  const rows = [row(11,false,null,true),row(10,false,'bad-date',true),row(6,false,'2026-11-18T10:00:00Z',true),row(3,false,'2026-11-18T10:00:00Z',true)];
  assert.deepEqual(sortProblems(rows).map(p=>p.key), ['p1-3','p1-6','p1-10','p1-11']);
});
test('评测中和未知结果不能抢在最近的错误题之前，也不标成失败', () => {
  const waiting = {...row(90,false,'2026-11-19T12:00:00Z',true),last:{time:'2026-11-19T12:00:00Z',result:'PENDING'}};
  const unknown = {...waiting,key:'p1-91',last:{time:'2026-11-19T11:00:00Z',result:'UNKNOWN'}};
  assert.deepEqual(sortProblems([waiting,row(7,false,'2026-11-18T10:00:00Z',true),row(2),row(30,true,'2026-11-19T12:00:00Z',true),unknown]).map(p=>p.key),
    ['p1-7','p1-2','p1-90','p1-91','p1-30']);
});
