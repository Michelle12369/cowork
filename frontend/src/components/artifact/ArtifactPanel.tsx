import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { App, Button, Segmented, Select } from 'antd';
import {
  DashboardOutlined,
  ExportOutlined,
  FilePptOutlined,
  ReloadOutlined,
  SyncOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { VERSION_TITLE_PREFIX } from '@/constants/messages';
import { refreshArtifactData } from '@/api/artifactApi';
import type { ArtifactVersion, BrowserJsError } from '@/types';

/** Shape of each entry in the sorted Select options array. */
interface VersionSelectOption {
  label: string;
  value: string;
  title: string;
  version: number;
}

/** Minimal shape of rc-select's optionRender argument — only the fields we read. */
interface FlatVersionOption {
  data: VersionSelectOption;
}

export interface ArtifactRef {
  artifactId: string;
  title: string;
}

export interface Props {
  artifact: ArtifactRef | null;
  artifacts?: ArtifactVersion[];
  onSelectArtifact?: (artifact: ArtifactRef) => void;
  /** True while the agent is streaming; disables the refresh button to avoid reloading mid-generation. */
  regenerating?: boolean;
  /** Called when the iframe reports browser runtime JS errors; the parent decides what to do. */
  onRuntimeErrors?: (artifactId: string, errors: BrowserJsError[]) => void;
  /** Incrementing this value reloads the iframe by appending ?r={nonce} to the src. */
  reloadNonce?: number;
}

const ArtifactPanel: React.FC<Props> = ({
  artifact,
  artifacts,
  onSelectArtifact,
  regenerating,
  onRuntimeErrors,
  reloadNonce = 0,
}) => {
  const { message } = App.useApp();
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [localRefreshCounter, setLocalRefreshCounter] = useState(0);
  // Transient HTML from a "更新資料" (recipe replay) call — never persisted server-side, so it
  // can only be shown via iframe srcDoc, not by reloading the stored src.
  const [replayedHtml, setReplayedHtml] = useState<string | null>(null);
  const [dataRefreshing, setDataRefreshing] = useState(false);

  const handleRefresh = useCallback((): void => {
    // View-reload of the stored artifact discards any unpersisted replay preview.
    setReplayedHtml(null);
    setLocalRefreshCounter((previous) => previous + 1);
  }, []);

  // Switching artifacts (version select, new stream) must drop a stale replay preview —
  // it belonged to the previously displayed artifact, not the newly selected one.
  useEffect(() => {
    setReplayedHtml(null);
  }, [artifact?.artifactId]);

  const handleRefreshData = useCallback(async (): Promise<void> => {
    if (!artifact) return;
    setDataRefreshing(true);
    try {
      const result = await refreshArtifactData(artifact.artifactId);
      switch (result.status) {
        case 'success':
          setReplayedHtml(result.html);
          break;
        case 'not_refreshable':
          void message.info(result.message);
          break;
        case 'source_error':
        case 'unknown_error':
          void message.error(result.message);
          break;
      }
    } finally {
      setDataRefreshing(false);
    }
  }, [artifact, message]);

  // Listen for browser runtime errors from the iframe and forward them to the parent.
  useEffect(() => {
    const handleMessage = (event: MessageEvent): void => {
      if (
        event.data?.type !== 'erd-artifact-error' ||
        event.source !== iframeRef.current?.contentWindow
      ) {
        return;
      }

      const currentArtifactId = artifact?.artifactId;
      if (!currentArtifactId) return;

      const errors: BrowserJsError[] = event.data.errors ?? [];
      onRuntimeErrors?.(currentArtifactId, errors);
    };

    window.addEventListener('message', handleMessage);
    return (): void => {
      window.removeEventListener('message', handleMessage);
    };
  }, [artifact?.artifactId, onRuntimeErrors]);

  const handleOpenFullscreen = useCallback((): void => {
    if (artifact) {
      // noopener,noreferrer：導出頁抓不到 opener 參照(防反向 tabnabbing)、不外洩 referrer。
      window.open(`/api/artifacts/${artifact.artifactId}`, '_blank', 'noopener,noreferrer');
    }
  }, [artifact]);

  // Add the active artifact if it's not yet in the persisted list (e.g. live stream just
  // arrived, invalidation not yet complete).
  const allOptions = useMemo<ArtifactVersion[]>(() => {
    const base = artifacts ?? [];
    if (base.length === 0) {
      // First-stream case: no persisted history yet but a live artifact exists.
      if (artifact) {
        return [{ artifactId: artifact.artifactId, title: artifact.title, version: 1 }];
      }
      return [];
    }
    const hasActive = base.some((version) => version.artifactId === artifact?.artifactId);
    if (artifact && !hasActive) {
      return [
        ...base,
        {
          artifactId: artifact.artifactId,
          title: artifact.title,
          version: base.length + 1,
        },
      ];
    }
    return base;
  }, [artifacts, artifact]);

  /** Highest version number in the merged list; used to identify the "Latest" entry. */
  const maxVersion = useMemo<number>(
    () =>
      allOptions.length === 0
        ? 0
        : Math.max(...allOptions.map((versionEntry) => versionEntry.version)),
    [allOptions],
  );

  /** Options sorted newest-first for the Select dropdown. */
  const sortedSelectOptions = useMemo<VersionSelectOption[]>(
    () =>
      [...allOptions]
        .sort((first, second) => second.version - first.version)
        .map((versionEntry) => ({
          label: `v${versionEntry.version}`,
          value: versionEntry.artifactId,
          title: versionEntry.title,
          version: versionEntry.version,
        })),
    [allOptions],
  );

  /** Renders the collapsed trigger label: titles starting with VERSION_TITLE_PREFIX become
   *  "v{N} · Latest" or "v{N}"; other titles (custom/legacy) show as-is. */
  const labelRender = useCallback(
    (props: { label: React.ReactNode; value: string | number }): React.ReactNode => {
      const matched = allOptions.find((versionEntry) => versionEntry.artifactId === props.value);
      if (!matched) return props.label;
      const isLatest = matched.version === maxVersion;
      if (matched.title.startsWith(VERSION_TITLE_PREFIX)) {
        const versionNumber = matched.title.slice(VERSION_TITLE_PREFIX.length);
        return isLatest ? `v${versionNumber} · Latest` : `v${versionNumber}`;
      }
      return matched.title;
    },
    [allOptions, maxVersion],
  );

  /** Renders each dropdown row: artifact title with a grey "Latest" badge on the newest entry. */
  const optionRender = useCallback(
    (oriOption: FlatVersionOption): React.ReactNode => {
      const { version, title } = oriOption.data;
      return (
        <div className="flex items-center justify-between">
          <span>{title}</span>
          {version === maxVersion && <span className="ml-2 text-xs text-gray-400">Latest</span>}
        </div>
      );
    },
    [maxVersion],
  );

  const handleSelectChange = useCallback(
    (val: string): void => {
      const selected = allOptions.find((version) => version.artifactId === val);
      if (selected) {
        onSelectArtifact?.({ artifactId: selected.artifactId, title: selected.title });
      }
    },
    [allOptions, onSelectArtifact],
  );

  // Combined nonce: parent-driven repair reloads (reloadNonce) + user-triggered refreshes (localRefreshCounter).
  // Either being > 0 appends a cache-buster; the iframe key includes both so React remounts on any change.
  const combinedNonce = reloadNonce + localRefreshCounter;
  const iframeSrc = artifact
    ? `/api/artifacts/${artifact.artifactId}${combinedNonce > 0 ? `?r=${combinedNonce}` : ''}`
    : undefined;

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      {/* Header */}
      <div className="flex h-[54px] flex-none items-center gap-3.5 border-b border-gray-200 bg-white px-5">
        {allOptions.length > 0 ? (
          <>
            <div className="flex-shrink-0">
              <Select
                size="small"
                style={{ minWidth: 180 }}
                value={artifact?.artifactId ?? undefined}
                onChange={handleSelectChange}
                options={sortedSelectOptions}
                labelRender={labelRender}
                optionRender={optionRender}
              />
            </div>
          </>
        ) : artifact ? (
          <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-gray-700">
            {artifact.title}
          </span>
        ) : (
          <span className="flex items-center gap-1.5 text-[13px] text-gray-400">
            <ThunderboltOutlined className="text-blue-500" /> Generated by eRD AI
          </span>
        )}
        <Segmented
          options={[
            { label: 'Dashboard', value: 'dashboard', icon: <DashboardOutlined /> },
            { label: 'Slides', value: 'slides', icon: <FilePptOutlined />, disabled: true },
          ]}
          value="dashboard"
        />
        <div className="ml-auto flex items-center gap-2">
          <Button
            icon={<ReloadOutlined />}
            disabled={!artifact || regenerating}
            onClick={handleRefresh}
            title="重新整理儀表板"
            aria-label="重新整理儀表板"
          />
          <Button
            icon={<SyncOutlined />}
            loading={dataRefreshing}
            disabled={!artifact || regenerating}
            onClick={handleRefreshData}
            title="更新資料"
            aria-label="更新資料"
          />
          <Button
            type="primary"
            icon={<ExportOutlined />}
            disabled={!artifact}
            onClick={handleOpenFullscreen}
            title="Open the dashboard full-screen in a new tab"
          />
        </div>
      </div>

      {/* Content */}
      <div className="relative flex-1">
        {artifact ? (
          replayedHtml !== null ? (
            <iframe
              ref={iframeRef}
              key={`${artifact.artifactId}-${combinedNonce}-replay`}
              srcDoc={replayedHtml}
              sandbox="allow-scripts"
              className="absolute inset-0 h-full w-full border-0"
              title={artifact.title}
            />
          ) : (
            <iframe
              ref={iframeRef}
              key={`${artifact.artifactId}-${combinedNonce}`}
              src={iframeSrc}
              sandbox="allow-scripts"
              className="absolute inset-0 h-full w-full border-0"
              title={artifact.title}
            />
          )
        ) : (
          <div className="flex h-full min-h-[360px] flex-col items-center justify-center text-center text-gray-400">
            <div className="mb-3.5 flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-50 text-blue-500">
              <ThunderboltOutlined style={{ fontSize: 28 }} />
            </div>
            <div className="text-[15px] font-semibold text-gray-700">No artifact yet</div>
            <div className="mt-1.5 max-w-[330px] text-[13px] leading-normal">
              Import your data on the left and ask eRD AI to build a dashboard or a deck — e.g.
              &ldquo;Run an SPC analysis on Vt.&rdquo;
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default ArtifactPanel;
