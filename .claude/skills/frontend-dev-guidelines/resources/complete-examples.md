# Complete Examples

Full working examples combining all modern patterns: React.FC, lazy loading, Suspense, useSuspenseQuery, Tailwind styling, Ant Design X, React Router, and error handling.

---

## Example 1: Complete Chatbot Window Component

Combines: React.FC, useSuspenseQuery, Ant Design X Bubble/Sender, Tailwind, useCallback, error handling

```typescript
import React, { useCallback, useRef, useEffect } from 'react';
import { App } from 'antd';
import { Bubble, Sender } from '@ant-design/x';
import { useSuspenseQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { chatApi } from '../api/chatApi';
import type { Message } from '~types/chat';

interface ChatWindowProps {
    conversationId: string;
    onNewMessage?: () => void;
}

export const ChatWindow: React.FC<ChatWindowProps> = ({
    conversationId,
    onNewMessage,
}) => {
    const queryClient = useQueryClient();
    const { message } = App.useApp();
    const bottomRef = useRef<HTMLDivElement>(null);

    const { data: messages } = useSuspenseQuery({
        queryKey: ['messages', conversationId],
        queryFn: () => chatApi.getMessages(conversationId),
        staleTime: 30 * 1000,
    });

    // Auto-scroll to bottom on new message
    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages.length]);

    const sendMutation = useMutation({
        mutationFn: (content: string) =>
            chatApi.sendMessage(conversationId, content),

        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['messages', conversationId] });
            onNewMessage?.();
        },

        onError: () => {
            message.error('Failed to send message');
        },
    });

    // Use sendMutation.mutate (not mutateAsync) — onError in the mutation handles the error.
    // Use sendMutation.isPending for loading state — no separate isSending state needed.
    const handleSend = useCallback((content: string) => {
        if (!content.trim() || sendMutation.isPending) return;
        sendMutation.mutate(content);
    }, [sendMutation.mutate]);

    return (
        <div className='flex flex-col h-full bg-gray-50'>
            {/* Message list */}
            <div className='flex-1 overflow-y-auto p-4 space-y-3'>
                {messages.map((msg: Message) => (
                    <Bubble
                        key={msg.id}
                        content={msg.content}
                        placement={msg.role === 'user' ? 'end' : 'start'}
                        typing={msg.isStreaming}
                    />
                ))}
                <div ref={bottomRef} />
            </div>

            {/* Input area */}
            <div className='border-t border-gray-200 bg-white p-4'>
                <Sender
                    onSubmit={handleSend}
                    loading={sendMutation.isPending}
                    placeholder='Type a message...'
                />
            </div>
        </div>
    );
};

export default ChatWindow;
```

---

## Example 2: Complete Feature Structure

```
features/
  chat/
    api/
      chatApi.ts                  # API service layer
    components/
      ChatWindow.tsx              # Main chat UI (from Example 1)
      ConversationSidebar.tsx     # Sidebar with conversation list
      WelcomeScreen.tsx           # Shown before first message
    hooks/
      useSuspenseMessages.ts      # Suspense query hook
      useChatMutations.ts         # Mutation hooks
    helpers/
      chatHelpers.ts              # Utility functions
    types/
      index.ts                    # TypeScript interfaces
    index.ts                      # Public API exports
```

### API Service (chatApi.ts)

```typescript
import apiClient from '@/lib/apiClient';
import type { Message, Conversation, SendMessagePayload } from '../types';

export const chatApi = {
    getMessages: async (conversationId: string): Promise<Message[]> => {
        const { data } = await apiClient.get(`/chat/conversations/${conversationId}/messages`);
        return data;
    },

    sendMessage: async (conversationId: string, content: string): Promise<Message> => {
        const { data } = await apiClient.post(
            `/chat/conversations/${conversationId}/messages`,
            { content },
        );
        return data;
    },

    getConversations: async (): Promise<Conversation[]> => {
        const { data } = await apiClient.get('/chat/conversations');
        return data;
    },

    createConversation: async (title?: string): Promise<Conversation> => {
        const { data } = await apiClient.post('/chat/conversations', { title });
        return data;
    },

    deleteConversation: async (id: string): Promise<void> => {
        await apiClient.delete(`/chat/conversations/${id}`);
    },
};
```

### Suspense Hook (useSuspenseMessages.ts)

```typescript
import { useSuspenseQuery } from '@tanstack/react-query';
import { chatApi } from '../api/chatApi';
import type { Message } from '../types';

export function useSuspenseMessages(conversationId: string) {
    return useSuspenseQuery<Message[], Error>({
        queryKey: ['messages', conversationId],
        queryFn: () => chatApi.getMessages(conversationId),
        staleTime: 30 * 1000,
        refetchOnWindowFocus: false,
    });
}

export function useSuspenseConversations() {
    return useSuspenseQuery({
        queryKey: ['conversations'],
        queryFn: () => chatApi.getConversations(),
        staleTime: 60 * 1000,
    });
}
```

