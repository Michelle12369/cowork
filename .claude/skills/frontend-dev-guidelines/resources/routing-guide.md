# Routing Guide

React Router v6 implementation with component-based routing and lazy loading patterns.

---

## React Router Overview

**React Router v6** with centralized route definitions:
- Routes defined in `src/routes/index.tsx` (or `App.tsx`)
- Lazy loading for code splitting
- `useNavigate`, `useParams`, `Link`, `Outlet` hooks/components
- Nested layouts via `Outlet`

---

## Setup

### Install

```bash
npm install react-router-dom
```

### Root Setup (main.tsx)

```typescript
import { BrowserRouter } from 'react-router-dom';
import { AppRoutes } from './routes';

function App() {
    return (
        <BrowserRouter>
            <AppRoutes />
        </BrowserRouter>
    );
}
```

---

## Route Definitions

### Central Routes File (src/routes/index.tsx)

```typescript
import { lazy, Suspense } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { AppLayout } from '~components/AppLayout';
import { SuspenseLoader } from '~components/SuspenseLoader';

// Lazy-load page components
const ChatPage = lazy(() => import('@/features/chat/components/ChatPage'));
const ConversationsPage = lazy(() => import('@/features/conversations/components/ConversationsPage'));
const SettingsPage = lazy(() => import('@/features/settings/components/SettingsPage'));
const NotFoundPage = lazy(() => import('@/components/NotFoundPage'));

export const AppRoutes: React.FC = () => {
    return (
        <Routes>
            {/* Layout wrapper */}
            <Route element={<AppLayout />}>
                <Route index element={<Navigate to='/chat' replace />} />

                <Route path='/chat' element={
                    <SuspenseLoader>
                        <ChatPage />
                    </SuspenseLoader>
                } />

                <Route path='/conversations' element={
                    <SuspenseLoader>
                        <ConversationsPage />
                    </SuspenseLoader>
                } />

                <Route path='/conversations/:conversationId' element={
                    <SuspenseLoader>
                        <ConversationsPage />
                    </SuspenseLoader>
                } />

                <Route path='/settings' element={
                    <SuspenseLoader>
                        <SettingsPage />
                    </SuspenseLoader>
                } />
            </Route>

            {/* 404 */}
            <Route path='*' element={
                <SuspenseLoader>
                    <NotFoundPage />
                </SuspenseLoader>
            } />
        </Routes>
    );
};
```

---

## Layout with Outlet

### AppLayout Component

```typescript
import { Outlet } from 'react-router-dom';
import { AppSidebar } from './AppSidebar';

export const AppLayout: React.FC = () => {
    return (
        <div className='flex h-screen overflow-hidden'>
            <AppSidebar />
            <main className='flex-1 overflow-auto'>
                <Outlet />  {/* Child routes render here */}
            </main>
        </div>
    );
};
```

### Nested Layout

```typescript
// Dashboard layout with its own sub-routes
<Route path='/dashboard' element={<DashboardLayout />}>
    <Route index element={<DashboardHome />} />
    <Route path='analytics' element={<Analytics />} />
    <Route path='settings' element={<DashboardSettings />} />
</Route>

// DashboardLayout.tsx
export const DashboardLayout: React.FC = () => {
    return (
        <div className='flex'>
            <DashboardSidebar />
            <div className='flex-1'>
                <Outlet />
            </div>
        </div>
    );
};
```

---

## Lazy Loading Routes

### Named Export Pattern

```typescript
import { lazy } from 'react';

// For named exports, use .then() to remap to default
const ChatPage = lazy(() =>
    import('@/features/chat/components/ChatPage').then(
        (module) => ({ default: module.ChatPage })
    )
);
```

### Default Export Pattern

```typescript
// For default exports (simpler)
const SettingsPage = lazy(() => import('@/features/settings/components/SettingsPage'));
```

---

## Hooks

### useNavigate — Programmatic Navigation

```typescript
import { useNavigate } from 'react-router-dom';

export const MyComponent: React.FC = () => {
    const navigate = useNavigate();

    const handleClick = () => {
        navigate('/chat');
    };

    const handleBack = () => {
        navigate(-1);  // Go back
    };

    const handleNewConversation = (id: string) => {
        navigate(`/conversations/${id}`);
    };

    return <button onClick={handleClick}>Go to Chat</button>;
};
```

### useParams — URL Parameters

```typescript
import { useParams } from 'react-router-dom';

export const ConversationPage: React.FC = () => {
    const { conversationId } = useParams<{ conversationId: string }>();

    return <ChatWindow conversationId={conversationId!} />;
};
```

