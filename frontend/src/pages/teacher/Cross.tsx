// 跨课程（只在数据结构这边）：C 基础预警、跨课程联系（类型、来源、数据检验、采用与否）。
// 全部自动：智能体每天夜里补充联系、用数据检验、按结果采用或不用；老师可以改，但不用去点。
// 只有 C 语言知识点的掌握程度，没有 C 语言的答题记录。
import { useMemo, useState } from "react";
import { Check, CircleAlert, X } from "lucide-react";
import { get, post } from "../../api";
import { Card, Empty, Loading, Problem, Segmented } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { StaffLink, Code, pct, useClasses, type ClassInfo } from "./common";

interface SkillSide { code: string; name: string; kp: string; kpCode?: string }
interface CrossLinkRow {
  id: number; c: SkillSide; d: SkillSide; relation: string; relationName: string; source: string; status: string;
  rationale: string; confidence: number | null; nWith: number; nWithout: number; rateWith: number | null;
  rateWithout: number | null; probSupport: number | null; verdict: string; weight: number; checkedAt: string | null;
  decidedBy: "teacher" | "auto" | "";
}
interface CrossData {
  class: { key: string; name: string };
  focus: { code: string; name: string }[];
  warning: {
    total: number;
    topics: { code: string; name: string; weak: number; noData: number; supports: string[] }[];
    students: { userId: string; name: string; weak: { code: string; name: string; level: string; p: number | null; supports: string[] }[] }[];
  };
  links: CrossLinkRow[];
  relations: { code: string; name: string; weight: number }[];
  params: { n0: number; mastered: number; minN: number; supportProb: number; reliability: Record<string, number> };
  dKps: { code: string; name: string }[];
}

const SOURCE: Record<string, string> = { seed: "人工初稿", llm: "智能体提出", teacher: "老师添加" };
const STATUS: Record<string, string> = { accepted: "采用", proposed: "待检验", rejected: "不用" };
const VERDICT: Record<string, { text: string; cls: string }> = {
  supported: { text: "数据支持", cls: "pill pill-pass" },
  unsupported: { text: "数据不支持", cls: "pill pill-fail" },
  unknown: { text: "数据还不够", cls: "pill" },
};

