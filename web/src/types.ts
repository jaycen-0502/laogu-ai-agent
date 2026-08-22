export type Role = "ADMIN" | "OWNER" | "MEMBER";
export type User = {
  user_id: string;
  username: string;
  role: Role;
  workspace_id: string | null;
  workspace_name?: string | null;
  status: string;
  created_at?: string;
  permissions?: Record<string, boolean>;
};
export type Invitation = {
  invitation_id: string;
  workspace_id: string;
  workspace_name: string;
  role: "OWNER" | "MEMBER";
  status: "ACTIVE" | "ACCEPTED" | "EXPIRED" | "REVOKED";
  created_by: string;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
  accepted_user_id: string | null;
  token?: string;
  invite_path?: string;
};
export type Page<T> = {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
};
export type Workspace = {
  workspace_id: string;
  id?: string;
  name: string;
  status: string;
  created_at?: string;
  user_count?: number;
  agent_count?: number;
  profile_count?: number;
  account_count?: number;
  task_count?: number;
};
export type Agent = {
  agent_id: string;
  workspace_id: string;
  agent_name: string;
  machine_name: string;
  client_version: string;
  status: string;
  last_heartbeat: string | null;
  profile_count: number;
  running_task_count: number;
  binding_status?: "BOUND" | "UNBOUND" | string;
  bound_ip?: string | null;
  last_ip?: string | null;
  ip_country?: string;
};
export type Account = {
  id: string;
  workspace_id: string;
  agent_id: string;
  profile_id: string;
  instance_id: string;
  x_username: string;
  x_account_id: string;
  login_status: string;
  browser_status: string;
  account_status: string;
  last_checked: string | null;
  mapping_updated_at: string | null;
};
export type Profile = {
  profile_id: string;
  agent_id: string;
  workspace_id: string;
  browser_status: string;
  x_username: string;
  x_account_id: string;
  login_status: string;
  account_status: string;
  last_checked: string | null;
};
export type Task = {
  task_id: string;
  workspace_id: string;
  agent_id: string;
  profile_id: string;
  x_account_id: string;
  task_type: string;
  params: Record<string, unknown>;
  status: string;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration: number;
  result?: unknown;
  error?: string | null;
  activity?: Activity | null;
  script_id?: string | null;
  script_version_id?: string | null;
  script_name?: string;
  script_version?: number | null;
};
export type Activity = {
  activity_id: string;
  workspace_id: string;
  agent_id: string;
  profile_id: string;
  task_id: string;
  activity_type: string;
  action: string;
  status: string;
  duration: number;
  summary: string;
  result?: unknown;
  logs?: string[];
  timestamp: string;
};
export type Audit = {
  audit_id: string;
  timestamp: string;
  user_id: string | null;
  workspace_id: string | null;
  agent_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  result: string;
  ip: string;
  user_agent: string;
  message: string;
};
export type Dashboard = {
  period: string;
  workspace_count: number;
  agent_count: number;
  online_agents: number;
  offline_agents: number;
  agent_online_rate: number;
  profile_count: number;
  logged_in_accounts: number;
  running_tasks: number;
  success_tasks: number;
  failed_tasks: number;
  task_success_rate: number;
  recent_activities: Activity[];
  recent_tasks: Task[];
};

export type ControlSummary = {
  workspace_count: number;
  agent_count: number;
  online_agents: number;
  offline_agents: number;
  profile_count: number;
  account_count: number;
  logged_in_accounts: number;
  task_count: number;
  pending_tasks: number;
  running_tasks: number;
  success_tasks: number;
  failed_tasks: number;
  cancelled_tasks: number;
  script_count: number;
  enabled_scripts: number;
  enabled_providers: number;
  ai_request_count: number;
  ai_total_tokens: number;
  analysis_count: number;
  writing_count: number;
  image_count: number;
  chat_session_count: number;
  today_automation_runs: number;
  today_processed_count: number;
  today_likes: number;
  today_follows: number;
  today_comments: number;
  today_scanned_posts: number;
};

export type AutomationMetrics = {
  automation_runs: number;
  processed_count: number;
  likes: number;
  follows: number;
  comments: number;
  scanned_posts: number;
};

export type AutomationMetricRun = Omit<AutomationMetrics, "automation_runs"> & {
  run_id: string;
  profile_id: string;
  x_account_id: string;
  account_tag: string;
  metric_date: string;
  started_at: string;
  finished_at: string;
  status: string;
  own_followers: number | null;
  own_following: number | null;
};

export type ControlAgent = Agent & {
  profile_total: number;
  pending_tasks: number;
  running_tasks: number;
};

export type ControlProfile = Profile & {
  profile_record_id: string;
  agent_name: string;
  current_task: Task | null;
  task_count: number;
  today_metrics: AutomationMetrics;
};

export type ControlAudit = Audit;

export type ControlOverview = {
  generated_at: string;
  scope: "global" | "workspace";
  summary: ControlSummary;
  agents: ControlAgent[];
  profiles: ControlProfile[];
  recent_tasks: (Task & { script_name?: string; script_version?: number | null })[];
  recent_activities: Activity[];
  recent_audits: ControlAudit[];
};

export type ControlProfileDetail = {
  profile: ControlProfile;
  agent: Agent | null;
  account: Account | null;
  tasks: (Task & { script_name?: string; script_version?: number | null })[];
  activities: Activity[];
  today_metrics: AutomationMetrics;
  automation_metrics: AutomationMetricRun[];
};

