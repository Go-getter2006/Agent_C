import { ArrowRight, BookOpen, Code2, RotateCcw } from "lucide-react";
import { Link } from "react-router-dom";
import { deadline } from "../lib/time";
import { sortProblems } from "../lib/problems";
import type { Homework, ProblemRow } from "../types";

export default function LearningStart({ homework, problems, due, loading, error }: {
  homework: Homework | null; problems: ProblemRow[]; due: number; loading: boolean; error?: string | null;
}) {
  const ordered = sortProblems(problems);
  const next = ordered.find(p => !p.passed) ?? ordered[0];
  const pending = ordered.filter(p => !p.passed);
  return <div className="learning-start">
    <div className="learning-intro"><span className="eyebrow">这次，从这里继续</span>
      <h2>{homework ? homework.title : "开始你的练习"}</h2>
      <p>{homework ? deadline(homework.endTime, homework.ended) : "选一道题，聊聊思路，一步步把问题弄明白。"}</p>
    </div>
    <section className="learning-focus" aria-busy={loading}>
      <Code2 size={28} aria-hidden="true" />
      <span className="eyebrow">{next?.last && !next.passed ? "先解决这个问题" : next?.passed ? "回顾一次练习" : "下一道练习"}</span>
      <h3>{next?.title ?? (loading ? "正在整理这次作业" : error ? "暂时无法取得作业题目" : "还没有题目")}</h3>
      <p>{next?.last ? `第 ${next.last.attemptNo} 次提交 · ${next.last.resultText}` : next ? "还没有提交，可以先一起读题、梳理思路。" : error ? "题目会自动重试获取，你也可以先问概念和题意。" : "也可以直接询问概念和题意。"}</p>
      <div className="learning-actions">
        {next && <Link className="btn btn-primary" to={`/chat/${next.key}`}>{next.last && !next.passed ? "继续分析这次提交" : next.passed ? "回顾这道题" : "开始这道题"}<ArrowRight size={16} /></Link>}
        <Link className="btn" to="/chat/general">问一个问题</Link>
      </div>
    </section>
    <div className="learning-bottom">
      <section className="learning-section">
        <h3><BookOpen size={18} aria-hidden="true" />当前作业</h3>
        <p className="note">已通过 {homework?.passed ?? 0} / {homework?.total ?? 0} 题</p>
        <div className="bar bar-pass"><span style={{width:`${homework?.total ? 100 * homework.passed / homework.total : 0}%`}} /></div>
        {pending.slice(0, 3).map(p => <Link className="learning-row" key={p.key} to={`/chat/${p.key}`}><span>{p.title}</span><span className="note">{p.last ? p.last.resultText : "还没做"}<ArrowRight size={14} /></span></Link>)}
        {!loading && !error && homework && homework.total > 0 && pending.length === 0 && <p className="note">这次作业里的题目已经全部通过。</p>}
        {!homework && !loading && <p className="note">老师还没有布置作业，可以先询问概念和题意。</p>}
      </section>
      <section className="learning-section">
        <h3><RotateCcw size={18} aria-hidden="true" />回顾与巩固</h3>
        <p>{due ? `有 ${due} 项到了复习时间` : "做过的题，也值得再想一遍"}</p>
        <p className="note">换个小场景，检验自己是否理解了改法。</p>
        <Link className="btn" to={due ? "/review" : "/progress"}>{due ? "开始复习" : "查看我的学情"}<ArrowRight size={15} /></Link>
      </section>
    </div>
  </div>;
}
