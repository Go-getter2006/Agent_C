import { Meter, levelClass } from './ui';
import type { KnowledgeItem } from '../types';

/** 原学情页知识卡片：保留事实、掌握档位及继续练习入口。 */
export function KnowledgeTile({ kp, onPractice }: { kp: KnowledgeItem; onPractice?: (key: string) => void }) {
  return <div className={`kp-tile ${levelClass(kp.level)}${kp.covered ? '' : ' kp-uncovered'}`}>
    <div className="kp-tile-top"><strong>{kp.name}</strong><Meter level={kp.level} segments={kp.segments}/></div>
    <span className="kp-basis">{kp.basis}</span>
    {kp.practice && onPractice && <button type="button" className="link kp-practice" onClick={()=>onPractice(kp.practice!.key)}
      aria-label={`去练一题：${kp.practice.title}`}>去练一题：{kp.practice.title}</button>}
  </div>;
}
