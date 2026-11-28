import {useEffect,useId,useRef,useState} from 'react';
import {ArrowLeft,ArrowUpRight,ChevronRight,List} from 'lucide-react';
import type {KnowledgeItem} from '../types';

const LEVELS=['已掌握','基本掌握','待加强','未练习'];
const COLORS=['var(--portrait-mastered)','var(--portrait-basic)','var(--portrait-weak)','var(--portrait-none)'];
const BASE=-Math.PI/2,TAU=2*Math.PI;
const level=(p:KnowledgeItem)=>LEVELS.includes(p.level)?p.level:'未练习';
export type MasteryPanel={title:string;points?:KnowledgeItem[]};
type Painted={key:string;start:number;end:number;fill:string};
type Anchor={start:number;end:number;fill:string};
type Motion={id:number;mode:'in'|'out';anchor:Anchor;previous:Painted[];from:number|null;keyboard:boolean};
const lerp=(a:number,b:number,t:number)=>a+(b-a)*t;
function selectedPoints(points:KnowledgeItem[],path:string[]){return points.filter(p=>(!path[0]||level(p)===path[0])&&(!path[1]||(p.group||'其他')===path[1]));}
function percentage(points:KnowledgeItem[],path:string[]){
 if(!path.length)return points.length?Math.round(100*points.filter(p=>level(p)==='已掌握').length/points.length):null;
 const scored=selectedPoints(points,path).filter(p=>p.degree!=null);
 return scored.length?Math.round(scored.reduce((n,p)=>n+(p.degree||0),0)/scored.length):null;
}
function colour(p:KnowledgeItem){
 const i=LEVELS.indexOf(level(p)),base=COLORS[i];if(p.degree==null||i===3)return base;
 const floor=i===0?75:i===1?50:0,ceiling=i===0?100:i===1?75:50;
 return `color-mix(in srgb, ${base} ${40+60*Math.max(0,Math.min(1,(p.degree-floor)/(ceiling-floor)))}%, var(--surface))`;
}
function arc(start:number,end:number){
 const p=(a:number,r:number)=>({x:210+Math.cos(a)*r,y:210+Math.sin(a)*r});
 const a=p(start,188),b=p(end,188),c=p(end,99),d=p(start,99),big=end-start>Math.PI?1:0;
 return `M${a.x},${a.y}A188,188 0 ${big} 1 ${b.x},${b.y}L${c.x},${c.y}A99,99 0 ${big} 0 ${d.x},${d.y}Z`;
}
function Ring({points,path,motion,painted,number,onDrill,onPoint}:{points:KnowledgeItem[];path:string[];motion:Motion|null;painted:React.MutableRefObject<Painted[]>;number:React.MutableRefObject<number|null>;onDrill:(path:string[],anchor:Anchor,keyboard:boolean)=>void;onPoint:(point:KnowledgeItem)=>void}){
 const selected=selectedPoints(points,path),names=path.length===0?LEVELS:path.length===1?[...new Set(selected.map(p=>p.group||'其他'))]:selected.map(p=>p.id||p.name);
 const parts=names.map((key,index)=>({key,name:path.length===2?selected[index].name:key,items:path.length===0?selected.filter(p=>level(p)===key):path.length===1?selected.filter(p=>(p.group||'其他')===key):[selected[index]],index}));
 const fill=(part:typeof parts[number])=>path.length===0?COLORS[LEVELS.indexOf(part.name)]:path.length===1?`var(--portrait-domain-${part.index%4+1})`:colour(part.items[0]);
 const [frame,setFrame]=useState({id:0,value:1}),[hover,setHover]=useState<{key:string;x:number;y:number}|null>(null);
 const box=useRef<HTMLDivElement>(null),refs=useRef<(SVGPathElement|null)[]>([]),tooltip=useId();
 const skip=!motion||motion.keyboard||window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 const progress=skip?1:frame.id===motion.id?frame.value:0;
 useEffect(()=>{
  if(!motion)return;
  if(motion.keyboard||window.matchMedia('(prefers-reduced-motion: reduce)').matches){setFrame({id:motion.id,value:1});if(motion.keyboard)refs.current[0]?.focus();return;}
  let raf=0;const start=performance.now(),duration=motion.mode==='in'?320:240;
  const step=(now:number)=>{const t=Math.min(1,(now-start)/duration);setFrame({id:motion.id,value:1-Math.pow(1-t,3)});if(t<1)raf=requestAnimationFrame(step);};
  raf=requestAnimationFrame(step);return()=>cancelAnimationFrame(raf);
 },[motion]);
 let angle=BASE;
 const sectors=parts.filter(p=>p.items.length).map(part=>{const start=angle,end=angle+TAU*part.items.length/(selected.length||1);angle=end;return{...part,start:start+.006,end:end-.006,fill:fill(part)};});
 const span=motion?motion.anchor.end-motion.anchor.start:TAU;
 const display=sectors.map(part=>({...part,start:motion?.mode==='in'?lerp(motion.anchor.start+(part.start-BASE)/TAU*span,part.start,progress):part.start,
  end:motion?.mode==='in'?lerp(motion.anchor.start+(part.end-BASE)/TAU*span,part.end,progress):part.end}));
 painted.current=display.map(({key,start,end,fill})=>({key,start,end,fill}));
 const percent=percentage(points,path),shown=motion&&motion.from!==null&&percent!==null?Math.round(lerp(motion.from,percent,progress)):percent;
 number.current=shown;
 const active=progress===1?parts.find(p=>p.key===hover?.key):undefined;
 const highlight=(key:string,clientX?:number,clientY?:number)=>{const b=box.current?.getBoundingClientRect();if(b)setHover({key,x:Math.max(8,Math.min(b.width-194,clientX==null?b.width/2-90:clientX-b.left+14)),y:Math.max(8,Math.min(b.height-90,clientY==null?24:clientY-b.top+16))});};
 const choose=(part:typeof parts[number],keyboard=false)=>{setHover(null);if(!part.items.length)return;
  if(path.length<2){const sector=display.find(p=>p.key===part.key)!;onDrill([...path,part.name],{start:sector.start,end:sector.end,fill:sector.fill},keyboard);}else onPoint(part.items[0]);};
 return <div className="portrait-mastery-body">
  <div className="portrait-ring-canvas" ref={box} onPointerLeave={()=>setHover(null)}>
   <svg viewBox="0 0 420 420" role="group" aria-label="知识掌握圆环" aria-busy={progress<1} data-motion-progress={progress.toFixed(3)}>
    {motion&&progress<1&&motion.previous.map((old,i)=>{const start=motion.mode==='out'?lerp(old.start,motion.anchor.start+(old.start-BASE)/TAU*span,progress):old.start;
     const end=motion.mode==='out'?lerp(old.end,motion.anchor.start+(old.end-BASE)/TAU*span,progress):old.end;
     return <path key={'previous-'+i} d={arc(start,end)} fill={old.fill} opacity={motion.mode==='in'?1-progress:1} aria-hidden="true" pointerEvents="none"/>;})}
    {display.map((part,i)=><path key={part.key} ref={el=>{refs.current[i]=el;}} className={'portrait-sector'+(active?.key===part.key?' is-hovered':'')} d={arc(part.start,part.end)} fill={part.fill}
     opacity={motion?.mode==='out'?progress:1} role="button" tabIndex={i===0?0:-1} aria-label={`${part.name}，${part.items.length} 个知识点`}
     aria-describedby={active?.key===part.key?tooltip:undefined} onPointerEnter={e=>highlight(part.key,e.clientX,e.clientY)} onPointerMove={e=>highlight(part.key,e.clientX,e.clientY)}
     onFocus={()=>highlight(part.key)} onBlur={()=>setHover(null)} onClick={e=>choose(part,e.detail===0)}
     onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choose(part,true);}else if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();refs.current[(i+(e.key==='ArrowRight'?1:display.length-1))%display.length]?.focus();}}}/>) }
    {motion?.mode==='in'&&progress<1&&<path d={arc(lerp(motion.anchor.start,BASE+.006,progress),lerp(motion.anchor.end,BASE+TAU-.006,progress))}
     fill={motion.anchor.fill} opacity={Math.max(0,1-Math.max(0,(progress-.35)/.65))} aria-hidden="true" pointerEvents="none"/>}
    <text x="210" y="211" dominantBaseline="central" textAnchor="middle" className="portrait-centre-value" aria-label={percent===null?'暂无评分':`${percent}%`}>{shown===null?'—':<>{shown}<tspan>%</tspan></>}</text>
   </svg>
   {active&&hover&&<div id={tooltip} role="tooltip" className="portrait-ring-tooltip" style={{left:hover.x,top:hover.y}}><strong>{active.name}</strong><span>{active.items.length} 个知识点 · 占本层 {selected.length?Math.round(100*active.items.length/selected.length):0}%</span>{path.length===2&&<span>{active.items[0].degree==null?'尚未练习':`掌握程度 ${active.items[0].degree}%`}</span>}</div>}
  </div>
  <div className="portrait-mastery-nav"><div className="portrait-level-heading"><b>{path.length===0?'掌握档位':path.length===1?'知识大类':'具体知识点'}</b><small>{parts.length} 项</small></div>
   <div className="portrait-level-list">{parts.map(part=><button key={part.key} disabled={!part.items.length} onClick={e=>choose(part,e.detail===0)} onPointerEnter={()=>highlight(part.key)} onPointerLeave={()=>setHover(null)} onFocus={()=>highlight(part.key)} onBlur={()=>setHover(null)}><i style={{background:fill(part)}}/><span>{part.name}</span>{path.length<2&&<b>{part.items.length}<small>{selected.length?Math.round(100*part.items.length/selected.length):0}%</small></b>}<ChevronRight size={12}/></button>)}</div>
  </div>
 </div>;
}
export function MasteryOverview({points,open}:{points:KnowledgeItem[];open:(panel:MasteryPanel)=>void}){
 const [view,setView]=useState<{path:string[];motion:Motion|null}>({path:[],motion:null}),history=useRef<Anchor[]>([]),painted=useRef<Painted[]>([]),sequence=useRef(0),number=useRef<number|null>(percentage(points,[]));
 const drill=(path:string[],anchor:Anchor,keyboard:boolean)=>{history.current[view.path.length]=anchor;setView({path,motion:{id:++sequence.current,mode:'in',anchor,keyboard,previous:painted.current,from:number.current}});};
 const back=(depth:number,keyboard:boolean)=>{const anchor=history.current[depth];if(!anchor)return;history.current=history.current.slice(0,depth);setView({path:view.path.slice(0,depth),motion:{id:++sequence.current,mode:'out',anchor,keyboard,previous:painted.current,from:number.current}});};
 return <section className="card portrait-card portrait-mastery">
  <header className="portrait-head"><div><h2>知识掌握</h2><p>哪些知识已经掌握</p></div><button className="portrait-small-button" onClick={()=>open({title:'全部知识点',points})}><List size={13}/>知识点列表</button></header>
  <nav className="portrait-breadcrumb" aria-label="当前知识层级"><button onClick={e=>back(0,e.detail===0)} disabled={!view.path.length}>全部知识</button>{view.path.map((name,i)=><span key={name}><ChevronRight size={11}/><button disabled={i===view.path.length-1} onClick={e=>back(i+1,e.detail===0)}>{name}</button></span>)}{view.path.length>0&&<button className="portrait-back" aria-label="返回上一层知识" onClick={e=>back(view.path.length-1,e.detail===0)}><ArrowLeft size={13}/></button>}</nav>
  <Ring points={points} path={view.path} motion={view.motion} painted={painted} number={number} onDrill={drill} onPoint={point=>open({title:point.name,points:[point]})}/>
  <footer className="portrait-footer"><span>{view.path.length?'本组平均掌握程度':'整体已掌握占比'}</span><button onClick={()=>open({title:view.path.join(' · ')||'全部知识点',points:selectedPoints(points,view.path)})}>查看本层知识点<ArrowUpRight size={12}/></button></footer>
 </section>;
}