function Warning({ d }: { d: CrossData }) {
  const w = d.warning;
  return (
    <Card title={<><CircleAlert size={16} aria-hidden="true" /> C 基础预警</>}
      sub={d.focus.length ? `按这个班作业涉及的知识点（${d.focus.map((f) => f.name).join("、")}）看要用到的 C 语言基础` : "这个班还没有作业，先按全部数据结构知识点看"}>
      {w.topics.length === 0 ? <p className="note">{w.total} 名学生里，没有发现薄弱的 C 基础（没有 C 语言数据的学生不算在内）。</p> : (
        <div className="grid-2">
          <div>
            <h3 className="sub-head">全班最薄弱的 C 基础</h3>
            <table className="table">
              <thead><tr><th scope="col">C 语言知识点</th><th scope="col" className="num">薄弱人数</th><th scope="col">影响</th></tr></thead>
              <tbody>
                {w.topics.slice(0, 8).map((t) => (
                  <tr key={t.code}>
                    <td className="cell-title">{t.name} <Code>{t.code}</Code></td>
                    <td className="num">{t.weak} / {w.total}{t.noData ? <span className="muted">（{t.noData} 人没有数据）</span> : null}</td>
                    <td className="muted">{t.supports.join("、")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div>
            <h3 className="sub-head">需要先补基础的学生</h3>
            <ul className="rv-list">
              {w.students.slice(0, 10).map((st) => (
                <li key={st.userId}>
                  <div className="rv-top"><StaffLink className="link" to={`/students/${st.userId}`}>{st.name}</StaffLink><span className="note">{st.weak.length} 处</span></div>
                  <span className="note">{st.weak.map((x) => `${x.name}（${x.level}${x.p !== null ? ` ${x.p.toFixed(2)}` : ""}）`).join("、")}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </Card>
  );
}

function Links({ d, reload }: { d: CrossData; reload: () => void }) {
  const [show, setShow] = useState<"all" | "proposed" | "accepted" | "rejected">("all");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const rows = useMemo(() => d.links.filter((l) => show === "all" || l.status === show), [d.links, show]);
  const pending = d.links.filter((l) => l.status === "proposed").length;

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true);
    setNote(null);
    try {
      await fn();
      if (ok) setNote(ok);
      reload();
    } catch (err) {
      setNote((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="跨课程联系" sub={`C 语言的技能和数据结构的技能之间带类型的联系，全部自动维护：智能体每天夜里补充，数据检验以后自动采用或不用。只有“采用”的进入数据结构的估计；分量 = 类型 × 数据检验（${Object.entries(d.params.reliability).map(([k, v]) => `${VERDICT[k]?.text ?? k} ${v}`).join("，")}）`}
      bodyClass="">
      <div className="card-body cross-tools">
        <Segmented label="显示" value={show} onChange={setShow}
          options={[{ value: "all", label: "全部" }, { value: "proposed", label: `待检验${pending ? ` ${pending}` : ""}` }, { value: "accepted", label: "采用" }, { value: "rejected", label: "不用" }]} />
        {note && <span className="note" role="status">{note}</span>}
      </div>
      {rows.length === 0 ? <Empty title="没有" /> : (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">C 语言</th><th scope="col">关系</th><th scope="col">数据结构</th><th scope="col">来源</th>
                <th scope="col">数据检验</th><th scope="col" className="num">分量</th><th scope="col">状态</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((l) => (
                <tr key={l.id}>
                  <td><span className="cell-title">{l.c.name}</span><span className="cell-sub">{l.c.kp} · <Code>{l.c.code}</Code></span></td>
                  <td className="nowrap">{l.relationName}</td>
                  <td><span className="cell-title">{l.d.name}</span><span className="cell-sub">{l.d.kp} · <Code>{l.d.code}</Code></span></td>
                  <td><span className="nowrap">{SOURCE[l.source] ?? l.source}</span>{l.rationale && <span className="cell-sub">{l.rationale}</span>}</td>
                  <td>
                    <span className={VERDICT[l.verdict]?.cls ?? "pill"}>{VERDICT[l.verdict]?.text ?? l.verdict}</span>
                    {(l.nWith + l.nWithout) > 0 && (
                      <span className="cell-sub num">
                        C 掌握了 {pct(l.rateWith)}（{l.nWith}）· 没掌握 {pct(l.rateWithout)}（{l.nWithout}）{l.probSupport !== null ? ` · P=${l.probSupport.toFixed(2)}` : ""}
                      </span>
                    )}
                  </td>
                  <td className="num">{l.status === "accepted" ? l.weight.toFixed(2) : "—"}</td>
                  <td className="nowrap">
                    <span className={l.status === "accepted" ? "pill pill-pass" : l.status === "proposed" ? "pill pill-warn" : "pill"}>{STATUS[l.status]}</span>
                    {l.decidedBy && <span className="cell-sub">{l.decidedBy === "teacher" ? "老师改的" : "自动"}</span>}
                    {l.status !== "accepted" && (
                      <button type="button" className="icon-btn" disabled={busy} aria-label="改成采用" title="改成采用（可选）"
                        onClick={() => void act(() => post(`/api/teacher/cross/${l.id}/review`, { action: "accept" }))}><Check size={16} /></button>
                    )}
                    {l.status !== "rejected" && (
                      <button type="button" className="icon-btn" disabled={busy} aria-label="改成不用" title="改成不用（可选）"
                        onClick={() => void act(() => post(`/api/teacher/cross/${l.id}/review`, { action: "reject" }))}><X size={16} /></button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="card-foot relation-help">
        <b>关系是什么意思</b>（和 TransKT 的四种关系一致；箭头都是从 C 语言指向数据结构）：
        <span><i>前置</i>学数据结构的这一点之前，要先会 C 的那一点（比如先会指针，才能写链表结点）</span>
        <span><i>用到</i>数据结构的这一点里直接用到 C 的那一点（比如循环队列的下标用到取模）</span>
        <span><i>组成部分</i>C 的那一点是它的一部分（比如顺序表插入里包含了数组元素后移）</span>
        <span><i>是一种</i>C 的那一点是它的一个特例或一种具体做法（比如冒泡排序是交换排序的一种）</span>
      </div>
      <p className="card-foot">
        数据检验：C 那头估计 ≥ {d.params.mastered} 算掌握了，比较两组学生在数据结构这头新题一次做对的比例；两组合起来至少 {d.params.minN} 次才下结论，
        “掌握了的更容易做对”的后验概率 ≥ {d.params.supportProb} 算数据支持。数据结构的估计 = η·C 那头给的先验 + (1−η)·自己的证据，
        η = n0 / (n0 + 2 + 证据量)，n0 = {d.params.n0} × 联系的分量：刚开课时主要看 C 基础，做的题越多越由数据结构自己的表现决定。
      </p>
    </Card>
  );
}

export default function Cross({ cls }: { cls: ClassInfo }) {
  const { tick } = useClasses();   // 数据变了才重取（common.tsx 的 useVersionTicks）
  const data = useLoad(() => get<CrossData>(`/api/teacher/classes/${cls.key}/cross`), [cls.key, tick], 0, false, `t:cross:${cls.key}`, String(tick));
  if (data.error) return <Problem message={data.error} onRetry={data.reload} />;
  if (!data.data) return <div className="card card-body"><Loading what="正在取跨课程的数据" lines={5} /></div>;
  return (
    <div className="stack">
      <Warning d={data.data} />
      <Links d={data.data} reload={data.reload} />
    </div>
  );
}
