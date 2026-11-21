// 日志导出（只给开发者）：学生的使用记录——哪些学号、什么时候、用了哪些样例（只有过没过）、做了哪些交互。
// 选好范围点“导出”就下载，不再有申请记录；原文、源码默认不给，要单独勾选。
import { useMemo, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { CircleCheck, FileDown, FileJson, FileSpreadsheet, ShieldCheck } from "lucide-react";
import { get, post } from "../../api";
import { Card, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";

interface LogMeta {
  kinds: { code: string; name: string }[];
  classes: { key: string; name: string }[];
  limits: { ids: number; days: number };
}

interface CreateResult {
  request: { id: number; token: string | null; format: string; granted: number; denied: string[]; rows: number };
  byKind: Record<string, number>;
}

const isoDay = (d: Date) => new Date(d.getTime() + 8 * 3600_000).toISOString().slice(0, 10); // 北京时间的日期

export default function Logs() {
  const [params] = useSearchParams();
  const meta = useLoad(() => get<LogMeta>("/api/teacher/logs"), [], 0, false, "t:logs");
  const [ids, setIds] = useState(params.get("ids") ?? "");
  const [classKeys, setClassKeys] = useState<string[]>([]);
  const [from, setFrom] = useState(() => isoDay(new Date(Date.now() - 30 * 86400_000)));
  const [to, setTo] = useState(() => isoDay(new Date()));
  const [kinds, setKinds] = useState<string[] | null>(null);
  const [includeText, setIncludeText] = useState(false);
  const [includeCode, setIncludeCode] = useState(false);
  const [format, setFormat] = useState<"csv" | "json">("csv");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CreateResult | null>(null);

  const m = meta.data;
  const chosenKinds = kinds ?? m?.kinds.map((k) => k.code) ?? [];
  const idCount = useMemo(() => new Set(ids.split(/[\s,，;；]+/).filter(Boolean)).size, [ids]);
  const allClasses = !!m && m.classes.length > 0 && m.classes.every((c) => classKeys.includes(c.key));
  const allKinds = !!m && m.kinds.every((k) => chosenKinds.includes(k.code));

  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await post<CreateResult>("/api/teacher/logs", {
        ids, classKeys, dateFrom: from, dateTo: to, kinds: chosenKinds, includeText,
        includeCode: includeText && includeCode, format,
      });
      setResult(r);
      if (r.request.token) {   // 马上下载（文件是现生成的，不落盘）
        window.location.href = `/api/teacher/logs/${r.request.id}/download?token=${encodeURIComponent(r.request.token)}`;
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (meta.error) return <Problem message={meta.error} onRetry={meta.reload} />;
  if (!m) return <div className="card card-body"><Loading lines={6} /></div>;

  return (
    <div className="grid-main">
      <Card title="导出学生日志">
        <form className="log-form" onSubmit={submit}>
          <fieldset>
            <legend>哪些学生</legend>
            <div className="check-row">
              {m.classes.length > 1 && (
                <label className="check-chip">
                  <input type="checkbox" checked={allClasses} onChange={() => setClassKeys(allClasses ? [] : m.classes.map((c) => c.key))} />
                  全选
                </label>
              )}
              {m.classes.map((c) => (
                <label key={c.key} className="check-chip">
                  <input type="checkbox" checked={classKeys.includes(c.key)} onChange={() => setClassKeys(toggle(classKeys, c.key))} />
                  {c.name}
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
              <label className="check-chip">
                <input type="checkbox" checked={allKinds} onChange={() => setKinds(allKinds ? [] : m.kinds.map((k) => k.code))} />
                全选
              </label>
              {m.kinds.map((k) => (
                <label key={k.code} className="check-chip">
                  <input type="checkbox" checked={chosenKinds.includes(k.code)} onChange={() => setKinds(toggle(chosenKinds, k.code))} />
                  {k.name}
                </label>
              ))}
            </div>
            <label className="check">
              <input type="checkbox" checked={includeText} onChange={(e) => setIncludeText(e.target.checked)} />
              包含原文：对话文字和贴的代码、复习小题的作答过程和问答
            </label>
            <label className="check check-sub">
              <input type="checkbox" checked={includeText && includeCode} disabled={!includeText}
                onChange={(e) => setIncludeCode(e.target.checked)} />
              包含提交的源码
            </label>
          </fieldset>
          <fieldset>
            <legend>格式</legend>
            <div className="check-row">
              <label className="check-chip"><input type="radio" name="fmt" checked={format === "csv"} onChange={() => setFormat("csv")} /><FileSpreadsheet size={15} aria-hidden="true" />CSV（Excel 能直接打开）</label>
              <label className="check-chip"><input type="radio" name="fmt" checked={format === "json"} onChange={() => setFormat("json")} /><FileJson size={15} aria-hidden="true" />JSON</label>
            </div>
          </fieldset>
          {error && <p className="error-text" role="alert">{error}</p>}
          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={busy || chosenKinds.length === 0}><FileDown size={16} aria-hidden="true" />{busy ? "正在整理" : "导出"}</button>
            <span className="note">一次最多 {m.limits.ids} 个学号、{m.limits.days} 天。</span>
          </div>
          {result && (
            <div className="log-result" role="status">
              <p><CircleCheck size={16} aria-hidden="true" />{result.request.granted} 名学生，{result.request.rows} 条记录，已开始下载。
                {result.request.denied.length > 0 && <span className="warn-text"> 有 {result.request.denied.length} 个学号没有记录：{result.request.denied.slice(0, 6).join("、")}{result.request.denied.length > 6 ? " 等" : ""}。</span>}</p>
              <div className="kind-counts">
                {Object.entries(result.byKind).map(([k, n]) => <span key={k} className="pill pill-outline">{k} <b className="num">{n}</b></span>)}
              </div>
            </div>
          )}
        </form>
      </Card>

      <Card title="会拿到什么">
        <ul className="facts-list">
          <li><ShieldCheck size={16} aria-hidden="true" /><span><b>每一行是一次交互</b>：学号、姓名、时间、类型、栏目、题目、提交编号。</span></li>
          <li><ShieldCheck size={16} aria-hidden="true" /><span><b>样例只有过没过</b>，比如“没通过第 3 组”；测试数据的输入输出不会给出。</span></li>
          <li><ShieldCheck size={16} aria-hidden="true" /><span><b>不含密码、令牌、登录 IP</b>；这些从来不进库。</span></li>
          <li><ShieldCheck size={16} aria-hidden="true" /><span><b>研究用</b>：求助回合、卡点、理解评估和小题的作答过程，带指标版本；按学号能和期末成绩对上。</span></li>
          <li><ShieldCheck size={16} aria-hidden="true" /><span>每一页上看到的汇总（总览、学生、误区、知识图谱、研究各页），在那一页右上角“导出”。</span></li>
        </ul>
      </Card>
    </div>
  );
}
