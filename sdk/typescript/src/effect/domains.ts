import * as Context from "effect/Context";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";
import * as Stream from "effect/Stream";

import type { EndpointBody, EndpointKey, EndpointQuery } from "../generated/endpoints.js";
import { OpenViking, type OpenVikingShape } from "./client.js";
import type { OpenVikingError } from "./errors.js";
import type {
  AclState,
  AccountInfo,
  BotChatResult,
  CompileResult,
  ConsoleSummary,
  SearchResult,
  FsEntry,
  FsStat,
  HealthResponse,
  McpList,
  MemoryStats,
  ObserverSnapshot,
  PackResult,
  PrivacyCategory,
  RuleSearchResult,
  SessionSummary,
  SkillList,
  SkillSummary,
  SnapshotDiff,
  SystemStatusResponse,
  TaskInfo,
  WatchInfo,
} from "./responses.js";
import type { ServerSentEvent } from "./stream.js";

type Call = <K extends EndpointKey>(
  key: K,
  args?: {
    path?: never;
    query?: EndpointQuery<K>;
    body?: EndpointBody<K>;
    headers?: Readonly<Record<string, string>> | undefined;
  },
) => Effect.Effect<unknown, OpenVikingError>;

type CallPath = <K extends EndpointKey>(
  key: K,
  path: Record<string, string>,
  args?: { query?: EndpointQuery<K>; body?: EndpointBody<K>; headers?: Readonly<Record<string, string>> | undefined },
) => Effect.Effect<unknown, OpenVikingError>;

export interface SearchDomain {
  readonly find: (body: EndpointBody<"findApiV1SearchFindPost">) => Effect.Effect<SearchResult, OpenVikingError>;
  readonly search: (body: EndpointBody<"searchApiV1SearchSearchPost">) => Effect.Effect<SearchResult, OpenVikingError>;
  readonly recall: (body: EndpointBody<"recallApiV1SearchRecallPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly grep: (body: EndpointBody<"grepApiV1SearchGrepPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly glob: (body: EndpointBody<"globApiV1SearchGlobPost">) => Effect.Effect<unknown, OpenVikingError>;
}

export interface ContentDomain {
  readonly download: (query: EndpointQuery<"downloadApiV1ContentDownloadGet">) => Effect.Effect<unknown, OpenVikingError>;
  readonly ensureDirectoryIndex: (body: EndpointBody<"ensureDirectoryIndexApiV1ContentEnsureDirectoryIndexPost">) => Effect.Effect<unknown, OpenVikingError>;

