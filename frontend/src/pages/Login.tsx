// 登录页：一个表单。不放插画，不放口号。
import { useState, type FormEvent } from "react";
import { api } from "../api";
import { useLoad } from "../lib/hooks";
import { useSession } from "../session";

export default function Login() {
  const { setMe } = useSession();
  const info = useLoad(() => api.authInfo(), []);
  const [tab, setTab] = useState<"student" | "teacher">("student");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [server, setServer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (tab === "teacher" && !useDevTeacher) await api.teacherLogin(server, name.trim(), password);
      else await api.login(name.trim(), password);
      setMe(await api.me());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const demo = info.data?.demo ?? false;
  // 演示数据没有真实的 XDOJ 可以试登，教师也用演示口令
  const useDevTeacher = demo && name.trim() === "demo-teacher";

  return (
    <main className="login">
      <form className="login-box" onSubmit={submit}>
        <h1>程序设计智能体</h1>
        <div className="login-tabs" role="tablist" aria-label="身份">
          <button type="button" role="tab" aria-selected={tab === "student"} onClick={() => { setTab("student"); setError(null); }}>学生</button>
          <button type="button" role="tab" aria-selected={tab === "teacher"} onClick={() => { setTab("teacher"); setError(null); }}>教师</button>
        </div>
        {tab === "teacher" && !demo && (
          <label>
            XDOJ 服务器地址
            <input className="field" value={server} onChange={(e) => setServer(e.target.value)} placeholder="http://服务器地址/xdoj-ssm" autoComplete="url" required />
          </label>
        )}
        <label>
          {tab === "student" ? "学号" : "XDOJ 教师账号"}
          <input className="field" value={name} onChange={(e) => setName(e.target.value)} autoComplete="username" inputMode={tab === "student" ? "numeric" : "text"} required autoFocus />
        </label>
        <label>
          密码
          <input className="field" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
        </label>
        {error && <p className="error-text" role="alert">{error}</p>}
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "正在登录" : "登录"}</button>
        {tab === "teacher" && !demo && (
          <p className="note">
            系统会用这个账号去 XDOJ 试着登录一次，成功后建立教师账号，并只读地同步你名下班级的练习数据。
            同步要长期进行，所以密码会加密保存在服务器上。
          </p>
        )}
        {demo && (
          <p className="note">
            现在装的是演示数据。{tab === "student" ? `学号 ${info.data?.demoAccount}` : "教师账号 demo-teacher"}，
            密码是服务器上设置的演示口令（DEV_LOGIN_PASSWORD）。
          </p>
        )}
        {info.data && !info.data.ready && (
          <p className="error-text">服务器还没有设置登录口令，暂时登录不了。请联系管理员。</p>
        )}
      </form>
    </main>
  );
}
