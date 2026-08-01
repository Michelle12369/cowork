import React from 'react';
import { Button } from 'antd';
import { MenuFoldOutlined, MessageOutlined, PlusOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import type { SessionSummary } from '@/types';

dayjs.extend(relativeTime);

export interface ChatHistorySidebarProps {
  sessions: SessionSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onCollapse: () => void;
}

const ChatHistorySidebar: React.FC<ChatHistorySidebarProps> = ({
  sessions,
  activeId,
  onSelect,
  onNew,
  onCollapse,
}) => {
  return (
    <div className="flex h-full w-[238px] flex-none flex-col border-r border-gray-200 bg-[#fafafa]">
      <div className="flex items-center justify-between px-3 pt-3.5 pb-2">
        <span className="pl-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
          Chats
        </span>
        <Button
          type="text"
          size="small"
          icon={<MenuFoldOutlined />}
          onClick={onCollapse}
          aria-label="Collapse chats"
        />
      </div>
      <div className="px-3 pb-2.5">
        <Button type="primary" block icon={<PlusOutlined />} onClick={onNew}>
          New chat
        </Button>
      </div>
      <div className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-2 pb-2.5">
        {sessions.map((session) => (
          <button
            key={session.id}
            type="button"
            onClick={() => onSelect(session.id)}
            className={`w-full cursor-pointer rounded-lg px-2 py-1.5 text-left hover:bg-gray-100 ${
              session.id === activeId ? 'bg-blue-50' : ''
            }`}
          >
            <div className="flex items-center gap-2 truncate text-[13px] font-medium text-gray-800">
              <MessageOutlined className="text-gray-400" style={{ fontSize: 13 }} />
              {session.title}
            </div>
            <div className="pl-[21px] text-[11px] text-gray-400">
              {dayjs(session.updatedAt).fromNow()}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
};

export default ChatHistorySidebar;
