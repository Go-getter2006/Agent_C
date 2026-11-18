// 学情页：几个数、接下来做什么、知识点掌握、常见错误、每周提交、作业进度。
// 只用档位和文字，不显示概率、排名、细技能编码。
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { BookOpen, CircleAlert, CircleCheck, ClipboardList, Gauge, ListChecks, RotateCcw } from "lucide-react";
import { api } from "../api";
import { ColumnChart, StackedBar } from "../components/charts";
import { StudentShell } from "../components/Shell";
import { Card, Empty, Facts, LEVELS, LevelLegend, Loading, Meter, Problem, levelClass } from "../components/ui";
import { useLoad } from "../lib/hooks";
import { deadline, shortTime } from "../lib/time";
import { useSession } from "../session";
import type { KnowledgeItem } from "../types";

const STATUS_PILL: Record<string, string> = { 已改正: "pill pill-pass", 改正后又出现: "pill pill-fail", 还没解决: "pill pill-warn" };
const LEVEL_COLORS = ["var(--lv-mastered)", "var(--lv-basic)", "var(--lv-weak)", "var(--lv-none)"];

function KpTile({ kp, onPractice }: { kp: KnowledgeItem; onPractice: (key: string) => void }) {
  return (
    <div className={`kp-tile ${levelClass(kp.level)}${kp.covered ? "" : " kp-uncovered"}`}>
      <div className="kp-tile-top">
        <strong>{kp.name}</strong>
        <Meter level={kp.level} segments={kp.segments} />
      </div>
      <span className="kp-basis">{kp.basis}</span>
      {kp.dims && kp.dims.length > 0 && (
        <span className="kp-dims">
          {kp.dims.map((d) => (
            <span key={d.name} className="kp-dim" title={d.hint}>
              {d.name}
              <span className="kp-dim-bars" role="img" aria-label={`${d.name}：三段里的第 ${["一", "二", "三"][d.segments - 1]}段（${d.hint}）`}>
                {[1, 2, 3].map((i) => <i key={i} className={i <= d.segments ? "on" : undefined} />)}
              </span>
            </span>
          ))}
        </span>
      )}
      {kp.insight && <span className="kp-insight">{kp.insight}</span>}
      {kp.practice && (
        <button type="button" className="link kp-practice" onClick={() => onPractice(kp.practice!.key)}
          aria-label={`去练一题：${kp.practice.title}`}>
          去练一题：{kp.practice.title}
        </button>
      )}
    </div>
  );
}

