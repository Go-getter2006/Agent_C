// 班级总览：几个数、两张趋势图、需要留意的学生、常见错误、知识点分布、作业完成情况。
import { Link } from "react-router-dom";
import { Activity, ArrowRight, CircleAlert, CircleCheck, Copy, Hourglass, RotateCcw, Send, Target, Users, Waypoints } from "lucide-react";
import { get } from "../../api";
import { BarList, ColumnChart, LineChart, StackedBar } from "../../components/charts";
import { Avatar, Card, Empty, LevelLegend, Loading, Problem, Stat } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { deadline } from "../../lib/time";
import { Code, type ClassInfo, type SkillRef } from "./common";

interface Overview {
  class: { key: string; name: string; students: number; activeThisWeek: number };
  kpis: {
    students: number; active: number; activeDelta: number; firstTryRate: number | null; firstTryDelta: number | null;
    submissions: number; submissionsDelta: number; stuckThisWeek: number; suspicious: number;
    review: { active: number; due: number; graduated: number; lapses: number; students: number };
  };
  trend: { label: string; start: string; submissions: number; active: number; firstTryRate: number | null; rounds: number }[];
  attention: { userId: string; name: string; reasons: string[] }[];
  levels: string[];
  knowledge: { code: string; name: string; topic: string; counts: number[] }[];
  errors: { code: string; name: string; students: number; count: number }[];
  overconfident: (SkillRef & { n: number; wrong: number })[];
  homeworks: { title: string; contestId: number; problems: number; allPassed: number; started: number; endTime: string | null; ended: boolean }[];
}

const LEVEL_COLORS = ["var(--lv-mastered)", "var(--lv-basic)", "var(--lv-weak)", "var(--lv-none)"];

