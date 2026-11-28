import {useEffect,useMemo,useRef,useState} from 'react';
import {ArrowLeft,ArrowUpRight,ChevronRight,List,Search,X} from 'lucide-react';
import {CodeBlock} from './ui';
import {KnowledgeTile} from './KnowledgeTile';
import {shortTime} from '../lib/time';
import type {KnowledgeItem,LearningPortraitData,PortraitDimension,PortraitRecord} from '../types';

const LEVELS=['已掌握','基本掌握','待加强','未练习'];
const LEVEL_COLORS=['var(--portrait-mastered)','var(--portrait-basic)','var(--portrait-weak)','var(--portrait-none)'];
const knownLevel=(kp:KnowledgeItem)=>LEVELS.includes(kp.level)?kp.level:'未练习';
const pointColour=(kp:KnowledgeItem)=>{const index=LEVELS.indexOf(knownLevel(kp)),base=LEVEL_COLORS[index];if(kp.degree==null||index===3)return base;const floor=index===0?75:index===1?50:0,ceiling=index===0?100:index===1?75:50;const amount=40+60*Math.max(0,Math.min(1,(kp.degree-floor)/(ceiling-floor)));return `color-mix(in srgb, ${base} ${amount}%, var(--surface))`;};
const centre={x:205,y:174},radius=127;
function point(i:number,value:number,n=6){const a=-Math.PI/2+i*2*Math.PI/n;return{x:centre.x+Math.cos(a)*radius*value,y:centre.y+Math.sin(a)*radius*value};}
function line(values:(number|null)[],close=false){
 if(!values.length)return '';
 const complete=values.every(v=>v!==null);
 let previous=false,out='';
 values.forEach((v,i)=>{if(v===null){previous=false;return;}const p=point(i,v,values.length);out+=(previous?'L':'M')+p.x+','+p.y;previous=true;});
 return out+(complete&&close?'Z':'');
}
function axisLabel(i:number,name:string,n:number){const p=point(i,1.27,n);return <text x={p.x} y={p.y} dy={i===0?-2:i===n/2?12:5} textAnchor={i===0||i===n/2?'middle':i<n/2?'start':'end'} className="portrait-axis-label">{name}</text>;}
type Panel={title:string;records?:PortraitRecord[];facts?:PortraitDimension['facts'];points?:KnowledgeItem[]};
function Header({title,sub,action}:{title:string;sub:string;action?:React.ReactNode}){return <header className="portrait-head"><div><h2>{title}</h2><p>{sub}</p></div>{action}</header>;}

function Radar({dimensions,history,currentLabel='本周表现',onSelect}:{dimensions:PortraitDimension[];history?:boolean;currentLabel?:string;onSelect:(dimension:PortraitDimension)=>void}){
 const n=dimensions.length,values=dimensions.map(d=>d.value);
 const low=dimensions.map(d=>d.low??null),high=dimensions.map(d=>d.high??null);
 const fullRange=low.length>0&&low.every(v=>v!==null)&&high.every(v=>v!==null);
 return <div className="portrait-radar-layout"><svg viewBox="0 0 410 355" role="group" aria-label={history?'代码风格：历史范围和最近一周表现':'六项能力分布'}>
  {(history?[.33,.66,1]:[.25,.5,.75,1]).map(r=>history?<circle key={r} cx={centre.x} cy={centre.y} r={radius*r} className="portrait-gridline"/>:<path key={r} d={line(dimensions.map(()=>r),true)} className="portrait-gridline"/>)}
  {dimensions.map((d,i)=>{const p=point(i,1,n);return <line key={d.id} x1={centre.x} y1={centre.y} x2={p.x} y2={p.y} className="portrait-spoke"/>;})}
  {history&&<>{fullRange&&<path d={line(high,true)+line(low,true)} fillRule="evenodd" className="portrait-range-band"/>}<path d={line(high,true)} className="portrait-range-high"><title>各项历史最高</title></path><path d={line(low,true)} className="portrait-range-low"><title>各项历史最低</title></path></>}
  <path d={line(values,true)} style={values.some(v=>v===null)?{fill:'none'}:undefined} className={history?'portrait-week-line':'portrait-ability-line'}/>
  {dimensions.map((d,i)=>{const p=d.value!==null?point(i,d.value,n):null;return <g key={d.id} role="button" tabIndex={0} aria-label={`查看${d.name}依据`} onClick={()=>onSelect(d)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onSelect(d);}}} className="portrait-radar-control">
   {p&&<><circle cx={p.x} cy={p.y} r={16} className="portrait-hit"/><circle cx={p.x} cy={p.y} r={4.5} className={history?'portrait-week-point':'portrait-ability-point'}/></>}
   {axisLabel(i,d.name,n)}<title>{d.name}：{d.value===null?'暂无可用记录':(d.state||'依据 '+d.count+' 道题')}</title>
  </g>;})}
 </svg><div className="portrait-dimensions-wrap">{history&&<div className="portrait-legends"><span><i className="portrait-legend-range"/>历史范围</span><span><i className="portrait-legend-week"/>{currentLabel}</span></div>}<div className="portrait-dimensions" aria-label={history?'代码风格细项':'能力细项'}>{dimensions.map(d=><button type="button" key={d.id} onClick={()=>onSelect(d)}><span>{!history&&<i className={'portrait-status-dot '+(d.value===null?'none':d.value>=.75?'good':d.value>=.5?'basic':'weak')}/>} {d.name}</span><small>{d.value===null?'暂无记录':d.state||(d.value>=.75?'较规范':d.value>=.5?'基本规范':'待改进')}</small><ChevronRight size={12}/></button>)}</div></div></div>;
}

