// Mirrors backend/src/ebb/api/schemas.py and the routes. Datetimes arrive as ISO strings.

export type Health = { database: string; tutor: string; timezone: string; costs: boolean };

export type SubjectSummary = {
  id: string;
  name: string;
  concept_count: number;
  due_count: number;
};

export type ConceptBrief = { name: string; description: string };
export type CreatedSubject = { id: string; name: string; concepts: ConceptBrief[] };

export type Source = {
  id: string;
  filename: string;
  kind: string;
  page_count: number | null;
  size_bytes: number;
  chunk_count: number;
  detail: string;
};

export type ConceptState = {
  id: string;
  name: string;
  description: string;
  stage: number;
  tide: number | null;
  band: string;
  next_review_at: string | null;
  due: boolean;
  projected_tide: number | null;
};

export type Concepts = {
  concepts: ConceptState[];
  due_count: number;
  next_review_at: string | null;
};

// Drafts and note uploads

export type CreatedDraft = { id: string };
export type CommittedDraft = { subject_id: string };

export type FileReading = { status: "reading" };
export type FileFinding = { status: "finding" };
export type DraftFileReady = {
  status: "ready";
  file_id: string;
  filename: string;
  page_count: number | null;
  passages: number;
  concepts: string[]; // matched and fresh names
  new: string[]; // the fresh ones
};
export type FileSaved = {
  status: "saved";
  detail: string;
  matched: string[];
  fresh: ConceptBrief[];
};
export type FileRefused = { status: "refused"; message: string; retry: boolean };

export type DraftFileEvent = FileReading | FileFinding | DraftFileReady | FileRefused;
export type SourceFileEvent = FileReading | FileFinding | FileSaved | FileRefused;

// Study sessions

export type Offer = {
  thread_id: string;
  concept: string;
  number: number;
  last_active_at: string;
  busy: boolean;
};
export type SessionOffer = { offer: Offer | null };

export type Citation = {
  kind: "chunk" | "web";
  ref: string;
  label: string;
  page: number | null;
  quote: string;
  name: string;
  heading: string | null;
  title: string | null;
};

export type GradeValue = "correct" | "partial" | "incorrect";

export type Grade = {
  value: GradeValue;
  explanation: string;
  misconception: string | null;
  claims: { source: string; quote: string }[];
  citations: Citation[];
  general_knowledge: boolean;
};

export type Lesson = { text: string; citations: Citation[] };

export type Question = {
  type: "mcq" | "short";
  difficulty: string;
  prompt: string;
  options: string[];
  source: "notes" | "web" | "general";
  source_line: string | null;
};

export type DisputeReason = "correct" | "ambiguous" | "notes_differ" | "other";
export type DisputeOutcome = "changed" | "voided" | "upheld" | "refused" | "failed";

// A refused or failed re-check carries only its outcome.
export type DisputeResult =
  | { outcome: "refused" | "failed" }
  | {
      outcome: "changed" | "voided" | "upheld";
      original_grade: GradeValue;
      new_grade: GradeValue | null;
      explanation: string;
      citations: Citation[];
      concept: string;
      stage: number;
    };

export type TurnPayload = {
  concept: string;
  number: number;
  question: Question;
  feedback: Grade | null;
  lesson: Lesson | null;
  notices: string[];
  can_dispute: boolean;
  dispute_result: DisputeResult | null;
};

export type ConceptResult = {
  concept: string;
  concept_id: string;
  tally: { correct: number; partial: number; incorrect: number };
  stage_before: number;
  stage_after: number;
  tide_before: number | null;
  tide_after: number | null;
  next_review_at: string;
};

export type Finished = {
  results: ConceptResult[];
  final: { grade: Grade | null; lesson: Lesson | null; notices: string[] };
  caught_up: boolean;
  next_review_at: string | null;
  next_review_line: string | null;
};

export type Started = { thread_id: string; greeting: string };
export type Step = { label: string };
export type StreamError = { notice: string } | { error: string };

