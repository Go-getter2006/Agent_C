// 作业引导。左栏：全部作业 → 本次题目；中间：这道题的对话；右栏：题面和本题的每一次提交。窄了以后两边收进抽屉。
// - 进入一次作业自动选排序第一的题（最近没通过的）；返回只退左栏，这道题的对话、右栏、草稿都留着；
// - 没选题时可以先写下问题，选了题自动带进那道题的输入框；
// - 右栏每次提交可以“看代码”（就在那一行下面展开，不另开窗口），“就这一版问”把那次提交带进下一条消息（从全部提交记录点回来也是这样）。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowUp, ChevronDown, CloudOff, List, MessagesSquare, PanelRight } from "lucide-react";
import { api, ApiError } from "../api";
import DiffOverlay from "../components/Diff";
import { SidePanel } from "../components/Lists";
import HomeworkNavigator from "../components/HomeworkNavigator";
import { StudentShell } from "../components/Shell";
import Thread, { type ThreadContext } from "../components/Thread";
import { Cells, Drawer, Loading, Problem, ResultPill, gridLabel } from "../components/ui";
import { useLayout, useLoad } from "../lib/hooks";
import { forgetSection, readDraft, writeDraft } from "../lib/memory";
import { clock } from "../lib/time";
import { sortProblems } from "../lib/problems";
import { useHomeworks, useStatus, useStudentCache } from "../session";

const INDEX_DRAFT = "homework:index";

/** 还没选题：可以先写下问题，选了题以后带进那道题的输入框。 */
function HomeworkIntro({ noHomework }: { noHomework: boolean }) {
  const [text, setText] = useState(() => readDraft(INDEX_DRAFT));
  return (
    <>
      <div className="chat-scroll">
        <div className="chat-list">
          <div className="chat-empty homework-intro">
            <strong>{noHomework ? "还没有作业" : "这次想讨论哪道作业题？"}</strong>
            <span>{noHomework ? "老师还没有布置作业。可以先去知识问答问概念。" : "在左边选一次作业，进入题目。也可以先写下问题，选了题会带过去。"}</span>
            {noHomework && <Link className="btn" to="/ask"><MessagesSquare size={16} aria-hidden="true" />去知识问答</Link>}
          </div>
        </div>
      </div>
      {!noHomework && (
        <form className="composer" onSubmit={(e) => e.preventDefault()}>
          <div className="composer-box">
            <label className="sr-only" htmlFor="intro-input">先记下你的作业问题</label>
            <textarea id="intro-input" rows={1} value={text} placeholder="先记下你的作业问题…" spellCheck={false}
              onChange={(e) => { setText(e.target.value); writeDraft(INDEX_DRAFT, e.target.value); }} />
            <button type="submit" className="send" disabled aria-label="选一道题以后发送"><ArrowUp size={20} strokeWidth={2.2} /></button>
          </div>
          <p className="composer-hint">选一道题以后再发送。</p>
        </form>
      )}
    </>
  );
}

function ProblemDocument({ problemKey }: { problemKey: string }) {
  const doc = useLoad(() => api.problemDocument(problemKey), [problemKey], 2500);
  if (doc.error) return <Problem message={doc.error} onRetry={doc.reload} />;
  if (!doc.data?.ready) return <Loading what="正在下载题面" />;
  return <div className="problem-document">
    <a className="link" href={doc.data.url!} target="_blank" rel="noreferrer">在新窗口打开题面</a>
    {doc.data.pdf ? <iframe title="题目原始 PDF" src={doc.data.url!} /> : <pre>{doc.data.text}</pre>}
  </div>;
}