export default function Progress() {
  const { me } = useSession();
  const navigate = useNavigate();
  const progress = useLoad(() => api.progress(), [], 60000);
  const [all, setAll] = useState(false);
  const p = progress.data;

  const practice = (key: string) => {
    void api.event("practice", key);
    navigate(`/chat/${key}`);
  };

  const levelCounts = p ? LEVELS.slice(0, 3).map((l) => p.summary.levels[l] ?? 0) : [];
  const uncovered = p ? p.knowledge.reduce((n, g) => n + g.items.filter((k) => !k.covered).length, 0) : 0;

  return (
    <StudentShell>
      <main className="page">
        <div className="page-inner">
          <div className="page-head">
            <div>
              <h1>{me?.name ? `${me.name}的学情` : "学情"}</h1>
              <p className="page-sub">根据你在 XDOJ 上的提交、对话和小题整理。只给档位，不打分，也不和同学比。</p>
            </div>
          </div>

          {progress.loading && !p && <div className="card card-body"><Loading what="正在整理学情" /></div>}
          {progress.error && <Problem message={progress.error} onRetry={progress.reload} />}

          {p && (
            <div className="stack">
              <Facts label="做题情况" items={[
                {
                  label: "作业里的题", value: <>已通过 {p.summary.passed} 题，共 {p.summary.total} 题</>,
                  note: <span className="bar bar-pass" aria-hidden="true"><span style={{ width: `${p.summary.total ? (100 * p.summary.passed) / p.summary.total : 0}%` }} /></span>,
                },
                {
                  label: "第一次提交就通过", value: `${p.activity.firstTryPassed} 题`,
                  note: p.summary.firstTryRate === null ? "还没有数据" : `占做过的 ${p.activity.tried} 题的 ${p.summary.firstTryRate}%`,
                },
                { label: "平均每题提交", value: p.summary.avgSubmissions === null ? "还没有数据" : `${p.summary.avgSubmissions} 次`, note: `做过 ${p.activity.tried} 题` },
                {
                  label: "本周提交", value: `${p.summary.thisWeek} 次`,
                  note: p.summary.reviewDue ? <>上周 {p.summary.lastWeek} 次。<Link to="/review" className="link">有 {p.summary.reviewDue} 项该复习了</Link></> : `上周 ${p.summary.lastWeek} 次`,
                },
              ]} />

              <div className="grid-main">
                <div className="stack">
                  <Card title="接下来做什么" sub="按要紧程度排，最多三件">
                    {p.next.length === 0 ? (
                      <Empty icon={CircleCheck} title="目前没有特别要补的">继续做作业里的题就好。</Empty>
                    ) : (
                      <ol className="next-list">
                        {p.next.map((item, i) => (
                          <li key={i} className="next-item">
                            <span className={item.kind === "review" ? "next-icon next-icon-review" : "next-icon"}>
                              {item.kind === "review" ? <RotateCcw size={17} aria-hidden="true" /> : <BookOpen size={17} aria-hidden="true" />}
                            </span>
                            <div className="next-main">
                              <strong>{item.kind === "review" ? item.title : item.problem.title}</strong>
                              <span className="note">{item.reason}</span>
                            </div>
                            {item.kind === "review" ? (
                              <Link to="/review" className="btn btn-sm btn-primary">去复习</Link>
                            ) : (
                              <button type="button" className="btn btn-sm" onClick={() => practice(item.problem.key)}>打开这道题</button>
                            )}
                          </li>
                        ))}
                      </ol>
                    )}
                  </Card>

                  {p.foundation && p.foundation.length > 0 && (
                    <Card title="要补的 C 语言基础" sub="作业里要学的内容会用到这些，按你在 C 语言课上的情况看还薄弱">
                      <ul className="found-list">
                        {p.foundation.map((f) => (
                          <li key={f.name}>
                            <div className="kp-tile-top">
                              <strong>{f.name}</strong>
                              <Meter level={f.level} segments={f.segments} />
                            </div>
                            <span className="note">{f.text}</span>
                          </li>
                        ))}
                      </ul>
                    </Card>
                  )}

                  <Card title="知识点掌握" sub={`作业里涉及到的知识点。${all ? "" : uncovered ? `还有 ${uncovered} 个作业里没练到，先收起来了。` : ""}`}
                    action={uncovered > 0 ? (
                      <button type="button" className="btn btn-sm" aria-pressed={all} onClick={() => setAll(!all)}>
                        {all ? "只看练过的" : "显示全部"}
                      </button>
                    ) : null}>
                    <div className="level-summary">
                      <StackedBar counts={levelCounts} colors={LEVEL_COLORS} names={LEVELS} />
                      <div className="level-counts">
                        {LEVELS.slice(0, 3).map((l, i) => (
                          <span key={l}><i className={`swatch sw-${levelClass(l).slice(3)}`} />{l}<b className="num">{levelCounts[i]}</b></span>
                        ))}
                      </div>
                    </div>
                    {p.knowledge.length === 0 && <p className="note">还没有可以统计的题目。</p>}
                    {p.knowledge.map((group) => {
                      const items = all ? group.items : group.items.filter((k) => k.covered);
                      if (items.length === 0) return null;
                      return (
                        <div key={group.topic} className="kp-group">
                          <h3>{group.topic}</h3>
                          <div className="kp-grid">
                            {items.map((kp) => <KpTile key={kp.name} kp={kp} onPractice={practice} />)}
                          </div>
                        </div>
                      );
                    })}
                    <LevelLegend />
                  </Card>
                </div>

                <div className="stack">
                  <Card title="每周提交" sub="最近八周">
                    <ColumnChart label="最近八周每周的提交次数和新通过的题数"
                      labels={p.activity.weeks.map((w) => w.label)}
                      series={[{ name: "提交次数", color: "var(--series-1)" }, { name: "新通过的题", color: "var(--series-2)" }]}
                      values={[p.activity.weeks.map((w) => w.submissions), p.activity.weeks.map((w) => w.passed)]}
                      emphasizeLast height={180} />
                  </Card>

                  <Card title="常见错误" sub="最近 30 天，同一个问题出现几次">
                    {p.errors.length === 0 ? <Empty icon={CircleCheck} title="最近 30 天没有记录到错误" /> : (
                      <ul className="err-list">
                        {p.errors.map((e) => (
                          <li key={e.name}>
                            <div className="err-top">
                              <strong>{e.name}</strong>
                              <span className={STATUS_PILL[e.status] ?? "pill"}>{e.status === "已改正" ? <CircleCheck aria-hidden="true" /> : <CircleAlert aria-hidden="true" />}{e.status}</span>
                            </div>
                            <span className="note">
                              {e.count} 次，最近一次在 <Link to={`/chat/${e.last.key}`} className="link">{e.last.title}</Link>，{shortTime(e.lastTime)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </Card>

                  <Card title="作业进度">
                    <ul className="hw-list">
                      {p.activity.homeworks.map((hw) => (
                        <li key={hw.key}>
                          <div className="hw-top">
                            <strong><ClipboardList size={15} aria-hidden="true" />{hw.title}</strong>
                            <span className="num">{hw.passed}/{hw.total}</span>
                          </div>
                          <span className="bar bar-pass" role="img" aria-label={`${hw.total} 题通过 ${hw.passed} 题`}>
                            <span style={{ width: `${hw.total ? (100 * hw.passed) / hw.total : 0}%` }} />
                          </span>
                          <span className="note">{deadline(hw.endTime, hw.ended)}</span>
                        </li>
                      ))}
                    </ul>
                    <div className="hw-facts">
                      <span><Gauge size={14} aria-hidden="true" />做过 {p.activity.tried} 题，{p.activity.passed} 题已通过</span>
                      <span><ListChecks size={14} aria-hidden="true" />平均每题提交 {p.activity.avgSubmissions ?? "—"} 次</span>
                    </div>
                  </Card>
                </div>
              </div>
            </div>
          )}
        </div>
      </main>
    </StudentShell>
  );
}
