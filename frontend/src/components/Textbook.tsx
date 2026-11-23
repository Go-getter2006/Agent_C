import { useLayoutEffect, useMemo, useRef } from "react";
import { ArrowLeft, BookOpen, ChevronDown } from "lucide-react";
import { api } from "../api";
import { useLoad } from "../lib/hooks";
import { fullTime } from "../lib/time";
import type { TextbookCitation, TextbookContents } from "../types";
import { Inline, Loading, Problem } from "./ui";

export interface ReadingTarget {
  edition: number;
  section: number;
  path: string[];
  anchor?: number;
  time?: string;
  role?: TextbookCitation["role"];
}

function markClass(figure: boolean, role?: TextbookCitation["role"]) {
  return figure ? "textbook-mark is-figure" : role === "example" ? "textbook-mark is-example" : "textbook-mark";
}

export function CitationPreview({ citation, onOpen }: { citation: TextbookCitation; onOpen: () => void }) {
  const preview = citation.preview;
  const title = citation.path[citation.path.length - 1];
  // 预览与右栏使用同一张受保护的原图。只显示标记附近的几行，完整小节仍可打开。
  const pad = preview ? 18 / preview.height : 0;
  const top = preview ? Math.max(0, Math.min(...preview.boxes.map(b => b[1])) - pad) : 0;
  const bottom = preview ? Math.min(1, Math.max(...preview.boxes.map(b => b[1] + b[3])) + pad) : 1;
  const height = bottom - top;
  return <figure className="textbook-excerpt">
    <button type="button" className="textbook-citation" onClick={onOpen} title={citation.path.join(" › ")}
      aria-label={`打开教材原文：${title}`}>
      <span className="textbook-citation-heading"><BookOpen size={15} aria-hidden="true" />{title}
        {preview && <small>{preview.figure ? "教材插图" : citation.role === "example" ? "教材示例" : "教材原文"}</small>}
      </span>
      {preview && height > 0 && <span className="textbook-crop"
        style={{ aspectRatio: `${preview.width} / ${preview.height * height}` }} onContextMenu={event => event.preventDefault()}>
        <img src={`/api/textbook/${citation.edition}/sections/${citation.section}/images/${preview.page}`}
          loading="lazy" draggable={false} alt="" style={{ top: `${-100 * top / height}%`, height: `${100 / height}%` }} />
        {preview.boxes.map(([x, y, w, h], i) => <span key={i} aria-hidden="true" className={markClass(preview.figure, citation.role)}
          style={{ left: `${100 * x}%`, top: `${100 * (y - top) / height}%`, width: `${100 * w}%`, height: `${100 * h / height}%` }} />)}
      </span>}
    </button>
    {citation.explanation && <figcaption><Inline text={citation.explanation} /></figcaption>}
  </figure>;
}

type Section = TextbookContents["sections"][number];

function Contents({ nodes, onOpen, childrenOf, depth = 0 }: {
  nodes: Section[];
  onOpen: (node: Section) => void;
  childrenOf: Map<number | null, Section[]>;
  depth?: number;
}) {
  return <ul className="textbook-contents">
    {nodes.map((node) => {
      const children = childrenOf.get(node.id);
      return <li key={node.id}>
        {children?.length ? (
          <details>
            <summary><ChevronDown size={16} aria-hidden="true" /><span>{node.title}</span></summary>
            {depth > 0 && <button className="textbook-leaf textbook-intro" type="button" onClick={() => onOpen(node)}>本节开头</button>}
            <Contents nodes={children} onOpen={onOpen} childrenOf={childrenOf} depth={depth + 1} />
          </details>
        ) : (
          <button className="textbook-leaf" type="button" onClick={() => onOpen(node)}>{node.title}</button>
        )}
      </li>;
    })}
  </ul>;
}

function Reading({ target }: { target: ReadingTarget }) {
  const { edition, section, anchor } = target;
  const result = useLoad(() => api.textbookSection(edition, section, anchor), [edition, section, anchor]);
  const scroll = useRef<HTMLDivElement>(null);
  const firstMark = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    const area = scroll.current;
    const mark = firstMark.current;
    if (area && result.data) {
      area.scrollTop = mark ? mark.getBoundingClientRect().top - area.getBoundingClientRect().top
        + area.scrollTop - area.clientHeight * 0.25 : 0;
    }
  }, [result.data, target]);

  const highlighted = result.data?.highlight;
  const title = target.path[target.path.length - 1];
  return (
    <>
      {target.path.length > 0 && <header className="textbook-location">
        {target.path.length > 1 && <span>{target.path.slice(0, -1).join(" › ")}</span>}
        <h3>{title}</h3>
        {target.time && highlighted && <time dateTime={target.time}>高亮于 {fullTime(target.time)}</time>}
      </header>}
      <div className="textbook-scroll" ref={scroll}>
        {result.loading && !result.data && <Loading />}
        {result.error && <Problem message={result.error} onRetry={result.reload} />}
        {highlighted && <p className="sr-only">高亮的教材原文：{highlighted.text}</p>}
        {result.data?.pages.map((page, index) => (
          <figure key={page.url} className="textbook-page" style={{ aspectRatio: `${page.width} / ${page.height}` }}
            onContextMenu={(e) => e.preventDefault()}>
            <img src={page.url} alt={result.data?.title ?? ""} draggable={false}
              width={page.width} height={page.height}
              loading={index === 0 || index === highlighted?.boxes[0]?.[0] ? "eager" : "lazy"} />
            {highlighted?.boxes.map(([pageIndex, left, top, width, height], i) => pageIndex === index && (
              <span key={i} ref={i === 0 ? firstMark : undefined} aria-hidden="true"
                className={markClass(highlighted.figure, target.role)}
                style={{ left: `${left * 100}%`, top: `${top * 100}%`, width: `${width * 100}%`, height: `${height * 100}%` }} />
            ))}
          </figure>
        ))}
      </div>
    </>
  );
}

export default function Textbook({ book, target, onOpen, onContents }: {
  book: TextbookContents;
  target: ReadingTarget | null;
  onOpen: (target: ReadingTarget) => void;
  onContents: () => void;
}) {
  const catalog = useMemo(() => {
    const childrenOf = new Map<number | null, Section[]>();
    const paths = new Map<number, string[]>();
    for (const section of book.sections) {
      const siblings = childrenOf.get(section.parent) ?? [];
      siblings.push(section);
      childrenOf.set(section.parent, siblings);
      paths.set(section.id, [...(paths.get(section.parent ?? 0) ?? []), section.title]);
    }
    return { childrenOf, paths };
  }, [book.sections]);
  return (
    <div className="textbook-pane">
      <header className="textbook-head">
        <h2><BookOpen size={17} aria-hidden="true" />教材</h2>
        {target && <button className="btn btn-sm" type="button" onClick={onContents}><ArrowLeft size={15} aria-hidden="true" />目录</button>}
      </header>
      <nav className="textbook-scroll" aria-label="教材目录" hidden={Boolean(target)}>
        <p className="textbook-book-title">{book.title}</p>
        <Contents nodes={catalog.childrenOf.get(null) ?? []} childrenOf={catalog.childrenOf}
          onOpen={(node) => {
            onOpen({ edition: book.edition, section: node.id, path: catalog.paths.get(node.id) ?? [node.title] });
          }} />
      </nav>
      {target && <Reading key={`${target.edition}-${target.section}-${target.anchor ?? ""}`} target={target} />}
      <span className="sr-only" aria-live="polite">
        {target && (target.anchor === undefined ? "已打开" : "已定位到")}{target && target.path[target.path.length - 1]}
      </span>
    </div>
  );
}
