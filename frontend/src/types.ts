// 接口返回的数据形状。学生端拿到的就是这些：测试点只有“过没过”，没有概率。

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
  fix?: { lines: string[]; mark: number[]; start?: number };
  lastRun?: string;
}

export interface QuizView {
  attemptId: number;
  stem: string;
  caption: string;
  code: string[];
  startLine?: number;
  mark?: number[];
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

export interface KnowledgeItem {
  name: string;
  level: string;
  segments: number;
  basis: string;
  covered: boolean;
  practice: ProblemRef | null;
  // 多维：理解、独立、保持，各三段（没有数据的不出现），加一句话。没有数字
  dims?: { name: string; segments: number; hint: string }[];
  insight?: string;
}

export type NextItem =
  | { kind: "problem"; problem: ProblemRef; reason: string }
  | { kind: "review"; reviewId: number; title: string; reason: string };

export interface ProgressData {
  summary: {
    passed: number;
    total: number;
    firstTryRate: number | null;
    avgSubmissions: number | null;
    thisWeek: number;
    lastWeek: number;
    reviewDue: number;
    reviewActive: number;
    levels: Record<string, number>;
  };
  knowledge: { topic: string; items: KnowledgeItem[] }[];
  errors: { name: string; count: number; last: ProblemRef; lastTime: string; status: string }[];
  activity: {
    homeworks: Homework[];
    tried: number;
    passed: number;
    firstTryPassed: number;
    firstTryRate: number | null;
    avgSubmissions: number | null;
    weeks: { label: string; start: string; submissions: number; passed: number }[];
  };
  next: NextItem[];
}

export interface ReviewItemView {
  id: number;
  title: string; // 知识点
  detail: string; // 具体要点
  why: string;
  origin: ProblemRef | null;
  memory: { text: string; segments: number };
  due: string;
  dueNow: boolean;
  reps: number;
  streak: number;
  toGraduate: number;
  state: "active" | "graduated";
  pending: boolean;
}

export interface ReviewCurrent {
  itemId: number;
  lead: string;
  quiz: QuizView;
}

export interface ReviewPage {
  due: ReviewItemView[];
  upcoming: ReviewItemView[];
  graduated: ReviewItemView[];
  doneToday: number;
  dailyLimit: number;
  active: number;
  current?: ReviewCurrent;
}

export type CourseCode = "C" | "D";

export interface Me {
  role: "student" | "teacher" | "admin";
  name: string;
  loginName: string;
  course: CourseCode;
  courseName: string;
}

export interface StatusInfo {
  xdoj: { ok: boolean; dataTime: string | null; demo: boolean };
  dueReviews: number;
  llm: boolean;
}

export interface AuthInfo {
  mode: string;
  ready: boolean;
  demo: boolean;
  demoAccount: string | null;
  teacherLogin: boolean;
  // 可以登录的课程。只有一门时登录页不出现课程选择；server 是这门课的 XDOJ（演示数据时为空）
  courses: { code: CourseCode; name: string; server: string | null }[];
}
