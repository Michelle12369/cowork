# Component Patterns

Modern React component architecture emphasizing type safety, lazy loading, Suspense boundaries, and Ant Design X chatbot components.

---

## React.FC Pattern (PREFERRED)

### Basic Pattern

```typescript
import React from 'react';

interface MyComponentProps {
    /** User ID to display */
    userId: number;
    /** Optional callback when action occurs */
    onAction?: () => void;
}

export const MyComponent: React.FC<MyComponentProps> = ({ userId, onAction }) => {
    return (
        <div className='p-4'>
            User: {userId}
        </div>
    );
};

export default MyComponent;
```

**Key Points:**
- Props interface defined separately with JSDoc comments
- `React.FC<Props>` provides type safety
- Destructure props in parameters
- Default export at bottom

---

## Ant Design X — Chatbot Components

### Bubble (Chat Messages)

```typescript
import { Bubble } from '@ant-design/x';
import { RobotOutlined } from '@ant-design/icons';

interface MessageProps {
    content: string;
    role: 'user' | 'assistant';
    loading?: boolean;
}

export const ChatMessage: React.FC<MessageProps> = ({ content, role, loading }) => {
    return (
        <Bubble
            content={content}
            placement={role === 'user' ? 'end' : 'start'}
            loading={loading}
            avatar={role === 'assistant' ? { icon: <RobotOutlined /> } : undefined}
        />
    );
};
```

### Sender (Message Input)

```typescript
import { Sender } from '@ant-design/x';

interface ChatInputProps {
    onSend: (message: string) => void;
    loading?: boolean;
}

export const ChatInput: React.FC<ChatInputProps> = ({ onSend, loading }) => {
    return (
        <Sender
            onSubmit={onSend}
            loading={loading}
            placeholder='Type a message...'
        />
    );
};
```

### Conversations (Sidebar List)

```typescript
import { Conversations } from '@ant-design/x';
import type { ConversationsProps } from '@ant-design/x';

const items: ConversationsProps['items'] = [
    { key: '1', label: 'New chat', timestamp: Date.now() },
    { key: '2', label: 'Previous chat', timestamp: Date.now() - 86400000 },
];

export const ConversationSidebar: React.FC = () => {
    return (
        <Conversations
            items={items}
            onActiveChange={(key) => console.log('selected', key)}
        />
    );
};
```

### Welcome Screen

```typescript
import { Welcome } from '@ant-design/x';

export const WelcomeScreen: React.FC = () => {
    return (
        <Welcome
            icon='https://example.com/bot-avatar.png'
            title='Hello, I am your AI assistant'
            description='Ask me anything to get started'
        />
    );
};
```

### ThoughtChain (Reasoning Steps)

```typescript
import { ThoughtChain } from '@ant-design/x';

export const ReasoningDisplay: React.FC<{ steps: string[] }> = ({ steps }) => {
    const items = steps.map((step, i) => ({
        title: `Step ${i + 1}`,
        description: step,
        status: 'success' as const,
    }));

    return <ThoughtChain items={items} />;
};
```

---

## Lazy Loading Pattern

### When to Lazy Load

- Heavy components (chat history grids, rich text editors)
- Route-level page components
- Modal/dialog content not shown initially
- Below-the-fold content

### How to Lazy Load

```typescript
import React from 'react';

// For default exports
const ChatHistory = React.lazy(() => import('./ChatHistory'));

// For named exports
const ConversationList = React.lazy(() =>
    import('./ConversationList').then(module => ({
        default: module.ConversationList
    }))
);
```

**Example with SuspenseLoader:**

```typescript
import React from 'react';
import { SuspenseLoader } from '~components/SuspenseLoader';

const HeavyChatGrid = React.lazy(() => import('./grids/HeavyChatGrid'));

export const ChatContainer: React.FC<{ sessionId: string }> = ({ sessionId }) => {
    return (
        <div className='flex flex-col h-full'>
            <SuspenseLoader>
                <HeavyChatGrid sessionId={sessionId} />
            </SuspenseLoader>
        </div>
    );
};
```

---

## Suspense Boundaries

### SuspenseLoader Component

```typescript
import { SuspenseLoader } from '~components/SuspenseLoader';
// Or
import { SuspenseLoader } from '@/components/SuspenseLoader';
```

**Usage:**
```typescript
<SuspenseLoader>
    <LazyLoadedComponent />
</SuspenseLoader>
```

### Where to Place Suspense Boundaries

**Route Level:**
```typescript
// src/routes/index.tsx
<Route path='/chat' element={
    <SuspenseLoader>
        <ChatPage />
    </SuspenseLoader>
} />
```

**Component Level:**
```typescript
function ParentComponent() {
    return (
        <div className='flex flex-col gap-4'>
            <Header />
            <SuspenseLoader>
                <HeavyMessageList />
            </SuspenseLoader>
        </div>
    );
}
```

