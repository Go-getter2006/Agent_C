// 教师端的路由。只看自己名下的班。
import { Navigate, Route, Routes } from "react-router-dom";
import { ClassesProvider, TeacherPage, useClasses } from "./common";
import Cross from "./Cross";
import KnowledgeMap from "./KnowledgeMap";
import Logs from "./Logs";
import Models from "./Models";
import Rating from "./Rating";
import ClassOverview from "./Overview";
import Sources from "./Sources";
import StudentDetail from "./StudentDetail";
import Students from "./Students";
import Stuck from "./Stuck";

const MAP_SUB = {
  C: "C 语言的知识点，箭头从前置知识指向后续知识（学 B 之前要先会 A，就画 A → B）",
  D: "数据结构的知识点，箭头从前置知识指向后续知识（学 B 之前要先会 A，就画 A → B）；可以加上它要用到的 C 语言知识点（只有掌握程度，不含 C 语言的答题记录）",
};

function Routed() {
  const { course, loading } = useClasses();
  return (
      <Routes>
        <Route path="class" element={<TeacherPage title="班级总览" sub="这周全班的练习情况，和前几周比">{(cls) => cls && <ClassOverview cls={cls} />}</TeacherPage>} />
        <Route path="students" element={<TeacherPage title="学生" sub="点名字看这个学生的明细">{(cls) => cls && <Students cls={cls} />}</TeacherPage>} />
        <Route path="students/:id" element={<StudentDetail />} />
        <Route path="stuck" element={<TeacherPage title="卡点" sub="按知识点：卡在哪、卡住以后怎么样">{(cls) => cls && <Stuck cls={cls} />}</TeacherPage>} />
        <Route path="map" element={<TeacherPage title="知识图谱" wide sub={MAP_SUB[course]}>{(cls) => cls && <KnowledgeMap cls={cls} />}</TeacherPage>} />
        {/* 只在数据结构这边有（导航里也只在那边出现）；登录 C 语言时打开这个地址会回到总览 */}
        <Route path="cross" element={course === "D" || loading
          ? <TeacherPage title="跨课程" sub="这门课要用到的 C 语言基础：预警、联系和联系的数据检验。只有掌握程度，不含 C 语言的答题记录">{(cls) => cls && !loading && <Cross cls={cls} />}</TeacherPage>
          : <Navigate to="/teacher/class" replace />} />
        <Route path="models" element={<TeacherPage title="模型对比" sub="掌握程度怎么算才更准：用学生之后的真实表现来检验">{(cls) => cls && <Models cls={cls} />}</TeacherPage>} />
        <Route path="rating" element={<TeacherPage title="盲评" sub="按同一个量规评学生的理解，用来检验智能体评估">{(cls) => cls && <Rating cls={cls} />}</TeacherPage>} />
        <Route path="graph" element={<Navigate to="/teacher/models" replace />} />
        <Route path="logs" element={<TeacherPage title="日志申请" withClass={false} sub="按需取自己学生的使用记录：哪些学号、什么时候、用了哪些样例、做了哪些交互">{() => <Logs />}</TeacherPage>} />
        <Route path="sources" element={<TeacherPage title="数据源" withClass={false} sub="从 XDOJ 同步数据的状态">{() => <Sources />}</TeacherPage>} />
        <Route path="*" element={<Navigate to="/teacher/class" replace />} />
      </Routes>
  );
}

export default function Teacher() {
  return (
    <ClassesProvider>
      <Routed />
    </ClassesProvider>
  );
}
