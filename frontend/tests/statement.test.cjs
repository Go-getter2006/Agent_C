const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const ts=require('typescript');
const output=ts.transpileModule(fs.readFileSync('src/lib/statement.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
const subject={exports:{}};
new Function('exports','module',output)(subject.exports,subject);
const {statementBlocks}=subject.exports;
const cases=JSON.parse(fs.readFileSync('../backend/tests/fixtures/statement_sections.json','utf8'));
for(const fixture of cases) test(fixture.name,()=>{
  assert.deepEqual(statementBlocks(fixture.text).filter(b=>b.kind==='sample').map(b=>b.text),fixture.samples);
});

test('样例说明从输出数据中分离，按正常正文换行',()=>{
  const blocks=statementBlocks('样例输入\n3\n1 2 3\n样例输出\n0 1\n样例说明：\n计算过程，较长的说明不属于输出。');
  assert.deepEqual(blocks.filter(b=>b.kind==='sample').map(b=>b.text),['3\n1 2 3','0 1']);
  assert.equal(blocks.at(-2).text,'样例说明');
  assert.equal(blocks.at(-1).kind,'p');
});
test('编号输入输出示例不能吞进输出说明，支持同行数据',()=>{
  const blocks=statementBlocks('输出说明\n输出英文星期名称。\n输入示例1: 2020 11 5\n输出示例1：\nThursday\n输入样例 2\n2021 6 2\n输出样例2\nWednesday');
  assert.deepEqual(blocks.filter(b=>b.kind==='sample').map(b=>b.text),['2020 11 5','Thursday','2021 6 2','Wednesday']);
  assert.equal(blocks[1].text,'输出英文星期名称。');
});
