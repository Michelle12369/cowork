# Loading & Error States

**CRITICAL**: Proper loading and error state handling prevents layout shift and provides better user experience.

---

## ⚠️ CRITICAL RULE: No Early Returns That Swap In a Loading Spinner

**Scope of this rule:** it targets the specific anti-pattern of returning a *loading spinner* early, which makes the whole subtree pop in and shift layout. It does **not** ban early returns in general — early `return`s for auth redirects (`<Navigate />`), empty states (`<Empty />`), error states, or permission gates are perfectly good React and often clearer than nesting. The problem is exclusively about loading placeholders that change the page's dimensions.

### The Problem

```typescript
// ❌ NEVER DO THIS - Early return with loading spinner
const Component = () => {
    const { data, isLoading } = useQuery();

    // WRONG: This causes layout shift and poor UX
    if (isLoading) {
        return <Spin />;
    }

    return <Content data={data} />;
};
```

```typescript
// ✅ FINE - early returns that are NOT loading spinners
const Component = () => {
    const { user } = useAuth();
    if (!user) return <Navigate to='/login' replace />;   // auth gate — OK
    if (items.length === 0) return <Empty description='No messages yet' />;  // empty state — OK
    return <Content items={items} />;
};
```

**Why this is bad:**
1. **Layout Shift**: Content position jumps when loading completes
2. **CLS (Cumulative Layout Shift)**: Poor Core Web Vital score
3. **Jarring UX**: Page structure changes suddenly
4. **Lost Scroll Position**: User loses place on page

### The Solutions

**Option 1: SuspenseLoader (PREFERRED for new components)**

```typescript
import { SuspenseLoader } from '~components/SuspenseLoader';

const HeavyComponent = React.lazy(() => import('./HeavyComponent'));

export const MyComponent: React.FC = () => {
    return (
        <SuspenseLoader>
            <HeavyComponent />
        </SuspenseLoader>
    );
};
```

**Option 2: LoadingOverlay (for legacy useQuery patterns)**

```typescript
import { LoadingOverlay } from '~components/LoadingOverlay';

export const MyComponent: React.FC = () => {
    const { data, isLoading } = useQuery({ ... });

    return (
        <LoadingOverlay loading={isLoading}>
            <Content data={data} />
        </LoadingOverlay>
    );
};
```

---

## SuspenseLoader Component

### What It Does

- Shows loading indicator while lazy components load
- Smooth fade-in animation
- Prevents layout shift
- Consistent loading experience across app

### Import

```typescript
import { SuspenseLoader } from '~components/SuspenseLoader';
// Or
import { SuspenseLoader } from '@/components/SuspenseLoader';
```

### Basic Usage

```typescript
<SuspenseLoader>
    <LazyLoadedComponent />
</SuspenseLoader>
```

### With useSuspenseQuery

```typescript
import { useSuspenseQuery } from '@tanstack/react-query';
import { SuspenseLoader } from '~components/SuspenseLoader';

const Inner: React.FC = () => {
    // No isLoading needed!
    const { data } = useSuspenseQuery({
        queryKey: ['data'],
        queryFn: () => api.getData(),
    });

    return <Display data={data} />;
};

export const Outer: React.FC = () => {
    return (
        <SuspenseLoader>
            <Inner />
        </SuspenseLoader>
    );
};
```

### Multiple Suspense Boundaries

```typescript
export const ChatPage: React.FC = () => {
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
};
```

Each section loads independently — better perceived performance.

---

## LoadingOverlay Component

### When to Use

- Legacy components with `useQuery` not yet refactored to Suspense
- Overlay loading state needed
- Can't use Suspense boundaries

### Usage

```typescript
import { LoadingOverlay } from '~components/LoadingOverlay';

export const MyComponent: React.FC = () => {
    const { data, isLoading } = useQuery({
        queryKey: ['data'],
        queryFn: () => api.getData(),
    });

    return (
        <LoadingOverlay loading={isLoading}>
            <div className='p-4'>
                {data && <Content data={data} />}
            </div>
        </LoadingOverlay>
    );
};
```

---

## Error Handling

### antd message API (REQUIRED)

Use the antd `App.useApp()` hook for notifications inside components:

```typescript
import { App, Button } from 'antd';

export const MyComponent: React.FC = () => {
    const { message } = App.useApp();

    const handleAction = async () => {
        try {
            await api.doSomething();
            message.success('Operation completed successfully');
        } catch (error) {
            message.error('Operation failed');
        }
    };

    return <Button onClick={handleAction}>Do Action</Button>;
};
```

**Available methods:**
- `message.success(content)` — Green success toast
- `message.error(content)` — Red error toast
- `message.warning(content)` — Orange warning toast
- `message.info(content)` — Blue info toast
- `message.loading(content)` — Loading toast (returns a close fn)

### Setup: Wrap App with antd App Provider

```typescript
// main.tsx or App.tsx
import { App, ConfigProvider } from 'antd';

function Root() {
    return (
        <ConfigProvider>
            <App>
                <AppRoutes />
            </App>
        </ConfigProvider>
    );
}
```

### Static message (outside React components)

For cases outside component tree (e.g., in API interceptors):