function arc(start:number,end:number){const p=(a:number,r:number)=>({x:210+Math.cos(a)*r,y:210+Math.sin(a)*r}),a=p(start,188),b=p(end,188),c=p(end,99),d=p(start,99),big=end-start>Math.PI?1:0;return `M${a.x},${a.y}A188,188 0 ${big} 1 ${b.x},${b.y}L${c.x},${c.y}A99,99 0 ${big} 0 ${d.x},${d.y}Z`;}
function Mastery({points,open}:{points:KnowledgeItem[];open:(panel:Panel)=>void}){
 const [path,setPath]=useState<string[]>([]),[query,setQuery]=useState('');
 const selected=points.filter(p=>(!path[0]||knownLevel(p)===path[0])&&(!path[1]||(p.group||'其他')===path[1]));
 const names=path.length===0?LEVELS:path.length===1?[...new Set(selected.map(p=>p.group||'其他'))]:selected.map(p=>p.id||p.name);
 const parts=names.map((name,index)=>({key:name,name:path.length===2?selected[index].name:name,items:path.length===0?selected.filter(p=>knownLevel(p)===name):path.length===1?selected.filter(p=>(p.group||'其他')===name):[selected[index]],index})).filter(p=>p.items.length);
 const percentage=points.length?Math.round(100*points.filter(p=>knownLevel(p)==='已掌握').length/points.length):null;
 const focus=useRef<(SVGPathElement|null)[]>([]);
 const go=(part:typeof parts[number])=>{setQuery('');if(path.length<2)setPath([...path,part.name]);else open({title:part.name,points:part.items});};
 let angle=-Math.PI/2;
 const sectors=parts.map(part=>{const start=angle,end=angle+2*Math.PI*part.items.length/(selected.length||1);angle=end;return{...part,start:start+.006,end:end-.006};});
 const colour=(part:typeof parts[number])=>path.length===0?LEVEL_COLORS[LEVELS.indexOf(part.name)]:path.length===1?`var(--portrait-domain-${part.index%4+1})`:pointColour(part.items[0]);
 return <section className="card portrait-card portrait-mastery"><Header title="知识掌握" sub="哪些知识已经掌握" action={<button className="portrait-small-button" onClick={()=>open({title:'全部知识点',points})}><List size={13}/>知识点列表</button>}/>
  <div className="portrait-breadcrumb"><button onClick={()=>{setPath([]);setQuery('');}}>全部知识</button>{path.map((part,i)=><span key={part}><ChevronRight size={11}/><button onClick={()=>{setPath(path.slice(0,i+1));setQuery('');}}>{part}</button></span>)}{path.length>0&&<button className="portrait-back" aria-label="返回上一层知识" onClick={()=>{setPath(path.slice(0,-1));setQuery('');}}><ArrowLeft size={13}/></button>}</div>
  <div className="portrait-mastery-body"><svg viewBox="0 0 420 420" role="group" aria-label="知识掌握，点击分区逐层展开">
   {sectors.map((part,i)=><path key={part.key} ref={el=>{focus.current[i]=el;}} className="portrait-sector" d={arc(part.start,part.end)} fill={colour(part)} role="button" tabIndex={i===0?0:-1} aria-label={`${part.name}，${part.items.length} 个知识点`} onClick={()=>go(part)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go(part);}else if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();focus.current[(i+(e.key==='ArrowRight'?1:parts.length-1))%parts.length]?.focus();}else if(e.key==='Escape'){setPath(path.slice(0,-1));}}}><title>{part.name} · {part.items.length} 个知识点</title></path>)}
   <text x="210" y="211" dominantBaseline="central" textAnchor="middle" className="portrait-centre-value">{percentage===null?'—':<>{percentage}<tspan>%</tspan></>}</text>
  </svg><div className="portrait-mastery-nav"><div className="portrait-level-heading"><b>{path.length===0?'掌握档位':path.length===1?'知识大类':'具体知识点'}</b><small>{parts.length} 项</small></div>
   {path.length===2&&<label className="portrait-search"><Search size={13}/><input placeholder="搜索知识点" aria-label="搜索本层知识点" value={query} onChange={e=>setQuery(e.target.value)}/></label>}
   <div className="portrait-level-list">{parts.filter(p=>!query||p.name.includes(query)).map(part=><button key={part.key} onClick={()=>go(part)}><i style={{background:colour(part)}}/><span>{part.name}</span>{path.length<2&&<b>{part.items.length}<small>{selected.length?Math.round(100*part.items.length/selected.length):0}%</small></b>}<ChevronRight size={12}/></button>)}</div>
   <p>{path.length<2?'点选一项，继续查看下一层':'点选知识点，查看练习依据'}</p>
  </div></div><footer className="portrait-footer"><span>整体已掌握占比</span><button onClick={()=>open({title:path.join(' · ')||'全部知识点',points:selected})}>查看本层细项<ArrowUpRight size={12}/></button></footer>
 </section>;
}

