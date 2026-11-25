// 知识问答：查概念、语法、库函数怎么用。左边是问答记录（可以新建），中间是回答。
// - 默认回到上次的问答；第一次来是新的问答。以前的“综合”对话列在里面，叫“以前的问答”。
// - 不自动带上作业题；学生点了“结合当前题”才带上作业引导里最近打开的那道题（题名和代码），可以去掉。
// - C 语言教材按最小小节浏览，引用只定位右栏。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { BookOpen, List, Plus, Trash } from "lucide-react";
import { ApiError, api } from "../api";
import { StudentShell } from "../components/Shell";
import KnowledgeMarkdown from "../components/KnowledgeMarkdown";
import Thread, { type ThreadContext } from "../components/Thread";
import { Drawer, Loading, Problem } from "../components/ui";
import Textbook, { type ReadingTarget } from "../components/Textbook";
import { useLayout, useLoad } from "../lib/hooks";
import { forgetSection, forgetThread, lastProblemKey } from "../lib/memory";
import { dayLabel } from "../lib/time";
import { useSession, useStudentCache } from "../session";
import type { Topic } from "../types";

function TopicList({ topics, current, deleting, error, onNew, onNavigate, onDelete }: {
  topics: Topic[]; current: string; onNew: () => void; onNavigate: (key: string) => void;
  deleting: string | null; error: string | null; onDelete: (topic: Topic) => void;
}) {
  return (
    <div className="topic-pane">
      <header className="topic-head">
        <h2>问答记录</h2>
        <button type="button" className="btn btn-sm" onClick={onNew}><Plus size={15} aria-hidden="true" />新建问答</button>
      </header>
      {error && <Problem message={error} />}
      <nav className="topic-list" aria-label="问答记录">
        {current === "new" && (
          <span className="topic-item" aria-current="true"><strong>新的问答</strong><small>还没开始</small></span>
        )}
        {topics.map((t) => (
          <div className="topic-row" key={t.key}>
            <button type="button" className="topic-item" aria-current={t.key === current ? "true" : undefined}
              onClick={() => onNavigate(t.key)}>
              <strong>{t.title}</strong>
              <small>{dayLabel(t.time)}</small>
            </button>
            <button type="button" className="icon-btn topic-delete" title="删除会话" aria-label={`删除「${t.title}」`}
              disabled={deleting !== null} onClick={() => onDelete(t)}>
              <Trash size={16} aria-hidden="true" />
            </button>
          </div>
        ))}
        {topics.length === 0 && current !== "new" && <p className="plist-empty">还没有问过</p>}
      </nav>
    </div>
  );
}

