// 教师端的路由。只看自己名下的班。分三块（docs/教师端结构.md）：班级（上课用）、研究（论文用）、数据；
// 研究和数据只给开发者账号，老师打开这几个地址回到总览。
import { Navigate, Route, Routes } from "react-router-dom";
import type { ReactElement } from "react";
import { staffRoot } from "../../lib/staff";
import { ClassesProvider, TeacherPage, isDeveloper, useClasses } from "./common";
import Cross from "./Cross";
import Help from "./Help";
import KnowledgeMap from "./KnowledgeMap";
import Misconceptions from "./Misconceptions";
import Logs from "./Logs";
import Models from "./Models";
import Rating from "./Rating";
import ClassOverview from "./Overview";
import Sources from "./Sources";
import StudentDetail from "./StudentDetail";
import Students from "./Students";
import Submissions from "./Submissions";

const MAP_SUB = {
  C: "哪些知识点没学好，谁卡在这里。箭头从前置知识指向后续知识",
  D: "哪些知识点没学好，谁卡在这里。箭头从前置知识指向后续知识；可以加上要用到的 C 语言基础（只有掌握程度）",
};

function Routed() {
  const { course, loading, role } = useClasses();
  const root = staffRoot(role);
  const overview = <Navigate to={`${root}/class`} replace />;
  // 研究、数据两块：只给开发者；还不知道是谁时先不画（免得老师那边先发一次请求）
  const dev = (el: ReactElement) => (loading ? null : isDeveloper(role) ? el : overview);
  return (
      <Routes>
        {/* 班级（上课用） */}
        <Route path="class" element={<TeacherPage title="班级总览" sub="这周班里怎么样，先关注谁、课上讲什么" exportPage={{ page: "overview" }}>{(cls) => cls && <ClassOverview cls={cls} />}</TeacherPage>} />
        <Route path="students" element={<TeacherPage title="学生" sub="每个学生做得怎么样" exportPage={{ page: "students", csv: true }}>{(cls) => cls && <Students cls={cls} />}</TeacherPage>} />
        <Route path="students/:id" element={<StudentDetail />} />
        <Route path="submissions" element={<TeacherPage title="提交记录" sub="班里每一次提交，点开看代码和测试点" exportPage={{ page: "submissions", csv: true }}>{(cls) => cls && <Submissions cls={cls} />}</TeacherPage>} />
        <Route path="misconceptions" element={<TeacherPage title="误区" sub="课上要强调的错误点：谁、在哪道题、他们怎么想的" exportPage={{ page: "misconceptions", csv: true }}>{(cls) => cls && <Misconceptions cls={cls} />}</TeacherPage>} />
        <Route path="map" element={<TeacherPage title="知识图谱" wide sub={MAP_SUB[course]} exportPage={{ page: "map", csv: true }}>{(cls) => cls && <KnowledgeMap cls={cls} />}</TeacherPage>} />
        {/* 只在数据结构这边有（导航里也只在那边出现）；登录 C 语言时打开这个地址会回到总览 */}
        <Route path="cross" element={course === "D" || loading
          ? <TeacherPage title="跨课程" sub="要先补哪些 C 语言基础。只有掌握程度，不含 C 语言的答题记录">{(cls) => cls && !loading && <Cross cls={cls} />}</TeacherPage>
          : overview} />
        {/* 研究（论文用，只给开发者） */}
        <Route path="models" element={dev(<TeacherPage title="模型检验" sub="掌握程度怎么算更准：用学生之后的真实表现来检验" exportPage={{ page: "models" }}>{(cls) => cls && <Models cls={cls} />}</TeacherPage>)} />
        <Route path="rating" element={dev(<TeacherPage title="理解评估" sub="智能体评“理解”和只看事实的规则对照：一致率、按事实下调了多少" exportPage={{ page: "rating" }}>{(cls) => cls && <Rating cls={cls} />}</TeacherPage>)} />
        <Route path="help" element={dev(<TeacherPage title="求助效果" sub="来问的和没来问的，卡住以后怎么样了；每次帮助之后怎么样了" exportPage={{ page: "help", csv: true }}>{(cls) => cls && <Help cls={cls} />}</TeacherPage>)} />
        {/* 数据（只给开发者） */}
        <Route path="logs" element={dev(<TeacherPage title="日志导出" withClass={false} sub="学生的使用记录，带学号和姓名">{() => <Logs />}</TeacherPage>)} />
        <Route path="sources" element={dev(<TeacherPage title="教师与班级" withClass={false} sub="管理本课程的教师账号、班级和 XDOJ 同步">{() => <Sources />}</TeacherPage>)} />
        {/* 旧地址：原来的“卡点”页（谁卡在哪个知识点）现在在知识图谱的表和知识点详情里；“模型对比”改叫模型检验 */}
        <Route path="stuck" element={<Navigate to={`${root}/map?view=table&sort=stuck`} replace />} />
        <Route path="graph" element={<Navigate to={`${root}/models`} replace />} />
        <Route path="*" element={overview} />
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
