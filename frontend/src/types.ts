// 接口返回的数据形状。学生端拿到的就是这些：测试点只有“过没过”。

export interface SubmissionView {
  key: string;
  attemptNo: number;
  result: string; // AC / WA / CE / RE / TLE / ...
  resultText: string;
  passed: number | null;
  total: number | null;
  grid: boolean[] | null; // 编译错误、评测中时为 null：不显示格子
  time: string | null;
}

export interface Homework {
  key: string;
  title: string;
  passed: number;
  total: number;
  endTime: string | null;
  ended: boolean;
  lastSubmit: string | null;
}

export interface ProblemRow {
  key: string;
  title: string;
  brief: string;
  attempts: number;
  passed: boolean;
  last: SubmissionView | null;
}

export interface KnowledgeLevel {
  name: string;
  level: string;
  segments: number;
}

export interface ProblemDetail {
  key: string;
  title: string;
  brief: string;
  homeworkKey: string | null;
  submissions: SubmissionView[]; // 新的在前
  knowledge: KnowledgeLevel[];
}

export type Block =
  | { t: "p"; text: string }
  | { t: "code"; caption: string; start: number; lines: string[]; mark: number[] };

export type HelpAction = "think" | "hint" | "tell";

export interface QuizResult {
  outcome: "right" | "wrong" | "skipped";
  headline: string;
  explanation?: string;
  finalCode?: string[];
  bugLine?: number | null;
  fix?: { lines: string[]; mark: number[] };
  lastRun?: string;
}

export interface QuizView {
  attemptId: number;
  stem: string;
  caption: string;
  code: string[];
  state: "confidence" | "judge" | "edit" | "done";
  confidence: string | null;
  judged: "bug" | "ok" | null;
  triesLeft?: number;
  choices?: { index: number; label: string }[];
  draft?: string;
  message?: { kind: string; text: string } | null;
  result?: QuizResult;
}

export interface Message {
  id: number;
  role: "student" | "agent";
  kind: "text" | "quiz";
  time: string;
  blocks: Block[];
  actions: HelpAction[];
  quiz?: QuizView;
}

export interface DiffRow {
  type: "same" | "change" | "del" | "add";
  left: { n: number; text: string } | null;
  right: { n: number; text: string } | null;
}

export interface DiffView {
  problem: { key: string; title: string; brief: string };
  from: SubmissionView;
  to: SubmissionView;
  rows: DiffRow[];
  changed: number;
}

export interface ProblemRef {
  key: string;
  title: string;
  brief: string;
}

export interface ProgressData {
  knowledge: {
    topic: string;
    items: { name: string; level: string; segments: number; basis: string; practice: ProblemRef | null }[];
  }[];
  errors: { name: string; count: number; last: ProblemRef; lastTime: string; status: string }[];
  activity: {
    homeworks: Homework[];
    tried: number;
    passed: number;
    firstTryPassed: number;
    firstTryRate: number | null;
    avgSubmissions: number | null;
    weeks: { label: string; submissions: number; passed: number }[];
  };
  next: (
    | { kind: "problem"; problem: ProblemRef; reason: string }
    | { kind: "quiz"; revisitId: number; title: string; reason: string }
  )[];
}

export interface Me {
  role: "student" | "teacher" | "admin";
  name: string;
  loginName: string;
}

export interface StatusInfo {
  xdoj: { ok: boolean; dataTime: string | null; demo: boolean };
  dueRevisits: number;
  llm: boolean;
}

export interface AuthInfo {
  mode: string;
  ready: boolean;
  demo: boolean;
  demoAccount: string | null;
  teacherLogin: boolean;
}
