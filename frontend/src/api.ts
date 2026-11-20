// 和后端通信。出错时抛 ApiError，message 是可以直接给人看的一句中文。
import type {
  AuthInfo, DiffView, Homework, Me, Message, ProblemDetail, ProblemRow, ProgressData, QuizView, ReviewCurrent,
  ReviewAttemptRow, ReviewPage, ReviewTurn, StatusInfo, SubmissionChange, SubmissionView, Topic,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

async function request<T>(method: string, path: string, body?: unknown, quiet401 = false, signal?: AbortSignal): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(path, {
      method, signal,
      credentials: "same-origin",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "连不上服务器，检查一下网络再试。");
  }
  if (resp.status === 401 && !quiet401 && onUnauthorized) onUnauthorized();
  let data: unknown = null;
  try {
    data = await resp.json();
  } catch {
    data = null;
  }
  if (!resp.ok) {
    const detail = (data as { error?: unknown } | null)?.error;
    const message = typeof detail === "string" ? detail : resp.status >= 500 ? "服务器出了点问题，稍后再试。" : "请求没有成功。";
    throw new ApiError(resp.status, message);
  }
  return data as T;
}

export const get = <T,>(path: string) => request<T>("GET", path);
export const post = <T,>(path: string, body: unknown = {}) => request<T>("POST", path, body);

export const api = {
  authInfo: () => get<AuthInfo>("/api/auth/info"),
  me: () => request<Me>("GET", "/api/auth/me", undefined, true),
  login: (loginName: string, password: string, course: Me["course"]) =>
    request<{ role: Me["role"]; name: string }>("POST", "/api/auth/login", { loginName, password, course }, true),
  logout: () => post<{ ok: boolean }>("/api/auth/logout"),

  readNotices: (key: string, ids: number[]) => post<{ ok: boolean }>(`/api/threads/${key}/read`, { ids }),
  status: () => get<StatusInfo>("/api/status"),
  homeworks: () => get<{ homeworks: Homework[] }>("/api/homeworks"),
  homework: (key: string) => get<{ homework: Homework; problems: ProblemRow[] }>(`/api/homeworks/${key}`),
  homeworkSubmissions: (key: string) =>
    get<{ homework: Homework; problems: (ProblemRow & { submissions: SubmissionView[]; changes: Record<string, SubmissionChange> })[] }>(
      `/api/homeworks/${key}/submissions`,
    ),
  problem: (key: string, signal?: AbortSignal) => request<ProblemDetail>("GET", `/api/problems/${key}`, undefined, false, signal),
  problemDocument: (key: string) => get<{ ready: boolean; pdf: boolean; url: string | null; text: string | null }>(`/api/problems/${key}/document`),
  diff: (problemKey: string, a: string, b: string) =>
    get<DiffView>(`/api/problems/${problemKey}/diff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`),
  source: (key: string) => get<{ key: string; lines: string[] }>(`/api/submissions/${key}/source`),

  thread: (key: string, signal?: AbortSignal) => request<{ messages: Message[] }>("GET", `/api/threads/${key}`, undefined, false, signal),
  send: (key: string, body: { text?: string; action?: string; submissionKey?: string; problemKey?: string }) =>
    post<{ messages: Message[]; thread: string }>(`/api/threads/${key}/messages`, body),
  topics: () => get<{ topics: Topic[] }>("/api/topics"),

  quizConfidence: (id: number, value: string, elapsedMs: number) =>
    post<QuizView>(`/api/quiz/${id}/confidence`, { value, elapsedMs }),
  quizJudge: (id: number, hasBug: boolean, confidence?: string | null, elapsedMs?: number) =>
    post<QuizView>(`/api/quiz/${id}/judge`, { hasBug, confidence: confidence ?? null, elapsedMs }),
  quizViewed: (id: number) => post(`/api/quiz/${id}/viewed`),
  quizLocate: (id: number, lines: number[]) => post<QuizView>(`/api/quiz/${id}/locate`, { lines }),
  quizSample: (id: number, code: string) => post<QuizView>(`/api/quiz/${id}/sample`, { code }),
  quizHint: (id: number) => post<QuizView>(`/api/quiz/${id}/hint`),
  quizSubmit: (id: number, code: string) => post<QuizView>(`/api/quiz/${id}/submit`, { code }),
  quizRun: (id: number, body: { code?: string; choice?: number }) => post<QuizView>(`/api/quiz/${id}/run`, body),
  quizSkip: (id: number) => post<QuizView>(`/api/quiz/${id}/skip`),

  progress: () => get<ProgressData>("/api/progress"),
  review: () => get<ReviewPage>("/api/review"),
  startReview: (id: number) => post<ReviewCurrent>(`/api/review/${id}/start`),
  requestReview: (id: number) => post<ReviewCurrent>(`/api/review/${id}/request`),
  reviewAttempts: () => get<{ attempts: ReviewAttemptRow[] }>("/api/review/attempts"),
  reviewAttempt: (id: number) => get<{ attemptId: number; title: string; time: string; quiz: QuizView }>(`/api/review/attempts/${id}`),
  reviewTurns: (id: number) => get<{ turns: ReviewTurn[] }>(`/api/review/${id}/discussion`),
  reviewAsk: (id: number, text: string, requestId: string) => post<{ turns: ReviewTurn[] }>(`/api/review/${id}/discussion`, { text, requestId }),
  event: (kind: string, target = "") => post<{ ok: boolean }>("/api/events", { kind, target }).catch(() => undefined),
};
