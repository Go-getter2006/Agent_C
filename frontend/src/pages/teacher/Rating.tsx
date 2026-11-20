// 研究 · 理解评估（app/assess.py 的 overview）：智能体评“理解”和只看事实的规则对照。
// 一致率（大模型原始等级和规则）；大模型的等级按事实下调了多少、因为什么；各档、各种材料各有多少。
// 盲评不做了（2026-11-20），只留和规则的对照。
import { get } from "../../api";
import { Card, Facts, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { pct, useClasses, type ClassInfo } from "./common";

type Agree = { n: number; exact: number | null; kappa: number | null; within1: number | null; weighted: number | null };
interface Summary {
  agreement: Agree; total: number; llm: number; rule: number; unsure: number;
  adjusted: { n: number; d: number; reasons: Record<string, number> };
  levels: Record<"0" | "1" | "2" | "3", number>;
  basis: { chat: number; reflect: number; code: number };
}

const k2 = (x: number | null) => (x === null ? "—" : x.toFixed(2));
const LEVELS: [keyof Summary["levels"], string][] = [["0", "不理解"], ["1", "能照做"], ["2", "能解释"], ["3", "能迁移"]];

export default function Rating({ cls }: { cls: ClassInfo }) {
  const { tick } = useClasses();   // 数据变了才重取（common.tsx 的 useVersionTicks）
  const data = useLoad(() => get<{ summary: Summary }>(`/api/teacher/classes/${cls.key}/rating`), [cls.key, tick], 0, false,
    `t:rating:${cls.key}`, String(tick));
  if (data.error && !data.data) return <Problem message={data.error} onRetry={data.reload} />;
  if (!data.data) return <div className="card card-body"><Loading what="正在汇总" lines={4} /></div>;
  const s = data.data.summary;
  const a = s.agreement;
  return (
    <div className="stack">
      <Facts label="全班" items={[
        { label: "评了几次", value: `${s.total} 次`, note: `大模型 ${s.llm} · 规则 ${s.rule} · 看不出来 ${s.unsure}` },
        { label: "看什么评的", value: `对话 ${s.basis.chat}`, note: `通过后的回顾 ${s.basis.reflect} · 看代码怎么改的 ${s.basis.code}` },
        { label: "按事实下调", value: s.adjusted.d ? `${s.adjusted.n}/${s.adjusted.d}` : "—", note: "大模型评的等级和程序确定的事实冲突" },
      ]} />

      <Card title="大模型和规则" sub="同一个回合，大模型读学生原话评的等级（下调之前）和只看事实的规则给的等级" bodyClass="">
        {a.n === 0 ? <p className="card-body note">还没有两者都给了等级的回合。</p> : (
          <div className="table-scroll">
            <table className="table">
              <thead><tr><th scope="col">回合</th><th scope="col" className="num">完全一致</th><th scope="col" className="num">差一档内</th><th scope="col" className="num">κ</th><th scope="col" className="num">加权 κ</th></tr></thead>
              <tbody><tr><td className="num">{a.n}</td><td className="num">{pct(a.exact)}</td><td className="num">{pct(a.within1)}</td><td className="num">{k2(a.kappa)}</td><td className="num">{k2(a.weighted)}</td></tr></tbody>
            </table>
          </div>
        )}
        <p className="card-foot">规则看不出“能迁移”，也看不出说法本身错没错，所以两者不会完全一致；太低时要检查评估的提示词。加权 κ 按二次加权，差两档比差一档扣得多。</p>
      </Card>

      <div className="grid-2">
        <Card title="各档有多少" sub="最后用的等级（下调之后）">
          <ul className="plain-list">
            {LEVELS.map(([k, name]) => <li key={k}><span>{k} {name}</span><span className="num">{s.levels[k]}</span></li>)}
          </ul>
        </Card>
        <Card title="为什么下调" sub="大模型给的等级和程序确定的事实冲突时，按事实往下调">
          {Object.keys(s.adjusted.reasons).length === 0 ? <p className="note">还没有下调过。</p> : (
            <ul className="plain-list">
              {Object.entries(s.adjusted.reasons).map(([why, n]) => <li key={why}><span>{why}</span><span className="num">{n}</span></li>)}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
