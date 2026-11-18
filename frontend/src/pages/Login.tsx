// 登录首页。
// - 顶上一个滑动开关选课程：C 语言 | 数据结构。两门课是两台 XDOJ、两套账号，两个选项一直都在；
//   数据结构那台还没接入（.env 的 XDOJ_DS_URL 没填）时，照样能切过去看，但表单说明还没接入、不能登录。
// - 背景是立体场景（CourseScene）：C 语言是内存单元围成的开口的环，切到数据结构时整个环转过来，变成深蓝的循环链表和树。
// - 学生和老师一个入口：填学号或工号。后端去 XDOJ 核对，XDOJ 说是教师就进教师端。
// - 文字只留必要的：标题、一句话、三个要点、登录框；不写操作说明。
// - 配色和登录以后的页面是同一套（<html data-course>）：C 语言暖色的纸，数据结构冷色的纸，图案是深蓝的。
import { useEffect, useState, type FormEvent } from "react";
import { ArrowRight, Braces, Eye, EyeOff } from "lucide-react";
import { api } from "../api";
import type { CourseCode } from "../types";
import CourseScene from "../components/CourseScene";
import CourseSwitch from "../components/CourseSwitch";
import { useLoad } from "../lib/hooks";
import { useSession } from "../session";

const COPY: Record<CourseCode, { title: [string, string]; body: string; points: string[]; code: string }> = {
  C: {
    title: ["C 语言", "上机练习助手"],
    body: "提交没过、报错看不懂，直接问。",
    points: ["错因自动分析", "按你出小题", "到点提醒复习"],
    code: "return 0;",
  },
  D: {
    title: ["数据结构", "上机练习助手"],
    body: "链表、栈、树、图，卡住了直接问。",
    points: ["错因自动分析", "补 C 语言基础", "到点提醒复习"],
    code: "p = p->next;",
  },
};

const FALLBACK = [
  { code: "C" as CourseCode, name: "C 语言程序设计", server: null, available: true },
  { code: "D" as CourseCode, name: "数据结构", server: null, available: false },
];
const SHORT: Record<CourseCode, string> = { C: "C 语言", D: "数据结构" };
const KEY = "login-course";

function savedCourse(): CourseCode {
  try {
    const v = localStorage.getItem(KEY);
    return v === "D" ? "D" : "C";
  } catch {
    return "C";
  }
}

export default function Login() {
  const { setMe } = useSession();
  const info = useLoad(() => api.authInfo(), []);
  const [course, setCourse] = useState<CourseCode>(savedCourse);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const all = info.data?.allCourses?.length ? info.data.allCourses : FALLBACK;
  const chosen = all.find((c) => c.code === course) ?? all[0];
  const open = chosen.available;
  const copy = COPY[course];
  const demo = info.data?.demo ?? false;

  useEffect(() => {
    document.documentElement.dataset.course = course;   // 配色跟着课程换（tokens.css），和登录以后的页面一致
    try {
      localStorage.setItem(KEY, course);
    } catch {
      /* 存不了就算了，只是记住上次选的课 */
    }
  }, [course]);

  const pick = (code: CourseCode) => {
    setCourse(code);
    setError(null);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy || !open) return;
    setBusy(true);
    setError(null);
    try {
      await api.login(name.trim(), password, course);
      setMe(await api.me());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login" data-course={course}>
      <div className="login-bg" aria-hidden="true" />
      <CourseScene course={course} />

      <header className="login-top">
        <div className="brand">
          <span className="brand-mark"><Braces size={18} strokeWidth={2.4} /></span>
          <span className="brand-name">程序设计智能体</span>
        </div>
        <CourseSwitch value={course} onChange={pick}
          options={all.map((c) => ({ code: c.code, name: c.name, short: SHORT[c.code] }))} />
      </header>

      <section className="login-hero" aria-live="polite">
        <h1>{copy.title[0]}<br />{copy.title[1]}</h1>
        <p>{copy.body}</p>
        <ul className="login-points">
          {copy.points.map((t) => <li key={t}>{t}</li>)}
        </ul>
      </section>

      <main className="login-main">
        <form className="login-box" onSubmit={submit} aria-labelledby="login-title">
          <div className="login-form-heading">
            <h2 id="login-title">{course === "C" ? "登录 C 语言程序设计" : "登录数据结构"}</h2>
          </div>
          {!open && (
            <p className="login-closed" role="status">数据结构的 XDOJ 尚未接入</p>
          )}
          <label className="label">
            学号 / 工号
            <input className="field" value={name} onChange={(e) => setName(e.target.value)} autoComplete="username"
              autoCapitalize="none" spellCheck={false} required autoFocus disabled={!open} />
          </label>
          <label className="label">
            密码
            <span className="field-wrap">
              <input className="field" type={show ? "text" : "password"} value={password}
                onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required disabled={!open} />
              <button type="button" className="field-btn" onClick={() => setShow(!show)} disabled={!open}
                aria-label={show ? "隐藏密码" : "显示密码"}>
                {show ? <EyeOff size={17} /> : <Eye size={17} />}
              </button>
            </span>
          </label>
          {error && <p className="error-text" role="alert">{error}</p>}
          <button type="submit" className="btn btn-primary btn-block login-submit" disabled={busy || !open} aria-busy={busy}>
            {busy ? "正在登录" : "登录"}<ArrowRight size={18} aria-hidden="true" />
          </button>
          {demo && open && (
            <p className="login-demo">
              演示：学生 <code>{info.data?.demoAccount}</code>，老师 <code>demo-teacher</code>，密码为演示口令
            </p>
          )}
          {info.data && !info.data.ready && (
            <p className="error-text">服务器还没设置登录口令，请联系管理员</p>
          )}
        </form>
      </main>

      <footer className="login-bottom">
        <span>只读 XDOJ，不改动任何数据</span>
        <code aria-hidden="true">{copy.code}</code>
      </footer>
    </div>
  );
}
