// 登录页：左边一块深色说明这个系统做什么（宽屏才有），右边是表单。
import { useState, type FormEvent } from "react";
import { Braces, Eye, EyeOff, GraduationCap, Lock, MessageSquare, RotateCcw, ShieldCheck, Users } from "lucide-react";
import { api } from "../api";
import { Segmented } from "../components/ui";
import { useLoad } from "../lib/hooks";
import { useSession } from "../session";

const FACTS = [
  { icon: ShieldCheck, title: "只读同步 XDOJ", text: "你在 XDOJ 上交题，几秒后这里就能看到结果。测试点只显示过没过。" },
  { icon: MessageSquare, title: "按你的节奏给帮助", text: "先问你几个问题；想自己想、要提示、还是直接讲，由你决定。" },
  { icon: RotateCcw, title: "快忘的时候提醒复习", text: "卡过的地方，过几天用一道小题帮你回忆，记住了间隔就拉长。" },
];

export default function Login() {
  const { setMe } = useSession();
  const info = useLoad(() => api.authInfo(), []);
  const servers = useLoad(() => api.teacherServers().then((r) => r.servers), []);
  const [tab, setTab] = useState<"student" | "teacher">("student");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [server, setServer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const demo = info.data?.demo ?? false;
  // 演示数据没有真实的 XDOJ 可以试登：演示教师、管理员用服务器上的口令登录
  const useDevTeacher = (demo && name.trim() === "demo-teacher") || !(servers.data?.length);
  const teacherXdoj = tab === "teacher" && !useDevTeacher && (servers.data?.length ?? 0) > 0;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (teacherXdoj) await api.teacherLogin(server || servers.data![0].code, name.trim(), password);
      else await api.login(name.trim(), password);
      setMe(await api.me());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <aside className="login-aside" aria-hidden="true">
        <div className="brand">
          <span className="brand-mark"><Braces size={18} strokeWidth={2.4} /></span>
          <span className="brand-name">程序设计智能体</span>
        </div>
        <div className="login-pitch">
          <h2>C 语言和数据结构的上机练习助手</h2>
          <p>接在学校的 XDOJ 上。学生问问题、做小题、复习；老师看全班卡在哪里。</p>
        </div>
        <ul className="login-facts">
          {FACTS.map(({ icon: Icon, title, text }) => (
            <li key={title}>
              <span className="login-fact-icon"><Icon size={18} /></span>
              <span><strong>{title}</strong>{text}</span>
            </li>
          ))}
        </ul>
        <div className="login-preview">
          <div className="lp-row"><span className="cell cell-pass" /><span className="cell cell-pass" /><span className="cell cell-fail" /><span className="cell cell-pass" /><span className="cell cell-pass" /><span className="lp-text">第 3 次 · 5 组过 4 组</span></div>
          <div className="lp-bubble">如果输入的数全是负数，<code>max</code> 一开始等于 0，会发生什么？</div>
          <div className="lp-chips"><span>我想自己想想</span><span>再给点提示</span><span>直接讲给我</span></div>
        </div>
      </aside>

      <main className="login-main">
        <form className="login-box" onSubmit={submit}>
          <div className="brand login-brand-sm">
            <span className="brand-mark"><Braces size={18} strokeWidth={2.4} /></span>
            <span className="brand-name">程序设计智能体</span>
          </div>
          <div>
            <h1>登录</h1>
            <p className="page-sub">{tab === "student" ? "用 XDOJ 的学号和密码登录。密码只拿去 XDOJ 核对一次，不会保存。" : "用你在 XDOJ 上的教师账号登录。第一次登录后会自动同步你名下的班级和学生名单。"}</p>
          </div>
          <Segmented label="身份" value={tab} onChange={(v) => { setTab(v); setError(null); }}
            options={[
              { value: "student", label: <><GraduationCap size={16} aria-hidden="true" />学生</> },
              { value: "teacher", label: <><Users size={16} aria-hidden="true" />教师</> },
            ]} />
          {teacherXdoj && (
            <label className="label">
              XDOJ 服务器
              <select className="field" value={server || servers.data![0].code} onChange={(e) => setServer(e.target.value)}>
                {servers.data!.map((sv) => <option key={sv.code} value={sv.code}>{sv.name}</option>)}
              </select>
            </label>
          )}
          <label className="label">
            {tab === "student" ? "学号" : "XDOJ 教师账号"}
            <input className="field" value={name} onChange={(e) => setName(e.target.value)} autoComplete="username"
              inputMode={tab === "student" ? "numeric" : "text"} placeholder={tab === "student" ? "例如 2500000001" : ""} required autoFocus />
          </label>
          <label className="label">
            密码
            <span className="field-wrap">
              <input className="field" type={show ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password" required />
              <button type="button" className="field-btn" onClick={() => setShow(!show)} aria-label={show ? "隐藏密码" : "显示密码"}>
                {show ? <EyeOff size={17} /> : <Eye size={17} />}
              </button>
            </span>
          </label>
          {error && <p className="error-text" role="alert">{error}</p>}
          <button type="submit" className="btn btn-primary btn-block" disabled={busy}>{busy ? "正在登录" : "登录"}</button>
          {teacherXdoj && (
            <p className="note login-note">
              <Lock size={14} aria-hidden="true" />
              系统会用这个账号去 XDOJ 试着登录一次，成功后只读地同步你名下班级的练习数据。同步要长期进行，所以密码会加密保存在服务器上。
            </p>
          )}
          {demo && (
            <p className="login-demo">
              现在装的是演示数据。{tab === "student" ? <>学号 <code>{info.data?.demoAccount}</code></> : <>教师账号 <code>demo-teacher</code></>}，
              密码是服务器上设置的演示口令（DEV_LOGIN_PASSWORD）。
            </p>
          )}
          {info.data && !info.data.ready && (
            <p className="error-text">服务器还没有设置登录口令，暂时登录不了。请联系管理员。</p>
          )}
        </form>
      </main>
    </div>
  );
}