export default function Workbench() {
  const { key } = useParams();
  const navigate = useNavigate();
  const layout = useLayout();
  const [search, setSearch] = useSearchParams();
  const { homeworks, current, pick, loading: hwLoading, error: hwError } = useHomeworks();
  const { status, reload: reloadStatus } = useStatus();
  const isProblem = !!key;
  const [entered, setEntered] = useState(isProblem);
  const [selectFirstFor, setSelectFirstFor] = useState<string | null>(null);
  const needsList = entered || isProblem;

  // keep：切换作业、题目时先留着上一份，新的到了再换，页面不闪
  const list = useLoad(async () => {
    if (!current || !needsList) return null;
    return api.homework(current.key);
  }, [current?.key, needsList], 15000, false, current && needsList ? `s:homework:${current.key}` : undefined);
  const detailCache = useStudentCache().problems;
  const [detailError, setDetailError] = useState<{ key: string; error: Error } | null>(null);
  const [version, setVersion] = useState(0);
  const reloadDetail = useCallback(() => setVersion(n => n + 1), []);
  const [, updateDetail] = useState(0);
  useEffect(() => {
    if (!key) return;
    let controller: AbortController | undefined;
    const read = async () => {
      if (controller) return;
      const request = new AbortController();
      controller = request;
      try {
        const value = await api.problem(key, request.signal);
        if (request.signal.aborted || value.key !== key) return;
        detailCache.put(key, value);
        setDetailError(null);
        updateDetail(n => n + 1);
      } catch (err) {
        if (request.signal.aborted) return;
        if (err instanceof ApiError && (err.status === 403 || err.status === 404)) detailCache.delete(key);
        setDetailError({ key, error: err as Error });
      } finally { controller = undefined; }
    };
    if (!detailCache.fresh(key)) void read();
    const timer = window.setInterval(() => { if (!document.hidden) void read(); }, 15000);
    return () => { window.clearInterval(timer); controller?.abort(); };
  }, [key, version]);
  const d = key ? detailCache.get(key) : undefined;
  const detailFailure = detailError?.key === key ? detailError?.error : undefined;
  const [drawer, setDrawer] = useState<"list" | "side" | null>(null);
  const [showDocument, setShowDocument] = useState(false);
  const shownList = list.data?.homework.key === current?.key ? list.data : null;
  const listOk = !!shownList && !!current && shownList.homework.key === current.key;

  useEffect(() => { setDrawer(null); setShowDocument(false); }, [key, layout]);
  useEffect(() => { if (isProblem) setEntered(true); }, [key]); // 深链接也保留返回全部作业的入口。

  // 打开一道题（比如从提交页点过来）、它不在当前选的作业里：切到它所在的作业。
  // 只在换题的那一刻做一次——以前每次刷新都做，学生自己换作业马上又被拉回来，看起来“卡在一个练习里”
  const homeKey = d?.homeworkKey;
  const syncedFor = useRef<string | null>(null);
  useEffect(() => {
    if (!homeKey || !key || syncedFor.current === key) return;
    syncedFor.current = key;
    if (shownList?.homework.key === current?.key && shownList?.problems.some(p => p.key === key)) return;
    if (current && homeKey !== current.key && homeworks.some((h) => h.key === homeKey)) pick(homeKey);
  }, [homeKey, key, current, homeworks, pick]);

  // 左栏进入本次作业；列表准备好后，只选择一次排序第一题，刷新不自动抢走当前题。
  const pickHomework = useCallback((hw: string) => {
    syncedFor.current = key ?? null;
    setEntered(true);
    setSelectFirstFor(hw);
    pick(hw);
  }, [key, pick]);
  useEffect(() => {
    if (!entered || !selectFirstFor || !listOk || shownList?.homework.key !== selectFirstFor) return;
    const first = sortProblems(shownList.problems)[0];
    setSelectFirstFor(null);
    if (!first) return;
    syncedFor.current = first.key;
    navigate(`/homework/${first.key}`);
  }, [entered, selectFirstFor, listOk, shownList, navigate]);

  const refresh = useCallback(() => {
    list.reload();
    if (key) detailCache.expire(key);
    reloadDetail();
    reloadStatus();
  }, [key, list.reload, reloadDetail, reloadStatus]);

  const picked = search.get("sub");   // 带进下一条消息的那次提交
  const diff = search.get("diff")?.split("~") ?? null;

  // 没选题时写下的问题：进了题、而那道题自己没有草稿，就带过去（在对话读草稿之前做）
  useMemo(() => {
    if (!key) return;
    const intro = readDraft(INDEX_DRAFT);
    if (intro && !readDraft(key)) {
      writeDraft(key, intro);
      writeDraft(INDEX_DRAFT, "");
    }
  }, [key]);

  // 记着的题不在作业里了（换了班、题被撤了）：退回作业引导首页
  useEffect(() => {
    if (detailFailure instanceof ApiError && [403, 404].includes(detailFailure.status)) {
      forgetSection("homework");
      navigate("/homework", { replace: true });
    }
  }, [detailFailure, navigate]);
  const setParam = (name: string, value: string | null) => {
    const next = new URLSearchParams(search);
    if (value === null) next.delete(name);
    else next.set(name, value);
    setSearch(next, { replace: name === "ask" });
  };

  const problems = shownList?.problems ?? [];
  const row = problems.find((p) => p.key === key);
  const newerRow = !!row?.last && row.last.attemptNo > (d?.submissions[0]?.attemptNo ?? 0);
  const latest = newerRow || !d ? row?.last : d.submissions[0];
  const passed = d && !newerRow ? d.submissions.some(x => x.result === "AC") : !!row?.passed;
  const shown = newerRow ? undefined : d;
  const newerAttempt = newerRow ? row.last?.key : undefined;
  useEffect(() => {
    if (key && newerAttempt && detailCache.get(key)) {
      detailCache.expire(key);
      reloadDetail();
    }
  }, [key, newerAttempt, reloadDetail]);
  const title = d?.title ?? row?.title ?? "";
  const brief = d?.brief ?? row?.brief ?? "";
  const pickedSub = d?.submissions.find((x) => x.key === picked) ?? null;
  const context: ThreadContext | null = pickedSub
    ? { label: `针对第 ${pickedSub.attemptNo} 次提交`, submissionKey: pickedSub.key, onClear: () => setParam("sub", null) } : null;
  const xdoj = status?.xdoj;

  const listPane = (onNavigate?: () => void) => (
    <HomeworkNavigator unread={status?.unread} homeworks={homeworks} current={current} problems={problems} currentKey={key ?? null}
      entered={entered} loading={hwLoading} ready={listOk} error={list.error ?? hwError}
      onEnter={pickHomework} onBack={() => { setSelectFirstFor(null); setEntered(false); }} onNavigate={onNavigate} />
  );
  const sidePane = shown && isProblem ? (
    <div>
      <SidePanel detail={shown}
        onDocument={() => { setDrawer(null); setShowDocument(true); }}
        onAsk={(sub) => { setDrawer(null); setParam("sub", sub); }}
        onDiff={(a, b) => { setDrawer(null); void api.event("open_diff", shown.key); setParam("diff", `${a}~${b}`); }} />
    </div>
  ) : null;

  let emptyTitle = "这道题哪里卡住了？";
  let emptyText = "说说你的想法，或把代码贴过来。";
  if (passed) {
    emptyTitle = "这道题已经通过了";
    emptyText = "可以回顾怎么改对的，也可以接着问。";
  } else if (d && !latest) {
    emptyTitle = "这道题还没提交";
    emptyText = "可以先问题意。交过一次后，能看到你的代码和没过的测试点。";
  }

  const status_ = latest ? (
    <>
      <ResultPill sub={latest} />
      <span className="num">第 {latest.attemptNo} 次{latest.grid && latest.result !== "AC" ? `，${gridLabel(latest.grid)}` : ""}</span>
      {latest.grid && <Cells grid={latest.grid} />}
    </>
  ) : isProblem && d ? <span className="pill">还没提交</span> : null;

  if (detailFailure && isProblem && !d) {
    return (
      <StudentShell>
        <div className="wb">
          <aside className="wb-list">{listPane()}</aside>
          <main className="wb-chat"><Problem message={detailFailure.message} onRetry={reloadDetail} /></main>
        </div>
      </StudentShell>
    );
  }

  return (
    <StudentShell>
      <div className="wb">
        <aside className="wb-list" aria-label="作业和题目">{listPane()}</aside>
        <main className="wb-chat" id="chat">
          {xdoj && !xdoj.ok && (
            <div className="banner" role="status">
              <CloudOff size={16} aria-hidden="true" />
              XDOJ 暂时连不上{xdoj.dataTime ? `，显示的是 ${clock(xdoj.dataTime)} 的数据` : "，显示的是之前同步到的数据"}
            </div>
          )}
          <header className="chat-head" hidden={!key}>
            {layout === "phone" ? (
              <button type="button" className="chat-head-title" onClick={() => setDrawer("list")} aria-label={`${title}，换一道题`}>
                <span className="chat-head-main">
                  <h1>{title}</h1>
                  {brief && <span className="chat-head-brief">{brief}</span>}
                </span>
                <ChevronDown size={18} aria-hidden="true" />
              </button>
            ) : (
              <div className="chat-head-main">
                <h1>{title}</h1>
                {brief && <span className="chat-head-brief">{brief}</span>}
              </div>
            )}
            {layout === "phone" ? (
              status_ && (
                <button type="button" className="chat-head-status" disabled={!d} onClick={() => setDrawer("side")}
                  aria-label="看这道题的提交和知识点">
                  {status_}
                </button>
              )
            ) : (
              <>
                {status_ && <div className="chat-head-status">{status_}</div>}
                <span className="chat-head-tools">
                  <button type="button" className="btn btn-sm only-narrow" onClick={() => setDrawer("list")}>
                    <List size={15} aria-hidden="true" />题目
                  </button>
                  {d && (
                    <button type="button" className="btn btn-sm hide-wide" onClick={() => setDrawer("side")}>
                      <PanelRight size={15} aria-hidden="true" />这道题
                    </button>
                  )}
                </span>
              </>
            )}
          </header>
          {isProblem ? (
            <Thread threadKey={key!} layout={layout} emptyTitle={emptyTitle} emptyText={emptyText}
              latestNotice={status?.unread?.problems[key!]} onNoticeRead={reloadStatus}
              pollHistory={false} waitingContext={!!picked && !d} context={context} onActivity={refresh} />
          ) : (
            <HomeworkIntro noHomework={!current && !hwLoading && homeworks.length === 0} />
          )}
        </main>
        {layout === "four" && isProblem && <aside className="wb-side" aria-label="这道题的情况">{sidePane ?? <Loading what="正在取题目" />}</aside>}
      </div>
      {drawer === "list" && (layout === "phone" || layout === "two") && (
        <Drawer side="left" title="作业和题目" onClose={() => setDrawer(null)}>{listPane(() => setDrawer(null))}</Drawer>
      )}
      {drawer === "side" && layout !== "four" && sidePane && (
        <Drawer side="right" title="这道题" onClose={() => setDrawer(null)}>{sidePane}</Drawer>
      )}
      {diff && diff.length === 2 && isProblem && (
        <DiffOverlay problemKey={key!} a={diff[0]} b={diff[1]} onClose={() => (window.history.length > 1 ? navigate(-1) : setParam("diff", null))} />
      )}
      {showDocument && isProblem && <Drawer side="right" title="题目内容" onClose={() => setShowDocument(false)}>
        <ProblemDocument problemKey={key!} />
      </Drawer>}
    </StudentShell>
  );
}
