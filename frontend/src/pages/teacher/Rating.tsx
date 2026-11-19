// 研究 · 理解评估（app/rating.py）：智能体评“理解”可不可信。
// 盲评：看和大模型同样的材料，按同一个量规评；不显示学生是谁，也不显示大模型和规则的等级。选一档就记下、换下一条。
// 一致率：大模型和规则；老师和大模型原始、约束后、规则；老师之间。
import { useEffect, useState } from "react";
import { CircleCheck } from "lucide-react";
import { get, post } from "../../api";
import { Card, Empty, Loading, Problem, Segmented } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { pct, type ClassInfo } from "./common";

interface Item {
  id: number; kp: string; skill: string; problem: string; facts: string[];
  lines: { who: string; text: string }[]; shownAt: number;
}
type Agree = { n: number; exact: number | null; kappa: number | null; within1: number | null; weighted: number | null };
interface Summary {
  agreement: Agree;
  human: {
    rated: number; unsure: number; raters: number;
    llmRaw: Agree; llmFinal: Agree; rule: Agree; betweenRaters: Agree;
    adjusted: { n: number; closer: number; same: number; farther: number };
  };
}
interface RatingData { item: Item | null; rated: number; total: number; levels: { level: number; name: string; hint: string }[]; summary?: Summary }

const k2 = (x: number | null) => (x === null ? "—" : x.toFixed(2));

function AgreeTable({ rows }: { rows: [string, Agree][] }) {
  return (
    <div className="table-scroll">
      <table className="table">
        <thead><tr><th scope="col">对照</th><th scope="col" className="num">回合</th><th scope="col" className="num">完全一致</th><th scope="col" className="num">差一档内</th><th scope="col" className="num">κ</th><th scope="col" className="num">加权 κ</th></tr></thead>
        <tbody>
          {rows.map(([name, a]) => (
            <tr key={name}><td>{name}</td><td className="num">{a.n}</td><td className="num">{a.n ? pct(a.exact) : "—"}</td><td className="num">{a.n ? pct(a.within1) : "—"}</td><td className="num">{k2(a.kappa)}</td><td className="num">{k2(a.weighted)}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Results({ s }: { s: Summary }) {
  const h = s.human;
  const human: [string, Agree][] = [["老师和大模型（原始）", h.llmRaw], ["老师和大模型（约束后）", h.llmFinal], ["老师和规则", h.rule]];
  if (h.betweenRaters.n) human.push(["两位老师之间", h.betweenRaters]);
  return (
    <div className="stack">
      <Card title="老师盲评" sub="老师按同一个量规评同样的材料" bodyClass="">
        {h.rated === 0 ? <p className="card-body note">还没有评过。</p> : (
          <>
            <AgreeTable rows={human} />
            <p className="card-foot">
              {h.adjusted.n ? `约束下调过的 ${h.adjusted.n} 个：下调后离老师更近 ${h.adjusted.closer}、一样 ${h.adjusted.same}、更远 ${h.adjusted.farther}。` : ""}
              评了 {h.rated} 个，看不出来 {h.unsure} 个，{h.raters} 位老师。加权 κ 按二次加权，差两档比差一档扣得多。
            </p>
          </>
        )}
      </Card>
      <Card title="大模型和规则" sub="同一个回合，大模型读学生原话评的等级和只看事实的规则给的等级" bodyClass="">
        {s.agreement.n === 0 ? <p className="card-body note">还没有两者都给了等级的回合。</p> : <AgreeTable rows={[["大模型（原始）和规则", s.agreement]]} />}
        <p className="card-foot">规则看不出“能迁移”，也看不出说法本身错没错，所以两者不会完全一致；太低时要检查评估的提示词。</p>
      </Card>
    </div>
  );
}

function Blind({ cls, d, onDone }: { cls: ClassInfo; d: RatingData; onDone: (next: RatingData) => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const pick = async (level: number | null) => {
    if (!d.item || busy) return;
    setBusy(true);
    try {
      onDone(await post<RatingData>(`/api/teacher/classes/${cls.key}/rating`, { assessmentId: d.item.id, level, shownAt: d.item.shownAt }));
      setErr(null);
      window.scrollTo({ top: 0 });
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (!d.item) {
    return (
      <div className="card">
        <Empty icon={CircleCheck} title={d.total ? "这个班的回合都评过了" : "还没有可评的回合"}>
          {d.total ? `共 ${d.total} 个。` : "学生在对话里求助、回合结束以后，会出现在这里。"}
        </Empty>
      </div>
    );
  }
  const it = d.item;
  return (
    <div className="rate">
      <Card title={`${it.kp} · ${it.skill}`} sub={it.problem || "没有绑定到题"} action={<span className="pill num">已评 {d.rated} / {d.total}</span>}>
        {it.facts.length > 0 && <ul className="rate-facts">{it.facts.map((f) => <li key={f}>{f}</li>)}</ul>}
        <ol className="rate-dialog" aria-label="对话">
          {it.lines.map((x, i) => (
            <li key={i} className={x.who === "学生" ? "rate-student" : "rate-agent"}>
              <span className="rate-who">{x.who}</span>
              <p>{x.text}</p>
            </li>
          ))}
        </ol>
      </Card>
      <section className="card card-body rate-pick" aria-label="评一档">
        <h2 className="rate-q">学生对这个技能理解到哪一步？</h2>
        <div className="rate-levels">
          {d.levels.map((l) => (
            <button key={l.level} type="button" className="rate-level" disabled={busy} onClick={() => pick(l.level)}>
              <span className="rate-num num">{l.level}</span>
              <strong>{l.name}</strong>
              <small>{l.hint}</small>
            </button>
          ))}
        </div>
        <button type="button" className="btn btn-ghost rate-unsure" disabled={busy} onClick={() => pick(null)}>看不出来</button>
        {err && <p className="error-text" role="alert">{err}</p>}
      </section>
    </div>
  );
}

export default function Rating({ cls }: { cls: ClassInfo }) {
  const data = useLoad(() => get<RatingData>(`/api/teacher/classes/${cls.key}/rating`), [cls.key], 0, false, `t:rating:${cls.key}`);
  const [view, setView] = useState<"blind" | "results">("blind");
  const [cur, setCur] = useState<RatingData | null>(null);
  useEffect(() => { setCur(null); }, [cls.key]);
  const d = cur ?? data.data;

  if (data.error && !d) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <div className="card card-body"><Loading what="正在取一个回合" lines={6} /></div>;
  const summary = data.data?.summary;

  return (
    <div className="stack">
      <Segmented label="看什么" value={view} onChange={(v) => { setView(v); if (v === "results") { setCur(null); data.reload(); } }}
        options={[{ value: "blind", label: "盲评" }, { value: "results", label: "一致率" }]} />
      {view === "blind" ? <Blind cls={cls} d={d} onDone={setCur} /> : summary ? <Results s={summary} /> : <Loading lines={4} />}
    </div>
  );
}
