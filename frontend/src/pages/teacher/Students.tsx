// 学生列表：可以搜、可以按任何一列排序，点名字看明细。
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowDown, ArrowUp, ArrowUpDown, Copy, Search } from "lucide-react";
import { get } from "../../api";
import { Avatar, Card, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { shortTime } from "../../lib/time";
import { ScoreBadge, type ClassInfo } from "./common";

interface StudentRow {
  userId: string; name: string; tried: number; passed: number; submissions: number; avgToPass: number | null;
  weak: number; mastered: number; stuck: number; stubborn: number; rounds: number; lastActive: string | null;
  orderPct: number | null; copies: number; activeDays: number;
  score: number | null;   // 掌握分：练过的知识点的掌握程度估计的平均（做题和会话都算），0~100
}
type SortKey = keyof StudentRow;

// 先看掌握和进度，再看卡在哪，最后是活跃程度
const COLUMNS: { key: SortKey; label: string; num: boolean; title?: string }[] = [
  { key: "name", label: "学生", num: false },
  { key: "score", label: "掌握分", num: true, title: "练过的知识点的掌握程度平均，0~100。做题（一次做对、交几次改对、还没过）和会话里智能体评的理解都算在里面" },
  { key: "passed", label: "已通过", num: true },
  { key: "avgToPass", label: "平均几次改对", num: true },
  { key: "weak", label: "待加强", num: true, title: "待加强的知识点个数" },
  { key: "stuck", label: "卡点", num: true },
  { key: "rounds", label: "求助回合", num: true },
  { key: "lastActive", label: "最近提交", num: false },
  { key: "submissions", label: "提交", num: true },
  { key: "activeDays", label: "活跃天数", num: true },
  { key: "orderPct", label: "提交早晚", num: true, title: "每道题第一次提交在班里的先后，换成百分位后取平均：越小越早。Qu 等（2019）发现它和成绩明显相关" },
];

export default function Students({ cls }: { cls: ClassInfo }) {
  const data = useLoad(() => get<{ total: number; students: StudentRow[] }>(`/api/teacher/classes/${cls.key}/students`), [cls.key], 60000, false, `t:students:${cls.key}`);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "userId", dir: 1 });
  const [q, setQ] = useState("");
  const rows = useMemo(() => {
    const needle = q.trim();
    const list = (data.data?.students ?? []).filter((s) => !needle || s.name.includes(needle) || s.userId.includes(needle));
    list.sort((a, b) => {
      const x = a[sort.key], y = b[sort.key];
      if (x === y) return a.userId < b.userId ? -1 : 1;
      if (x === null) return 1;
      if (y === null) return -1;
      return (x < y ? -1 : 1) * sort.dir;
    });
    return list;
  }, [data.data, sort, q]);
  if (data.error) return <Problem message={data.error} onRetry={data.reload} />;
  if (!data.data) return <div className="card card-body"><Loading what="正在取学生名单" lines={6} /></div>;
  const total = data.data.total;

  return (
    <Card bodyClass="" className="table-card">
      <div className="table-tools">
        <label className="search">
          <Search size={16} aria-hidden="true" />
          <span className="sr-only">按姓名或学号找</span>
          <input className="field" value={q} onChange={(e) => setQ(e.target.value)} placeholder="按姓名或学号找" />
        </label>
        <span className="note">{rows.length} 人 · 这个班布置过 {total} 道题</span>
      </div>
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              {COLUMNS.map((c) => {
                const on = sort.key === c.key;
                return (
                  <th key={c.key} scope="col" className={c.num ? "num" : undefined} title={c.title}
                    aria-sort={on ? (sort.dir === 1 ? "ascending" : "descending") : undefined}>
                    <button type="button" onClick={() => setSort({ key: c.key, dir: on ? (sort.dir === 1 ? -1 : 1) : c.num ? -1 : 1 })}>
                      {c.label}
                      {on ? (sort.dir === 1 ? <ArrowUp aria-hidden="true" /> : <ArrowDown aria-hidden="true" />) : <ArrowUpDown aria-hidden="true" className="sort-idle" />}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.userId}>
                <td>
                  <Link to={`/teacher/students/${r.userId}`} className="who">
                    <Avatar name={r.name || "?"} />
                    <span>
                      <span className="cell-title">{r.name || "（没有姓名）"}</span>
                      <span className="cell-sub">{r.userId}</span>
                    </span>
                  </Link>
                </td>
                <td className="num">{r.score === null ? <span className="muted">—</span> : <ScoreBadge score={r.score} />}</td>
                <td className="num">
                  <span className="mini-progress">
                    <span className="num">{r.passed}/{total}</span>
                    <span className="bar bar-pass" aria-hidden="true"><span style={{ width: `${total ? (100 * r.passed) / total : 0}%` }} /></span>
                  </span>
                </td>
                <td className="num">{r.avgToPass === null ? "—" : `${r.avgToPass} 次`}</td>
                <td className="num">{r.weak ? <span className="pill pill-fail">{r.weak}</span> : <span className="muted">0</span>}</td>
                <td className="num">{r.stuck}{r.stubborn > 0 && <span className="pill pill-warn" style={{ marginLeft: 6 }}>顽固 {r.stubborn}</span>}</td>
                <td className="num">{r.rounds}</td>
                <td className="nowrap muted">{r.lastActive ? shortTime(r.lastActive) : "还没提交"}</td>
                <td className="num">{r.submissions}</td>
                <td className="num">{r.activeDays}</td>
                <td className="num">{r.orderPct === null ? "—" : r.orderPct}{r.copies > 0 && <span className="pill pill-warn" style={{ marginLeft: 6 }} title="通过的代码和更早的同学雷同"><Copy aria-hidden="true" />{r.copies}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
