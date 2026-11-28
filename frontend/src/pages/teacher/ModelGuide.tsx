import { Card } from "../../components/ui";
import type { GraphData } from "./Models";

export default function ModelGuide({ d }: { d: GraphData }) {
  const rules = d.scoringRules;
  return <Card title="掌握分、作业综合分和预测概率，各代表什么" sub="这里汇总学生端与教师端所用数字的计算依据。">
    <div className="model-guide">
      <details className="model-explanation">
        <summary>学情画像的六项能力、误区和代码风格</summary>
        <p>能力图为行为和作品的描述性画像，不写回掌握模型。程序理解优先用已有的代码理解量规，其次用对话量规；没有理解评估时使用OJ作品中的概念应用量规，再列复习首次判断。方案设计、效率分析来自有源码依据的代码作品量规，不能解释成独立设计能力或口头复杂度分析能力已被验证。</p>
        <p>编码实现为做过的不同题目中最终通过的比例；调试修复为曾经出错的不同题中后来通过的比例，不能当作无帮助独立改对。测试验证优先描述代码对题意要求的边界和特殊情况的处理，复习首次判断作为补充，不意味着学生主动设计过测试。点击每项可查原记录；没有记录不补零，不跨缺失轴闭合曲线。</p>
        <p>代码风格每个北京时间自然周、每道题取最后一份通过稿；这周未通过则取最新稿。模型按0至3档给六项作品量规，各题等权平均；不适用与低置信度结论不进分母。浅色轮廓分别为每项周均值的历史最低与最高，不是某个真实周的整套成绩，也不是置信区间。本周未结束，其结果仍会变化。</p>
        <p>新的代码画像按题面、源码、结果、模型名与量规版本做指纹，结果持久存库。相同代码不重复调用；一份代表稿及其去重修改轨迹一次请求，逐稿持久保存，避免整周输出截断；已有八项作品量规只补缺少的能力项，网页只排队和读结果。失败后延迟重试，最多三次，不用固定模板补分析。排版保留缩进规则对照，其他语义维度没有可靠规则时不伪造一致率。该量规仍需人工抽样验证。</p>
        <p>误区热力图按既有错误类型映射为粗类别，按原提交、首次判断或会话时间统计，晚导入不改变归属周。数字是已保存的相关记录数，不是错误率，也不能仅凭代码出错断言存在概念误解。量规与记录可从“代码画像评估”导出。</p>
      </details>
      <details className="model-explanation">
        <summary>技能与知识点掌握分</summary>
        <p>当前配置为 {rules.model}。先把与技能相关的行为记成正面证据 S 和负面证据 F。每条证据依次乘事件分量、可信度、每周学习的系数，以及时间衰减。</p>
        <p className="model-formula">技能掌握分 = 100 × (1 + S) ÷ (2 + S + F)</p>
        <p>忽略衰减和额外系数时，一次分量为 1 的成功得到 66.7 分，两次得到 75 分。知识点内各细技能再按有效证据量 S + F 加权平均。</p>
        <p>四舍五入后，{rules.mastered} 分及以上为已掌握，{rules.basic} 至 {rules.mastered - 1} 分为基本掌握，更低为待加强。实际作答记录或证据量不足时显示还没练到。</p>
        <div className="table-scroll"><table className="table"><thead><tr><th scope="col">行为</th><th scope="col">默认证据基数与调整</th></tr></thead><tbody>
          <tr><td>首交通过，系统内未记录求助</td><td>正面证据 1</td></tr>
          <tr><td>修改后通过，系统内未记录求助</td><td>正面证据 0.8</td></tr>
          <tr><td>反问或指出位置后通过</td><td>正面证据 0.5</td></tr>
          <tr><td>讲解原理后通过 / 给示例后通过</td><td>正面证据 0.2 / 0</td></tr>
          <tr><td>同一道题、同一技能的已标注失败</td><td>第一次 0.5，第二次 0.25；随后改正时，单次失败分量保留 {Math.round(rules.resolvedFailure * 100)}%，反复失败保留 {Math.round(rules.repeatedFailure * 100)}%</td></tr>
          <tr><td>疑似雷同的通过</td><td>正面证据乘 {rules.copyFactor}，用作可信度调整</td></tr>
          <tr><td>题目中的次技能</td><td>专门考查的乘 {rules.secondaryTested}；顺带涉及的乘 {rules.secondaryIncidental}</td></tr>
          <tr><td>复习作答与理解评估</td><td>按实际作答、帮助情况或理解等级记入证据；理解评估还乘置信度</td></tr>
        </tbody></table></div>
        <p>旧证据按 0.5^(间隔天数 ÷ 半衰期) 衰减。半衰期随隔天成功和失败情况调整，各类系数每周拟合并冻结，实际版本见下方参数表。以上分数与阈值需要通过后续独立表现持续验证。</p>
      </details>
      <details className="model-explanation">
        <summary>作业综合分为什么会偏低</summary>
        <p className="model-formula">作业综合分 = 已练知识点平均掌握分 × (题目覆盖率 + 知识点覆盖率) ÷ 2</p>
        <p>题目覆盖率按有明确评测结果的不同题目计数；知识点覆盖率按这些题涉及的知识点计数。同一题重复提交只覆盖一道题。当前分母是班级布置的全部不同题目及其知识点，包含后续布置的作业。</p>
        <p>这里先对有评分的已练知识点等权平均；每个知识点内部，再按细技能的证据量加权。练过某个知识点的相关题目，表示覆盖了它；这一步也计入尚未通过的题目。</p>
        <p>例如，已练部分平均 80 分，题目覆盖 40%，知识点覆盖 60%，得到 80 × (0.4 + 0.6) ÷ 2 = 40 分。分数降低主要来自覆盖不足。查看学生情况时应同时看掌握质量和覆盖数量。</p>
        <div className="table-scroll"><table className="table"><caption>当前班级的实际分数拆解</caption><thead><tr>
          <th scope="col">学生</th><th scope="col" className="num">掌握均分</th><th scope="col">题目覆盖</th><th scope="col">知识点覆盖</th><th scope="col" className="num">综合分</th>
        </tr></thead><tbody>{d.scoreBreakdown.map(row => <tr key={row.userId}>
          <td>{row.name}</td><td className="num">{row.quality ?? "—"}</td>
          <td>{row.coveredProblems} / {row.totalProblems} 题{row.totalProblems ? `（${(100 * row.coveredProblems / row.totalProblems).toFixed(1)}%）` : ""}</td>
          <td>{row.coveredTopics} / {row.totalTopics} 个{row.totalTopics ? `（${(100 * row.coveredTopics / row.totalTopics).toFixed(1)}%）` : ""}</td>
          <td className="num">{row.score ?? "—"}</td>
        </tr>)}</tbody></table></div>
        <p>这张表使用当前班级作业范围与当前掌握分。计算时先使用完整精度，再将显示分数取整。上方日期筛选用于预测记录的检验。</p>
        <p>该分数用于概括作业练习范围与已练部分表现。课程考试成绩与这项诊断指标应分别报告。</p>
      </details>
      <details className="model-explanation">
        <summary>三个预测模型具体怎么算</summary>
        <div className="table-scroll"><table className="table"><thead><tr><th scope="col">模型</th><th scope="col">输入与计算</th><th scope="col">输出</th></tr></thead><tbody>
          <tr><td>简单累计模型（v1）</td><td>按固定事件权重累计 S、F，每过一天乘 0.98；概率为 (1 + S) ÷ (2 + S + F)。作为历史基线保留。</td><td>用于与首交通过结果比较的估计概率</td></tr>
          <tr><td>证据加权模型（v2）</td><td>考虑已标注失败、改正后的失败折扣、通过可信度、个体半衰期和理解评估。研究预测另使用先修知识给定的起点，概率为 (a₀ + S) ÷ (a₀ + b₀ + S + F)。学生掌握分使用上方自身证据公式。</td><td>带先修起点的预测概率</td></tr>
          <tr><td>先修关系模型</td><td>把各技能连成“会 / 不会”的网络，使用作答证据。先验为 σ({d.params.beta} + {d.params.gamma} × 先修技能掌握比例)。证据似然采用猜对率 {d.params.guess}、失误率 {d.params.slip}，通过吉布斯采样求各技能掌握概率。</td><td>当前版再用 {d.params.guess} + (1 − {d.params.slip} − {d.params.guess}) × 掌握概率，换算首交通过概率</td></tr>
        </tbody></table></div>
        <p>例如，累计模型得到 S = 2、F = 1 时，概率为 (1 + 2) ÷ (2 + 2 + 1) = 60%。证据加权模型先调整每条证据的可信度与时间分量，再汇总成 S 和 F。</p>
        <p>证据加权模型的研究预测中，a₀ = 1 + κm，b₀ = 1 + κ(1 − m)。m 为先修技能的加权掌握估计，κ 表示先修影响的分量。以 m = 0.8、κ = 1、S = 2、F = 1 为例，预测概率为 3.8 ÷ 6，约 63.3%；同一组 S、F 对应的自身掌握分为 60 分。</p>
        <p>先修关系模型把每个技能设为“会 / 不会”两个状态。程序反复更新各节点，同时核对它的作答证据，以及先修、后续节点之间是否相容，再平均得到掌握概率。掌握时成功证据的概率为 {1 - d.params.slip}，未掌握时为 {d.params.guess}，所以成功证据会支持“会”，失败证据支持“不会”。</p>
        <p>假设最终掌握概率为 90%，按当前猜对率、失误率换算的首交通过概率为 {Math.round((d.params.guess + (1 - d.params.slip - d.params.guess) * .9) * 100)}%。实际预测还会随学生后续证据更新。</p>
        <p>σ(x) = 1 ÷ (1 + e⁻ˣ)，将计算结果映射到 0～1。每条预测日志保存当时各模型的结果。切换日期和版本会筛选已保存记录。</p>
        <p>图模型先修关系状态为“{d.prereqStatus}”，其参数目前为经验值。历史版直接保存掌握估计，当前版保存换算后的通过概率，比较时应保留这个差别。</p>
      </details>
    </div>
  </Card>;
}