export default function LearningPortrait({data,knowledge,onPractice}:{data:LearningPortraitData;knowledge:{topic:string;items:KnowledgeItem[]}[];onPractice?:(key:string)=>void}){
 const [panel,setPanel]=useState<Panel|null>(null),[period,setPeriod]=useState<'week'|'all'>('week'),[search,setSearch]=useState('');
 const dialog=useRef<HTMLDialogElement>(null),trigger=useRef<HTMLElement|null>(null);
 const points=useMemo(()=>knowledge.flatMap(g=>g.items),[knowledge]);
 const open=(next:Panel)=>{trigger.current=document.activeElement as HTMLElement;setSearch('');setPanel(next);};
 const close=()=>{dialog.current?.close();setPanel(null);trigger.current?.focus({preventScroll:true});};
 useEffect(()=>{if(panel)dialog.current?.showModal();},[panel]);
 const mis=data.misconceptions,style=data.style;
 const records=mis.records.filter(r=>period==='all'||r.time!==null&&r.time>=mis.weekStart);
 const counts=mis.patterns.map(p=>mis.domains.map(d=>records.filter(r=>r.pattern===p.id&&r.domain===d)));
 const heat=(n:number)=>n===0?'var(--surface)':`var(--portrait-heat-${Math.min(5,n)})`;
 const inspect=(d:PortraitDimension)=>open({title:d.name,records:d.records,facts:d.facts});
 return <><div className="learning-portrait" aria-label="四项学情画像">
  <section className="card portrait-card"><Header title="能力分布" sub="从理解程序，到独立解决问题" action={<span className="portrait-mini-legend"><i/>当前表现</span>}/><Radar dimensions={data.abilities} onSelect={inspect}/><footer className="portrait-footer"><span>OJ 练习为主 · 对话与复习补充</span><button onClick={()=>inspect(data.abilities[0])}>查看依据<ArrowUpRight size={12}/></button></footer></section>
  <Mastery points={points} open={open}/>
  <section className="card portrait-card portrait-misconceptions"><Header title="误区分布" sub="提交错误与复习误判，集中在哪些知识上" action={<div className="portrait-period" aria-label="误区统计范围">{(['week','all'] as const).map(p=><button key={p} aria-pressed={period===p} onClick={()=>setPeriod(p)}>{p==='week'?'本周':'本学期'}</button>)}</div>}/>
   <div className="portrait-matrix-caption">{records.length?`记录到 ${records.length} 条相关问题`:'当前范围暂无已记录的问题'}</div>
   <div className="portrait-matrix-scroll"><table className="portrait-matrix"><caption className="sr-only">误区类别与知识领域，数字为记录次数，点选查看依据</caption><thead><tr><th>误区类别</th>{mis.domains.map(d=><th key={d}>{d}</th>)}</tr></thead><tbody>{mis.patterns.map((p,r)=><tr key={p.id}><th scope="row">{p.name}</th>{mis.domains.map((d,c)=>{const entries=counts[r][c],observed=mis.observed.includes(d);return <td key={d}><button type="button" className={entries.length>=4?'portrait-heat-strong':observed?'':'portrait-unobserved'} style={{background:heat(entries.length)}} aria-label={`${p.name}，${d}，${observed?entries.length+' 条记录':'暂无观察'}`} onClick={()=>open({title:d+' · '+p.name,records:entries})} onKeyDown={e=>{const delta=e.key==='ArrowRight'?1:e.key==='ArrowLeft'?-1:e.key==='ArrowDown'?mis.domains.length:e.key==='ArrowUp'?-mis.domains.length:e.key==='Home'?-c:e.key==='End'?mis.domains.length-c-1:0;if(delta){e.preventDefault();const all=[...e.currentTarget.closest('tbody')!.querySelectorAll<HTMLButtonElement>('td button')],index=r*mis.domains.length+c,target=all[Math.max(0,Math.min(all.length-1,index+delta))];target?.focus({preventScroll:true});target?.scrollIntoView({block:'nearest',inline:'nearest'});}}}>{observed?entries.length:'—'}</button></td>;})}</tr>)}</tbody></table></div>
   <footer className="portrait-footer"><div className="portrait-heat-legend"><span>次数</span>{[0,1,2,3,4,5].map(n=><span key={n}><i style={{background:heat(n)}}/>{n===5?'5+':n}</span>)}</div><span>点选格子查看记录</span></footer>
  </section>
  <section className="card portrait-card"><Header title="代码风格" sub="本周表现与本学期的历史范围" action={<span className="portrait-date">{style.shownWeek?style.shownWeek.slice(5).replace('-','.'):'暂无代码'}</span>}/><Radar dimensions={style.dimensions} history currentLabel={style.isCurrent?'本周表现':'最近一周'} onSelect={inspect}/><footer className="portrait-footer"><span>{style.shownWeek?(style.isCurrent?'本周':'最近一周')+' '+style.count+' 道题':'还没有可分析的代码'}{style.pending>0?' · 分析中':''}</span><button onClick={()=>open({title:'代码风格与复杂度依据',records:style.dimensions.flatMap(d=>d.records).filter((r,i,all)=>all.findIndex(a=>a.problem?.key===r.problem?.key)===i)})}>查看代码与复杂度<ArrowUpRight size={12}/></button></footer></section>
 </div>
 <dialog ref={dialog} className="portrait-dialog" aria-labelledby="portrait-dialog-title" onCancel={e=>{e.preventDefault();close();}}><header><div><span>学情依据</span><h2 id="portrait-dialog-title">{panel?.title}</h2></div><button className="icon-btn" aria-label="关闭学情依据" onClick={close}><X size={20}/></button></header><div className="portrait-dialog-body">
  {panel?.facts&&<div className="portrait-evidence-facts">{panel.facts.map(f=><div key={f.label}><span>{f.label}</span><b>{f.d?`${f.n} / ${f.d}`:'暂无记录'}</b></div>)}</div>}
  {panel?.points&&<><label className="portrait-search"><Search size={14}/><input aria-label="搜索知识点卡片" placeholder="搜索知识点" value={search} onChange={e=>setSearch(e.target.value)}/></label><div className="kp-grid">{panel.points.filter(k=>!search||k.name.includes(search)).map(k=><div key={k.id||k.name}><KnowledgeTile kp={k} onPractice={onPractice}/>{k.degree!=null&&<div className="portrait-point-degree"><span>掌握程度</span><span><i style={{width:k.degree+'%',background:pointColour(k)}}/></span><b>{k.degree}</b></div>}</div>)}</div></>}
  {panel?.records?.length===0&&<p className="note">这里还没有可展示的记录。</p>}
  {panel?.records?.map((record,i)=><article className="portrait-record" key={i}><div><strong>{record.title||record.problem?.title||record.source}</strong><small>{record.source}{record.time?' · '+shortTime(record.time):''}</small></div><p>{record.text}</p>{record.lines&&<CodeBlock caption="当时提交的代码" lines={record.lines} mark={record.mark}/>}<div className="portrait-complexity">{[record.timeComplexity,record.spaceComplexity].map((value,j)=>value&&<div key={j}><span>{j===0?'时间复杂度':'空间复杂度'}</span><b>{value.value||'暂无结论'}</b><p>{value.variable}</p><p>{value.reason}</p></div>)}</div></article>)}
 </div></dialog></>;
}
