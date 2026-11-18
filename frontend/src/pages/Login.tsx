// 登录首页：暖色画布、原创大括号主视觉；登录后直接进入对话。
// C 语言和数据结构分开登录：两门课都接上时先选课程；只有一门时不出现课程选择，也不提另一门课。
import { useState, type FormEvent } from "react";
import { ArrowUpRight, Braces, Eye, EyeOff, GraduationCap, Users } from "lucide-react";
import { api } from "../api";
import type { CourseCode } from "../types";
import { Segmented } from "../components/ui";
import { useLoad } from "../lib/hooks";
import { useSession } from "../session";

export default function Login() {
  const { setMe } = useSession();
  const info = useLoad(() => api.authInfo(), []);
  const [tab, setTab] = useState<"student" | "teacher">("student");
  const [course, setCourse] = useState<CourseCode>("C");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const demo = info.data?.demo ?? false;
  const courses = info.data?.courses ?? [];
  const chosen = courses.find((c) => c.code === course) ?? courses[0] ?? null;
  // 演示数据没有真实的 XDOJ 可以试登：演示教师、管理员用服务器上的口令登录
  const useDevTeacher = (demo && name.trim() === "demo-teacher") || !chosen?.server;
  const teacherXdoj = tab === "teacher" && !useDevTeacher;
  const pitch = courses.length > 1 ? "C 语言和数据结构的上机练习助手" : `${chosen?.name ?? "C 语言程序设计"}的上机练习助手`;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (teacherXdoj) await api.teacherLogin(chosen!.server!, name.trim(), password);
      else await api.login(name.trim(), password, chosen?.code ?? "C");
      setMe(await api.me());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <aside className="login-aside">
        <div className="brand">
          <span className="brand-mark"><Braces size={18} strokeWidth={2.4} /></span>
          <span className="brand-name">程序设计智能体</span>
        </div>
        <div className="login-pitch">
          <span className="eyebrow">{pitch}</span>
          <h1>让每一次尝试，<br />都有下一步<span>。</span></h1>
          <p>带上你的问题，从一段代码开始。<br />一起读懂题目，找到思路，把不懂的变成会的。</p>
        </div>
        <div className="login-art" aria-hidden="true">
          <svg viewBox="0 0 760 390" fill="none">
            <path d="M242 42h-25c-52 0-65 28-65 72v28c0 35-17 51-52 53 35 2 52 18 52 53v28c0 44 13 72 65 72h25" />
            <path d="M518 42h25c52 0 65 28 65 72v28c0 35 17 51 52 53-35 2-52 18-52 53v28c0 44-13 72-65 72h-25" />
            <path className="login-art-slash" d="m419 113-78 164" />
          </svg>
          <span className="login-art-note">想明白，再写下一行。</span>
        </div>
        <div className="login-bottom"><span>从练习出发 · 在对话中进步</span><span className="login-bottom-code">return a_better_you;</span></div>
      </aside>

      <main className="login-main">
        <form className="login-box" onSubmit={submit} aria-labelledby="login-title">
          <div className="brand login-brand-sm">
            <span className="brand-mark"><Braces size={18} strokeWidth={2.4} /></span>
            <span className="brand-name">程序设计智能体</span>
          </div>
          <div className="login-form-heading">
            <span className="login-form-kicker">开始这一段学习</span>
            <h2 id="login-title">{tab === "student" ? "从这里，继续。" : "看见每一步成长。"}</h2>
            <p className="page-sub">
              {courses.length > 1 ? `${chosen?.name ?? ""}：` : ""}
              {tab === "student" ? "用 XDOJ 的学号和密码登录。" : "用这门课 XDOJ 的教师账号登录。"}
            </p>
          </div>
          {courses.length > 1 && (
            <Segmented label="课程" value={chosen?.code ?? "C"} onChange={(v) => { setCourse(v); setError(null); }}
              options={courses.map((c) => ({ value: c.code, label: c.name }))} />
          )}
          <Segmented label="身份" value={tab} onChange={(v) => { setTab(v); setError(null); }}
            options={[
              { value: "student", label: <><GraduationCap size={16} aria-hidden="true" />学生</> },
              { value: "teacher", label: <><Users size={16} aria-hidden="true" />教师</> },
            ]} />
          {teacherXdoj && courses.length > 1 && (
            <p className="note">两门课是两台 XDOJ，账号密码可能不一样。两门都教的老师，分别登录两门课。</p>
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
          <button type="submit" className="btn btn-primary btn-block login-submit" disabled={busy} aria-busy={busy}>
            {busy ? "正在登录" : tab === "student" ? "进入学习" : "进入教师工作台"}<ArrowUpRight size={18} aria-hidden="true" />
          </button>
          {demo && (
            <p className="login-demo">
              现在装的是演示数据。{tab === "student" ? <>学号 <code>{info.data?.demoAccount}</code></> : <>教师账号 <code>demo-teacher</code></>}，
              密码请使用管理员提供的演示口令。
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
