// 登录首页。
// - 顶上一个滑动开关选课程：C 语言 | 数据结构。两门课是两台 XDOJ、两套账号，两个选项一直都在；
//   数据结构那台还没接入（.env 的 XDOJ_DS_URL 没填）时，照样能切过去看，但表单说明还没接入、不能登录。
// - 背景是立体场景（CourseScene）：C 语言是内存单元围成的开口的环，切到数据结构时整个环转过来，变成深蓝的循环链表和树。
// - 学生和老师一个入口：填学号或工号。后端去 XDOJ 核对，XDOJ 说是教师就进教师端。
import { useEffect, useState, type FormEvent } from "react";
import { ArrowRight, Braces, Eye, EyeOff } from "lucide-react";
import { api } from "../api";
import type { CourseCode } from "../types";
import CourseScene from "../components/CourseScene";
import CourseSwitch from "../components/CourseSwitch";
import { useLoad } from "../lib/hooks";
import { useSession } from "../session";

const COPY: Record<CourseCode, { eyebrow: string; title: [string, string]; body: string; points: string[]; code: string }> = {
  C: {
    eyebrow: "C 语言程序设计 · XDOJ 上机练习",
    title: ["C 语言", "上机练习助手"],
    body: "它看得到你在 XDOJ 上的每一次提交。题目没思路、编译报错、结果不对，都可以直接问。",
    points: ["没通过的提交，自动分析错在哪", "按你自己的情况出代码小题", "快忘的时候提醒复习"],
    code: "int main(void) { return 0; }",
  },
  D: {
    eyebrow: "数据结构 · XDOJ 上机练习",
    title: ["数据结构", "上机练习助手"],
    body: "链表、栈和队列、树、图的上机题，同样可以随时问。数据结构用的是另一台 XDOJ，账号和 C 语言分开。",
    points: ["没通过的提交，自动分析错在哪", "提醒你哪些 C 语言基础还要补", "快忘的时候提醒复习"],
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
        <span className="eyebrow">{copy.eyebrow}</span>
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
            <p className="page-sub">
              {course === "C" ? "学生填学号，老师填工号，密码和 C 语言 XDOJ 的一样。"
                : "学生填学号，老师填工号，密码和数据结构 XDOJ 的一样（可能和 C 语言的不同）。"}
            </p>
          </div>
          {!open && (
            <p className="login-closed" role="status">数据结构的 XDOJ 还没接入。接入以后，用那边的账号在这里登录。</p>
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
              现在是演示数据：学生学号 <code>{info.data?.demoAccount}</code>，老师工号 <code>demo-teacher</code>，
              密码是管理员给的演示口令。
            </p>
          )}
          {info.data && !info.data.ready && (
            <p className="error-text">服务器还没有设置登录口令，暂时登录不了。请联系管理员。</p>
          )}
          <p className="login-privacy">密码只用来向 XDOJ 核对。老师的密码加密保存，只用于自动读取班级的练习数据。</p>
        </form>
      </main>

      <footer className="login-bottom">
        <span>只读取 XDOJ 上的练习记录，不改动 XDOJ 上的任何数据</span>
        <code aria-hidden="true">{copy.code}</code>
      </footer>
    </div>
  );
}