### Types (types/index.ts)

```typescript
export interface Message {
    id: string;
    conversationId: string;
    role: 'user' | 'assistant';
    content: string;
    isStreaming?: boolean;
    createdAt: string;
}

export interface Conversation {
    id: string;
    title: string;
    lastMessageAt: string;
    messageCount: number;
}

export interface SendMessagePayload {
    content: string;
}
```

### Public Exports (index.ts)

```typescript
export { ChatWindow } from './components/ChatWindow';
export { ConversationSidebar } from './components/ConversationSidebar';
export { WelcomeScreen } from './components/WelcomeScreen';

export { useSuspenseMessages, useSuspenseConversations } from './hooks/useSuspenseMessages';
export { useChatMutations } from './hooks/useChatMutations';

export { chatApi } from './api/chatApi';

export type { Message, Conversation } from './types';
```

---

## Example 3: Complete Route Setup (React Router)

```typescript
// src/routes/index.tsx

import { lazy } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { AppLayout } from '~components/AppLayout';
import { SuspenseLoader } from '~components/SuspenseLoader';
import { ProtectedRoute } from '~components/ProtectedRoute';

// Lazy-load pages
const ChatPage = lazy(() => import('@/features/chat/components/ChatPage'));
const ConversationPage = lazy(() =>
    import('@/features/chat/components/ConversationPage').then(
        (m) => ({ default: m.ConversationPage })
    )
);
const SettingsPage = lazy(() => import('@/features/settings/components/SettingsPage'));
const LoginPage = lazy(() => import('@/features/auth/components/LoginPage'));

const wrap = (Component: React.ComponentType) => (
    <SuspenseLoader><Component /></SuspenseLoader>
);

export const AppRoutes: React.FC = () => {
    return (
        <Routes>
            <Route path='/login' element={wrap(LoginPage)} />

            <Route element={<ProtectedRoute />}>
                <Route element={<AppLayout />}>
                    <Route index element={<Navigate to='/chat' replace />} />

                    <Route path='/chat' element={wrap(ChatPage)} />

                    <Route
                        path='/conversations/:conversationId'
                        element={wrap(ConversationPage)}
                    />

                    <Route path='/settings' element={wrap(SettingsPage)} />
                </Route>
            </Route>

            <Route path='*' element={<Navigate to='/' replace />} />
        </Routes>
    );
};
```

---

## Example 4: Conversation Sidebar with Ant Design X

```typescript
import React, { useCallback } from 'react';
import { Button, App } from 'antd';
import { Conversations, type ConversationsProps } from '@ant-design/x';
import { PlusOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import { useSuspenseConversations } from '~features/chat';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { chatApi } from '~features/chat';

export const ConversationSidebar: React.FC = () => {
    const navigate = useNavigate();
    const { conversationId } = useParams();
    const queryClient = useQueryClient();
    const { message } = App.useApp();

    const { data: conversations } = useSuspenseConversations();

    const createMutation = useMutation({
        mutationFn: () => chatApi.createConversation(),
        onSuccess: (newConv) => {
            queryClient.invalidateQueries({ queryKey: ['conversations'] });
            navigate(`/conversations/${newConv.id}`);
        },
        onError: () => {
            message.error('Failed to create conversation');
        },
    });

    const items: ConversationsProps['items'] = conversations.map((c) => ({
        key: c.id,
        label: c.title || 'New Chat',
        timestamp: new Date(c.lastMessageAt).getTime(),
    }));

    const handleActiveChange = useCallback((key: string) => {
        navigate(`/conversations/${key}`);
    }, [navigate]);

    return (
        <div className='flex flex-col h-full border-r border-gray-200 bg-white w-64'>
            <div className='p-3 border-b border-gray-200'>
                <Button
                    type='primary'
                    icon={<PlusOutlined />}
                    block
                    onClick={() => createMutation.mutate()}
                    loading={createMutation.isPending}
                >
                    New Chat
                </Button>
            </div>

            <div className='flex-1 overflow-y-auto'>
                <Conversations
                    items={items}
                    activeKey={conversationId}
                    onActiveChange={handleActiveChange}
                />
            </div>
        </div>
    );
};
```

---

## Example 5: Form with antd + React Hook Form + Zod