```typescript
import { message } from 'antd';

// Use sparingly — prefer App.useApp() inside components
message.error('Session expired');
```

### TanStack Query Error Callbacks

> **Note (TanStack Query v5):** `onError` was removed from query options in v5. Errors thrown by `useSuspenseQuery` propagate to the nearest `<ErrorBoundary>` — that is the correct place to handle them. `onError` is still valid on `useMutation`.

```typescript
import { useSuspenseQuery, useMutation } from '@tanstack/react-query';
import { App } from 'antd';
import { ErrorBoundary } from 'react-error-boundary';

// Query errors → ErrorBoundary (no onError option in v5)
const Inner: React.FC = () => {
    const { data } = useSuspenseQuery({
        queryKey: ['data'],
        queryFn: () => api.getData(),
        // ❌ onError does NOT exist here in TanStack Query v5
    });
    return <Content data={data} />;
};

// Mutation errors → onError callback is valid
export const MyComponent: React.FC = () => {
    const { message } = App.useApp();

    const mutation = useMutation({
        mutationFn: (payload) => api.update(payload),
        onError: () => {
            message.error('Failed to update');  // ✅ valid on mutations
        },
    });

    return (
        <ErrorBoundary FallbackComponent={ErrorFallback}>
            <SuspenseLoader>
                <Inner />
            </SuspenseLoader>
        </ErrorBoundary>
    );
};
```

### Error Boundaries

```typescript
import { ErrorBoundary } from 'react-error-boundary';
import { Button, Result } from 'antd';

function ErrorFallback({ error, resetErrorBoundary }) {
    return (
        <Result
            status='error'
            title='Something went wrong'
            subTitle={error.message}
            extra={
                <Button onClick={resetErrorBoundary}>Try Again</Button>
            }
        />
    );
}

export const MyPage: React.FC = () => {
    return (
        <ErrorBoundary
            FallbackComponent={ErrorFallback}
            onError={(error) => console.error('Boundary caught:', error)}
        >
            <SuspenseLoader>
                <ComponentThatMightError />
            </SuspenseLoader>
        </ErrorBoundary>
    );
};
```

---

## Skeleton Loading (Alternative)

### antd Skeleton Component

```typescript
import { Skeleton } from 'antd';

export const MyComponent: React.FC = () => {
    const { data, isLoading } = useQuery({ ... });

    return (
        <div className='p-4'>
            {isLoading ? (
                <Skeleton active paragraph={{ rows: 4 }} />
            ) : (
                <>
                    <h2 className='text-lg font-semibold'>{data.title}</h2>
                    <p className='text-gray-600'>{data.description}</p>
                </>
            )}
        </div>
    );
};
```

**Ant Design X skeleton for chat:**
```typescript
import { Bubble } from '@ant-design/x';
import { Skeleton } from 'antd';

// Show skeleton bubbles while loading
export const MessageSkeleton: React.FC = () => {
    return (
        <div className='space-y-4 p-4'>
            <Skeleton.Input active style={{ width: '60%' }} />
            <Skeleton.Input active style={{ width: '40%', marginLeft: 'auto', display: 'block' }} />
            <Skeleton.Input active style={{ width: '70%' }} />
        </div>
    );
};
```

**Key**: Skeleton must have **same layout** as actual content (no shift)

---

## Loading State Anti-Patterns

### ❌ What NOT to Do

```typescript
// ❌ NEVER - Early return
if (isLoading) {
    return <Spin />;
}

// ❌ NEVER - Conditional rendering that shifts layout
{isLoading ? <Spin /> : <Content />}

// ❌ NEVER - Different heights between loading and loaded
if (isLoading) {
    return <div className='h-10'><Spin /></div>;
}
return <div className='h-64'><Content /></div>;  // Different height!
```

### ✅ What TO Do

```typescript
// ✅ BEST - useSuspenseQuery + SuspenseLoader
<SuspenseLoader>
    <ComponentWithSuspenseQuery />
</SuspenseLoader>

// ✅ ACCEPTABLE - LoadingOverlay
<LoadingOverlay loading={isLoading}>
    <Content />
</LoadingOverlay>

// ✅ OK - Skeleton with same layout
<div className='h-64'>
    {isLoading ? <Skeleton active /> : <Content />}
</div>
```

---

## Summary

**Loading States:**
- ✅ **PREFERRED**: SuspenseLoader + useSuspenseQuery (modern pattern)
- ✅ **ACCEPTABLE**: LoadingOverlay (legacy pattern)
- ✅ **OK**: Skeleton with same layout
- ❌ **NEVER**: Early returns or conditional layout

**Error Handling:**
- ✅ **ALWAYS**: `App.useApp().message` for user feedback inside components
- ❌ **NEVER**: inline alert() or console.error as user feedback
- ✅ Use `onError` callbacks on `useMutation` (NOT on `useSuspenseQuery` — removed in TanStack Query v5)
- ✅ `useSuspenseQuery` errors propagate to the nearest `<ErrorBoundary>` — wrap with one
- ✅ Error boundaries with antd `Result` for component-level errors

**See Also:**
- [component-patterns.md](component-patterns.md) - Suspense integration
- [data-fetching.md](data-fetching.md) - useSuspenseQuery details
