// 数据源：这门课的 XDOJ 账号的同步状态、标注用的表。两门课分开登录，这里只有登录的这门课。
// 同步全部是自动的：名单和作业每两小时一次（发现不认识的学生、作业时马上一次），新提交几十秒内就到，不用点任何按钮。
import { Database, Server } from "lucide-react";
import { get } from "../../api";
import { Card, Empty, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { shortTime } from "../../lib/time";
import { Code } from "./common";

interface SourceRow {
  id: number; server: string; serverCode: string; account: string; enabled: boolean; fromEnv: boolean; demo: boolean; classes: number;
  lastLogin: string | null; lastPoll: string | null; lastMeta: string | null; pollFailures: number; submissions: number;
  pendingDetails: number; backlog: Record<string, number>; requestOkRate: number | null;
}
interface SourcesData {
  sources: SourceRow[]; interval: number;
  known: { code: string; name: string; course: string; baseUrl: string | null; basis: string }[];
  daemon: { running: boolean; lastBeat: string | null; activity: string };
  candidates: { kind: string; name: string; description: string; nearest: string[]; time: string }[];
  libraries: { errors: number; skills: number; knowledge: number; errorVersion: string; taxonomyVersion: string };
}

function sourceState(src: SourceRow): { text: string; cls: string } {
  if (src.demo) return { text: "演示数据", cls: "pill pill-info" };
  if (!src.enabled) return { text: "没有启用", cls: "pill" };
  if (src.pollFailures >= 3) return { text: `连不上（连续失败 ${src.pollFailures} 次）`, cls: "pill pill-fail" };
  return { text: "正常", cls: "pill pill-pass" };
}

export default function Sources() {
  const data = useLoad(() => get<SourcesData>("/api/teacher/sources"), [], 60000);   // 同步状态页：心跳不算“数据变了”，自己一分钟问一次
  const d = data.data;
  if (data.error) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <div className="card card-body"><Loading what="正在加载" lines={4} /></div>;
  const real = d.sources.filter((x) => !x.demo);
  return (
    <div className="stack">
      {!d.daemon.running && real.length > 0 && (
        <div className="problem" role="alert">
          <span><b>同步停了</b>：班级、名单和提交暂时不会更新。请重新启动程序；还不行的话把启动窗口里的报错发给管理员。</span>
        </div>
      )}
      {d.sources.length === 0 && <div className="card"><Empty icon={Database} title="还没有数据源" /></div>}
      {d.sources.map((src) => {
        const st = sourceState(src);
        return (
          <Card key={src.id} title={<><Server size={16} aria-hidden="true" /> {src.server}</>} sub={<>账号 <Code>{src.account}</Code></>}
            action={<span className={st.cls}>{st.text}</span>}>
            {src.pollFailures >= 3 && (
              <div className="problem" role="alert"><span>连不上 XDOJ，已经连续失败 {src.pollFailures} 次。请检查网络，或者确认 XDOJ 的账号密码没有改过。</span></div>
            )}
            <div className="source-grid">
              <div><span className="eyebrow">名下的班级</span><b className="num">{src.classes}</b></div>
              <div><span className="eyebrow">已同步的提交</span><b className="num">{src.submissions}</b></div>
              <div><span className="eyebrow">上次同步名单和作业</span><b>{src.lastMeta ? shortTime(src.lastMeta) : "—"}</b></div>
            </div>
            <p className="note">名单和作业每两小时自动同步一次，学生刚加进班、刚布置的作业会自动补上；新提交几十秒内自动同步。</p>
          </Card>
        );
      })}

      {d.candidates.length > 0 && (
        <Card title="候选的新错误类型" sub="自动标注时库里没有合适编码的情况" bodyClass="">
          <div className="table-scroll">
            <table className="table">
              <thead><tr><th scope="col">候选名称</th><th scope="col">描述</th><th scope="col">最接近的现有编码</th><th scope="col">时间</th></tr></thead>
              <tbody>{d.candidates.map((c, i) => <tr key={i}><td className="cell-title">{c.name}</td><td>{c.description}</td><td><Code>{c.nearest.join("、")}</Code></td><td className="nowrap muted">{shortTime(c.time)}</td></tr>)}</tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
