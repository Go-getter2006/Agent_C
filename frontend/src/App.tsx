import { lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Login from "./pages/Login";
import Progress from "./pages/Progress";
import Review from "./pages/Review";
import Submissions from "./pages/Submissions";
import Workbench from "./pages/Workbench";
import { HomeworksProvider, SessionProvider, StatusProvider, useSession } from "./session";

const Teacher = lazy(() => import("./pages/teacher"));

function Routed() {
  const { me, checked } = useSession();
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
        <Route path="/chat" element={<Workbench />} />
        <Route path="/chat/:key" element={<Workbench />} />
        <Route path="/submissions" element={<Submissions />} />
        <Route path="/review" element={<Review />} />
        <Route path="/progress" element={<Progress />} />
        <Route path="*" element={<Navigate to="/chat" replace />} />
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
