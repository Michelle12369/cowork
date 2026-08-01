/** Default title for a draft session, shown before the first question renames it.
 *  MUST match backend SessionGuard.DEFAULT_SESSION_TITLE. */
export const DRAFT_SESSION_TITLE = 'New analysis' as const;

/** Interrupted-response texts persisted when the SSE client disconnects mid-stream; both
 *  current and legacy (parenthesized) forms so older history rows are recognised.
 *  NEVER compare against raw string literals — always reference this array. */
export const INTERRUPTED_TEXTS: readonly string[] = [
  '回應已中斷，請重新送出以繼續',
  '（回應已中斷，請重新送出以繼續）',
] as const;

/** Prefixes for repair-outcome records persisted by ArtifactRepairService; MessageBubble uses
 *  these to render them as gray hints instead of routing through ReactMarkdown.
 *  NEVER compare against raw string literals — always reference this array. */
export const REPAIR_RECORD_PREFIXES: readonly string[] = [
  '已修復儀表板執行錯誤',
  '儀表板執行錯誤自動修復未成功',
] as const;

/** Prefix the backend uses for persisted artifact titles ("Version N"); ArtifactPanel derives
 *  the collapsed "v{N}" format from it. Must match ArtifactService.ARTIFACT_TITLE_PREFIX. */
export const VERSION_TITLE_PREFIX = 'Version ' as const;
