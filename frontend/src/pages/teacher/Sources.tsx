// 数据源：已知的两台 XDOJ 服务器、每个数据源的同步状态、标注用的表。
import { useState } from "react";
import { Database, Globe, KeyRound, RefreshCw, Server, ShieldCheck } from "lucide-react";
import { get, post } from "../../api";
import { Card, Empty, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { fullTime, shortTime } from "../../lib/time";
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
  const data = useLoad(() => get<SourcesData>("/api/teacher/sources"), [], 30000);
  const [note, setNote] = useState<{ id: number; text: string; bad: boolean } | null>(null);
  const d = data.data;
  const sync = async (id: number) => {
    try {
      const r = await post<{ daemon: { running: boolean } }>(`/api/teacher/sources/${id}/sync`);
      setNote(r.daemon.running
        ? { id, text: "已经通知后台同步，名单和作业会在几分钟内更新，这一页会自己刷新。", bad: false }
        : { id, text: "已经记下了，但后台同步现在没有在运行，所以不会有反应。见页面顶部的说明。", bad: true });
      data.reload();
    } catch (err) {
      setNote({ id, text: (err as Error).message, bad: true });
    }
  };
  if (data.error) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <div className="card card-body"><Loading what="正在取数据源的状态" lines={5} /></div>;
  return (
    <div className="stack">
      {!d.daemon.running && d.sources.some((x) => !x.demo) && (
        <div className="problem" role="alert">
          <span>
            <b>后台同步没有在运行</b>
            {d.daemon.lastBeat ? `（上次在 ${fullTime(d.daemon.lastBeat)}，当时在${d.daemon.activity || "处理"}）` : ""}，所以班级、名单、提交都不会更新。
            用 <code>./run.sh</code> 或 <code>python -m app.cli serve</code> 启动时会自动带上它；
            如果设了 <code>SYNC_IN_WEB=0</code>，要另外运行 <code>python -m app.cli daemon</code>。日志里搜“后台同步”能看到出错原因。
          </span>
        </div>
      )}
      {d.daemon.running && (
        <div className="trial trial-info">
          <RefreshCw size={18} aria-hidden="true" />
          <p><b>后台同步正在运行</b>：{d.daemon.activity || "处理中"}。第一次登录后先同步名单和作业里的题，一般几分钟；以前的提交会在后面慢慢补齐（几千条要几个小时），这一页每 30 秒刷新一次。</p>
        </div>
      )}
      <div className="trial trial-info">
        <ShieldCheck size={18} aria-hidden="true" />
        <p>系统对 XDOJ 只读：只访问写死的十几个查询接口，所有请求排队发送，两次请求至少相隔 {d.interval} 秒。名单接口带回的学生密码、令牌、登录 IP 在内存里就丢掉，不进库也不进日志。</p>
      </div>

      <div className="grid-2">
        {d.known.map((k) => (
          <Card key={k.code} title={<><Globe size={16} aria-hidden="true" /> {k.name}</>} sub={k.course === "C" ? "C 语言程序设计用的 XDOJ" : "数据结构用的 XDOJ"}>
            <dl className="facts">
              <dt>地址</dt>
              <dd>{k.baseUrl ? <span className="mono url">{k.baseUrl}</span> : <span className="muted">还没有给出（和 C 语言的地址不同）</span>}</dd>
              <dt>出处</dt>
              <dd className="muted">{k.basis}</dd>
              <dt>代号</dt>
              <dd><Code>{k.code}</Code></dd>
            </dl>
          </Card>
        ))}
      </div>

      {d.sources.length === 0 && <div className="card"><Empty icon={Database} title="还没有数据源" /></div>}
      {d.sources.map((src) => {
        const backlog = Object.values(src.backlog).reduce((a, b) => a + b, 0);
        const st = sourceState(src);
        return (
          <Card key={src.id} title={<><Server size={16} aria-hidden="true" /> {src.server}</>} sub={<>账号 <Code>{src.account}</Code></>}
            action={<span className={st.cls}>{st.text}</span>}>
            <div className="source-grid">
              <div><span className="eyebrow">名下的班级</span><b className="num">{src.classes}</b></div>
              <div><span className="eyebrow">已同步的提交</span><b className="num">{src.submissions}</b>{src.pendingDetails > 0 && <span className="note">{src.pendingDetails} 条详情还在取</span>}</div>
              <div><span className="eyebrow">排队中的任务</span><b className="num">{backlog}</b></div>
              <div><span className="eyebrow">最近请求成功率</span><b className="num">{src.requestOkRate === null ? "—" : `${src.requestOkRate}%`}</b></div>
            </div>
            <dl className="facts">
              <dt>上次查到新提交</dt><dd>{src.lastPoll ? fullTime(src.lastPoll) : "还没有"}</dd>
              <dt>上次同步名单和作业</dt><dd>{src.lastMeta ? fullTime(src.lastMeta) : "还没有"}</dd>
              <dt><KeyRound size={13} aria-hidden="true" /> 密码</dt><dd>{src.demo ? "不需要" : src.fromEnv ? "来自服务器的环境变量，不在库里" : "加密保存在服务器上"}</dd>
            </dl>
            <div className="form-actions">
              <button type="button" className="btn" disabled={!src.enabled} onClick={() => void sync(src.id)}><RefreshCw size={15} aria-hidden="true" />立即同步名单和作业</button>
              {note?.id === src.id && <span className={note.bad ? "error-text" : "note"} role="status">{note.text}</span>}
            </div>
          </Card>
        );
      })}

      <Card title="标注用的表">
        <div className="source-grid">
          <div><span className="eyebrow">知识点（两门课）</span><b className="num">{d.libraries.knowledge}</b></div>
          <div><span className="eyebrow">技能</span><b className="num">{d.libraries.skills}</b><span className="note">第 {d.libraries.taxonomyVersion} 版，骨架初稿</span></div>
          <div><span className="eyebrow">错误类型</span><b className="num">{d.libraries.errors}</b><span className="note">第 {d.libraries.errorVersion} 版</span></div>
        </div>
      </Card>

      {d.candidates.length > 0 && (
        <Card title="候选的新错误类型" sub="自动标注时库里没有合适编码的情况。怎么入库还没有定，这里只列出来" bodyClass="">
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