### useSearchParams — Query String

```typescript
import { useSearchParams } from 'react-router-dom';

export const SearchPage: React.FC = () => {
    const [searchParams, setSearchParams] = useSearchParams();
    const query = searchParams.get('q') ?? '';

    const handleSearch = (value: string) => {
        setSearchParams({ q: value });
    };

    return <input value={query} onChange={(e) => handleSearch(e.target.value)} />;
};
```

### useLocation — Current Location

```typescript
import { useLocation } from 'react-router-dom';

export const NavItem: React.FC<{ to: string; label: string }> = ({ to, label }) => {
    const { pathname } = useLocation();
    const isActive = pathname.startsWith(to);

    return (
        <Link
            to={to}
            className={cn(
                'flex items-center gap-2 px-3 py-2 rounded-md text-sm',
                isActive
                    ? 'bg-blue-50 text-blue-700 font-medium'
                    : 'text-gray-600 hover:bg-gray-100',
            )}
        >
            {label}
        </Link>
    );
};
```

---

## Link Component

```typescript
import { Link, NavLink } from 'react-router-dom';

// Basic link
<Link to='/chat'>Go to Chat</Link>

// NavLink - adds active class automatically
<NavLink
    to='/conversations'
    className={({ isActive }) =>
        cn('px-3 py-2 rounded', isActive ? 'bg-blue-100 text-blue-700' : 'text-gray-600')
    }
>
    Conversations
</NavLink>
```

---

## Protected Routes

```typescript
import { Navigate, Outlet } from 'react-router-dom';
import { Spin } from 'antd';
import { useAuth } from '@/hooks/useAuth';

export const ProtectedRoute: React.FC = () => {
    const { user, isLoading } = useAuth();

    // Auth state must resolve before we can render anything — a deliberate
    // exception to the "no early returns" rule. Show a centered spinner
    // rather than null so the user sees something while the auth check runs.
    if (isLoading) {
        return (
            <div className='flex h-screen items-center justify-center'>
                <Spin size='large' />
            </div>
        );
    }

    if (!user) {
        return <Navigate to='/login' replace />;
    }

    return <Outlet />;
};

// In routes:
<Route element={<ProtectedRoute />}>
    <Route element={<AppLayout />}>
        <Route path='/chat' element={<ChatPage />} />
        <Route path='/conversations' element={<ConversationsPage />} />
    </Route>
</Route>
```

---

## Complete Route Example

```typescript
// src/routes/index.tsx

import { lazy } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { AppLayout } from '~components/AppLayout';
import { SuspenseLoader } from '~components/SuspenseLoader';
import { ProtectedRoute } from '~components/ProtectedRoute';

const LoginPage = lazy(() => import('@/features/auth/components/LoginPage'));
const ChatPage = lazy(() => import('@/features/chat/components/ChatPage'));
const ConversationDetailPage = lazy(() =>
    import('@/features/conversations/components/ConversationDetailPage')
);

const wrap = (Component: React.ComponentType) => (
    <SuspenseLoader>
        <Component />
    </SuspenseLoader>
);

export const AppRoutes: React.FC = () => {
    return (
        <Routes>
            <Route path='/login' element={wrap(LoginPage)} />

            <Route element={<ProtectedRoute />}>
                <Route element={<AppLayout />}>
                    <Route index element={<Navigate to='/chat' replace />} />
                    <Route path='/chat' element={wrap(ChatPage)} />
                    <Route path='/conversations/:id' element={wrap(ConversationDetailPage)} />
                </Route>
            </Route>

            <Route path='*' element={<Navigate to='/' replace />} />
        </Routes>
    );
};
```

---

## Summary

**Routing Checklist:**
- ✅ Define routes centrally in `src/routes/index.tsx`
- ✅ Lazy load all page components: `React.lazy(() => import())`
- ✅ Wrap lazy routes in `<SuspenseLoader>`
- ✅ Use `<Outlet>` in layout components
- ✅ `useNavigate()` for programmatic navigation
- ✅ `useParams()` for dynamic route params
- ✅ `NavLink` for nav items with active state
- ✅ `ProtectedRoute` wrapper for auth-gated routes

**See Also:**
- [component-patterns.md](component-patterns.md) - Lazy loading patterns
- [loading-and-error-states.md](loading-and-error-states.md) - SuspenseLoader usage
- [complete-examples.md](complete-examples.md) - Full route examples
