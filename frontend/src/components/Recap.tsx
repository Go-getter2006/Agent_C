// 小题做完、反馈解释过以后，智能体回顾他以前在这里出过的错（后端 app/recap.py）：
// 程序整理的事实（每一条可以点开：作业题回到那道题的对话，小题回到那次作答），大模型对这几次错在哪的分析。
// 第一次打开时后端生成（这时显示“正在输入”），之后用存的。之后可以接着问。
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { QuizRecap } from "../types";

/** 取这次作答的回顾。undefined：还在取；null：没有要回顾的。 */
export function useRecap(attemptId: number, done: boolean): QuizRecap | null | undefined {
  const [recap, setRecap] = useState<QuizRecap | null | undefined>(undefined);
  useEffect(() => {
    if (!done) { setRecap(undefined); return; }
    let alive = true;
    setRecap(undefined);
    api.quizRecap(attemptId).then((r) => { if (alive) setRecap(r.recap); }).catch(() => { if (alive) setRecap(null); });
    return () => { alive = false; };
  }, [attemptId, done]);
  return recap;
}

export function RecapBody({ recap }: { recap: QuizRecap | undefined }) {
  if (recap === undefined) return <span className="typing" role="status" aria-label="正在输入"><i /><i /><i /></span>;
  return (
    <div className="recap">
      <p>{recap.intro}</p>
      <ul className="recap-list">
        {recap.items.map((it, i) => (
          <li key={i}>
            <Link className="link" to={it.kind === "problem" ? `/homework/${it.key}` : `/review/a${it.attemptId}`}>{it.title}</Link>
            <span>：{it.text}</span>
          </li>
        ))}
      </ul>
      {recap.analysis && <p>{recap.analysis}</p>}
      <p>{recap.outro}</p>
    </div>
  );
}

/** 嵌在小题里（作业引导的对话里出的小题）：没有要回顾的就什么都不显示。 */
export default function Recap({ attemptId }: { attemptId: number }) {
  const recap = useRecap(attemptId, true);
  if (recap === null) return null;
  return <RecapBody recap={recap} />;
}
