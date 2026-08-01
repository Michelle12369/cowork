---
name: frontend-dev-guidelines
description: Frontend development guidelines for React/TypeScript chatbot applications. Modern patterns including Suspense, lazy loading, useSuspenseQuery, file organization with features directory, Ant Design (antd) + Ant Design X components, Tailwind CSS, React Router, performance optimization, and TypeScript best practices. Use when creating components, pages, features, fetching data, styling, routing, or working with frontend code.
---

# Frontend Development Guidelines

## Purpose

Comprehensive guide for modern React development, emphasizing Suspense-based data fetching, lazy loading, proper file organization, and performance optimization. Designed for chatbot/AI applications using Ant Design X.

## When to Use This Skill

- Creating new components or pages
- Building new features
- Fetching data with TanStack Query
- Setting up routing with React Router
- Styling components with Tailwind CSS
- Using Ant Design (antd) or Ant Design X components
- Building chatbot/AI conversation UI
- Performance optimization
- Organizing frontend code
- TypeScript best practices

---

## Quick Start

### New Component Checklist

Creating a component? Follow this checklist:

- [ ] Use `React.FC<Props>` pattern with TypeScript
- [ ] Lazy load if heavy component: `React.lazy(() => import())`
- [ ] Wrap in `<SuspenseLoader>` for loading states
- [ ] Use `useSuspenseQuery` for data fetching
- [ ] Import aliases: `@/`, `~types`, `~components`, `~features`
- [ ] Styles: Tailwind classes inline; extract to `cn()` helper if complex
- [ ] Use `useCallback` for event handlers passed to children
- [ ] Default export at bottom
- [ ] No early returns with loading spinners
- [ ] Use antd `message` or `App.useApp()` for user notifications

### New Feature Checklist

Creating a feature? Set up this structure:

- [ ] Create `features/{feature-name}/` directory
- [ ] Create subdirectories: `api/`, `components/`, `hooks/`, `helpers/`, `types/`
- [ ] Create API service file: `api/{feature}Api.ts`
- [ ] Set up TypeScript types in `types/`
- [ ] Create route in `routes/{feature-name}/index.tsx`
- [ ] Lazy load feature components
- [ ] Use Suspense boundaries
- [ ] Export public API from feature `index.ts`

---

## Import Aliases Quick Reference

| Alias | Resolves To | Example |
|-------|-------------|---------|
| `@/` | `src/` | `import { apiClient } from '@/lib/apiClient'` |
| `~types` | `src/types` | `import type { User } from '~types/user'` |
| `~components` | `src/components` | `import { SuspenseLoader } from '~components/SuspenseLoader'` |
| `~features` | `src/features` | `import { authApi } from '~features/auth'` |

Defined in: [vite.config.ts](../../vite.config.ts) lines 180-185

---

## Common Imports Cheatsheet

```typescript
// React & Lazy Loading
import React, { useState, useCallback, useMemo } from 'react';
const Heavy = React.lazy(() => import('./Heavy'));

// Ant Design core components
import { Button, Input, Form, Modal, Typography, Space, Flex } from 'antd';

// Ant Design X (chatbot/AI components)
import { Bubble, Sender, Conversations, Welcome, ThoughtChain } from '@ant-design/x';

// Tailwind utility
import { cn } from '@/lib/utils';

// TanStack Query (Suspense)
import { useSuspenseQuery, useQueryClient } from '@tanstack/react-query';

// React Router
import { useNavigate, useParams, Link, Outlet } from 'react-router-dom';

// Project Components
import { SuspenseLoader } from '~components/SuspenseLoader';

// Hooks
import { useAuth } from '@/hooks/useAuth';

// Types
import type { Post } from '~types/post';
```

---

## Topic Guides

### 🤖 Chatbot Components (Ant Design X)

**Ant Design X** (`@ant-design/x`) provides AI-native UI components:
- `<Bubble>` — Chat message bubbles (user/assistant)
- `<Sender>` — Message input with submit
- `<Conversations>` — Conversation list/sidebar
- `<Welcome>` — Onboarding/welcome screen
- `<ThoughtChain>` — Reasoning/thinking steps display
- `<Attachments>` — File attachment UI

**[📖 Complete Guide: resources/component-patterns.md](resources/component-patterns.md)**

