// 数据源：已知的两台 XDOJ 服务器、每个数据源的同步状态、标注用的表。
import { useState } from "react";
import { Database, RefreshCw, Server } from "lucide-react";
import { get, post } from "../../api";
import { Card, Empty, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { shortTime } from "../../lib/time";
import { Code, useClasses } from "./common";

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
  const data = useLoad(() => get<SourcesData>("/api/teacher/sources"), [], 30000);
  const [note, setNote] = useState<{ id: number; text: string } | null>(null);
  const { reload: reloadClasses } = useClasses();
  const d = data.data;
  const sync = async (id: number) => {
    setNote(null);
    try {
      await post(`/api/teacher/sources/${id}/sync`);
      data.reload();
      window.setTimeout(reloadClasses, 20000); // 名单一般十几秒到一两分钟同步完；之后还有定时刷新
    } catch (err) {
      setNote({ id, text: (err as Error).message });
    }
  };
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
            <div className="form-actions">
              <button type="button" className="btn" disabled={!src.enabled} onClick={() => void sync(src.id)}><RefreshCw size={15} aria-hidden="true" />立即同步名单和作业</button>
              {note?.id === src.id && <span className="error-text" role="alert">{note.text}</span>}
            </div>
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
