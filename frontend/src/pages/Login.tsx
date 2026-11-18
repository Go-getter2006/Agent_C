// 登录页：左边一块深色说明这个系统做什么（宽屏才有），右边是表单。
// C 语言和数据结构分开登录：两门课都接上时先选课程；只有一门时不出现课程选择，也不提另一门课。
import { useState, type FormEvent } from "react";
import { Braces, Eye, EyeOff, GraduationCap, MessageSquare, RotateCcw, ShieldCheck, Users } from "lucide-react";
import { api } from "../api";
import type { CourseCode } from "../types";
import { Segmented } from "../components/ui";
import { useLoad } from "../lib/hooks";
import { useSession } from "../session";

const FACTS = [
  { icon: ShieldCheck, title: "从你的练习继续", text: "自动整理 XDOJ 的练习提交，把代码、题目和分析放在一起。" },
  { icon: MessageSquare, title: "按你的节奏给帮助", text: "先问你几个问题；想自己想、要提示、还是直接讲，由你决定。" },
  { icon: RotateCcw, title: "快忘的时候提醒复习", text: "卡过的地方，过几天用一道小题帮你回忆，记住了间隔就拉长。" },
];

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
      <aside className="login-aside" aria-hidden="true">
        <div className="brand">
          <span className="brand-mark"><Braces size={18} strokeWidth={2.4} /></span>
          <span className="brand-name">程序设计智能体</span>
        </div>
        <div className="login-pitch">
          <span className="eyebrow">{pitch}</span>
          <h2>从一次错误提交，<br />找到下一步怎么改。</h2>
          <p>读懂题目，梳理思路，在合适的时候得到提示和代码示例。教师也能看到需要关注的问题。</p>
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
          <button type="submit" className="btn btn-primary btn-block" disabled={busy}>{busy ? "正在登录" : tab === "student" ? "进入学习" : "进入教师工作台"}</button>
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
