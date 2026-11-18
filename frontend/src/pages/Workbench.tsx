// 对话页（工作台）。题目在左边，对话在中间，这道题的情况在右边；窄了以后两边收进抽屉。
import { useCallback, useEffect, useState } from "react";
import { Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ChevronDown, CloudOff, List, PanelRight } from "lucide-react";
import { api } from "../api";
import DiffOverlay from "../components/Diff";
import { HomeworkPicker, ProblemList, SidePanel } from "../components/Lists";
import { StudentShell } from "../components/Shell";
import Thread from "../components/Thread";
import { Cells, Drawer, ResultPill, gridLabel } from "../components/ui";
import { useLayout, useLoad } from "../lib/hooks";
import { clock } from "../lib/time";
import { useHomeworks, useStatus } from "../session";

export default function Workbench() {
  const { key } = useParams();
  const navigate = useNavigate();
  const layout = useLayout();
  const [search, setSearch] = useSearchParams();
  const { homeworks, current, pick, loading: hwLoading } = useHomeworks();
  const { status, reload: reloadStatus } = useStatus();
  const isProblem = !!key && key !== "general";

  const list = useLoad(() => (current ? api.homework(current.key) : Promise.resolve(null)), [current?.key], 15000);
  const detail = useLoad(() => (isProblem ? api.problem(key!) : Promise.resolve(null)), [key], 15000);
  const [drawer, setDrawer] = useState<"list" | "side" | null>(null);

  useEffect(() => setDrawer(null), [key, layout]);

  // 地址里的题不在当前选的作业里：切到它所在的作业
  const homeKey = detail.data?.homeworkKey;
  useEffect(() => {
    if (homeKey && current && homeKey !== current.key && homeworks.some((h) => h.key === homeKey)) pick(homeKey);
  }, [homeKey, current, homeworks, pick]);

  const refresh = useCallback(() => {
    list.reload();
    detail.reload();
    reloadStatus();
  }, [list.reload, detail.reload, reloadStatus]); // eslint-disable-line react-hooks/exhaustive-deps

  const ask = search.get("ask");
  const diff = search.get("diff")?.split("~") ?? null;
  const setParam = (name: string, value: string | null) => {
    const next = new URLSearchParams(search);
    if (value === null) next.delete(name);
    else next.set(name, value);
    setSearch(next, { replace: name === "ask" });
  };

  // 没有指定题目：进最近提交过的那道
  if (!key) {
    if (hwLoading || (current && list.loading && !list.data)) return <StudentShell><div className="wb" /></StudentShell>;
    const rows = list.data?.problems ?? [];
    const recent = rows.filter((p) => p.last?.time).sort((a, b) => (a.last!.time! < b.last!.time! ? 1 : -1))[0];
    if (recent) return <Navigate to={`/chat/${recent.key}`} replace />;
  }

  const problems = list.data?.problems ?? [];
  const d = detail.data;
  const latest = d?.submissions[0] ?? null;
  const threadKey = isProblem ? key! : "general";
  const title = isProblem ? d?.title ?? problems.find((p) => p.key === key)?.title ?? "" : "其他问题";
  const brief = isProblem ? d?.brief ?? "" : "概念、题意，或者不确定是哪道题";
  const due = status?.dueReviews ?? 0;
  const xdoj = status?.xdoj;

  const listPane = (onNavigate?: () => void) => (
    <>
      <HomeworkPicker homeworks={homeworks} current={current} onPick={pick} />
      {current && list.data && problems.length === 0 && <p className="plist-empty">这个作业里还没有题目</p>}
      <ProblemList problems={problems} currentKey={key ?? "general"} dueReviews={due} onNavigate={onNavigate} />
    </>
  );
  const sidePane = d ? (
    <SidePanel detail={d}
      onDiff={(a, b) => { setDrawer(null); void api.event("open_diff", d.key); setParam("diff", `${a}~${b}`); }}
      onAsk={(sub) => { setDrawer(null); setParam("ask", sub); }} />
  ) : null;

  let emptyTitle = "选一道题，或者直接问";
  let emptyText = "左边是这次作业的题目。也可以在下面直接问概念和题意。";
  if (isProblem) {
    emptyTitle = latest ? "这道题哪里卡住了？" : "这道题还没提交";
    emptyText = latest ? "直接说你的想法，也可以把代码贴过来。我会先问你几个问题，再一点点给提示。" : "可以照常提问。交过一次以后，我能看到你的代码和哪几组测试点没过。";
  } else if (!current && !hwLoading) {
    emptyTitle = "还没有作业";
    emptyText = "老师还没有给你的班级布置作业。";
  } else if (!isProblem) {
    emptyTitle = "其他问题";
    emptyText = "概念、题意，或者不确定是哪道题，都可以在这里问。";
  }

  const status_ = latest ? (
    <>
      <ResultPill sub={latest} />
      <span className="num">第 {latest.attemptNo} 次{latest.grid && latest.result !== "AC" ? `，${gridLabel(latest.grid)}` : ""}</span>
      {latest.grid && <Cells grid={latest.grid} />}
    </>
  ) : isProblem && d ? <span className="pill">还没提交</span> : null;

  if (detail.error && isProblem && !d) {
    return (
      <StudentShell>
        <div className="wb">
          <aside className="wb-list">{listPane()}</aside>
          <main className="wb-chat"><div className="chat-scroll"><div className="chat-empty"><strong>{detail.error}</strong></div></div></main>
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
          <header className="chat-head">
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
          <Thread threadKey={threadKey} layout={layout} emptyTitle={emptyTitle} emptyText={emptyText}
            askKey={isProblem ? ask : null} onAsked={() => setParam("ask", null)} onActivity={refresh} />
        </main>
        {layout === "four" && sidePane && <aside className="wb-side" aria-label="这道题的情况">{sidePane}</aside>}
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
    </StudentShell>
  );
}
