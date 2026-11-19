const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const ts=require('typescript');
const output=ts.transpileModule(fs.readFileSync('src/lib/cSyntax.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
const subject={exports:{}};
new Function('exports','module',output)(subject.exports,subject);
const {highlightC}=subject.exports;
const kinds=(line)=>highlightC([line])[0].filter(t=>t.k).map(t=>[t.k,t.s]);

test('拼起来和原文一样（只上色，不改字）',()=>{
  const code=['#include <stdio.h>','int main(void) {','  /* 多行','     注释 */ int a[10] = {0}, n;','  scanf("%d", &n); // 读入','  printf("%5.2f\\n", 1.5e3);','  return 0;','}'];
  const out=highlightC(code);
  assert.equal(out.length,code.length);
  out.forEach((toks,i)=>assert.equal(toks.map(t=>t.s).join(''),code[i]));
});
test('关键字、类型、函数名、数字、预处理',()=>{
  assert.deepEqual(kinds('#include <stdio.h>'),[['pre','#include'],['str','<stdio.h>']]);
  assert.deepEqual(kinds('for (int i = 0; i < n; i++) sum += f(i);'),[['kw','for'],['type','int'],['num','0'],['fn','f']]);
  assert.deepEqual(kinds('if (p == NULL) return 0x1F;'),[['kw','if'],['num','NULL'],['kw','return'],['num','0x1F']]);
  assert.deepEqual(kinds('int a1 = b2;'),[['type','int']]);                     // 变量名里的数字不算数字
});
test('字符串里的格式和转义另标',()=>{
  assert.deepEqual(kinds('printf("sum=%lld\\n", s);'),[['fn','printf'],['str','"sum='],['esc','%lld'],['esc','\\n'],['str','"']]);
  assert.deepEqual(kinds("c = '\\0';"),[['str',"'"],['esc','\\0'],['str',"'"]]);
  assert.deepEqual(kinds('s = "a\\"b // 不是注释";'),[['str','"a'],['esc','\\"'],['str','b // 不是注释"']]);
});
test('注释：行尾、块、跨行',()=>{
  assert.deepEqual(kinds('x = 1; // 一 if'),[['num','1'],['com','// 一 if']]);
  const out=highlightC(['/* 开头','  中间 int','结尾 */ return;']);
  assert.deepEqual(out[1],[{k:'com',s:'  中间 int'}]);
  assert.deepEqual(out[2].filter(t=>t.k).map(t=>t.k),['com','kw']);
});
