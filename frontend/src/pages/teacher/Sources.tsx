// 数据源：这门课的 XDOJ 账号的同步状态、标注用的表。两门课分开登录，这里只有登录的这门课。
// 同步全部是自动的：名单和作业每两小时一次（发现不认识的学生、作业时马上一次），新提交几十秒内就到，不用点任何按钮。
import { Fragment, useState } from "react";
import { ChevronDown, Database } from "lucide-react";
import { get, post } from "../../api";
import { Card, Empty, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { shortTime } from "../../lib/time";
import { Code, useClasses } from "./common";

interface SourceRow {
  id: number; server: string; serverCode: string; account: string; enabled: boolean; fromEnv: boolean; demo: boolean; classes: number;
  teacherName: string; removed: boolean;
  rosters: { key: string; name: string; removed: boolean }[];
  lastLogin: string | null; lastPoll: string | null; lastMeta: string | null; pollFailures: number; submissions: number;
  pendingDetails: number; backlog: Record<string, number>; requestOkRate: number | null;
}
interface SourcesData {
  sources: SourceRow[]; interval: number;
  known: { code: string; name: string; course: string; baseUrl: string | null; basis: string }[];
  daemon: { running: boolean; lastBeat: string | null; activity: string };
  candidates: {
    name: string; description: string; count: number; students: number; names: number;
    problems: { title: string; count: number }[]; moreProblems: number;
    nearest: { code: string; name: string }[]; first: string; last: string;
  }[];
  libraries: { errors: number; skills: number; knowledge: number; errorVersion: string; taxonomyVersion: string };
}

function sourceState(src: SourceRow): { text: string; cls: string } {
  if (src.removed) return { text: "已移除", cls: "pill" };
  if (src.demo) return { text: "演示数据", cls: "pill pill-info" };
  if (!src.enabled) return { text: "没有启用", cls: "pill" };
  if (!src.lastLogin) return { text: "待首次登录", cls: "pill pill-warn" };
  if (src.pollFailures >= 3) return { text: `连不上（连续失败 ${src.pollFailures} 次）`, cls: "pill pill-fail" };
  return { text: "正常", cls: "pill pill-pass" };
}

export default function Sources() {
  const { reload: reloadClasses } = useClasses();
  const data = useLoad(() => get<SourcesData>("/api/teacher/sources"), [], 60000, false, "t:sources");   // 同步状态页：心跳不算“数据变了”，自己一分钟问一次
  const [expanded, setExpanded] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [manageError, setManageError] = useState("");
  async function remove(path: string, details: string) {
    if (busy || !window.confirm(details)) return;
    setBusy(true);
    setManageError("");
    try {
      await post(path);
      data.setData(await get<SourcesData>("/api/teacher/sources"));
      reloadClasses();
    } catch (error) {
      setManageError(error instanceof Error ? error.message : "请求未完成，请稍后再试。");
    } finally {
      setBusy(false);
    }
  }
  const d = data.data;
  if (data.error) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <div className="card card-body"><Loading what="正在加载" lines={4} /></div>;
  const real = d.sources.filter((x) => !x.demo && x.enabled && !x.removed);
  return (
    <div className="stack">
      {manageError && <div className="problem" role="alert">{manageError}</div>}
      {!d.daemon.running && real.length > 0 && (
        <div className="problem" role="alert">
          <span><b>同步停了</b>：班级、名单和提交暂时不会更新。请重新启动程序；还不行的话把启动窗口里的报错发给管理员。</span>
        </div>
      )}
      {d.sources.length === 0 ? (
        <div className="card"><Empty icon={Database} title="本课程还没有教师">教师首次登录后会出现在这里。</Empty></div>
      ) : (
        <div className="card">
          <div className="table-scroll">
            <table className="table teacher-roster" aria-label="本课程的教师">
              <thead><tr><th scope="col">教师</th><th scope="col">班级</th><th scope="col">同步状态</th><th scope="col">操作</th></tr></thead>
              <tbody>
                {d.sources.map((src) => {
                  const status = sourceState(src);
                  const name = src.teacherName || "姓名待同步";
                  const open = expanded === src.id;
                  const rosterLabel = `查看 ${name}（账号 ${src.account}）的班级`;
                  const label = `${name}（账号 ${src.account}）`;
                  return (
                    <Fragment key={src.id}>
                      <tr className={open ? "teacher-roster-open" : undefined}>
                        <td><span className="cell-title">{name}</span>
                          <span className="cell-sub"><Code>{src.account}</Code></span></td>
                        <td>
                          <button type="button" className="btn btn-sm btn-ghost teacher-roster-toggle"
                            aria-label={rosterLabel} aria-expanded={open}
                            onClick={() => setExpanded(open ? null : src.id)}>
                            {src.classes} 个班级 <ChevronDown size={15} aria-hidden="true" />
                          </button>
                        </td>
                        <td><span className={status.cls}>{status.text}</span>
                          {src.lastMeta && <span className="cell-sub">同步于 {shortTime(src.lastMeta)}</span>}</td>
                        <td>{!src.removed && (
                          <button type="button" className="btn btn-sm teacher-roster-remove" disabled={busy}
                            aria-label={`移除教师 ${label}`}
                            onClick={() => void remove(`/api/teacher/sources/${src.id}/remove`,
                              `移除 ${label}？会停止该账号的同步，并移除仅由这位教师管理的班级。已有记录保留。`)}>
                            移除教师
                          </button>
                        )}</td>
                      </tr>
                      {open && (
                        <tr className="teacher-roster-detail">
                          <td colSpan={4}>
                            {!src.removed && src.pollFailures >= 3 && (
                              <div className="problem" role="alert"><span>连不上 XDOJ，已经连续失败 {src.pollFailures} 次。请检查网络，或者确认 XDOJ 的账号密码没有改过。</span></div>
                            )}
                            <div className="teacher-roster-source">
                              <span>{src.server}</span>
                              <span>服务器已同步 {src.submissions} 次提交</span>
                            </div>
                            <ul className="teacher-roster-classes" aria-label={`${label} 的班级`}>
                              {(src.rosters ?? []).map((cls) => (
                                <li key={cls.key}>
                                  <span className="cell-title">{cls.name || cls.key}</span>
                                  {cls.removed ? <span className="pill">已移除</span> : (
                                    <button type="button" className="btn btn-sm" disabled={busy}
                                      aria-label={`移除班级 ${cls.name || cls.key}`}
                                      onClick={() => void remove(`/api/teacher/sources/classes/${cls.key}/remove`,
                                        `移除班级「${cls.name || cls.key}」？该班会停止同步。仅在这个班有学籍的学生将无法登录本课程。已有记录保留。`)}>
                                      移除班级
                                    </button>
                                  )}
                                </li>
                              ))}
                              {!src.rosters?.length && <li className="muted">还没有同步到班级</li>}
                            </ul>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {d.candidates.length > 0 && (
        <Card title="错误库里没有的错误" sub="自动标注时找不到合适编码的，相近的归在一起；次数多的值得加进错误库" bodyClass="">
          <div className="table-scroll">
            <table className="table">
              <thead><tr><th scope="col">错误</th><th scope="col" className="num">次数</th><th scope="col" className="num">学生</th>
                <th scope="col">出现在</th><th scope="col">现在被归到</th><th scope="col">最近</th></tr></thead>
              <tbody>{d.candidates.map((c, i) => (
                <tr key={i}>
                  <td><span className="cell-title">{c.name}</span><span className="cell-sub">{c.description}{c.names > 1 ? `（${c.names} 种叫法）` : ""}</span></td>
                  <td className="num">{c.count}</td>
                  <td className="num">{c.students}</td>
                  <td>{c.problems.map((p) => `${p.title}${p.count > 1 ? ` ×${p.count}` : ""}`).join("、")}{c.moreProblems ? ` 等 ${c.problems.length + c.moreProblems} 道` : ""}</td>
                  <td>{c.nearest.map((n) => <span key={n.code} className="cand-near"><Code>{n.code}</Code> {n.name}</span>)}</td>
                  <td className="nowrap muted">{shortTime(c.last)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