**Multiple Boundaries — Each Section Loads Independently:**
```typescript
function ChatPage() {
    return (
        <div className='flex h-screen'>
            <SuspenseLoader>
                <ConversationSidebar />
            </SuspenseLoader>

            <SuspenseLoader>
                <ChatWindow />
            </SuspenseLoader>
        </div>
    );
}
```

---

## Component Structure Template

```typescript
import React, { useState, useCallback, useMemo, useEffect } from 'react';
import { Button, App } from 'antd';
import { Bubble, Sender } from '@ant-design/x';
import { useSuspenseQuery } from '@tanstack/react-query';
import { cn } from '@/lib/utils';

import { chatApi } from '../api/chatApi';
import type { Message } from '~types/chat';
import { SuspenseLoader } from '~components/SuspenseLoader';
import { useAuth } from '@/hooks/useAuth';

// 1. PROPS INTERFACE (with JSDoc)
interface ChatWindowProps {
    /** The conversation ID to display */
    conversationId: string;
    /** Optional callback when message is sent */
    onMessageSent?: () => void;
}

// 2. COMPONENT DEFINITION
export const ChatWindow: React.FC<ChatWindowProps> = ({
    conversationId,
    onMessageSent,
}) => {
    // 3. HOOKS (in this order)
    // - Context hooks first
    const { user } = useAuth();
    const { message } = App.useApp();

    // - Data fetching
    const { data: messages } = useSuspenseQuery({
        queryKey: ['messages', conversationId],
        queryFn: () => chatApi.getMessages(conversationId),
    });

    // - Local state
    const [isSending, setIsSending] = useState(false);

    // - Memoized values
    const sortedMessages = useMemo(() => {
        return [...messages].sort((a, b) =>
            new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime()
        );
    }, [messages]);

    // 4. EVENT HANDLERS (with useCallback)
    const handleSend = useCallback(async (content: string) => {
        setIsSending(true);
        try {
            await chatApi.sendMessage(conversationId, content);
            onMessageSent?.();
        } catch {
            message.error('Failed to send message');
        } finally {
            setIsSending(false);
        }
    }, [conversationId, onMessageSent, message]);

    // 5. RENDER
    return (
        <div className='flex flex-col h-full'>
            <div className='flex-1 overflow-y-auto p-4 space-y-4'>
                {sortedMessages.map((msg) => (
                    <Bubble
                        key={msg.id}
                        content={msg.content}
                        placement={msg.role === 'user' ? 'end' : 'start'}
                    />
                ))}
            </div>

            <div className='border-t border-gray-200 p-4'>
                <Sender onSubmit={handleSend} loading={isSending} />
            </div>
        </div>
    );
};

export default ChatWindow;
```

---

## Component Separation

### When to Split Components

**Split when:**
- Component exceeds 300 lines
- Multiple distinct responsibilities
- Reusable sections
- Complex nested JSX

```typescript
// ❌ AVOID - Monolithic
function MassiveChatPage() {
    // 500+ lines: sidebar, messages, input, settings...
}

// ✅ PREFERRED - Modular
function ChatPage() {
    return (
        <div className='flex h-screen'>
            <ConversationSidebar />
            <ChatWindow />
            <InfoPanel />
        </div>
    );
}
```

---

## Export Patterns

### Named Const + Default Export (PREFERRED)

```typescript
export const MyComponent: React.FC<Props> = ({ ... }) => {
    // Component logic
};

export default MyComponent;
```

**Why:**
- Named export for testing/refactoring
- Default export for lazy loading convenience

### Lazy Loading Named Exports

```typescript
const MyComponent = React.lazy(() =>
    import('./MyComponent').then(module => ({
        default: module.MyComponent
    }))
);
```

---

## Component Communication

### Props Down, Events Up

```typescript
// Parent
function Parent() {
    const [selectedId, setSelectedId] = useState<string | null>(null);

    return (
        <ConversationList
            conversations={data}
            onSelect={setSelectedId}
        />
    );
}

// Child
interface ConversationListProps {
    conversations: Conversation[];
    onSelect: (id: string) => void;
}

export const ConversationList: React.FC<ConversationListProps> = ({
    conversations,
    onSelect,
}) => {
    return (
        <ul>
            {conversations.map((c) => (
                <li key={c.id} onClick={() => onSelect(c.id)}>
                    {c.title}
                </li>
            ))}
        </ul>
    );
};
```

---

## Summary

**Modern Component Recipe:**
1. `React.FC<Props>` with TypeScript
2. Use Ant Design X components for chatbot UI (`Bubble`, `Sender`, `Conversations`, etc.)
3. Lazy load if heavy: `React.lazy(() => import())`
4. Wrap in `<SuspenseLoader>` for loading
5. Use `useSuspenseQuery` for data
6. Tailwind classes for layout and styling
7. `cn()` for conditional classes
8. Event handlers with `useCallback`
9. Default export at bottom
10. No early returns for loading states

**See Also:**
- [data-fetching.md](data-fetching.md) - useSuspenseQuery details
- [loading-and-error-states.md](loading-and-error-states.md) - Suspense best practices
- [complete-examples.md](complete-examples.md) - Full working examples
