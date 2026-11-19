// 老师盲评“理解”（app/rating.py）：看和大模型同样的材料，按同一个量规评。不显示学生是谁，也不显示大模型和规则的等级。
// 选一档就记下、换下一条；一致率在“模型对比”。
import { useEffect, useState } from "react";
import { CircleCheck } from "lucide-react";
import { get, post } from "../../api";
import { Card, Empty, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import type { ClassInfo } from "./common";

interface Item {
  id: number; kp: string; skill: string; problem: string; facts: string[];
  lines: { who: string; text: string }[]; shownAt: number;
}
interface RatingData { item: Item | null; rated: number; total: number; levels: { level: number; name: string; hint: string }[] }

export default function Rating({ cls }: { cls: ClassInfo }) {
  const data = useLoad(() => get<RatingData>(`/api/teacher/classes/${cls.key}/rating`), [cls.key]);
  const [cur, setCur] = useState<RatingData | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setCur(null); setErr(null); }, [cls.key]);
  const d = cur ?? data.data;

  if (data.error && !d) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <div className="card card-body"><Loading what="正在取一个回合" lines={6} /></div>;

  const pick = async (level: number | null) => {
    if (!d.item || busy) return;
    setBusy(true);
    try {
      setCur(await post<RatingData>(`/api/teacher/classes/${cls.key}/rating`, { assessmentId: d.item.id, level, shownAt: d.item.shownAt }));
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
          {d.total ? `共 ${d.total} 个。一致率见“模型对比”。` : "学生在对话里求助、回合结束以后，会出现在这里。"}
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
