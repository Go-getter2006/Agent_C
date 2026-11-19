// 日志申请：老师按需取自己学生的使用记录——哪些学号、什么时候、用了哪些样例（只有过没过）、做了哪些交互。
// 每次申请都要写用途、留审计记录；只能取自己名下班级的学生；聊天原文默认不给，要单独勾选；下载链接 7 天后失效。
import { useMemo, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { Ban, CircleCheck, Clock, Download, FileDown, FileJson, FileSpreadsheet, ShieldCheck } from "lucide-react";
import { get, post } from "../../api";
import { Card, Empty, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { fullTime, shortTime } from "../../lib/time";

interface LogMeta {
  kinds: { code: string; name: string }[];
  classes: { key: string; name: string }[];
  requests: RequestView[];
  limits: { ids: number; days: number; daily: number; ttlDays: number };
  admin: boolean;
}

interface RequestView {
  id: number; createdAt: string; requester: string; from: string; to: string; kinds: string[]; kindNames: string[];
  includeText: boolean; format: string; purpose: string; granted: number; denied: string[]; rows: number;
  status: "ready" | "expired" | "revoked"; expiresAt: string; downloads: number; token: string | null;
}

interface CreateResult {
  request: RequestView;
  preview: { userId: string; name: string; time: string; kind: string; action: string; problem: string | null; problemId: number | null; samples: string | null; detail: string | null }[];
  byKind: Record<string, number>;
}

const STATUS: Record<RequestView["status"], { text: string; cls: string }> = {
  ready: { text: "可以下载", cls: "pill pill-pass" },
  expired: { text: "已过期", cls: "pill" },
  revoked: { text: "已撤销", cls: "pill pill-fail" },
};

const isoDay = (d: Date) => new Date(d.getTime() + 8 * 3600_000).toISOString().slice(0, 10); // 北京时间的日期

const downloadUrl = (r: RequestView) => `/api/teacher/logs/${r.id}/download?token=${encodeURIComponent(r.token ?? "")}`;

export default function Logs() {
  const [params] = useSearchParams();
  const meta = useLoad(() => get<LogMeta>("/api/teacher/logs"), [], 0, false, "t:logs");
  const [ids, setIds] = useState(params.get("ids") ?? "");
  const [classKeys, setClassKeys] = useState<string[]>([]);
  const [from, setFrom] = useState(() => isoDay(new Date(Date.now() - 30 * 86400_000)));
  const [to, setTo] = useState(() => isoDay(new Date()));
  const [kinds, setKinds] = useState<string[] | null>(null);
  const [includeText, setIncludeText] = useState(false);
  const [format, setFormat] = useState<"csv" | "json">("csv");
  const [purpose, setPurpose] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CreateResult | null>(null);

  const m = meta.data;
  const chosenKinds = kinds ?? m?.kinds.map((k) => k.code) ?? [];
  const idCount = useMemo(() => new Set(ids.split(/[\s,，;；]+/).filter(Boolean)).size, [ids]);

  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await post<CreateResult>("/api/teacher/logs", {
        ids, classKeys, dateFrom: from, dateTo: to, kinds: chosenKinds, includeText, format, purpose,
      });
      setResult(r);
      meta.reload();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (id: number) => {
    try {
      await post(`/api/teacher/logs/${id}/revoke`);
      meta.reload();
      if (result?.request.id === id) setResult(null);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  if (meta.error) return <Problem message={meta.error} onRetry={meta.reload} />;
  if (!m) return <div className="card card-body"><Loading lines={6} /></div>;

  return (
    <div className="stack">
      <div className="grid-main">
        <Card title="新的申请" sub="只能取你名下班级的学生；不在名下的学号会被跳过，并在结果里列出">
          <form className="log-form" onSubmit={submit}>
            <fieldset>
              <legend>哪些学生</legend>
              <div className="check-row">
                {m.classes.map((c) => (
                  <label key={c.key} className="check-chip">
                    <input type="checkbox" checked={classKeys.includes(c.key)} onChange={() => setClassKeys(toggle(classKeys, c.key))} />
                    整个{c.name}
                  </label>
                ))}
              </div>
              <label className="label">
                <span>也可以填学号{idCount ? `（已填 ${idCount} 个）` : ""}</span>
                <textarea className="field mono" rows={3} value={ids} onChange={(e) => setIds(e.target.value)}
                  placeholder="一行一个，或用逗号、空格隔开" spellCheck={false} />
              </label>
            </fieldset>
            <fieldset>
              <legend>什么时候</legend>
              <div className="date-row">
                <label className="label">从<input className="field" type="date" value={from} onChange={(e) => setFrom(e.target.value)} required /></label>
                <label className="label">到<input className="field" type="date" value={to} onChange={(e) => setTo(e.target.value)} required /></label>
              </div>
            </fieldset>
            <fieldset>
              <legend>哪些记录</legend>
              <div className="check-row">
                {m.kinds.map((k) => (
                  <label key={k.code} className="check-chip">
                    <input type="checkbox" checked={chosenKinds.includes(k.code)} onChange={() => setKinds(toggle(chosenKinds, k.code))} />
                    {k.name}
                  </label>
                ))}
              </div>
              <label className="check">
                <input type="checkbox" checked={includeText} onChange={(e) => setIncludeText(e.target.checked)} />
                包括学生提问和智能体回复的原文（默认不给，只给长度和类型）
              </label>
            </fieldset>
            <fieldset>
              <legend>格式和用途</legend>
              <div className="check-row">
                <label className="check-chip"><input type="radio" name="fmt" checked={format === "csv"} onChange={() => setFormat("csv")} /><FileSpreadsheet size={15} aria-hidden="true" />CSV（Excel 能直接打开）</label>
                <label className="check-chip"><input type="radio" name="fmt" checked={format === "json"} onChange={() => setFormat("json")} /><FileJson size={15} aria-hidden="true" />JSON</label>
              </div>
              <label className="label">
                用途（必填，会记进审计记录）
                <input className="field" value={purpose} onChange={(e) => setPurpose(e.target.value)} required
                  placeholder="比如：期中前了解哪些同学还没开始做作业" />
              </label>
            </fieldset>
            {error && <p className="error-text" role="alert">{error}</p>}
            <div className="form-actions">
              <button type="submit" className="btn btn-primary" disabled={busy}><FileDown size={16} aria-hidden="true" />{busy ? "正在整理" : "生成下载链接"}</button>
              <span className="note">一次最多 {m.limits.ids} 个学号、{m.limits.days} 天，每天最多申请 {m.limits.daily} 次。</span>
            </div>
          </form>
        </Card>

        <div className="stack">
          <Card title="会拿到什么">
            <ul className="facts-list">
              <li><ShieldCheck size={16} aria-hidden="true" /><span><b>每一行是一次交互</b>：学号、姓名、时间、类型（登录、提交、提问、回复、小题、复习、看代码）、题目、提交编号。</span></li>
              <li><ShieldCheck size={16} aria-hidden="true" /><span><b>样例只有过没过</b>，比如“没通过第 3 组”“过过×过过”；测试数据的输入输出不会给出。</span></li>
              <li><ShieldCheck size={16} aria-hidden="true" /><span><b>不含密码、令牌、登录 IP</b>；这些从来不进库。</span></li>
              <li><ShieldCheck size={16} aria-hidden="true" /><span><b>研究用</b>：求助回合、卡点、理解评估和小题的作答过程，带指标版本；按学号能和期末成绩对上。</span></li>
              <li><Clock size={16} aria-hidden="true" /><span>下载链接 {m.limits.ttlDays} 天后失效，随时可以撤销；每次下载都会记录。</span></li>
            </ul>
          </Card>
          {result && (
            <Card title="已经生成" className="log-result"
              action={<span className={STATUS[result.request.status].cls}><CircleCheck aria-hidden="true" />{STATUS[result.request.status].text}</span>}>
              <p>{result.request.granted} 名学生，{result.request.rows} 条记录。{result.request.denied.length > 0 && <span className="warn-text">有 {result.request.denied.length} 个学号不在你名下，已跳过：{result.request.denied.slice(0, 6).join("、")}{result.request.denied.length > 6 ? " 等" : ""}。</span>}</p>
              <div className="kind-counts">
                {Object.entries(result.byKind).map(([k, n]) => <span key={k} className="pill pill-outline">{k} <b className="num">{n}</b></span>)}
              </div>
              {result.request.token && (
                <a className="btn btn-primary" href={downloadUrl(result.request)}><Download size={16} aria-hidden="true" />下载 {result.request.format.toUpperCase()}</a>
              )}
            </Card>
          )}
        </div>
      </div>

      {result && result.preview.length > 0 && (
        <Card title="预览" sub="最后 12 条" bodyClass="">
          <div className="table-scroll">
            <table className="table">
              <thead><tr><th scope="col">学号</th><th scope="col">姓名</th><th scope="col">时间</th><th scope="col">类型</th><th scope="col">交互</th><th scope="col">题目</th><th scope="col">样例</th><th scope="col">详情</th></tr></thead>
              <tbody>
                {result.preview.map((r, i) => (
                  <tr key={i}>
                    <td className="nowrap">{r.userId}</td><td className="nowrap">{r.name}</td><td className="nowrap muted">{fullTime(r.time)}</td>
                    <td className="nowrap">{r.kind}</td><td>{r.action}</td><td>{r.problem ?? ""}</td>
                    <td className="mono nowrap">{r.samples ?? ""}</td><td className="muted">{r.detail ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <Card title="申请记录" sub={m.admin ? "所有老师的申请（管理员可见）" : "你提过的申请"} bodyClass="">
        {m.requests.length === 0 ? <Empty icon={FileDown} title="还没有申请过" /> : (
          <div className="table-scroll">
            <table className="table">
              <thead><tr><th scope="col">时间</th>{m.admin && <th scope="col">申请人</th>}<th scope="col">范围</th><th scope="col">记录</th><th scope="col">用途</th><th scope="col">状态</th><th scope="col" className="num">下载次数</th><th scope="col"><span className="sr-only">操作</span></th></tr></thead>
              <tbody>
                {m.requests.map((r) => (
                  <tr key={r.id}>
                    <td className="nowrap muted">{shortTime(r.createdAt)}</td>
                    {m.admin && <td>{r.requester}</td>}
                    <td><span className="cell-title">{r.granted} 人 · {r.rows} 条</span><span className="cell-sub">{r.from.slice(0, 10)} 到 {r.to.slice(0, 10)} · {r.format.toUpperCase()}{r.includeText ? " · 含原文" : ""}</span></td>
                    <td className="muted">{r.kindNames.join("、")}</td>
                    <td>{r.purpose}</td>
                    <td><span className={STATUS[r.status].cls}>{STATUS[r.status].text}</span>{r.status === "ready" && <span className="cell-sub">{shortTime(r.expiresAt)} 失效</span>}</td>
                    <td className="num">{r.downloads}</td>
                    <td className="nowrap">
                      {r.status === "ready" && r.token && <a className="btn btn-sm btn-ghost" href={downloadUrl(r)}><Download size={14} aria-hidden="true" />下载</a>}
                      {r.status === "ready" && <button type="button" className="btn btn-sm btn-ghost" onClick={() => void revoke(r.id)}><Ban size={14} aria-hidden="true" />撤销</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
