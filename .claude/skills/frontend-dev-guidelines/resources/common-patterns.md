# Common Patterns

Frequently used patterns for forms, authentication, tables, modals, and other common UI elements using Ant Design.

---

## Authentication with useAuth

### Getting Current User

```typescript
import { useAuth } from '@/hooks/useAuth';

export const MyComponent: React.FC = () => {
    const { user } = useAuth();

    // Available properties:
    // - user.id: string
    // - user.email: string
    // - user.username: string
    // - user.roles: string[]

    return (
        <div className='p-4'>
            <p>Logged in as: {user.email}</p>
            <p>Username: {user.username}</p>
            <p>Roles: {user.roles.join(', ')}</p>
        </div>
    );
};
```

**NEVER make direct API calls for auth** — always use `useAuth` hook.

---

## Forms — Two Approaches

Choose one approach per form. Do NOT mix them in the same form.

| Approach | When to use |
|----------|-------------|
| **antd `Form.useForm()` (native)** | Simple forms inside modals, <4 fields, no complex cross-field validation |
| **React Hook Form + Controller** | Complex forms, many fields, Zod schema validation, reusable form components |

---

### Approach A: antd Native Form (simple modals)

See the `AddUserModal` example below — uses `Form.useForm()` + `form.validateFields()`.

### Approach B: React Hook Form + antd (recommended for complex forms)

Uses `react-hook-form` `Controller` to wrap antd inputs.

## Forms with React Hook Form + antd

### Basic Form

```typescript
import { useForm, Controller } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Form, Input, Button, App } from 'antd';

const formSchema = z.object({
    username: z.string().min(3, 'Username must be at least 3 characters'),
    email: z.string().email('Invalid email address'),
});

type FormData = z.infer<typeof formSchema>;

export const MyForm: React.FC = () => {
    const { message } = App.useApp();

    const { control, handleSubmit, formState: { errors } } = useForm<FormData>({
        resolver: zodResolver(formSchema),
        defaultValues: {
            username: '',
            email: '',
        },
    });

    const onSubmit = async (data: FormData) => {
        try {
            await api.submitForm(data);
            message.success('Form submitted successfully');
        } catch {
            message.error('Failed to submit form');
        }
    };

    return (
        <Form layout='vertical' onFinish={handleSubmit(onSubmit)}>
            <Form.Item
                label='Username'
                validateStatus={errors.username ? 'error' : ''}
                help={errors.username?.message}
            >
                <Controller
                    name='username'
                    control={control}
                    render={({ field }) => (
                        <Input {...field} placeholder='Enter username' />
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

            <Form.Item>
                <Button type='primary' htmlType='submit' block>
                    Submit
                </Button>
            </Form.Item>
        </Form>
    );
};
```

---

## Modal Component Pattern

### Standard Modal Structure

All modals should have:
- Descriptive title
- Close button (built into antd Modal)
- Action buttons in footer

```typescript
import { Modal, Button, Form, Input, App } from 'antd';
import { useState } from 'react';

interface AddUserModalProps {
    open: boolean;
    onClose: () => void;
    onSuccess?: () => void;
}

export const AddUserModal: React.FC<AddUserModalProps> = ({ open, onClose, onSuccess }) => {
    const { message } = App.useApp();
    const [loading, setLoading] = useState(false);
    const [form] = Form.useForm();

    const handleOk = async () => {
        try {
            const values = await form.validateFields();
            setLoading(true);
            await api.createUser(values);
            message.success('User created');
            form.resetFields();
            onSuccess?.();
            onClose();
        } catch (error) {
            if (error instanceof Error) {
                message.error('Failed to create user');
            }
            // Ant Design form validation errors are handled automatically
        } finally {
            setLoading(false);
        }
    };

    const handleCancel = () => {
        form.resetFields();
        onClose();
    };

    return (
        <Modal
            title='Add User'
            open={open}
            onOk={handleOk}
            onCancel={handleCancel}
            confirmLoading={loading}
            okText='Add'
            cancelText='Cancel'
            destroyOnClose
        >
            <Form form={form} layout='vertical' className='mt-4'>
                <Form.Item
                    name='name'
                    label='Name'
                    rules={[{ required: true, message: 'Name is required' }]}
                >
                    <Input placeholder='Enter name' autoFocus />
                </Form.Item>

                <Form.Item
                    name='email'
                    label='Email'
                    rules={[
                        { required: true, message: 'Email is required' },
                        { type: 'email', message: 'Invalid email' },
                    ]}
                >
                    <Input type='email' placeholder='Enter email' />
                </Form.Item>
            </Form>
        </Modal>
    );
};
```