export default function Knowledge() {
  const { topic } = useParams();
  const navigate = useNavigate();
  const layout = useLayout();
  const { me } = useSession();
  const threadCache = useStudentCache().threads;
  const topics = useLoad(() => api.topics(), [], 60000, true, "s:topics");
  const course = me?.course;
  const textbook = useLoad(() => course === "C" ? api.textbookContents() : Promise.resolve({ book: null }),
    [course], 0, false, `s:textbook:${course}`);
  const book = textbook.data?.book;
  const [reading, setReading] = useState<ReadingTarget | null>(null);
  const [readerOpen, setReaderOpen] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [withProblem, setWithProblem] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const list = topics.data?.topics ?? [];
  const key = topic ?? (topics.data ? list[0]?.key ?? "new" : "");
  const fresh = useRef(new Set<string>());   // 刚建好的问答：列表还没刷新到它，也不算“没了”
  const deleted = useRef(new Set<string>());
  const isDeleted = useCallback((next: string) => deleted.current.has(next), []);
  const currentKey = useRef(key);
  currentKey.current = key;

  // /ask：回到最近的问答；一个都没有就是新的问答。问答没了（别的课程、被删）：也退回新的问答
  useEffect(() => {
    if (!topics.data) return;
    if (!topic) {
      navigate(`/ask/${list[0]?.key ?? "new"}`, { replace: true });
    } else if (topic !== "new" && !fresh.current.has(topic) && !list.some((t) => t.key === topic)) {
      forgetSection("ask");
      navigate("/ask/new", { replace: true });
    }
  }, [topic, topics.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { void api.event("open_knowledge", key || "ask"); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // “结合当前题”：作业引导里最近打开的那道题
  const problemKey = useMemo(() => lastProblemKey(), []);
  const problem = useLoad(() => (problemKey ? api.problem(problemKey) : Promise.resolve(null)), [problemKey]);
  const problemTitle = problem.data?.title ?? null;
  const context: ThreadContext | null = withProblem && problemKey && problemTitle
    ? { label: `结合：${problemTitle}`, problemKey, onClear: () => setWithProblem(false) } : null;
  const offer = !withProblem && problemKey && problemTitle
    ? { label: `结合当前题：${problemTitle}`, onPick: () => setWithProblem(true) } : null;

  const open = useCallback((next: string) => {
    setDrawer(false);
    navigate(`/ask/${next}`);
  }, [navigate]);
  const onThread = useCallback((created: string) => {
    fresh.current.add(created);
    navigate(`/ask/${created}`, { replace: true });
    topics.reload();
  }, [navigate, topics.reload]); // eslint-disable-line react-hooks/exhaustive-deps

  const remove = async (target: Topic) => {
    if (deleting || !window.confirm(`删除「${target.title}」？会话和消息将永久删除。`)) return;
    setDeleting(target.key);
    setDeleteError(null);
    try {
      let remaining: Topic[];
      try {
        remaining = (await api.deleteTopic(target.key)).topics;
      } catch (err) {
        if (!(err instanceof ApiError && err.status === 404)) throw err;
        remaining = (await api.topics()).topics;
      }
      deleted.current.add(target.key);
      fresh.current.delete(target.key);
      threadCache.delete(target.key);
      forgetThread(target.key);
      if (currentKey.current === target.key) {
        setReading(null);
        setReaderOpen(false);
        forgetSection("ask");
        navigate(`/ask/${remaining[0]?.key ?? "new"}`, { replace: true });
      }
      topics.setData({ topics: remaining });
    } catch (err) {
      setDeleteError(`删除失败。${(err as Error).message}`);
    } finally {
      setDeleting(null);
    }
  };

  const title = key === "new" ? "新的问答" : list.find((t) => t.key === key)?.title ?? "";
  const ds = me?.course === "D";
  const pane = (
    <TopicList topics={list} current={key} deleting={deleting} error={deleteError}
      onNew={() => open("new")} onNavigate={open} onDelete={(target) => void remove(target)} />
  );
  const reader = book ? <Textbook book={book} target={reading} onOpen={setReading} onContents={() => setReading(null)} />
    : reading && textbook.error ? <Problem message={textbook.error} onRetry={textbook.reload} />
    : reading && textbook.loading ? <Loading what="正在取教材目录" /> : null;

  return (
    <StudentShell>
      <div className="wb wb-knowledge">
        <aside className="wb-list" aria-label="问答记录">{pane}</aside>
        <main className="wb-chat" id="chat">
          <header className="chat-head" hidden={!key}>
            <div className="chat-head-main">
              <h1>{title}</h1>
            </div>
            <span className="chat-head-tools">
              <button type="button" className="btn btn-sm only-narrow" onClick={() => setDrawer(true)}>
                <List size={15} aria-hidden="true" />问答记录
              </button>
              {book && layout !== "four" && (
                <button type="button" className="btn btn-sm" onClick={() => setReaderOpen(true)}>
                  <BookOpen size={15} aria-hidden="true" />教材
                </button>
              )}
            </span>
          </header>
          {key && (
            <Thread threadKey={key} layout={layout} AgentText={KnowledgeMarkdown}
              emptyTitle="问一个知识问题"
              emptyText={ds ? "链表、栈、树、图的概念和写法，都可以直接问。" : "概念、语法、库函数怎么用，都可以直接问。"}
              placeholder={ds ? "问一个数据结构知识问题…" : "问一个 C 语言知识问题…"}
              context={context} contextOffer={offer} onThread={onThread} onActivity={topics.reload} isDeleted={isDeleted}
              onTextbook={course === "C" ? (citation, time) => {
                setReading({ ...citation, time });
                if (layout !== "four") setReaderOpen(true);
              } : undefined} />
          )}
        </main>
        {reader && layout === "four" && <aside className="wb-side textbook-side" aria-label="教材阅读">{reader}</aside>}
      </div>
      {drawer && (
        <Drawer side="left" title="问答记录" onClose={() => setDrawer(false)}>{pane}</Drawer>
      )}
      {reader && readerOpen && layout !== "four" && (
        <Drawer side="right" title={book?.title ?? "教材"} onClose={() => setReaderOpen(false)}>{reader}</Drawer>
      )}
    </StudentShell>
  );
}
