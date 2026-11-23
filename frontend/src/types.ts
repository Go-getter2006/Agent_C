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

/** 一次提交和上一次比：改了哪几行、修好了什么、还剩什么（只有他自己的代码和错误的名称）。 */
export interface SubmissionChange {
  from: number;
  available: boolean;
  changed: number;
  big: boolean;
  lines: { from: number; to: number }[];
  excerpt: { type: "add" | "del"; n: number; text: string }[];
  fixed: string[];
  still: string[];
  new: string[];
  passedNow: boolean;
}

export interface ProblemDetail {
  key: string;
  title: string;
  brief: string;
  homeworkKey: string | null;
  statement: string;   // 题面文字，[图N] 是示意图的位置
  figures: { n: number; url: string; width: number; height: number }[];
  pages: string[];   // 文字整理不出来时：题面 PDF 各页的图，直接显示
  submissions: SubmissionView[]; // 新的在前
  changes: Record<string, SubmissionChange>;
  knowledge: KnowledgeLevel[];
}

export interface FlowStep { title: string; how: string; kp: string }

export type Block =
  | { t: "p"; text: string }
  | { t: "code"; caption: string; start: number; lines: string[]; mark: number[] }
  // 解题流程图（还没做的题问“怎么做”）：pick 时让他选卡在哪几步；之后 current 是正在讲的那一步
  | { t: "flow"; steps: FlowStep[]; pick: boolean; current: number | null; done: number[] };

export type HelpAction = "think" | "hint" | "tell";

export interface QuizRecap {
  intro: string;
  items: { kind: "problem" | "quiz"; key?: string; attemptId?: number; title: string; text: string }[];
  analysis?: string;   // 大模型对这几次错在哪的分析（没写出合格的就是空的，只列事实）
  outro: string;
}

export interface QuizResult {
  outcome: "right" | "wrong" | "skipped";
  headline: string;
  explanation?: string;
  finalCode?: string[];
  bugLine?: number | null;
  fix?: { lines: string[]; mark: number[]; start?: number };
  lastRun?: string;
  lines?: number[];
  bugLines?: number[];
  origin?: { key: string; title: string } | null;
  challenge?: "kept" | "flipped" | null;   // 判断满足要求以后被追问：坚持还是改判
}

export interface QuizView {
  attemptId: number;
  /** 做完以后的一问一答（取一道做完的题时一起给，右边一次画全） */
  turns?: ReviewTurn[];
  stem: string;
  caption: string;
  code: string[];
  startLine?: number;
  mark?: number[];
  state: "confidence" | "judge" | "challenge" | "locate" | "edit" | "done";
  confidence: string | null;
  judged: "bug" | "ok" | null;
  protocol?: number;          // 2：独立作答（先判断 → 再选位置/依据 → 判断有问题的修改）；3：判断满足要求的改成智能体追问
  current?: "ok" | "bug" | null;   // 现在的判断（被追问后改判的是 bug；首次判断仍是 judged）
  challenge?: { text: string; reply: "kept" | "flipped" | null };
  lines?: number[];           // 学生首次选的行
  hints?: string[];
  moreHints?: boolean;
  hasSample?: boolean;
  triesLeft?: number;
  choices?: { index: number; label: string }[];
  draft?: string;
  message?: { kind: string; text: string } | null;
  result?: QuizResult;
  recap?: QuizRecap | null;    // 已经存入服务器的回顾，随复习历史返回
}

export interface Message {
  unreadNotice?: boolean;
  quotedAttemptNo?: number;
  id: number;
  role: "student" | "agent";
  kind: "text" | "quiz";
  time: string;
  blocks: Block[];
  actions: HelpAction[];
  quiz?: QuizView;
  textbook?: TextbookCitation[];
}

export interface TextbookCitation {
  edition: number;
  section: number;
  anchor: number;
  path: string[];
}

export interface TextbookContents {
  edition: number;
  title: string;
  sections: { id: number; parent: number | null; title: string }[];
}

export interface TextbookSection {
  title: string;
  pages: { url: string; width: number; height: number }[];
  highlight: { text: string; figure: boolean; boxes: [number, number, number, number, number][] } | null;
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
}

export type NextItem =
  | { kind: "problem"; problem: ProblemRef; reason: string }
  | { kind: "review"; reviewId: number; title: string; reason: string };

export interface ProgressData {
  summary: {
    passed: number;
    total: number;
    avgToPass: number | null;  // 通过的题平均交到第几次才通过
    avgSubmissions: number | null;
    thisWeek: number;
    lastWeek: number;
    reviewDue: number;
    reviewActive: number;
    levels: Record<string, number>;
  };
  knowledge: { topic: string; items: KnowledgeItem[] }[];
  // 只在数据结构这边有：作业要用到、还薄弱的 C 语言基础（只有档位）
  foundation: { name: string; level: string; segments: number; supports: string[]; text: string }[];
  errors: { name: string; count: number; last: ProblemRef; lastTime: string; status: string }[];
  activity: {
    homeworks: Homework[];
    tried: number;
    passed: number;
    avgToPass: number | null;
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

/** 复习提升左边列表里一道做完的复习题。 */
export interface ReviewAttemptRow { attemptId: number; title: string; time: string; right: boolean; asked: number }

/** 做完一道复习题以后，就这道题的一问一答。 */
export interface ReviewTurn { id: string; question: string; answer: string }

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
  last?: { attemptId: number; explanation: string; title: string; time: string; headline: string; summary: { label: string; text: string }[] } | null;
}

export type CourseCode = "C" | "D";

/** 知识问答的一个问答主题。general 是以前的综合对话。 */
export interface Topic {
  key: string;
  title: string;
  time: string;
}

export interface Me {
  role: "student" | "teacher" | "admin";
  name: string;
  loginName: string;
  course: CourseCode;
  courseName: string;
}

export interface StatusInfo {
  unread?: { problems: Record<string, number>; homeworks: string[] };
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
  // 可以登录的课程；server 是这门课的 XDOJ（演示数据时为空）
  courses: { code: CourseCode; name: string; server: string | null }[];
  // 首页一直显示的两门课。available 为 false：那台 XDOJ 还没接入，首页能切过去但不能登录
  allCourses?: { code: CourseCode; name: string; server: string | null; available: boolean }[];
}
