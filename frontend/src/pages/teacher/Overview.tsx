// 班级总览：这周班里怎么样，先关注谁、课上讲什么。
// 三个数；误区（本周 / 全部，课上要强调的错误点）；要讲的知识点（和误区对齐）；需要留意的学生（一直不做题、
// 一直卡在某处、其他）、作业完成情况；两张趋势图。全部误区在“误区”页，全部知识点在知识图谱，每个学生在学生明细。
import { useState } from "react";
import { Activity, ArrowRight, CircleAlert, CircleCheck, Copy, Hourglass, Send, Target, Users } from "lucide-react";
import { get } from "../../api";
import { ColumnChart, LineChart } from "../../components/charts";
import { Avatar, Card, Empty, Loading, Problem, Stat, Tabs } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { deadline } from "../../lib/time";
import { StaffLink, useClasses, type ClassInfo } from "./common";
import { MisconceptionGrid, PERIOD_TEXT, type Misconception, type Period } from "./Misconceptions";

interface AttentionGroup {
  key: "idle" | "stuck" | "other"; title: string; rule: string;
  students: { userId: string; name: string; reasons: string[] }[];
}

interface Overview {
  class: { key: string; name: string; students: number; activeThisWeek: number };
  kpis: {
    students: number; active: number; activeDelta: number; passed: number; passedDelta: number;
    avgToPass: number | null; avgToPassDelta: number | null;
    submissions: number; submissionsDelta: number; stuckThisWeek: number; suspicious: number;
    review: { active: number; due: number; graduated: number; lapses: number; students: number };
  };
  trend: { label: string; start: string; submissions: number; active: number; passed: number; avgToPass: number | null; rounds: number }[];
  attentionGroups: AttentionGroup[];
  misconceptions: Record<Period, { items: Misconception[]; total: number }>;
  focus: { code: string; name: string; weak: number; stuck: number; silent: number; error: { code: string; name: string; count: number } | null }[];
  homeworks: { title: string; contestId: number; problems: number; allPassed: number; started: number; endTime: string | null; ended: boolean }[];
}

const OVERVIEW_MISC = 6;   // 总览里每个范围列几条误区，其余在“误区”页

function MisconceptionCard({ d }: { d: Overview }) {
  const [period, setPeriod] = useState<Period>(d.misconceptions.week.total ? "week" : "all");
  const block = d.misconceptions[period];
  return (
    <Card title="误区" sub="课上要强调的错误点：一种错误 × 一个技能，人数多的在前"
      action={<StaffLink to={`/misconceptions${period === "all" ? "?period=all" : ""}`} className="btn btn-sm">
        全部 {block.total} 条<ArrowRight size={14} aria-hidden="true" /></StaffLink>}>
      <Tabs label="误区的范围" value={period} onChange={setPeriod}
        options={(["week", "all"] as Period[]).map((p) => ({ value: p, label: `${PERIOD_TEXT[p]}误区`, count: d.misconceptions[p].total }))} />
      <div role="tabpanel" id={`panel-${period}`} aria-labelledby={`tab-${period}`} className="misc-panel">
        {block.items.length === 0
          ? <Empty icon={CircleCheck} title={`${PERIOD_TEXT[period]}没有记到误区`} />
          : <MisconceptionGrid items={block.items.slice(0, OVERVIEW_MISC)} />}
      </div>
    </Card>
  );
}