```typescript
import React from 'react';
import { Form, Input, Button, Select, App } from 'antd';
import { useForm, Controller } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { userApi } from '../api/userApi';

const schema = z.object({
    name: z.string().min(1, 'Name is required'),
    email: z.string().email('Invalid email'),
    role: z.enum(['admin', 'user', 'viewer']),
});

type FormData = z.infer<typeof schema>;

interface CreateUserFormProps {
    onSuccess?: () => void;
}

export const CreateUserForm: React.FC<CreateUserFormProps> = ({ onSuccess }) => {
    const queryClient = useQueryClient();
    const { message } = App.useApp();

    const { control, handleSubmit, reset, formState: { errors } } = useForm<FormData>({
        resolver: zodResolver(schema),
        defaultValues: { name: '', email: '', role: 'user' },
    });

    const createMutation = useMutation({
        mutationFn: (data: FormData) => userApi.createUser(data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['users'] });
            message.success('User created successfully');
            reset();
            onSuccess?.();
        },
        onError: () => {
            message.error('Failed to create user');
        },
    });

    const onSubmit = (data: FormData) => {
        createMutation.mutate(data);
    };

    return (
        <Form layout='vertical' onFinish={handleSubmit(onSubmit)} className='max-w-md'>
            <Form.Item
                label='Name'
                validateStatus={errors.name ? 'error' : ''}
                help={errors.name?.message}
            >
                <Controller
                    name='name'
                    control={control}
                    render={({ field }) => (
                        <Input {...field} placeholder='Enter name' />
                    )}
                />
            </Form.Item>

            <Form.Item
                label='Email'
                validateStatus={errors.email ? 'error' : ''}
                help={errors.email?.message}
            >
                <Controller
                    name='email'
                    control={control}
                    render={({ field }) => (
                        <Input {...field} type='email' placeholder='Enter email' />
                    )}
                />
            </Form.Item>

            <Form.Item
                label='Role'
                validateStatus={errors.role ? 'error' : ''}
                help={errors.role?.message}
            >
                <Controller
                    name='role'
                    control={control}
                    render={({ field }) => (
                        <Select
                            {...field}
                            options={[
                                { value: 'admin', label: 'Admin' },
                                { value: 'user', label: 'User' },
                                { value: 'viewer', label: 'Viewer' },
                            ]}
                        />
                    )}
                />
            </Form.Item>

            <Form.Item>
                <Button
                    type='primary'
                    htmlType='submit'
                    loading={createMutation.isPending}
                    block
                >
                    Create User
                </Button>
            </Form.Item>
        </Form>
    );
};

export default CreateUserForm;
```

---

## Example 6: Parallel Data Fetching

```typescript
import React from 'react';
import { Statistic, Card } from 'antd';
import { useSuspenseQueries } from '@tanstack/react-query';
import { statsApi } from '../api/statsApi';

export const Dashboard: React.FC = () => {
    const [statsQuery, activeUsersQuery, activityQuery] = useSuspenseQueries({
        queries: [
            {
                queryKey: ['stats'],
                queryFn: () => statsApi.getStats(),
            },
            {
                queryKey: ['users', 'active'],
                queryFn: () => statsApi.getActiveUsers(),
            },
            {
                queryKey: ['activity', 'recent'],
                queryFn: () => statsApi.getRecentActivity(),
            },
        ],
    });

    return (
        <div className='grid grid-cols-1 md:grid-cols-3 gap-4 p-4'>
            <Card>
                <Statistic title='Total Messages' value={statsQuery.data.totalMessages} />
            </Card>

            <Card>
                <Statistic title='Active Users' value={activeUsersQuery.data.length} />
            </Card>

            <Card>
                <Statistic title='Recent Events' value={activityQuery.data.length} />
            </Card>
        </div>
    );
};

// Usage with Suspense:
<SuspenseLoader>
    <Dashboard />
</SuspenseLoader>
```

---

## Example 7: Optimistic Update

```typescript
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { App } from 'antd';
import type { Conversation } from '../types';

export const useTogglePin = () => {
    const queryClient = useQueryClient();
    const { message } = App.useApp();

    return useMutation({
        mutationFn: (conversationId: string) =>
            chatApi.togglePin(conversationId),

        onMutate: async (conversationId) => {
            await queryClient.cancelQueries({ queryKey: ['conversations'] });

            const previous = queryClient.getQueryData<Conversation[]>(['conversations']);

            queryClient.setQueryData<Conversation[]>(['conversations'], (old) =>
                old?.map((c) =>
                    c.id === conversationId ? { ...c, pinned: !c.pinned } : c
                ) ?? []
            );

            return { previous };
        },

        onError: (err, id, context) => {
            queryClient.setQueryData(['conversations'], context?.previous);
            message.error('Failed to update');
        },

        onSettled: () => {
            queryClient.invalidateQueries({ queryKey: ['conversations'] });
        },
    });
};
```

---

## Summary

**Key Takeaways:**

1. **Chatbot UI**: Ant Design X `Bubble`, `Sender`, `Conversations` for chat interface
2. **Component Pattern**: React.FC + lazy + Suspense + useSuspenseQuery
3. **Feature Structure**: Organized subdirectories (api/, components/, hooks/, etc.)
4. **Routing**: React Router v6 with lazy loading + ProtectedRoute
5. **Data Fetching**: useSuspenseQuery with cache-first strategy
6. **Forms**: React Hook Form + Zod + antd Form.Item + Controller
7. **Error Handling**: App.useApp().message + onError callbacks
8. **Styling**: Tailwind classes + `cn()` for conditional logic; antd for components

**See other resources for detailed explanations of each pattern.**
