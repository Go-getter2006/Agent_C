import { lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { landing, rememberLocation } from "./lib/memory";
import Knowledge from "./pages/Knowledge";
import Login from "./pages/Login";
import Progress from "./pages/Progress";
import Review from "./pages/Review";
import Submissions from "./pages/Submissions";
import Workbench from "./pages/Workbench";
import { HomeworksProvider, SessionProvider, StatusProvider, useSession } from "./session";

const Teacher = lazy(() => import("./pages/teacher"));

/** 旧链接：/chat/general 是以前的综合对话，到知识问答里；/chat/题目 到作业引导，查询参数（看哪次提交、对比哪两次）照带。 */
function LegacyChat() {
  const { key } = useParams();
  const { search } = useLocation();
  if (!key || key === "general") return <Navigate to="/ask/general" replace />;
  const params = new URLSearchParams(search);
  const ask = params.get("ask");   // 以前的“问问为什么错”：现在只带上那次提交，不替学生自动发问
  if (ask && !params.get("sub")) params.set("sub", ask);
  params.delete("ask");
  const rest = params.toString();
  return <Navigate to={`/homework/${key}${rest ? `?${rest}` : ""}`} replace />;
}

function Routed() {
  const { me, checked } = useSession();
  const location = useLocation();
  // 记下学生每个栏目最近的位置（换栏目再回来、下次登录时用）
  useEffect(() => {
    if (me?.role === "student") rememberLocation(location.pathname + location.search);
  }, [me?.role, location.pathname, location.search]);
  // 登录以后的配色跟着这次登录的课程（tokens.css 里两套纸面）；登录页自己跟着课程开关设
  useEffect(() => {
    if (me?.course) document.documentElement.dataset.course = me.course;
  }, [me?.course]);
  if (!checked) return null;
  if (!me) return <Login />;
  if (me.role !== "student") {
    return (
      <Suspense fallback={null}>
        <Routes>
          <Route path="/teacher/*" element={<Teacher />} />
          <Route path="*" element={<Navigate to="/teacher" replace />} />
        </Routes>
      </Suspense>
    );
  }
  return (
    <StatusProvider>
      <HomeworksProvider>
      <Routes>
        <Route path="/ask" element={<Knowledge />} />
        <Route path="/ask/:topic" element={<Knowledge />} />
        <Route path="/homework" element={<Workbench />} />
        <Route path="/homework/:key" element={<Workbench />} />
        <Route path="/chat" element={<Navigate to="/homework" replace />} />
        <Route path="/chat/:key" element={<LegacyChat />} />
        <Route path="/submissions" element={<Submissions />} />
        <Route path="/review" element={<Review />} />
        <Route path="/review/:key" element={<Review />} />
        <Route path="/progress" element={<Progress />} />
        <Route path="*" element={<Navigate to={landing()} replace />} />
      </Routes>
      </HomeworksProvider>
    </StatusProvider>
  );
}

export default function App() {
  return (
    <SessionProvider>
      <BrowserRouter>
        <Routed />
      </BrowserRouter>
    </SessionProvider>
  );
}
