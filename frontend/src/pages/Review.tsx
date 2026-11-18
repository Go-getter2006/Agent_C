// 复习是对话里的主动小题；此页只负责说明安排与回顾历史。
import { Link } from "react-router-dom";
import { CircleCheck, MessageCircle } from "lucide-react";
import { api } from "../api";
import { StudentShell } from "../components/Shell";
import { Card, Empty, Facts, Loading, Meter, Problem } from "../components/ui";
import { useLoad } from "../lib/hooks";
import { futureDay } from "../lib/time";
import type { ReviewItemView } from "../types";

function Item({ item }: { item: ReviewItemView }) {
  const segments = item.memory.segments;
  return <li className="review-plan-item">
    <div><strong>{item.title}</strong><p className="note">{item.detail}</p><p className="note">
      {item.why}{item.origin && <> · <Link className="link" to={`/homework/${item.origin.key}`}>{item.origin.title}</Link></>}
    </p></div>
    <Meter level={segments >= 3 ? "已掌握" : segments === 2 ? "基本掌握" : "待加强"} segments={segments} label={item.memory.text} />
  </li>;
}

export default function Review() {
  const data = useLoad(() => api.review(), [], 30000);
  const d = data.data;
  return <StudentShell><main className="page"><div className="page-inner">
    <div className="page-head"><div><h1>复习安排</h1>
      <p className="page-sub">优先回顾做错、改正过和需要帮助的地方，偶尔抽查独立做对的题。小题会在空闲的对话里主动出现。</p>
    </div><Link className="btn" to="/homework"><MessageCircle size={16} />回到作业引导</Link></div>
    {data.loading && !d && <Loading what="正在整理复习安排" />}
    {data.error && <Problem message={data.error} onRetry={data.reload} />}
    {d && <div className="stack">
      <Facts label="复习安排" items={[
        {label:"当前待回顾",value:`${d.due.length} 项`,note:"未完成的小题会保留，下一次接着做"},
        {label:"今天已安排",value:`${d.doneToday} / ${d.dailyLimit} 道`,note:"每天控制数量，避免打断练习"},
        {label:"正在巩固",value:`${d.active} 项`,note:"根据作答情况调整下次时间"},
      ]} />
      <div className="grid-main">
        <Card title="优先回顾" sub="智能体会在综合对话空闲时递来一道小题，不插入正在进行的题目分析">
          {d.due.length ? <ul className="review-list">{d.due.map(item=><Item key={item.id} item={item} />)}</ul>
            : <Empty icon={CircleCheck} title={d.doneToday >= d.dailyLimit ? "今天的小题已安排够了" : "当前没有到期的复习"}>
              {d.doneToday >= d.dailyLimit ? "已有的小题可以在对话里继续，其他内容会按计划再出现。" : "继续练习即可，有需要回顾的内容会自动安排。"}
            </Empty>}
        </Card>
        <div className="stack"><Card title="接下来">
          {d.upcoming.length ? <ul className="review-list">{d.upcoming.map(item=><li key={item.id}>
            <span className="review-date">{item.dueNow ? "后续安排" : futureDay(item.due)}</span><div className="review-row-main"><strong>{item.title}</strong><span className="note">{item.why}</span></div>
          </li>)}</ul> : <p className="note">暂时没有其他安排。</p>}
        </Card>{d.graduated.length > 0 && <Card title="已经巩固"><ul className="review-list">
          {d.graduated.map(item=><li key={item.id}><strong>{item.title}</strong><span className="pill pill-pass">已巩固</span></li>)}
        </ul></Card>}</div>
      </div>
    </div>}
  </div></main></StudentShell>;
}
