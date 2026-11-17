// 对话页（工作台）。题目在左边，对话在中间，这道题的情况在右边。
import { useCallback, useEffect, useState } from "react";
import { Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import DiffOverlay from "../components/Diff";
import { HomeworkPicker, ProblemList, SidePanel } from "../components/Lists";
import { Shell } from "../components/Shell";
import Thread from "../components/Thread";
import { Cells, Drawer, Icon, gridLabel, resultLine } from "../components/ui";
import { useLayout, useLoad } from "../lib/hooks";
import { clock } from "../lib/time";
import { useHomeworks } from "../session";

export default function Workbench() {
  const { key } = useParams();
  const navigate = useNavigate();
  const layout = useLayout();
  const [search, setSearch] = useSearchParams();
  const { homeworks, current, pick, loading: hwLoading } = useHomeworks();
  const isProblem = !!key && key !== "general";

  const list = useLoad(() => (current ? api.homework(current.key) : Promise.resolve(null)), [current?.key], 20000);
  const detail = useLoad(() => (isProblem ? api.problem(key!) : Promise.resolve(null)), [key], 20000);
  const status = useLoad(() => api.status(), [], 60000);
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
    status.reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    if (hwLoading || (current && list.loading && !list.data)) return <Shell><div className="chatcol" /></Shell>;
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
  const due = status.data?.dueRevisits ?? 0;
  const xdoj = status.data?.xdoj;

  const listPane = (onNavigate?: () => void) => (
    <>
      <HomeworkPicker homeworks={homeworks} current={current} onPick={pick} />
      {current && list.data && problems.length === 0 && <p className="plist-empty">这个作业里还没有题目</p>}
      <ProblemList problems={problems} currentKey={key ?? "general"} dueRevisits={due} onNavigate={onNavigate} />
    </>
  );
  const sidePane = d ? (
    <SidePanel detail={d}
      onDiff={(a, b) => { setDrawer(null); void api.event("open_diff", d.key); setParam("diff", `${a}~${b}`); }}
      onAsk={(sub) => { setDrawer(null); setParam("ask", sub); }} />
  ) : null;

  const statusText = latest
    ? layout === "phone" ? resultLine(latest, true) : `第 ${latest.attemptNo} 次提交，${latest.grid ? gridLabel(latest.grid) : latest.resultText}`
    : isProblem && d ? "还没提交" : "";

  let emptyText = "选一道题，或者直接问问题";
  if (isProblem) emptyText = latest ? "这道题哪里卡住了，直接说。也可以把代码贴过来。" : "这道题还没提交，可以照常提问。";
  else if (!current && !hwLoading) emptyText = "老师还没有给你的班级布置作业";

  if (detail.error && isProblem && !d) {
    return (
      <Shell>
        <aside className="listcol">{listPane()}</aside>
        <main className="chatcol"><div className="chat-scroll"><p className="chat-empty">{detail.error}</p></div></main>
      </Shell>
    );
  }

  return (
    <Shell>
      <aside className="listcol" aria-label="作业和题目">{listPane()}</aside>
      <main className="chatcol" id="chat">
        {xdoj && !xdoj.ok && (
          <div className="banner" role="status">
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
              <Icon.chevron />
            </button>
          ) : (
            <div className="chat-head-main">
              <h1>{title}</h1>
              {brief && <span className="chat-head-brief">{brief}</span>}
            </div>
          )}
          {layout === "phone" ? (
            statusText && (
              <button type="button" className="chat-head-title chat-head-side" style={{ minHeight: 32 }} disabled={!d}
                onClick={() => setDrawer("side")} aria-label={`${statusText}，看这道题的情况`}>
                <span style={{ fontSize: "var(--fs-13)" }}>{statusText}</span>
                {latest?.grid && <Cells grid={latest.grid} />}
              </button>
            )
          ) : (
            <div className="chat-head-side">
              {statusText && <span className={layout === "two" ? undefined : ""}>{statusText}</span>}
              <span className="chat-head-tools">
                <button type="button" className="btn only-narrow" onClick={() => setDrawer("list")}>题目</button>
                {d && <button type="button" className="btn" onClick={() => setDrawer("side")}>这道题</button>}
              </span>
            </div>
          )}
        </header>
        <Thread threadKey={threadKey} layout={layout} emptyText={emptyText} askKey={isProblem ? ask : null}
          onAsked={() => setParam("ask", null)} onActivity={refresh} />
      </main>
      {layout === "four" && sidePane && <aside className="sidecol" aria-label="这道题的情况">{sidePane}</aside>}
      {drawer === "list" && layout !== "four" && layout !== "three" && (
        <Drawer side="left" title="作业和题目" onClose={() => setDrawer(null)}>{listPane(() => setDrawer(null))}</Drawer>
      )}
      {drawer === "side" && layout !== "four" && sidePane && (
        <Drawer side="right" title="这道题的情况" onClose={() => setDrawer(null)}>{sidePane}</Drawer>
      )}
      {diff && diff.length === 2 && isProblem && (
        <DiffOverlay problemKey={key!} a={diff[0]} b={diff[1]} onClose={() => (window.history.length > 1 ? navigate(-1) : setParam("diff", null))} />
      )}
    </Shell>
  );
}
