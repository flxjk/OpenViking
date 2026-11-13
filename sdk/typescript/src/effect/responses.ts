export interface HealthResponse {
  readonly status: string;
  readonly healthy: boolean;
  readonly version: string;
  readonly auth_mode: string;
}

export interface SystemStatusResponse {
  readonly initialized: boolean;
  readonly user: string;
}

export interface FsEntry {
  readonly uri: string;
  readonly size: number;
  readonly isDir: boolean;
  readonly modTime: string;
  readonly abstract?: string;
}

export interface FsStat {
  readonly uri: string;
  readonly size: number;
  readonly isDir: boolean;
  readonly modTime: string;
  readonly abstract?: string;
  readonly [key: string]: unknown;
}

export interface SearchHit {
  readonly context_type: string;
  readonly uri: string;
  readonly level: number;
  readonly score: number;
  readonly abstract: string;
  readonly tags: readonly string[];
}

export interface SearchResult {
  readonly memories: readonly SearchHit[];
  readonly resources: readonly SearchHit[];
  readonly skills: readonly SearchHit[];
  readonly total: number;
}

export interface SessionSummary {
  readonly session_id: string;
  readonly uri: string;
  readonly is_dir: boolean;
  readonly mod_time: string;
}

export interface SkillSummary {
  readonly type: string;
  readonly name: string;
  readonly uri: string;
  readonly root_uri: string;
  readonly skill_md_uri: string;
  readonly description?: string;
}

export interface SkillList {
  readonly root_uris: readonly string[];
  readonly skills: readonly SkillSummary[];
}

export interface TaskInfo {
  readonly task_id: string;
  readonly status: string;
  readonly [key: string]: unknown;
}

export interface MemoryStats {
  readonly [key: string]: unknown;
}

export interface McpServer {
  readonly name: string;
  readonly url?: string;
  readonly tools?: readonly unknown[];
  readonly [key: string]: unknown;
}

export interface McpList {
  readonly servers?: readonly McpServer[];
  readonly [key: string]: unknown;
}

export interface WatchInfo {
  readonly task_id?: string;
  readonly uri?: string;
  readonly [key: string]: unknown;
}

export interface AccountInfo {
  readonly account_id: string;
  readonly [key: string]: unknown;
}

export interface ConsoleSummary {
  readonly [key: string]: unknown;
}

export interface ObserverSnapshot {
  readonly [key: string]: unknown;
}

export interface RuleSearchResult {
  readonly [key: string]: unknown;
}

export interface PrivacyCategory {
  readonly [key: string]: unknown;
}

export interface AclState {
  readonly [key: string]: unknown;
}

export interface CompileResult {
  readonly [key: string]: unknown;
}

export interface BotChatResult {
  readonly [key: string]: unknown;
}

export interface PackResult {
  readonly [key: string]: unknown;
}

export interface SnapshotDiff {
  readonly [key: string]: unknown;
}
