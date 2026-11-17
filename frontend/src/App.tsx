import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Login from "./pages/Login";
import Progress from "./pages/Progress";
import Submissions from "./pages/Submissions";
import Workbench from "./pages/Workbench";
import { SessionProvider, useSession } from "./session";

const Teacher = lazy(() => import("./pages/Teacher"));

function Routed() {
  const { me, checked } = useSession();
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
    <Routes>
      <Route path="/chat" element={<Workbench />} />
      <Route path="/chat/:key" element={<Workbench />} />
      <Route path="/submissions" element={<Submissions />} />
      <Route path="/progress" element={<Progress />} />
      <Route path="*" element={<Navigate to="/chat" replace />} />
    </Routes>
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
