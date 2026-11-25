import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, BookOpen, ChevronDown } from "lucide-react";
import { api } from "../api";
import { useLoad } from "../lib/hooks";
import { fullTime } from "../lib/time";
import type { TextbookCitation, TextbookContents, TextbookSection } from "../types";
import { Inline, Loading, Problem } from "./ui";

export interface ReadingTarget {
  edition: number;
  section: number;
  path: string[];
  anchor?: number;
  figureAnchor?: number;
  time?: string;
  role?: TextbookCitation["role"];
}

function markClass(figure: boolean, role?: TextbookCitation["role"]) {
  return figure ? "textbook-mark is-figure" : role === "example" ? "textbook-mark is-example" : "textbook-mark";
}

export function TextbookCitations({ citations, onOpen }: {
  citations: TextbookCitation[];
  onOpen: (citation: TextbookCitation) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const titles = [...new Set(citations.map(citation => citation.path[citation.path.length - 1]))];
  return <details className="textbook-citations" onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary className="textbook-citation-heading">
      <BookOpen size={15} aria-hidden="true" />
      <span>教材：{titles.join("、")}</span>
      <ChevronDown size={15} aria-hidden="true" />
    </summary>
    {expanded && <div className="textbook-citation-list">
      {citations.map(citation => <CitationPreview key={`${citation.edition}-${citation.section}-${citation.anchor}`}
        citation={citation} onOpen={() => onOpen(citation)} />)}
    </div>}
  </details>;
}

function CitationPreview({ citation, onOpen }: { citation: TextbookCitation; onOpen: () => void }) {
  const preview = citation.preview;
  const title = citation.path[citation.path.length - 1];
  return <figure className="textbook-excerpt">
    <div className="textbook-excerpt-head">
      <span>{title}</span>
      <button type="button" className="btn btn-sm" onClick={onOpen} aria-label={`教材详情：${title}`}>详情</button>
    </div>
      {preview && [preview, ...(citation.continuation ?? [])].map(page => {
        const marks = page.frame ? [...page.boxes, page.frame] : page.boxes;
        const [top, bottom] = page.crop ?? [0, page.height];
        const url = `/api/textbook/${citation.edition}/sections/${citation.section}/images/${page.page}`;
        const height = bottom - top;
        return <span key={page.page} className="textbook-crop"
          style={{ aspectRatio: `${page.width} / ${height}` }} onContextMenu={event => event.preventDefault()}>
          <img src={page.crop ? `${url}?top=${top}&bottom=${bottom}` : url}
            loading="lazy" width={page.width} height={height} draggable={false} alt={`${title}的教材节选`} />
          {marks.map((box, i) => <span key={i} aria-hidden="true" className={markClass(page.figure || box === page.frame, citation.role)}
            style={{ left: `${100 * box[0]}%`, top: `${100 * (box[1] * page.height - top) / height}%`,
              width: `${100 * box[2]}%`, height: `${100 * box[3] * page.height / height}%` }} />)}
        </span>;
      })}
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
  const { edition, section, anchor, figureAnchor } = target;
  const result = useLoad(() => api.textbookSection(edition, section, anchor, figureAnchor), [edition, section, anchor, figureAnchor, target.time]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const annotations = result.data?.annotations ?? [];
  const selected = annotations.find(item => item.id === selectedId);
  const highlighted = selected?.highlight ?? result.data?.highlight;
  const time = selected?.time ?? target.time;
  const marks = useMemo(() => {
    const sources = [
      ...(highlighted ? [{ highlight: highlighted, role: selected?.role ?? target.role }] : []),
      ...annotations,
    ];
    const unique = new Map<string, { box: NonNullable<TextbookSection["highlight"]>["boxes"][number]; figure: boolean; role?: TextbookCitation["role"] }>();
    for (const source of sources) {
      for (const box of source.highlight.boxes) {
        const key = `${source.highlight.figure}-${box.join(",")}`;
        if (!unique.has(key)) unique.set(key, { box, figure: source.highlight.figure, role: source.role });
      }
      if (source.highlight.frame) {
        const box = source.highlight.frame;
        const key = `true-${box.join(",")}`;
        if (!unique.has(key)) unique.set(key, { box, figure: true });
      }
    }
    return [...unique.entries()];
  }, [highlighted, annotations, selected?.role, target.role]);
  const firstBox = highlighted?.boxes[0];
  const focusKey = firstBox ? `${highlighted.figure}-${firstBox.join(",")}` : null;
  const scroll = useRef<HTMLDivElement>(null);
  const firstMark = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    const area = scroll.current;
    const mark = firstMark.current;
    if (area && result.data) {
      area.scrollTop = mark ? mark.getBoundingClientRect().top - area.getBoundingClientRect().top
        + area.scrollTop - area.clientHeight * 0.25 : 0;
    }
  }, [result.data, target, selectedId]);

  const title = target.path[target.path.length - 1];
  return (
    <>
      {target.path.length > 0 && <header className="textbook-location">
        {target.path.length > 1 && <span>{target.path.slice(0, -1).join(" › ")}</span>}
        <h3>{title}</h3>
        {time && highlighted && <time dateTime={time}>高亮于 {fullTime(time)}</time>}
      </header>}
      <div className="textbook-scroll" ref={scroll}>
        {result.loading && !result.data && <Loading />}
        {result.error && <Problem message={result.error} onRetry={result.reload} />}
        {highlighted && <p className="sr-only">高亮的教材原文：{highlighted.text}</p>}
        {annotations.length > 0 && <details className="textbook-annotations">
          <summary>标注记录（{annotations.length}）</summary>
          <ul>
            {annotations.map(item => <li key={item.id}>
              <button type="button" className="textbook-annotation-jump" aria-pressed={selectedId === item.id}
                onClick={() => setSelectedId(item.id)}>
                <time dateTime={item.time}>{fullTime(item.time)}</time><span>查看标注</span>
              </button>
              <details>
                <summary>查看原提问</summary>
                {item.question && <p>{item.question}</p>}
                {item.explanation && <p><Inline text={item.explanation} /></p>}
              </details>
            </li>)}
          </ul>
        </details>}
        {result.data?.pages.map((page, index) => (
          <figure key={page.url} className="textbook-page" style={{ aspectRatio: `${page.width} / ${page.height}` }}
            onContextMenu={(e) => e.preventDefault()}>
            <img src={page.url} alt={result.data?.title ?? ""} draggable={false}
              width={page.width} height={page.height}
              loading={index === 0 || index === firstBox?.[0] ? "eager" : "lazy"} />
            {marks.map(([key, mark]) => mark.box[0] === index && (
              <span key={key} ref={key === focusKey ? firstMark : undefined} aria-hidden="true"
                className={markClass(mark.figure, mark.role)}
                style={{ left: `${mark.box[1] * 100}%`, top: `${mark.box[2] * 100}%`, width: `${mark.box[3] * 100}%`, height: `${mark.box[4] * 100}%` }} />
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