---

### 🎨 Component Patterns

**Modern React components use:**
- `React.FC<Props>` for type safety
- `React.lazy()` for code splitting
- `SuspenseLoader` for loading states
- Named const + default export pattern

**Key Concepts:**
- Lazy load heavy components (chat history, rich editors)
- Always wrap lazy components in Suspense
- Use SuspenseLoader component (with fade animation)
- Component structure: Props → Hooks → Handlers → Render → Export

**[📖 Complete Guide: resources/component-patterns.md](resources/component-patterns.md)**

---

### 📊 Data Fetching

**PRIMARY PATTERN: useSuspenseQuery**
- Use with Suspense boundaries
- Cache-first strategy (check cache before API)
- Replaces `isLoading` checks
- Type-safe with generics

**API Service Layer:**
- Create `features/{feature}/api/{feature}Api.ts`
- Use `apiClient` axios instance
- Centralized methods per feature
- Route format: `/chat/route` (NOT `/api/chat/route`)

**[📖 Complete Guide: resources/data-fetching.md](resources/data-fetching.md)**

---

### 📁 File Organization

**features/ vs components/:**
- `features/`: Domain-specific (chat, conversations, auth)
- `components/`: Truly reusable (SuspenseLoader, AppLayout)

**Feature Subdirectories:**
```
features/
  my-feature/
    api/          # API service layer
    components/   # Feature components
    hooks/        # Custom hooks
    helpers/      # Utility functions
    types/        # TypeScript types
```

**[📖 Complete Guide: resources/file-organization.md](resources/file-organization.md)**

---

### 🎨 Styling

**Primary Method: Tailwind CSS**
- Use Tailwind utility classes for all styling
- Use `cn()` (clsx/tailwind-merge) for conditional classes
- Ant Design component styles handled by antd theme config

**Pattern:**
- Simple: inline Tailwind classes directly
- Complex/conditional: `cn()` helper
- Separate `.classes.ts` for >100 lines of class strings

**[📖 Complete Guide: resources/styling-guide.md](resources/styling-guide.md)**

---

### 🛣️ Routing

**React Router v6 — Component-Based:**
- Define routes in `App.tsx` or a central `routes.tsx`
- Lazy load page components with `React.lazy`
- Use `useNavigate`, `useParams`, `Link`, `Outlet`

**Example:**
```typescript
import { lazy } from 'react';
import { Routes, Route } from 'react-router-dom';

const ChatPage = lazy(() => import('@/features/chat/components/ChatPage'));

// In routes definition:
<Route path='/chat' element={
    <SuspenseLoader><ChatPage /></SuspenseLoader>
} />
```

**[📖 Complete Guide: resources/routing-guide.md](resources/routing-guide.md)**

---

### ⏳ Loading & Error States

**CRITICAL RULE: No early return that swaps in a loading spinner**

```typescript
// ❌ NEVER - Loading spinner early return causes layout shift
if (isLoading) {
    return <Spin />;
}

// ✅ ALWAYS - Consistent layout
<SuspenseLoader>
    <Content />
</SuspenseLoader>
```

**Why:** Prevents Cumulative Layout Shift (CLS), better UX. (Early returns for auth redirects, empty states, and errors are still fine — the rule is specifically about loading placeholders.)

**Error Handling:**
- Use antd `message` (via `App.useApp()`) for user feedback
- `useMutation` `onError` for mutation failures; `<ErrorBoundary>` for `useSuspenseQuery` errors

**[📖 Complete Guide: resources/loading-and-error-states.md](resources/loading-and-error-states.md)**

---

### ⚡ Performance

**Optimization Patterns:**
- `useMemo`: Expensive computations (filter, sort, map)
- `useCallback`: Event handlers passed to children
- `React.memo`: Expensive components
- Debounced search (300-500ms)
- Memory leak prevention (cleanup in useEffect)

**[📖 Complete Guide: resources/performance.md](resources/performance.md)**

---

### 📘 TypeScript

**Standards:**
- Strict mode, no `any` type
- Explicit return types on functions
- Type imports: `import type { User } from '~types/user'`
- Component prop interfaces with JSDoc

**[📖 Complete Guide: resources/typescript-standards.md](resources/typescript-standards.md)**