export type OpsMetrics = {
  service: { version: string; started_at: string | null; uptime_seconds: number };
  database: { reachable: boolean };
  agents: { total: number; online: number; offline: number };
  commands: {
    total: number;
    by_status: Record<string, number>;
    stale_delivered: number;
    lease_seconds: number;
  };
  channels: { websocket: boolean; http_pull_fallback: boolean };
};

export type License = {
  id: string;
  license_id: string;
  customer: string;
  issued_at: string;
  expires_at: string;
  features: string[];
  status: string;
  offline_grace_days: number;
  created_by: string;
  created_at: string;
  updated_at: string;
  revoked_at: string | null;
  device_count: number;
  last_check: string | null;
};

export type LicenseDevice = {
  id: string;
  device_id: string;
  app_version: string;
  first_seen_at: string;
  last_seen_at: string;
  last_ip: string;
  status: string;
};

export type LicenseCheck = {
  id: string;
  device_id: string;
  app_version: string;
  result: string;
  reason: string;
  checked_at: string;
  ip: string;
};

export type ScriptVersion = {
  script_version_id: string;
  script_id: string;
  version: number;
  source?: string;
  params_schema: Record<string, unknown>;
  sha256: string;
  created_by: string;
  created_at: string;
};

export type Script = {
  script_id: string;
  workspace_id: string;
  name: string;
  description: string;
  language: "javascript";
  status: "ENABLED" | "DISABLED";
  current_version: number;
  created_by: string;
  created_by_username: string;
  created_at: string;
  updated_at: string;
  current_version_detail?: ScriptVersion | null;
};

export type AIProvider = {
  provider_id: string;
  workspace_id: string;
  name: string;
  provider_type: "OPENAI" | "OPENAI_COMPATIBLE";
  base_url: string;
  api_key_masked: string;
  has_api_key: boolean;
  default_model: string;
  models: string[];
  status: "ENABLED" | "DISABLED";
  is_default: boolean;
  last_test_status: "UNKNOWN" | "SUCCESS" | "FAILED";
  last_tested_at: string | null;
  last_error: string;
  created_by: string;
  created_at: string;
  updated_at: string;
};

export type ChatUsage = {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  latency_ms: number;
};

export type ChatMessage = {
  message_id: string;
  session_id: string;
  role: "system" | "user" | "assistant";
  content: string;
  status: "PENDING" | "STREAMING" | "SUCCESS" | "FAILED" | "CANCELLED";
  error: string;
  created_at: string;
  usage: ChatUsage;
};

export type ChatSession = {
  session_id: string;
  workspace_id: string;
  user_id: string;
  title: string;
  provider_id: string;
  provider_name: string;
  model: string;
  is_running: boolean;
  created_at: string;
  updated_at: string;
};

export type ChatSessionDetail = ChatSession & {
  messages: ChatMessage[];
  usage: ChatUsage;
};

export type AIImage = {
  image_id: string;
  workspace_id: string;
  user_id: string;
  provider_id: string;
  provider_name: string;
  model: "gpt-image-2";
  prompt: string;
  resolution: "1K" | "2K";
  size: string;
  quality: "low" | "medium" | "high";
  status: "PENDING" | "SUCCESS" | "FAILED";
  mime_type: string;
  byte_size: number;
  prompt_tokens: number;
  image_tokens: number;
  total_tokens: number;
  latency_ms: number;
  error_code: string;
  error: string;
  content_url: string;
  created_at: string;
  completed_at: string | null;
};

export type AIAnalysis = {
  analysis_id: string;
  workspace_id: string;
  user_id: string;
  provider_id: string;
  provider_name: string;
  account_id: string | null;
  analysis_type: "ACCOUNT" | "KEYWORD";
  title: string;
  model: string;
  status: "PENDING" | "SUCCESS" | "FAILED";
  keywords: string[];
  summary: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  latency_ms: number;
  error_code: string;
  error: string;
  input_text?: string;
  source_snapshot?: Record<string, unknown>;
  result?: Record<string, unknown>;
  created_at: string;
  completed_at: string | null;
};

export type AIWritingReply = {
  text: string;
  tone: string;
  reason: string;
  character_count: number;
};

export type AIWritingRecord = {
  record_id: string;
  workspace_id: string;
  user_id: string;
  provider_id: string;
  provider_name: string;
  account_id: string | null;
  record_type: "ANALYSIS" | "REPLY";
  title: string;
  model: string;
  status: "PENDING" | "SUCCESS" | "FAILED";
  parameters: Record<string, unknown>;
  summary: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  latency_ms: number;
  error_code: string;
  error: string;
  source_text?: string;
  context_text?: string;
  result?: Record<string, unknown> & { replies?: AIWritingReply[] };
  created_at: string;
  completed_at: string | null;
};

export type AITaskProposalPlan = {
  summary?: string;
  script_id?: string | null;
  script_version_id?: string | null;
  script_name?: string;
  script_version?: number | null;
  profile_ids?: string[];
  profile_labels?: { profile_id: string; x_username?: string }[];
  params?: Record<string, unknown>;
  timeout?: number;
  reason?: string;
  risk_notes?: string[];
  needs_confirmation?: boolean;
  catalog_match?: boolean;
};

export type AITaskProposal = {
  proposal_id: string;
  workspace_id: string;
  user_id: string;
  provider_id: string;
  provider_name: string;
  model: string;
  status: "PENDING" | "DRAFT" | "CONFIRMED" | "REJECTED" | "FAILED";
  summary: string;
  plan: AITaskProposalPlan;
  task_ids: string[];
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  latency_ms: number;
  error_code: string;
  error: string;
  request_text?: string;
  result?: Record<string, unknown>;
  tasks?: Task[];
  created_at: string;
  completed_at: string | null;
};