  readonly read: (query: EndpointQuery<"readApiV1ContentReadGet">) => Effect.Effect<string, OpenVikingError>;
  readonly abstract: (query: EndpointQuery<"abstractApiV1ContentAbstractGet">) => Effect.Effect<string, OpenVikingError>;
  readonly overview: (query: EndpointQuery<"overviewApiV1ContentOverviewGet">) => Effect.Effect<string, OpenVikingError>;
  readonly write: (body: EndpointBody<"writeApiV1ContentWritePost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly batchWrite: (body: EndpointBody<"batchWriteApiV1ContentBatchWritePost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly reindex: (body: EndpointBody<"reindexApiV1ContentReindexPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly setTags: (body: EndpointBody<"setTagsApiV1ContentSetTagsPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly batchSetTags: (body: EndpointBody<"batchSetTagsApiV1ContentBatchSetTagsPost">) => Effect.Effect<unknown, OpenVikingError>;
}

export interface FsDomain {
  readonly setTags: (body: EndpointBody<"attrsSetTagsApiV1FsAttrsSetTagsPost">) => Effect.Effect<unknown, OpenVikingError>;

  readonly ls: (query: EndpointQuery<"lsApiV1FsLsGet">) => Effect.Effect<readonly FsEntry[], OpenVikingError>;
  readonly tree: (query: EndpointQuery<"treeApiV1FsTreeGet">) => Effect.Effect<readonly FsEntry[], OpenVikingError>;
  readonly stat: (query: EndpointQuery<"statApiV1FsStatGet">) => Effect.Effect<FsStat, OpenVikingError>;
  readonly attrs: (query: EndpointQuery<"attrsApiV1FsAttrsGet">) => Effect.Effect<unknown, OpenVikingError>;
  readonly mkdir: (body: EndpointBody<"mkdirApiV1FsMkdirPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly rm: (query: EndpointQuery<"rmApiV1FsDelete">) => Effect.Effect<unknown, OpenVikingError>;
  readonly cp: (body: EndpointBody<"cpApiV1FsCpPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly mv: (body: EndpointBody<"mvApiV1FsMvPost">) => Effect.Effect<unknown, OpenVikingError>;
}

export interface SessionsDomain {
  readonly archive: (sessionId: string, archiveId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly retryArchive: (sessionId: string, archiveId: string, body: EndpointBody<"retrySessionArchiveApiV1SessionsSessionIdArchivesArchiveIdRetryPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly toolResults: (sessionId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly toolResult: (sessionId: string, toolResultId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly searchToolResult: (sessionId: string, toolResultId: string, query: EndpointQuery<"searchToolResultApiV1SessionsSessionIdToolResultsToolResultIdSearchGet">) => Effect.Effect<unknown, OpenVikingError>;

  readonly list: () => Effect.Effect<readonly SessionSummary[], OpenVikingError>;
  readonly create: (body: EndpointBody<"createSessionApiV1SessionsPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly get: (sessionId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly remove: (sessionId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly addMessage: (
    sessionId: string,
    body: EndpointBody<"addMessageApiV1SessionsSessionIdMessagesPost">,
  ) => Effect.Effect<unknown, OpenVikingError>;
  readonly batchAddMessages: (
    sessionId: string,
    body: EndpointBody<"batchAddMessagesApiV1SessionsSessionIdMessagesBatchPost">,
  ) => Effect.Effect<unknown, OpenVikingError>;
  readonly commit: (
    sessionId: string,
    body: EndpointBody<"commitSessionApiV1SessionsSessionIdCommitPost">,
  ) => Effect.Effect<unknown, OpenVikingError>;
  readonly context: (sessionId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly updateConfig: (
    sessionId: string,
    body: EndpointBody<"updateSessionConfigApiV1SessionsSessionIdConfigPatch">,
  ) => Effect.Effect<unknown, OpenVikingError>;
}

export interface SkillsDomain {
  readonly list: () => Effect.Effect<SkillList, OpenVikingError>;
  readonly find: (body: EndpointBody<"findSkillsApiV1SkillsFindPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly get: (name: string) => Effect.Effect<SkillSummary, OpenVikingError>;
  readonly add: (body: EndpointBody<"addSkillApiV1SkillsPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly update: (
    name: string,
    body: EndpointBody<"updateSkillApiV1SkillsSkillNamePut">,
  ) => Effect.Effect<unknown, OpenVikingError>;
  readonly remove: (name: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly validate: (body: EndpointBody<"validateSkillApiV1SkillsValidatePost">) => Effect.Effect<unknown, OpenVikingError>;
}

export interface McpsDomain {
  readonly batchMount: (body: EndpointBody<"batchMountMcpToolsApiV1McpsBatchMountPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly batchUnmount: (body: EndpointBody<"batchUnmountMcpToolsApiV1McpsBatchUnmountPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly status: () => Effect.Effect<unknown, OpenVikingError>;

  readonly list: () => Effect.Effect<McpList, OpenVikingError>;
  readonly add: (body: EndpointBody<"addMcpApiV1McpsPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly find: (body: EndpointBody<"findMcpToolsApiV1McpsFindPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly invoke: (body: EndpointBody<"invokeMcpToolApiV1McpsInvokePost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly mount: (body: EndpointBody<"mountMcpToolApiV1McpsMountPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly unmount: (query: EndpointQuery<"unmountMcpToolApiV1McpsMountDelete">) => Effect.Effect<unknown, OpenVikingError>;
  readonly mounts: () => Effect.Effect<unknown, OpenVikingError>;
  readonly show: (name: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly remove: (name: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly sync: (name: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly validate: (body: EndpointBody<"validateMcpApiV1McpsValidatePost">) => Effect.Effect<unknown, OpenVikingError>;
}

export interface SystemDomain {
  readonly health: () => Effect.Effect<HealthResponse, OpenVikingError>;
  readonly ready: () => Effect.Effect<unknown, OpenVikingError>;
  readonly status: () => Effect.Effect<SystemStatusResponse, OpenVikingError>;
  readonly wait: (body: EndpointBody<"waitProcessedApiV1SystemWaitPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly consistency: (
    body: EndpointBody<"checkConsistencyApiV1SystemConsistencyPost">,
  ) => Effect.Effect<unknown, OpenVikingError>;
}

export interface TasksDomain {
  readonly list: (query: EndpointQuery<"listTasksApiV1TasksGet">) => Effect.Effect<readonly TaskInfo[], OpenVikingError>;
  readonly get: (taskId: string) => Effect.Effect<TaskInfo, OpenVikingError>;
  readonly cancel: (taskId: string) => Effect.Effect<unknown, OpenVikingError>;
}

export interface StatsDomain {
  readonly memories: () => Effect.Effect<MemoryStats, OpenVikingError>;
  readonly session: (sessionId: string) => Effect.Effect<unknown, OpenVikingError>;
}

export interface WatchesDomain {
  readonly patchById: (taskId: string, body: EndpointBody<"patchWatchByIdApiV1WatchesTaskIdPatch">) => Effect.Effect<unknown, OpenVikingError>;
  readonly removeById: (taskId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly triggerById: (taskId: string) => Effect.Effect<unknown, OpenVikingError>;

  readonly list: (query: EndpointQuery<"listOrGetWatchApiV1WatchesGet">) => Effect.Effect<readonly WatchInfo[], OpenVikingError>;
  readonly get: (taskId: string) => Effect.Effect<WatchInfo, OpenVikingError>;
  readonly patch: (body: EndpointBody<"patchWatchByUriApiV1WatchesPatch">) => Effect.Effect<unknown, OpenVikingError>;
  readonly remove: (query: EndpointQuery<"deleteWatchByUriApiV1WatchesDelete">) => Effect.Effect<unknown, OpenVikingError>;
  readonly trigger: (body: EndpointBody<"triggerWatchByUriApiV1WatchesTriggerPost">) => Effect.Effect<unknown, OpenVikingError>;
}

export interface AdminDomain {
  readonly accountConfiguration: (accountId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly patchAccountConfiguration: (accountId: string, body: EndpointBody<"patchAccountConfigurationApiV1AdminAccountsAccountIdConfigurationPatch">) => Effect.Effect<unknown, OpenVikingError>;
  readonly createGroup: (accountId: string, body: EndpointBody<"createGroupApiV1AdminAccountsAccountIdGroupsPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly deleteGroup: (accountId: string, groupId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly groupMembers: (accountId: string, groupId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly addGroupMembers: (accountId: string, groupId: string, body: EndpointBody<"addGroupMembersApiV1AdminAccountsAccountIdGroupsGroupIdMembersPut">) => Effect.Effect<unknown, OpenVikingError>;
  readonly removeGroupMembers: (accountId: string, groupId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly memoryTemplates: (accountId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly memoryTemplate: (accountId: string, memoryType: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly putMemoryTemplate: (accountId: string, memoryType: string, body: EndpointBody<"putMemoryTemplateApiV1AdminAccountsAccountIdMemoryTemplatesMemoryTypePut">) => Effect.Effect<unknown, OpenVikingError>;
  readonly resetMemoryTemplate: (accountId: string, memoryType: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly accountSettings: (accountId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly patchAccountSettings: (accountId: string, body: EndpointBody<"patchAccountSettingsApiV1AdminAccountsAccountIdSettingsPatch">) => Effect.Effect<unknown, OpenVikingError>;
  readonly user: (accountId: string, userId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly removeUser: (accountId: string, userId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly regenerateKey: (accountId: string, userId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly setUserRole: (accountId: string, userId: string, body: EndpointBody<"setUserRoleApiV1AdminAccountsAccountIdUsersUserIdRolePut">) => Effect.Effect<unknown, OpenVikingError>;
  readonly userSettings: (accountId: string, userId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly patchUserSettings: (accountId: string, userId: string, body: EndpointBody<"patchUserSettingsApiV1AdminAccountsAccountIdUsersUserIdSettingsPatch">) => Effect.Effect<unknown, OpenVikingError>;
  readonly agentEvolution: () => Effect.Effect<unknown, OpenVikingError>;
  readonly setAgentEvolution: (body: EndpointBody<"setAgentEvolutionStatusApiV1AdminAgentEvolutionPut">) => Effect.Effect<unknown, OpenVikingError>;

  readonly accounts: () => Effect.Effect<readonly AccountInfo[], OpenVikingError>;
  readonly account: (accountId: string) => Effect.Effect<AccountInfo, OpenVikingError>;
  readonly createAccount: (body: EndpointBody<"createAccountApiV1AdminAccountsPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly deleteAccount: (accountId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly users: (accountId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly registerUser: (
    accountId: string,
    body: EndpointBody<"registerUserApiV1AdminAccountsAccountIdUsersPost">,
  ) => Effect.Effect<unknown, OpenVikingError>;
  readonly groups: (accountId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly configuration: () => Effect.Effect<unknown, OpenVikingError>;
  readonly patchConfiguration: (
    body: EndpointBody<"patchClusterConfigurationApiV1AdminConfigurationPatch">,
  ) => Effect.Effect<unknown, OpenVikingError>;
  readonly restart: (body: EndpointBody<"restartServerApiV1AdminRestartPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly migrate: (body: EndpointBody<"migrateDataApiV1AdminMigratePost">) => Effect.Effect<unknown, OpenVikingError>;
}

export interface PackDomain {
  readonly export: (body: EndpointBody<"exportOvpackApiV1PackExportPost">) => Effect.Effect<PackResult, OpenVikingError>;
  readonly import: (body: EndpointBody<"importOvpackApiV1PackImportPost">) => Effect.Effect<PackResult, OpenVikingError>;
  readonly backup: (body: EndpointBody<"backupOvpackApiV1PackBackupPost">) => Effect.Effect<PackResult, OpenVikingError>;
  readonly restore: (body: EndpointBody<"restoreOvpackApiV1PackRestorePost">) => Effect.Effect<PackResult, OpenVikingError>;
}

export interface SnapshotDomain {
  readonly ignore: () => Effect.Effect<unknown, OpenVikingError>;
  readonly setIgnore: (body: EndpointBody<"setIgnoreApiV1SnapshotIgnorePut">) => Effect.Effect<unknown, OpenVikingError>;
  readonly removeIgnore: () => Effect.Effect<unknown, OpenVikingError>;

  readonly diff: (query: EndpointQuery<"diffApiV1SnapshotDiffGet">) => Effect.Effect<SnapshotDiff, OpenVikingError>;
  readonly diffPaths: (query: EndpointQuery<"diffPathsApiV1SnapshotDiffPathsGet">) => Effect.Effect<unknown, OpenVikingError>;
  readonly commit: (body: EndpointBody<"commitApiV1SnapshotCommitPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly log: (query: EndpointQuery<"logApiV1SnapshotLogGet">) => Effect.Effect<unknown, OpenVikingError>;
  readonly show: (query: EndpointQuery<"showApiV1SnapshotShowGet">) => Effect.Effect<unknown, OpenVikingError>;
  readonly versions: (query: EndpointQuery<"versionsApiV1SnapshotVersionsGet">) => Effect.Effect<unknown, OpenVikingError>;
  readonly restore: (body: EndpointBody<"restoreApiV1SnapshotRestorePost">) => Effect.Effect<unknown, OpenVikingError>;
}

export interface ConsoleDomain {
  readonly summary: () => Effect.Effect<ConsoleSummary, OpenVikingError>;
  readonly audit: (query: EndpointQuery<"auditLogsApiV1ConsoleAuditGet">) => Effect.Effect<unknown, OpenVikingError>;
  readonly tokens: (query: EndpointQuery<"tokenSeriesApiV1ConsoleTokensGet">) => Effect.Effect<unknown, OpenVikingError>;
  readonly commits: (query: EndpointQuery<"contextCommitsApiV1ConsoleContextCommitsGet">) => Effect.Effect<unknown, OpenVikingError>;
}

export interface ObserverDomain {
  readonly system: () => Effect.Effect<ObserverSnapshot, OpenVikingError>;
  readonly queue: () => Effect.Effect<ObserverSnapshot, OpenVikingError>;
  readonly models: () => Effect.Effect<ObserverSnapshot, OpenVikingError>;
  readonly retrieval: () => Effect.Effect<ObserverSnapshot, OpenVikingError>;
  readonly filesystem: () => Effect.Effect<ObserverSnapshot, OpenVikingError>;
  readonly lock: () => Effect.Effect<ObserverSnapshot, OpenVikingError>;
  readonly vikingdb: () => Effect.Effect<ObserverSnapshot, OpenVikingError>;
}

export interface RulesDomain {
  readonly search: (body: EndpointBody<"searchRulesApiV1RulesSearchPost">) => Effect.Effect<RuleSearchResult, OpenVikingError>;
  readonly compile: (body: EndpointBody<"compileRulesApiV1RulesCompilePost">) => Effect.Effect<unknown, OpenVikingError>;
}

export interface PrivacyDomain {
  readonly targets: (category: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly current: (category: string, targetKey: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly upsert: (category: string, targetKey: string, body: EndpointBody<"upsertPrivacyConfigApiV1PrivacyConfigsCategoryTargetKeyPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly patch: (category: string, targetKey: string, body: EndpointBody<"patchPrivacyConfigApiV1PrivacyConfigsCategoryTargetKeyPatch">) => Effect.Effect<unknown, OpenVikingError>;
  readonly remove: (category: string, targetKey: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly activate: (category: string, targetKey: string, body: EndpointBody<"activatePrivacyVersionApiV1PrivacyConfigsCategoryTargetKeyActivatePost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly fields: (category: string, targetKey: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly field: (category: string, targetKey: string, query: EndpointQuery<"getPrivacyFieldApiV1PrivacyConfigsCategoryTargetKeyFieldGet">) => Effect.Effect<unknown, OpenVikingError>;
  readonly removeField: (category: string, targetKey: string, query: EndpointQuery<"deletePrivacyFieldApiV1PrivacyConfigsCategoryTargetKeyFieldDelete">) => Effect.Effect<unknown, OpenVikingError>;
  readonly versions: (category: string, targetKey: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly version: (category: string, targetKey: string, version: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly removeSelector: (body: EndpointBody<"deletePrivacySelectorApiV1PrivacyConfigsSelectorDelete">) => Effect.Effect<unknown, OpenVikingError>;

  readonly categories: () => Effect.Effect<readonly PrivacyCategory[], OpenVikingError>;
  readonly tree: () => Effect.Effect<unknown, OpenVikingError>;
  readonly scan: (body: EndpointBody<"scanPrivacyConfigsApiV1PrivacyConfigsScanPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly resolve: (
    body: EndpointBody<"resolvePrivacyReferenceApiV1PrivacyConfigsResolvePost">,
  ) => Effect.Effect<unknown, OpenVikingError>;
}

export interface AclDomain {
  readonly get: (query: EndpointQuery<"getAclApiV1AclGet">) => Effect.Effect<AclState, OpenVikingError>;
  readonly set: (body: EndpointBody<"setAclApiV1AclPut">) => Effect.Effect<unknown, OpenVikingError>;
  readonly grant: (body: EndpointBody<"grantAclApiV1AclGrantPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly revoke: (body: EndpointBody<"revokeAclApiV1AclRevokePost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly remove: (query: EndpointQuery<"deleteAclApiV1AclDelete">) => Effect.Effect<unknown, OpenVikingError>;
}

export interface CompileDomain {
  readonly operationOutputs: (operationId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly submission: (key: string) => Effect.Effect<unknown, OpenVikingError>;

  readonly create: (body: EndpointBody<"createCompileApiV1CompilePost">) => Effect.Effect<CompileResult, OpenVikingError>;
  readonly capabilities: () => Effect.Effect<unknown, OpenVikingError>;
  readonly config: () => Effect.Effect<unknown, OpenVikingError>;
  readonly configs: () => Effect.Effect<unknown, OpenVikingError>;
  readonly updateConfig: (
    body: EndpointBody<"updateCompileConfigApiV1CompileConfigUpdatePost">,
  ) => Effect.Effect<unknown, OpenVikingError>;
  readonly embeddings: (
    body: EndpointBody<"compileEmbeddingsApiV1CompileEmbeddingsPost">,
  ) => Effect.Effect<unknown, OpenVikingError>;
  readonly operations: () => Effect.Effect<unknown, OpenVikingError>;
  readonly operation: (operationId: string) => Effect.Effect<unknown, OpenVikingError>;
}

export interface BotDomain {
  readonly compile: () => Effect.Effect<unknown, OpenVikingError>;
  readonly compileTask: (taskId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly cancelCompile: (taskId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly interrupt: (sessionId: string) => Effect.Effect<unknown, OpenVikingError>;
  readonly steer: (sessionId: string, body: EndpointBody<"steerSessionBotV1SessionsSessionIdSteerPost">) => Effect.Effect<unknown, OpenVikingError>;

  readonly chat: (body: EndpointBody<"chatBotV1ChatPost">) => Effect.Effect<BotChatResult, OpenVikingError>;
  readonly feedback: (body: EndpointBody<"feedbackBotV1FeedbackPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly health: () => Effect.Effect<unknown, OpenVikingError>;
  readonly stream: (body: EndpointBody<"chatStreamBotV1ChatStreamPost">) => Stream.Stream<ServerSentEvent, OpenVikingError>;
}

export interface ResourcesDomain {
  readonly add: (body: EndpointBody<"addResourceApiV1ResourcesPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly tempUpload: (body: EndpointBody<"tempUploadApiV1ResourcesTempUploadPost">) => Effect.Effect<unknown, OpenVikingError>;
}

export interface UserSettingsDomain {
  readonly addLocations: () => Effect.Effect<unknown, OpenVikingError>;
  readonly patchAddLocations: (body: EndpointBody<"patchAddLocationsApiV1UserSettingsAddLocationsPatch">) => Effect.Effect<unknown, OpenVikingError>;
  readonly removeAddLocations: () => Effect.Effect<unknown, OpenVikingError>;
}

export interface WebdavDomain {
  readonly get: (query: EndpointQuery<"getOrHeadWebdavResourcesGet">) => Effect.Effect<string | Uint8Array, OpenVikingError>;
  readonly head: (query?: EndpointQuery<"getOrHeadWebdavResourcesHead">) => Effect.Effect<void, OpenVikingError>;
  readonly options: (query?: EndpointQuery<"optionsWebdavResourcesOptions">) => Effect.Effect<void, OpenVikingError>;
  readonly propfind: (query: EndpointQuery<"propfindWebdavResourcesPropfind">, headers?: Readonly<Record<string, string>>) => Effect.Effect<string, OpenVikingError>;
  readonly put: (query: EndpointQuery<"putWebdavResourcesPut">, body: string | Uint8Array, headers?: Readonly<Record<string, string>>) => Effect.Effect<void, OpenVikingError>;
  readonly remove: (query: EndpointQuery<"deleteWebdavResourcesDelete">) => Effect.Effect<void, OpenVikingError>;
  readonly mkcol: (query: EndpointQuery<"mkcolWebdavResourcesMkcol">) => Effect.Effect<void, OpenVikingError>;
  readonly move: (query: EndpointQuery<"moveWebdavResourcesMove">, destination: string, overwrite?: boolean) => Effect.Effect<void, OpenVikingError>;
  readonly getPath: (resourcePath: string) => Effect.Effect<string | Uint8Array, OpenVikingError>;
  readonly headPath: (resourcePath: string) => Effect.Effect<void, OpenVikingError>;
  readonly optionsPath: (resourcePath: string) => Effect.Effect<void, OpenVikingError>;
  readonly propfindPath: (resourcePath: string, headers?: Readonly<Record<string, string>>) => Effect.Effect<string, OpenVikingError>;
  readonly putPath: (resourcePath: string, body: string | Uint8Array, headers?: Readonly<Record<string, string>>) => Effect.Effect<void, OpenVikingError>;
  readonly removePath: (resourcePath: string) => Effect.Effect<void, OpenVikingError>;
  readonly mkcolPath: (resourcePath: string) => Effect.Effect<void, OpenVikingError>;
  readonly movePath: (resourcePath: string, destination: string, overwrite?: boolean) => Effect.Effect<void, OpenVikingError>;
}

export interface DebugDomain {
  readonly health: () => Effect.Effect<unknown, OpenVikingError>;
  readonly vectorCount: () => Effect.Effect<unknown, OpenVikingError>;
  readonly vectorScroll: (query: EndpointQuery<"debugVectorScrollApiV1DebugVectorScrollGet">) => Effect.Effect<unknown, OpenVikingError>;
}

export interface AgentEvolutionDomain {
  readonly outcomes: () => Effect.Effect<unknown, OpenVikingError>;
  readonly trajectories: () => Effect.Effect<unknown, OpenVikingError>;
}

export interface AssetsDomain {
  readonly preflight: (body: EndpointBody<"preflightAssetApiV1OpenvikingAssetsPreflightPost">) => Effect.Effect<unknown, OpenVikingError>;
  readonly resolve: (body: EndpointBody<"resolveAssetsApiV1OpenvikingAssetsResolvePost">) => Effect.Effect<unknown, OpenVikingError>;
}

export interface MetricsDomain {
  readonly scrape: () => Effect.Effect<string, OpenVikingError>;
}

export interface DomainsShape {
  readonly search: SearchDomain;
  readonly content: ContentDomain;
  readonly fs: FsDomain;
  readonly sessions: SessionsDomain;
  readonly skills: SkillsDomain;
  readonly mcps: McpsDomain;
  readonly system: SystemDomain;
  readonly tasks: TasksDomain;
  readonly stats: StatsDomain;
  readonly watches: WatchesDomain;
  readonly admin: AdminDomain;
  readonly pack: PackDomain;
  readonly snapshot: SnapshotDomain;
  readonly console: ConsoleDomain;
  readonly observer: ObserverDomain;
  readonly rules: RulesDomain;
  readonly privacy: PrivacyDomain;
  readonly acl: AclDomain;
  readonly compile: CompileDomain;
  readonly bot: BotDomain;
  readonly resources: ResourcesDomain;
  readonly userSettings: UserSettingsDomain;
  readonly webdav: WebdavDomain;
  readonly debug: DebugDomain;
  readonly agentEvolution: AgentEvolutionDomain;
  readonly assets: AssetsDomain;
  readonly metrics: MetricsDomain;
}

export class Domains extends Context.Service<Domains, DomainsShape>()("Domains") {}

export const makeDomains = (ov: OpenVikingShape): DomainsShape => {
  const call: Call = (key, args) => ov.call(key, args as never) as Effect.Effect<unknown, OpenVikingError>;
  const callPath: CallPath = (key, path, args) =>
    ov.call(key, { path, ...(args ?? {}) } as never) as Effect.Effect<unknown, OpenVikingError>;

  return {
    search: {
      find: (body) => call("findApiV1SearchFindPost", { body }) as Effect.Effect<SearchResult, OpenVikingError>,
      search: (body) => call("searchApiV1SearchSearchPost", { body }) as Effect.Effect<SearchResult, OpenVikingError>,
      recall: (body) => call("recallApiV1SearchRecallPost", { body }),
      grep: (body) => call("grepApiV1SearchGrepPost", { body }),
      glob: (body) => call("globApiV1SearchGlobPost", { body }),
    },
    content: {
      read: (query) => call("readApiV1ContentReadGet", { query }) as Effect.Effect<string, OpenVikingError>,
      abstract: (query) => call("abstractApiV1ContentAbstractGet", { query }) as Effect.Effect<string, OpenVikingError>,
      overview: (query) => call("overviewApiV1ContentOverviewGet", { query }) as Effect.Effect<string, OpenVikingError>,
      write: (body) => call("writeApiV1ContentWritePost", { body }),
      batchWrite: (body) => call("batchWriteApiV1ContentBatchWritePost", { body }),
      reindex: (body) => call("reindexApiV1ContentReindexPost", { body }),
      setTags: (body) => call("setTagsApiV1ContentSetTagsPost", { body }),
      batchSetTags: (body) => call("batchSetTagsApiV1ContentBatchSetTagsPost", { body }),
      download: (query) => call("downloadApiV1ContentDownloadGet", { query }),
      ensureDirectoryIndex: (body) => call("ensureDirectoryIndexApiV1ContentEnsureDirectoryIndexPost", { body }),
    },
    fs: {
      ls: (query) => call("lsApiV1FsLsGet", { query }) as Effect.Effect<readonly FsEntry[], OpenVikingError>,
      tree: (query) => call("treeApiV1FsTreeGet", { query }) as Effect.Effect<readonly FsEntry[], OpenVikingError>,
      stat: (query) => call("statApiV1FsStatGet", { query }) as Effect.Effect<FsStat, OpenVikingError>,
      attrs: (query) => call("attrsApiV1FsAttrsGet", { query }),
      mkdir: (body) => call("mkdirApiV1FsMkdirPost", { body }),
      rm: (query) => call("rmApiV1FsDelete", { query }),
      cp: (body) => call("cpApiV1FsCpPost", { body }),
      mv: (body) => call("mvApiV1FsMvPost", { body }),
      setTags: (body) => call("attrsSetTagsApiV1FsAttrsSetTagsPost", { body }),
    },
    sessions: {
      list: () => call("listSessionsApiV1SessionsGet") as Effect.Effect<readonly SessionSummary[], OpenVikingError>,
      create: (body) => call("createSessionApiV1SessionsPost", { body }),
      get: (sessionId) => callPath("getSessionApiV1SessionsSessionIdGet", { session_id: sessionId }),
      remove: (sessionId) => callPath("deleteSessionApiV1SessionsSessionIdDelete", { session_id: sessionId }),
      addMessage: (sessionId, body) =>
        callPath("addMessageApiV1SessionsSessionIdMessagesPost", { session_id: sessionId }, { body }),
      batchAddMessages: (sessionId, body) =>
        callPath("batchAddMessagesApiV1SessionsSessionIdMessagesBatchPost", { session_id: sessionId }, { body }),
      commit: (sessionId, body) =>
        callPath("commitSessionApiV1SessionsSessionIdCommitPost", { session_id: sessionId }, { body }),
      context: (sessionId) => callPath("getSessionContextApiV1SessionsSessionIdContextGet", { session_id: sessionId }),
      updateConfig: (sessionId, body) =>
        callPath("updateSessionConfigApiV1SessionsSessionIdConfigPatch", { session_id: sessionId }, { body }),
      archive: (sessionId, archiveId) =>
        callPath("getSessionArchiveApiV1SessionsSessionIdArchivesArchiveIdGet", { session_id: sessionId, archive_id: archiveId }),
      retryArchive: (sessionId, archiveId, body) =>
        callPath("retrySessionArchiveApiV1SessionsSessionIdArchivesArchiveIdRetryPost", { session_id: sessionId, archive_id: archiveId }, { body }),
      toolResults: (sessionId) =>
        callPath("listToolResultsApiV1SessionsSessionIdToolResultsGet", { session_id: sessionId }),
      toolResult: (sessionId, toolResultId) =>
        callPath("readToolResultApiV1SessionsSessionIdToolResultsToolResultIdGet", { session_id: sessionId, tool_result_id: toolResultId }),
      searchToolResult: (sessionId, toolResultId, query) =>
        callPath("searchToolResultApiV1SessionsSessionIdToolResultsToolResultIdSearchGet", { session_id: sessionId, tool_result_id: toolResultId }, { query }),
    },
    skills: {
      list: () => call("listSkillsApiV1SkillsGet") as Effect.Effect<SkillList, OpenVikingError>,
      find: (body) => call("findSkillsApiV1SkillsFindPost", { body }),
      get: (name) => callPath("getSkillApiV1SkillsSkillNameGet", { skill_name: name }) as Effect.Effect<SkillSummary, OpenVikingError>,
      add: (body) => call("addSkillApiV1SkillsPost", { body }),
      update: (name, body) => callPath("updateSkillApiV1SkillsSkillNamePut", { skill_name: name }, { body }),
      remove: (name) => callPath("deleteSkillApiV1SkillsSkillNameDelete", { skill_name: name }),
      validate: (body) => call("validateSkillApiV1SkillsValidatePost", { body }),
    },
    mcps: {
      list: () => call("listMcpsApiV1McpsGet") as Effect.Effect<McpList, OpenVikingError>,
      add: (body) => call("addMcpApiV1McpsPost", { body }),
      find: (body) => call("findMcpToolsApiV1McpsFindPost", { body }),
      invoke: (body) => call("invokeMcpToolApiV1McpsInvokePost", { body }),
      mount: (body) => call("mountMcpToolApiV1McpsMountPost", { body }),
      unmount: (query) => call("unmountMcpToolApiV1McpsMountDelete", { query }),
      mounts: () => call("listMcpMountsApiV1McpsMountsGet"),
      show: (name) => callPath("showMcpApiV1McpsNameGet", { name }),
      remove: (name) => callPath("removeMcpApiV1McpsNameDelete", { name }),
      sync: (name) => callPath("syncMcpApiV1McpsNameSyncPost", { name }),
      validate: (body) => call("validateMcpApiV1McpsValidatePost", { body }),
      batchMount: (body) => call("batchMountMcpToolsApiV1McpsBatchMountPost", { body }),
      batchUnmount: (body) => call("batchUnmountMcpToolsApiV1McpsBatchUnmountPost", { body }),
      status: () => call("mcpStatusApiV1McpsStatusGet"),
    },
    system: {
      health: () => call("healthCheckHealthGet") as Effect.Effect<HealthResponse, OpenVikingError>,
      ready: () => call("readinessCheckReadyGet"),
      status: () => call("systemStatusApiV1SystemStatusGet") as Effect.Effect<SystemStatusResponse, OpenVikingError>,
      wait: (body) => call("waitProcessedApiV1SystemWaitPost", { body }),
      consistency: (body) => call("checkConsistencyApiV1SystemConsistencyPost", { body }),
    },
    tasks: {
      list: (query) => call("listTasksApiV1TasksGet", { query }) as Effect.Effect<readonly TaskInfo[], OpenVikingError>,
      get: (taskId) => callPath("getTaskApiV1TasksTaskIdGet", { task_id: taskId }) as Effect.Effect<TaskInfo, OpenVikingError>,
      cancel: (taskId) => callPath("cancelTaskApiV1TasksTaskIdCancelPost", { task_id: taskId }),
    },
    stats: {
      memories: () => call("getMemoryStatsApiV1StatsMemoriesGet") as Effect.Effect<MemoryStats, OpenVikingError>,
      session: (sessionId) => callPath("getSessionStatsApiV1StatsSessionsSessionIdGet", { session_id: sessionId }),
    },
    watches: {
      list: (query) => call("listOrGetWatchApiV1WatchesGet", { query }) as Effect.Effect<readonly WatchInfo[], OpenVikingError>,
      get: (taskId) => callPath("getWatchApiV1WatchesTaskIdGet", { task_id: taskId }) as Effect.Effect<WatchInfo, OpenVikingError>,
      patch: (body) => call("patchWatchByUriApiV1WatchesPatch", { body }),
      remove: (query) => call("deleteWatchByUriApiV1WatchesDelete", { query }),
      trigger: (body) => call("triggerWatchByUriApiV1WatchesTriggerPost", { body }),
      patchById: (taskId, body) => callPath("patchWatchByIdApiV1WatchesTaskIdPatch", { task_id: taskId }, { body }),
      removeById: (taskId) => callPath("deleteWatchByIdApiV1WatchesTaskIdDelete", { task_id: taskId }),
      triggerById: (taskId) => callPath("triggerWatchByIdApiV1WatchesTaskIdTriggerPost", { task_id: taskId }),
    },
    admin: {
      accounts: () => call("listAccountsApiV1AdminAccountsGet") as Effect.Effect<readonly AccountInfo[], OpenVikingError>,
      account: (accountId) => callPath("getAccountApiV1AdminAccountsAccountIdGet", { account_id: accountId }) as Effect.Effect<AccountInfo, OpenVikingError>,
      createAccount: (body) => call("createAccountApiV1AdminAccountsPost", { body }),
      deleteAccount: (accountId) => callPath("deleteAccountApiV1AdminAccountsAccountIdDelete", { account_id: accountId }),
      users: (accountId) => callPath("listUsersApiV1AdminAccountsAccountIdUsersGet", { account_id: accountId }),
      registerUser: (accountId, body) =>
        callPath("registerUserApiV1AdminAccountsAccountIdUsersPost", { account_id: accountId }, { body }),
      groups: (accountId) => callPath("listGroupsApiV1AdminAccountsAccountIdGroupsGet", { account_id: accountId }),
      configuration: () => call("getClusterConfigurationApiV1AdminConfigurationGet"),
      patchConfiguration: (body) => call("patchClusterConfigurationApiV1AdminConfigurationPatch", { body }),
      restart: (body) => call("restartServerApiV1AdminRestartPost", { body }),
      migrate: (body) => call("migrateDataApiV1AdminMigratePost", { body }),
      accountConfiguration: (accountId) =>
        callPath("getAccountConfigurationApiV1AdminAccountsAccountIdConfigurationGet", { account_id: accountId }),
      patchAccountConfiguration: (accountId, body) =>
        callPath("patchAccountConfigurationApiV1AdminAccountsAccountIdConfigurationPatch", { account_id: accountId }, { body }),
      createGroup: (accountId, body) =>
        callPath("createGroupApiV1AdminAccountsAccountIdGroupsPost", { account_id: accountId }, { body }),
      deleteGroup: (accountId, groupId) =>
        callPath("deleteGroupApiV1AdminAccountsAccountIdGroupsGroupIdDelete", { account_id: accountId, group_id: groupId }),
      groupMembers: (accountId, groupId) =>
        callPath("listGroupMembersApiV1AdminAccountsAccountIdGroupsGroupIdMembersGet", { account_id: accountId, group_id: groupId }),
      addGroupMembers: (accountId, groupId, body) =>
        callPath("addGroupMembersApiV1AdminAccountsAccountIdGroupsGroupIdMembersPut", { account_id: accountId, group_id: groupId }, { body }),
      removeGroupMembers: (accountId, groupId) =>
        callPath("removeGroupMembersApiV1AdminAccountsAccountIdGroupsGroupIdMembersDelete", { account_id: accountId, group_id: groupId }),
      memoryTemplates: (accountId) =>
        callPath("listMemoryTemplatesApiV1AdminAccountsAccountIdMemoryTemplatesGet", { account_id: accountId }),
      memoryTemplate: (accountId, memoryType) =>
        callPath("getMemoryTemplateApiV1AdminAccountsAccountIdMemoryTemplatesMemoryTypeGet", { account_id: accountId, memory_type: memoryType }),
      putMemoryTemplate: (accountId, memoryType, body) =>
        callPath("putMemoryTemplateApiV1AdminAccountsAccountIdMemoryTemplatesMemoryTypePut", { account_id: accountId, memory_type: memoryType }, { body }),
      resetMemoryTemplate: (accountId, memoryType) =>
        callPath("resetMemoryTemplateApiV1AdminAccountsAccountIdMemoryTemplatesMemoryTypeDelete", { account_id: accountId, memory_type: memoryType }),
      accountSettings: (accountId) =>
        callPath("getAccountSettingsApiV1AdminAccountsAccountIdSettingsGet", { account_id: accountId }),
      patchAccountSettings: (accountId, body) =>
        callPath("patchAccountSettingsApiV1AdminAccountsAccountIdSettingsPatch", { account_id: accountId }, { body }),
      user: (accountId, userId) =>
        callPath("getUserApiV1AdminAccountsAccountIdUsersUserIdGet", { account_id: accountId, user_id: userId }),
      removeUser: (accountId, userId) =>
        callPath("removeUserApiV1AdminAccountsAccountIdUsersUserIdDelete", { account_id: accountId, user_id: userId }),
      regenerateKey: (accountId, userId) =>
        callPath("regenerateKeyApiV1AdminAccountsAccountIdUsersUserIdKeyPost", { account_id: accountId, user_id: userId }),
      setUserRole: (accountId, userId, body) =>
        callPath("setUserRoleApiV1AdminAccountsAccountIdUsersUserIdRolePut", { account_id: accountId, user_id: userId }, { body }),
      userSettings: (accountId, userId) =>
        callPath("getUserSettingsApiV1AdminAccountsAccountIdUsersUserIdSettingsGet", { account_id: accountId, user_id: userId }),
      patchUserSettings: (accountId, userId, body) =>
        callPath("patchUserSettingsApiV1AdminAccountsAccountIdUsersUserIdSettingsPatch", { account_id: accountId, user_id: userId }, { body }),
      agentEvolution: () => call("getAgentEvolutionStatusApiV1AdminAgentEvolutionGet"),
      setAgentEvolution: (body) => call("setAgentEvolutionStatusApiV1AdminAgentEvolutionPut", { body }),
    },
    pack: {
      export: (body) => call("exportOvpackApiV1PackExportPost", { body }) as Effect.Effect<PackResult, OpenVikingError>,
      import: (body) => call("importOvpackApiV1PackImportPost", { body }) as Effect.Effect<PackResult, OpenVikingError>,
      backup: (body) => call("backupOvpackApiV1PackBackupPost", { body }) as Effect.Effect<PackResult, OpenVikingError>,
      restore: (body) => call("restoreOvpackApiV1PackRestorePost", { body }) as Effect.Effect<PackResult, OpenVikingError>,
    },
    snapshot: {
      diff: (query) => call("diffApiV1SnapshotDiffGet", { query }) as Effect.Effect<SnapshotDiff, OpenVikingError>,
      diffPaths: (query) => call("diffPathsApiV1SnapshotDiffPathsGet", { query }),
      commit: (body) => call("commitApiV1SnapshotCommitPost", { body }),
      log: (query) => call("logApiV1SnapshotLogGet", { query }),
      show: (query) => call("showApiV1SnapshotShowGet", { query }),
      versions: (query) => call("versionsApiV1SnapshotVersionsGet", { query }),
      restore: (body) => call("restoreApiV1SnapshotRestorePost", { body }),
      ignore: () => call("getIgnoreApiV1SnapshotIgnoreGet"),
      setIgnore: (body) => call("setIgnoreApiV1SnapshotIgnorePut", { body }),
      removeIgnore: () => call("deleteIgnoreApiV1SnapshotIgnoreDelete"),
    },
    console: {
      summary: () => call("dashboardSummaryApiV1ConsoleDashboardSummaryGet") as Effect.Effect<ConsoleSummary, OpenVikingError>,
      audit: (query) => call("auditLogsApiV1ConsoleAuditGet", { query }),
      tokens: (query) => call("tokenSeriesApiV1ConsoleTokensGet", { query }),
      commits: (query) => call("contextCommitsApiV1ConsoleContextCommitsGet", { query }),
    },
    observer: {
      system: () => call("observerSystemApiV1ObserverSystemGet") as Effect.Effect<ObserverSnapshot, OpenVikingError>,
      queue: () => call("observerQueueApiV1ObserverQueueGet") as Effect.Effect<ObserverSnapshot, OpenVikingError>,
      models: () => call("observerModelsApiV1ObserverModelsGet") as Effect.Effect<ObserverSnapshot, OpenVikingError>,
      retrieval: () => call("observerRetrievalApiV1ObserverRetrievalGet") as Effect.Effect<ObserverSnapshot, OpenVikingError>,
      filesystem: () => call("observerFilesystemApiV1ObserverFilesystemGet") as Effect.Effect<ObserverSnapshot, OpenVikingError>,
      lock: () => call("observerLockApiV1ObserverLockGet") as Effect.Effect<ObserverSnapshot, OpenVikingError>,
      vikingdb: () => call("observerVikingdbApiV1ObserverVikingdbGet") as Effect.Effect<ObserverSnapshot, OpenVikingError>,
    },
    rules: {
      search: (body) => call("searchRulesApiV1RulesSearchPost", { body }) as Effect.Effect<RuleSearchResult, OpenVikingError>,
      compile: (body) => call("compileRulesApiV1RulesCompilePost", { body }),
    },
    privacy: {
      categories: () => call("listPrivacyCategoriesApiV1PrivacyConfigsGet") as Effect.Effect<readonly PrivacyCategory[], OpenVikingError>,
      tree: () => call("listPrivacyTreeApiV1PrivacyConfigsTreeGet"),
      scan: (body) => call("scanPrivacyConfigsApiV1PrivacyConfigsScanPost", { body }),
      resolve: (body) => call("resolvePrivacyReferenceApiV1PrivacyConfigsResolvePost", { body }),
      targets: (category) => callPath("listPrivacyTargetsApiV1PrivacyConfigsCategoryGet", { category }),
      current: (category, targetKey) =>
        callPath("getPrivacyCurrentApiV1PrivacyConfigsCategoryTargetKeyGet", { category, target_key: targetKey }),
      upsert: (category, targetKey, body) =>
        callPath("upsertPrivacyConfigApiV1PrivacyConfigsCategoryTargetKeyPost", { category, target_key: targetKey }, { body }),
      patch: (category, targetKey, body) =>
        callPath("patchPrivacyConfigApiV1PrivacyConfigsCategoryTargetKeyPatch", { category, target_key: targetKey }, { body }),
      remove: (category, targetKey) =>
        callPath("deletePrivacyConfigApiV1PrivacyConfigsCategoryTargetKeyDelete", { category, target_key: targetKey }),
      activate: (category, targetKey, body) =>
        callPath("activatePrivacyVersionApiV1PrivacyConfigsCategoryTargetKeyActivatePost", { category, target_key: targetKey }, { body }),
      fields: (category, targetKey) =>
        callPath("listPrivacyFieldsApiV1PrivacyConfigsCategoryTargetKeyFieldsGet", { category, target_key: targetKey }),
      field: (category, targetKey, query) =>
        callPath("getPrivacyFieldApiV1PrivacyConfigsCategoryTargetKeyFieldGet", { category, target_key: targetKey }, { query }),
      removeField: (category, targetKey, query) =>
        callPath("deletePrivacyFieldApiV1PrivacyConfigsCategoryTargetKeyFieldDelete", { category, target_key: targetKey }, { query }),
      versions: (category, targetKey) =>
        callPath("listPrivacyVersionsApiV1PrivacyConfigsCategoryTargetKeyVersionsGet", { category, target_key: targetKey }),
      version: (category, targetKey, version) =>
        callPath("getPrivacyVersionApiV1PrivacyConfigsCategoryTargetKeyVersionsVersionGet", { category, target_key: targetKey, version }),
      removeSelector: (body) => call("deletePrivacySelectorApiV1PrivacyConfigsSelectorDelete", { body }),
    },
    acl: {
      get: (query) => call("getAclApiV1AclGet", { query }) as Effect.Effect<AclState, OpenVikingError>,
      set: (body) => call("setAclApiV1AclPut", { body }),
      grant: (body) => call("grantAclApiV1AclGrantPost", { body }),
      revoke: (body) => call("revokeAclApiV1AclRevokePost", { body }),
      remove: (query) => call("deleteAclApiV1AclDelete", { query }),
    },
    compile: {
      create: (body) => call("createCompileApiV1CompilePost", { body }) as Effect.Effect<CompileResult, OpenVikingError>,
      capabilities: () => call("compileCapabilitiesApiV1CompileCapabilitiesGet"),
      config: () => call("getCompileConfigApiV1CompileConfigGet"),
      configs: () => call("listCompileConfigsApiV1CompileConfigsGet"),
      updateConfig: (body) => call("updateCompileConfigApiV1CompileConfigUpdatePost", { body }),
      embeddings: (body) => call("compileEmbeddingsApiV1CompileEmbeddingsPost", { body }),
      operations: () => call("listCompileOperationsApiV1CompileOperationsGet"),
      operation: (operationId) =>
        callPath("getCompileOperationApiV1CompileOperationsOperationIdGet", { operation_id: operationId }),
      operationOutputs: (operationId) =>
        callPath("operationOutputsApiV1CompileOperationsOperationIdOutputsGet", { operation_id: operationId }),
      submission: (key) => callPath("getSubmissionApiV1CompileSubmissionsKeyGet", { key }),
    },
    bot: {
      chat: (body) => call("chatBotV1ChatPost", { body }) as Effect.Effect<BotChatResult, OpenVikingError>,
      feedback: (body) => call("feedbackBotV1FeedbackPost", { body }),
      health: () => call("healthCheckBotV1HealthGet"),
      stream: (body) => ov.streamCall("chatStreamBotV1ChatStreamPost", { body }),
      compile: () => call("createCompileBotV1CompilePost"),
      compileTask: (taskId) => callPath("getCompileBotV1CompileTaskIdGet", { task_id: taskId }),
      cancelCompile: (taskId) => callPath("cancelCompileBotV1CompileTaskIdCancelPost", { task_id: taskId }),
      interrupt: (sessionId) =>
        callPath("interruptSessionBotV1SessionsSessionIdInterruptPost", { session_id: sessionId }),
      steer: (sessionId, body) =>
        callPath("steerSessionBotV1SessionsSessionIdSteerPost", { session_id: sessionId }, { body }),
    },
    resources: {
      add: (body) => call("addResourceApiV1ResourcesPost", { body }),
      tempUpload: (body) => call("tempUploadApiV1ResourcesTempUploadPost", { body }),
    },
    userSettings: {
      addLocations: () => call("getAddLocationsApiV1UserSettingsAddLocationsGet"),
      patchAddLocations: (body) => call("patchAddLocationsApiV1UserSettingsAddLocationsPatch", { body }),
      removeAddLocations: () => call("deleteAddLocationsApiV1UserSettingsAddLocationsDelete"),
    },
    webdav: {
      get: (query) => call("getOrHeadWebdavResourcesGet", { query }) as Effect.Effect<string | Uint8Array, OpenVikingError>,
      head: (query = {}) => call("getOrHeadWebdavResourcesHead", { query }).pipe(Effect.asVoid),
      options: (query = {}) => call("optionsWebdavResourcesOptions", { query }).pipe(Effect.asVoid),
      propfind: (query, headers) => call("propfindWebdavResourcesPropfind", { query, headers }) as Effect.Effect<string, OpenVikingError>,
      put: (query, body, headers) => call("putWebdavResourcesPut", { query, body, headers }).pipe(Effect.asVoid),
      remove: (query) => call("deleteWebdavResourcesDelete", { query }).pipe(Effect.asVoid),
      mkcol: (query) => call("mkcolWebdavResourcesMkcol", { query }).pipe(Effect.asVoid),
      move: (query, destination, overwrite = true) => call("moveWebdavResourcesMove", { query, headers: { Destination: destination, Overwrite: overwrite ? "T" : "F" } }).pipe(Effect.asVoid),
      getPath: (resourcePath) =>
        callPath("getOrHeadWebdavResourcesResourcePathGet", { resource_path: resourcePath }) as Effect.Effect<string | Uint8Array, OpenVikingError>,
      headPath: (resourcePath) => callPath("getOrHeadWebdavResourcesResourcePathHead", { resource_path: resourcePath }).pipe(Effect.asVoid),
      optionsPath: (resourcePath) => callPath("optionsWebdavResourcesResourcePathOptions", { resource_path: resourcePath }).pipe(Effect.asVoid),
      propfindPath: (resourcePath, headers) => callPath("propfindWebdavResourcesResourcePathPropfind", { resource_path: resourcePath }, { headers }) as Effect.Effect<string, OpenVikingError>,
      putPath: (resourcePath, body, headers) =>
        callPath("putWebdavResourcesResourcePathPut", { resource_path: resourcePath }, { body, headers }).pipe(Effect.asVoid),
      removePath: (resourcePath) =>
        callPath("deleteWebdavResourcesResourcePathDelete", { resource_path: resourcePath }).pipe(Effect.asVoid),
      mkcolPath: (resourcePath) => callPath("mkcolWebdavResourcesResourcePathMkcol", { resource_path: resourcePath }).pipe(Effect.asVoid),
      movePath: (resourcePath, destination, overwrite = true) => callPath("moveWebdavResourcesResourcePathMove", { resource_path: resourcePath }, { headers: { Destination: destination, Overwrite: overwrite ? "T" : "F" } }).pipe(Effect.asVoid),
    },
    debug: {
      health: () => call("debugHealthApiV1DebugHealthGet"),
      vectorCount: () => call("debugVectorCountApiV1DebugVectorCountGet"),
      vectorScroll: (query) => call("debugVectorScrollApiV1DebugVectorScrollGet", { query }),
    },
    agentEvolution: {
      outcomes: () => call("getExperienceOutcomeDistributionApiV1AgentEvolutionExperiencesOutcomesGet"),
      trajectories: () => call("listExperienceTrajectoriesApiV1AgentEvolutionExperiencesTrajectoriesGet"),
    },
    assets: {
      preflight: (body) => call("preflightAssetApiV1OpenvikingAssetsPreflightPost", { body }),
      resolve: (body) => call("resolveAssetsApiV1OpenvikingAssetsResolvePost", { body }),
    },
    metrics: {
      scrape: () => call("metricsMetricsGet") as Effect.Effect<string, OpenVikingError>,
    },
  };
};

export const layer = Layer.effect(
  Domains,
  Effect.gen(function* () {
    const ov = yield* OpenViking;
    return makeDomains(ov);
  }),
);