export type StudyEvent =
  | { event: "started"; data: Started }
  | { event: "step"; data: Step }
  | { event: "turn"; data: { payload: TurnPayload } }
  | { event: "finished"; data: Finished }
  | { event: "error"; data: StreamError };

export type AnswerReply = { attempt_id: string; answer: string };
export type DisputeReply = {
  dispute: { id: string; reason: DisputeReason; explanation: string };
};
export type QuitReply = { quit: true };
export type Reply = AnswerReply | DisputeReply | QuitReply;
export type ReplyBody = { driver: string; reply: Reply };

// Progress

export type Streak = { current: number; best: number; days: boolean[] }; // days: the last 35, oldest first
export type DueConcept = { name: string; tide: number | null };
export type Average = {
  now: number | null;
  change: number | null;
  since: string;
  counted: number;
  not_counted: string[];
};
export type ChartConcept = {
  id: string;
  name: string;
  studied: boolean;
  now: number | null;
  past: [string, number | null][];
  ahead: [string, number][];
};
export type Chart = { enough_days: boolean; concepts: ChartConcept[] };
export type StagedConcept = { name: string; next_review_at: string | null };
export type Stage = { stage: number; concepts: StagedConcept[] };
export type Progress = {
  streak: Streak;
  due: DueConcept[];
  average: Average;
  chart: Chart;
  stages: Stage[];
};

// The dev Costs page (routes_costs.py). Decimals arrive as strings; null means unknown.
export type CostSummary = {
  cost_usd: string | null;
  unpriced_calls: number;
  input_tokens: number | null;
  output_tokens: number | null;
  calls: number;
  retried: number;
  fell_back: number;
  ttft_p50_ms: number | null;
  ttft_p95_ms: number | null;
  total_p50_ms: number | null;
  total_p95_ms: number | null;
  fallback_rate: number | null;
  retry_rate: number | null;
  all_free_tier: boolean;
};

export type CostBreakdown = {
  key: string;
  label: string;
  calls: number;
  cost_usd: string | null;
  unpriced_calls: number;
  input_tokens: number | null;
  output_tokens: number | null;
  ttft_p50_ms: number | null;
  ttft_p95_ms: number | null;
  total_p50_ms: number | null;
  total_p95_ms: number | null;
  output_tokens_per_second: number | null;
  fallbacks: number;
};

export type CostDay = {
  day: string;
  input_tokens: number | null;
  output_tokens: number | null;
  tokens: number | null;
  calls: number;
};

export type ModelRef = { provider: string; model: string };

export type CostRole = {
  role: string;
  main: ModelRef;
  backup: ModelRef | null;
  attempts: number;
  failed_share: number | null;
  p95_total_ms: number | null;
};

export type CostAnomaly = {
  kind: "tokens" | "ttft" | "total" | "day";
  call_id: string | null;
  day: string | null;
  endpoint: string | null;
  ratio: number;
  message: string;
};

export type CostCall = {
  id: string;
  call_id: string;
  request_id: string | null;
  user_id: string;
  at: string;
  role: string;
  endpoint: string;
  endpoint_label: string;
  provider: string;
  model: string;
  attempt: number;
  outcome: string;
  input_tokens: number | null;
  output_tokens: number | null;
  reasoning_tokens: number | null;
  ttft_ms: number | null;
  total_ms: number;
  cost_usd: string | null;
  backup: boolean;
  error_kind: string | null;
  anomaly: string | null;
};

export type CostSummaryPage = {
  summary: CostSummary;
  by_feature: CostBreakdown[];
  per_day: CostDay[];
  roles: CostRole[];
  by_model: CostBreakdown[];
  by_endpoint: CostBreakdown[];
  by_tenant: CostBreakdown[];
  anomalies: CostAnomaly[];
  failures: CostCall[];
};

export type CostPrice = {
  provider: string;
  model: string;
  input_per_m: string;
  output_per_m: string;
  source: string;
  as_of: string;
  free_tier: boolean;
};