export default function ClassOverview({ cls }: { cls: ClassInfo }) {
  const data = useLoad(() => get<Overview>(`/api/teacher/classes/${cls.key}/overview`), [cls.key], 60000);
  const d = data.data;
  if (data.error) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <div className="card card-body"><Loading what="正在汇总全班的情况" lines={5} /></div>;
  const k = d.kpis;
  const total = d.class.students || 1;
  const covered = d.knowledge;
  let topic = "";

  return (
    <div className="stack">
      <div className="stats stats-5">
        <Stat icon={Users} label="本周交过题" value={k.active} unit={`/${k.students} 人`} delta={k.activeDelta} deltaText=" 比上周" />
        <Stat icon={Target} label="第一次就通过" value={k.firstTryRate ?? "—"} unit={k.firstTryRate === null ? "" : "%"}
          delta={k.firstTryDelta} deltaText=" 个百分点" foot="本周新做的题里" />
        <Stat icon={Send} label="本周提交" value={k.submissions} unit="次" delta={k.submissionsDelta} deltaText=" 比上周"
          foot={k.suspicious ? <span className="warn-text"><Copy size={13} aria-hidden="true" />{k.suspicious} 次通过和同学代码雷同</span> : undefined} />
        <Stat icon={CircleAlert} label="本周卡点" value={k.stuckThisWeek} unit="次" goodWhenUp={false}
          foot={<Link to="/teacher/stuck" className="link">看卡点名单<ArrowRight size={13} aria-hidden="true" /></Link>} />
        <Stat icon={RotateCcw} label="正在复习" value={k.review.active} unit="项"
          foot={`${k.review.students} 人 · ${k.review.due} 项到期 · ${k.review.graduated} 项已巩固`} />
      </div>

      <div className="grid-2">
        <Card title="每周提交" sub="全班在 XDOJ 上的提交次数，最近八周">
          <ColumnChart label="最近八周全班每周的提交次数" labels={d.trend.map((w) => w.label)}
            series={[{ name: "提交次数", color: "var(--series-1)" }]} values={[d.trend.map((w) => w.submissions)]} unit=" 次" emphasizeLast />
        </Card>
        <Card title="第一次就通过的比例" sub="每周新做的题里，第一次提交就通过的占多少">
          <LineChart label="最近八周第一次就通过的比例" labels={d.trend.map((w) => w.label)} min={0} max={100} unit="%"
            series={[{ name: "一次通过率", color: "var(--series-1)", values: d.trend.map((w) => w.firstTryRate) }]} />
        </Card>
      </div>

      <div className="grid-main">
        <Card title="需要留意的学生" sub="按透明的规则列出，每人附上理由；不是预测模型"
          action={<Link to="/teacher/students" className="btn btn-sm">全部学生<ArrowRight size={14} aria-hidden="true" /></Link>}>
          {d.attention.length === 0 ? (
            <Empty icon={CircleCheck} title="目前没有需要特别留意的学生">规则：反复卡住、三个以上知识点待加强、一周没交题、一次通过率明显低于全班等。</Empty>
          ) : (
            <ul className="attn-list">
              {d.attention.map((a) => (
                <li key={a.userId}>
                  <Avatar name={a.name} />
                  <div className="attn-main">
                    <Link to={`/teacher/students/${a.userId}`} className="attn-name">{a.name}<span className="code-tag">{a.userId}</span></Link>
                    <span className="attn-reasons">
                      {a.reasons.map((r) => <span key={r} className="pill pill-warn"><CircleAlert aria-hidden="true" />{r}</span>)}
                    </span>
                  </div>
                  <Link to={`/teacher/students/${a.userId}`} className="btn btn-sm btn-ghost" aria-label={`看${a.name}的明细`}><ArrowRight size={15} /></Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="最常见的错误" sub="最近 30 天，按出现的提交次数">
          {d.errors.length === 0 ? <Empty icon={CircleCheck} title="最近 30 天没有标注到错误" /> : (
            <BarList unit=" 次" rows={d.errors.slice(0, 8).map((e) => ({
              key: e.code, label: <>{e.name} <Code>{e.code}</Code></>, value: e.count, sub: `${e.students} 人`,
            }))} />
          )}
        </Card>
      </div>

      <Card title="知识点掌握分布" sub="这个班布置过的题涉及到的知识点；每一条是全班的人数分布"
        action={<Link to="/teacher/map" className="btn btn-sm"><Waypoints size={15} aria-hidden="true" />在知识图谱里看</Link>}>
        <LevelLegend levels={d.levels} />
        <div className="dist-table" role="table" aria-label="知识点掌握分布">
          {covered.map((kp) => {
            const head = kp.topic !== topic ? ((topic = kp.topic), <div key={`t-${kp.topic}`} className="dist-topic" role="row"><span role="rowheader">{kp.topic}</span></div>) : null;
            return [
              head,
              <div key={kp.code} className="dist-row" role="row">
                <span className="dist-name" role="cell">{kp.name} <Code>{kp.code}</Code></span>
                <span className="dist-bar" role="cell"><StackedBar counts={kp.counts} colors={LEVEL_COLORS} names={d.levels} total={total} /></span>
                <span className="dist-nums num" role="cell" aria-label={d.levels.map((l, i) => `${l} ${kp.counts[i]} 人`).join("，")}>
                  {kp.counts.map((n, i) => <span key={i} className={i === 2 && n > 0 ? "dist-weak" : undefined}>{n}</span>)}
                </span>
              </div>,
            ];
          })}
        </div>
      </Card>

      <div className="grid-2">
        <Card title="作业完成情况">
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
        </Card>
        <Card title="自以为会的技能" sub="小题里选了“很有把握”却答错的；数据少的时候只作参考">
          {d.overconfident.length === 0 ? <Empty icon={CircleCheck} title="目前没有" /> : (
            <BarList color="var(--series-2)" rows={d.overconfident.map((o) => ({
              key: o.code, label: <>{o.name} <Code>{o.code}</Code></>, value: o.wrong, sub: `很有把握的作答 ${o.n} 次，其中答错 ${o.wrong} 次`,
            }))} />
          )}
        </Card>
      </div>
    </div>
  );
}