---

## Table Pattern (antd Table)

### Basic Table

```typescript
import { Table, Button, Space, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';

interface User {
    id: string;
    name: string;
    email: string;
    role: string;
    active: boolean;
}

interface UserTableProps {
    data: User[];
    loading?: boolean;
    onEdit: (user: User) => void;
    onDelete: (id: string) => void;
}

export const UserTable: React.FC<UserTableProps> = ({
    data,
    loading = false,
    onEdit,
    onDelete,
}) => {
    const columns: ColumnsType<User> = [
        {
            title: 'Name',
            dataIndex: 'name',
            key: 'name',
            sorter: (a, b) => a.name.localeCompare(b.name),
        },
        {
            title: 'Email',
            dataIndex: 'email',
            key: 'email',
        },
        {
            title: 'Role',
            dataIndex: 'role',
            key: 'role',
            render: (role) => <Tag>{role}</Tag>,
        },
        {
            title: 'Status',
            dataIndex: 'active',
            key: 'active',
            render: (active) => (
                <Tag color={active ? 'green' : 'default'}>
                    {active ? 'Active' : 'Inactive'}
                </Tag>
            ),
        },
        {
            title: 'Actions',
            key: 'actions',
            render: (_, record) => (
                <Space>
                    <Button size='small' onClick={() => onEdit(record)}>
                        Edit
                    </Button>
                    <Button
                        size='small'
                        danger
                        onClick={() => onDelete(record.id)}
                    >
                        Delete
                    </Button>
                </Space>
            ),
        },
    ];

    return (
        <Table
            rowKey='id'
            dataSource={data}
            columns={columns}
            loading={loading}
            pagination={{ pageSize: 25, showSizeChanger: true }}
        />
    );
};
```

---

## Mutation Patterns

### Update with Cache Invalidation

```typescript
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { App } from 'antd';

export const useUpdateEntity = () => {
    const queryClient = useQueryClient();
    const { message } = App.useApp();

    return useMutation({
        mutationFn: ({ id, data }: { id: number; data: any }) =>
            api.updateEntity(id, data),

        onSuccess: (result, variables) => {
            queryClient.invalidateQueries({ queryKey: ['entity', variables.id] });
            queryClient.invalidateQueries({ queryKey: ['entities'] });
            message.success('Entity updated');
        },

        onError: () => {
            message.error('Failed to update entity');
        },
    });
};

// Usage in component
const updateEntity = useUpdateEntity();

const handleSave = () => {
    updateEntity.mutate({ id: 123, data: { name: 'New Name' } });
};
```

---

## State Management Patterns

### TanStack Query for Server State (PRIMARY)

Use TanStack Query for **all server data**:

```typescript
// ✅ CORRECT - TanStack Query for server data
const { data: messages } = useSuspenseQuery({
    queryKey: ['messages', conversationId],
    queryFn: () => chatApi.getMessages(conversationId),
});
```

### useState for UI State

```typescript
// ✅ CORRECT - useState for UI state
const [modalOpen, setModalOpen] = useState(false);
const [selectedTab, setSelectedTab] = useState('chat');
```

### Zustand for Global Client State (Minimal)

```typescript
import { create } from 'zustand';

interface AppState {
    sidebarOpen: boolean;
    toggleSidebar: () => void;
}

export const useAppState = create<AppState>((set) => ({
    sidebarOpen: true,
    toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
}));
```

---

## Summary

**Common Patterns:**
- ✅ useAuth hook for current user (id, email, roles, username)
- ✅ React Hook Form + Zod + antd Form.Item + Controller for forms
- ✅ antd Modal with form and confirmLoading
- ✅ antd Table with ColumnsType for data display
- ✅ Mutations with cache invalidation + `App.useApp().message`
- ✅ TanStack Query for server state
- ✅ useState for UI state
- ✅ Zustand for global client state (minimal)

**See Also:**
- [data-fetching.md](data-fetching.md) - TanStack Query patterns
- [component-patterns.md](component-patterns.md) - Component structure
- [loading-and-error-states.md](loading-and-error-states.md) - Error handling