function AttentionCard({ groups }: { groups: AttentionGroup[] }) {
  const first = groups.find((g) => g.students.length)?.key ?? "idle";
  const [tab, setTab] = useState<AttentionGroup["key"]>(first);
  const g = groups.find((x) => x.key === tab) ?? groups[0];
  return (
    <Card title="需要留意的学生" sub="按写明的规则分三类，每人附理由"
      action={<StaffLink to="/students" className="btn btn-sm">全部学生<ArrowRight size={14} aria-hidden="true" /></StaffLink>}>
      <Tabs label="需要留意的学生分类" value={tab} onChange={setTab}
        options={groups.map((x) => ({ value: x.key, label: x.title, count: x.students.length }))} />
      <div role="tabpanel" id={`panel-${g.key}`} aria-labelledby={`tab-${g.key}`}>
        <p className="note attn-rule">{g.rule}</p>
        {g.students.length === 0 ? <Empty icon={CircleCheck} title="没有这类学生" /> : (
          <ul className="attn-list">
            {g.students.map((a) => (
              <li key={a.userId}>
                <Avatar name={a.name} />
                <div className="attn-main">
                  <StaffLink to={`/students/${a.userId}`} className="attn-name">{a.name}<span className="code-tag">{a.userId}</span></StaffLink>
                  <span className="attn-reasons">
                    {a.reasons.map((r) => <span key={r} className="pill pill-warn"><CircleAlert aria-hidden="true" />{r}</span>)}
                  </span>
                </div>
                <StaffLink to={`/students/${a.userId}`} className="btn btn-sm btn-ghost" aria-label={`看${a.name}的明细`}><ArrowRight size={15} /></StaffLink>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

export default function ClassOverview({ cls }: { cls: ClassInfo }) {
  const { tick } = useClasses();   // 数据变了才重取（common.tsx 的 useVersionTicks）
  const data = useLoad(() => get<Overview>(`/api/teacher/classes/${cls.key}/overview`), [cls.key, tick], 0, false, `t:overview:${cls.key}`, String(tick));
  const d = data.data;
  if (data.error && !d) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <div className="card card-body"><Loading what="正在汇总全班的情况" lines={5} /></div>;
  const k = d.kpis;
  const total = d.class.students || 1;

  return (
    <div className="stack overview-desktop">
      <div className="stats stats-3 overview-stats">
        <Stat icon={Users} label="本周交过题" value={k.active} unit={`/${k.students} 人`} delta={k.activeDelta} deltaText=" 比上周" />
        <Stat icon={Target} label="平均几次改对" value={k.avgToPass ?? "—"} unit={k.avgToPass === null ? "" : "次"}
          delta={k.avgToPassDelta} deltaText=" 次" goodWhenUp={false} foot={`本周通过 ${k.passed} 道题，平均交到第几次通过`} />
        <Stat icon={Send} label="本周提交" value={k.submissions} unit="次"
          foot={k.suspicious ? <span className="warn-text"><Copy size={13} aria-hidden="true" />{k.suspicious} 次通过和同学代码雷同</span> : undefined} />
      </div>

      <MisconceptionCard d={d} />

      <Card title="要讲的知识点" sub="待加强、卡住过、没来问的人多的排前面"
        action={<StaffLink to="/map?view=table" className="btn btn-sm">全部知识点<ArrowRight size={14} aria-hidden="true" /></StaffLink>}>
        {d.focus.length === 0 ? <Empty icon={CircleCheck} title="目前没有特别要讲的知识点" /> : (
          <ul className="focus-list focus-grid">
            {d.focus.map((f) => (
              <li key={f.code}>
                <div className="focus-top">
                  <StaffLink to={`/map?kp=${f.code}`} className="focus-name">{f.name}</StaffLink>
                  <span className="focus-counts">
                    {f.weak > 0 && <span className="pill pill-fail">待加强 {f.weak} 人</span>}
                    {f.stuck > 0 && <span className="pill pill-warn">卡住过 {f.stuck} 人</span>}
                    {f.silent > 0 && <span className="pill">没来问 {f.silent} 人</span>}
                  </span>
                </div>
                {f.error && <span className="note">最常见的错：{f.error.name}（{f.error.count} 次）</span>}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="grid-main">
        <AttentionCard groups={d.attentionGroups} />
        <Card title="作业完成情况">
          {d.homeworks.length === 0 ? <Empty title="这个班还没有作业" /> : (
            <ul className="hw-list">
              {d.homeworks.map((h) => (
                <li key={h.contestId}>
                  <div className="hw-top">
                    <strong>{h.title}</strong>
                    <span className="note">{h.problems} 题 · {deadline(h.endTime, h.ended)}</span>
                  </div>
                  <span className="bar bar-pass" role="img" aria-label={`${d.class.students} 人里 ${h.allPassed} 人全部通过`}>
                    <span style={{ width: `${(100 * h.allPassed) / total}%` }} />
                  </span>
                  <span className="hw-facts-row note">
                    <span><CircleCheck size={13} aria-hidden="true" />全部通过 {h.allPassed} 人</span>
                    <span><Activity size={13} aria-hidden="true" />开始做 {h.started} 人</span>
                    <span><Hourglass size={13} aria-hidden="true" />还没动 {Math.max(0, d.class.students - h.started)} 人</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid-2">
        <Card title="每周提交" sub="全班在 XDOJ 上的提交次数，最近八周">
          <ColumnChart label="最近八周全班每周的提交次数" labels={d.trend.map((w) => w.label)}
            series={[{ name: "提交次数", color: "var(--series-1)" }]} values={[d.trend.map((w) => w.submissions)]} unit=" 次" emphasizeLast />
        </Card>
        <Card title="平均几次改对" sub="每周通过的题平均交到第几次才通过；只说明修改过程，不能单凭次数判断掌握">
          <LineChart label="最近八周通过的题平均交几次" labels={d.trend.map((w) => w.label)} min={0} format={(v) => v.toFixed(1)} unit=" 次"
            series={[{ name: "平均几次改对", color: "var(--series-1)", values: d.trend.map((w) => w.avgToPass) }]} />
        </Card>
      </div>
      <p className="note overview-foot">正在复习 {k.review.active} 项 · {k.review.due} 项到期 · {k.review.graduated} 项已巩固</p>
    </div>
  );
}