---

### 🔧 Common Patterns

**Covered Topics:**
- React Hook Form with Zod validation
- antd Form + Input patterns
- Modal component standards
- `useAuth` hook for current user
- Mutation patterns with cache invalidation

**[📖 Complete Guide: resources/common-patterns.md](resources/common-patterns.md)**

---

### 📚 Complete Examples

**Full working examples:**
- Modern chatbot component with Ant Design X
- Complete feature structure
- API service layer
- Route with lazy loading
- Suspense + useSuspenseQuery
- Form with antd + Zod validation

**[📖 Complete Guide: resources/complete-examples.md](resources/complete-examples.md)**

---

## Navigation Guide

| Need to... | Read this resource |
|------------|-------------------|
| Create a component | [component-patterns.md](resources/component-patterns.md) |
| Fetch data | [data-fetching.md](resources/data-fetching.md) |
| Organize files/folders | [file-organization.md](resources/file-organization.md) |
| Style components | [styling-guide.md](resources/styling-guide.md) |
| Set up routing | [routing-guide.md](resources/routing-guide.md) |
| Handle loading/errors | [loading-and-error-states.md](resources/loading-and-error-states.md) |
| Optimize performance | [performance.md](resources/performance.md) |
| TypeScript types | [typescript-standards.md](resources/typescript-standards.md) |
| Forms/Auth/Tables | [common-patterns.md](resources/common-patterns.md) |
| See full examples | [complete-examples.md](resources/complete-examples.md) |

---

## Core Principles

1. **Lazy Load Everything Heavy**: Routes, chat history, rich editors
2. **Suspense for Loading**: Use SuspenseLoader, not early returns
3. **useSuspenseQuery**: Primary data fetching pattern for new code
4. **Features are Organized**: api/, components/, hooks/, helpers/ subdirs
5. **Tailwind for Styles**: Utility classes; `cn()` for conditional logic
6. **Import Aliases**: Use @/, ~types, ~components, ~features
7. **No Loading-Spinner Early Returns**: Prevents layout shift (other early returns are fine)
8. **antd message/notification**: For all user feedback

---

## Quick Reference: File Structure

```
src/
  features/
    chat/
      api/
        chatApi.ts            # API service
      components/
        ChatWindow.tsx         # Main chat UI
        MessageBubble.tsx      # Individual message
      hooks/
        useChat.ts             # Custom hooks
        useSuspenseMessages.ts # Suspense hooks
      helpers/
        chatHelpers.ts         # Utilities
      types/
        index.ts               # TypeScript types
      index.ts                 # Public exports

  components/
    SuspenseLoader/
      SuspenseLoader.tsx       # Reusable loader
    AppLayout/
      AppLayout.tsx            # App shell with Outlet

  routes/
    index.tsx                  # Central route definitions
```

---

## Modern Component Template (Quick Copy)

```typescript
import React, { useState, useCallback } from 'react';
import { Button } from 'antd';
import { useSuspenseQuery } from '@tanstack/react-query';
import { cn } from '@/lib/utils';
import { featureApi } from '../api/featureApi';
import type { FeatureData } from '~types/feature';

interface MyComponentProps {
    id: number;
    onAction?: () => void;
}

export const MyComponent: React.FC<MyComponentProps> = ({ id, onAction }) => {
    const [active, setActive] = useState(false);

    const { data } = useSuspenseQuery({
        queryKey: ['feature', id],
        queryFn: () => featureApi.getFeature(id),
    });

    const handleAction = useCallback(() => {
        setActive(true);
        onAction?.();
    }, [onAction]);

    return (
        <div className='p-4 rounded-lg border border-gray-200 bg-white'>
            <div className={cn('text-base', active && 'font-semibold')}>
                {data.title}
            </div>
            <Button onClick={handleAction} type='primary' className='mt-2'>
                Action
            </Button>
        </div>
    );
};

export default MyComponent;
```

For complete examples, see [resources/complete-examples.md](resources/complete-examples.md)

---

## Related Skills

- **error-tracking**: Error tracking with Sentry (applies to frontend too)
- **backend-dev-guidelines**: Backend API patterns that frontend consumes

---

**Skill Status**: Modular structure with progressive loading for optimal context management
