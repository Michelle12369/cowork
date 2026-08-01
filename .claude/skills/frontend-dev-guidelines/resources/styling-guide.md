# Styling Guide

Modern styling patterns using Tailwind CSS utility classes and Ant Design component theming.

---

## Primary Styling: Tailwind CSS

All layout, spacing, typography, and color styling is done with **Tailwind utility classes**. Ant Design component-level styles (colors, sizes, variants) are configured via the antd `theme` config.

---

## Setup: `cn()` Helper

The `cn()` function merges Tailwind classes correctly (handles conflicts, deduplication). Install and create it once:

```bash
npm install clsx tailwind-merge
```

```typescript
// src/lib/utils.ts
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs));
}
```

Import everywhere as `import { cn } from '@/lib/utils'`.

---

## Inline vs Extracted Classes

### Simple: Tailwind classes inline

```typescript
export const MyComponent: React.FC = () => {
    return (
        <div className='flex flex-col gap-4 p-4 rounded-lg bg-white border border-gray-200'>
            <h2 className='text-lg font-semibold text-gray-900'>Title</h2>
            <p className='text-sm text-gray-500'>Subtitle</p>
        </div>
    );
};
```

### Conditional: `cn()` helper (clsx + tailwind-merge)

```typescript
import { cn } from '@/lib/utils';

interface ButtonProps {
    active?: boolean;
    disabled?: boolean;
}

export const StatusBadge: React.FC<ButtonProps> = ({ active, disabled }) => {
    return (
        <span
            className={cn(
                'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium',
                active ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600',
                disabled && 'opacity-50 cursor-not-allowed',
            )}
        >
            {active ? 'Active' : 'Inactive'}
        </span>
    );
};
```

### Complex: Separate `.classes.ts` file (>100 lines of class strings)

```typescript
// MyComponent.classes.ts
export const classes = {
    container: 'flex flex-col h-full bg-gray-50',
    sidebar: 'w-64 shrink-0 border-r border-gray-200 bg-white overflow-y-auto',
    main: 'flex-1 flex flex-col overflow-hidden',
    header: 'h-14 flex items-center px-4 border-b border-gray-200 bg-white',
    messageList: 'flex-1 overflow-y-auto p-4 space-y-4',
    inputArea: 'border-t border-gray-200 bg-white p-4',
};

// MyComponent.tsx
import { classes } from './MyComponent.classes';

export const ChatLayout: React.FC = () => {
    return (
        <div className={classes.container}>
            <aside className={classes.sidebar}>...</aside>
            <main className={classes.main}>...</main>
        </div>
    );
};
```

---

## Common Layout Patterns

### Flexbox

```typescript
// Row
<div className='flex items-center gap-2'>
    <Icon />
    <span>Label</span>
</div>

// Column
<div className='flex flex-col gap-4'>
    <Header />
    <Content />
</div>

// Space between
<div className='flex items-center justify-between'>
    <Title />
    <Actions />
</div>
```

### Full-height Chat Layout

```typescript
export const ChatLayout: React.FC = () => {
    return (
        <div className='flex h-screen overflow-hidden'>
            {/* Sidebar */}
            <aside className='w-64 shrink-0 border-r border-gray-200 bg-white flex flex-col'>
                <div className='p-4 border-b border-gray-200'>
                    <h1 className='text-base font-semibold'>Conversations</h1>
                </div>
                <div className='flex-1 overflow-y-auto'>
                    {/* conversation list */}
                </div>
            </aside>

            {/* Main area */}
            <main className='flex-1 flex flex-col overflow-hidden'>
                <div className='flex-1 overflow-y-auto p-4'>
                    {/* messages */}
                </div>
                <div className='border-t border-gray-200 p-4'>
                    {/* input */}
                </div>
            </main>
        </div>
    );
};
```

### Grid

```typescript
// Responsive 2-col grid
<div className='grid grid-cols-1 md:grid-cols-2 gap-4'>
    <Card />
    <Card />
</div>

// 3-col with sidebar
<div className='grid grid-cols-12 gap-4'>
    <div className='col-span-12 md:col-span-8'>Main</div>
    <div className='col-span-12 md:col-span-4'>Sidebar</div>
</div>
```

---

## Spacing Reference

Tailwind spacing scale (1 unit = 4px):

```
p-1   = 4px      p-2   = 8px      p-3   = 12px
p-4   = 16px     p-6   = 24px     p-8   = 32px
gap-2 = 8px      gap-4 = 16px     gap-6 = 24px
```

Use `px-*` / `py-*` for horizontal/vertical, `pt-*` `pr-*` etc. for individual sides.

---

## Responsive Design

```typescript
// Mobile-first responsive
<div className='text-sm md:text-base lg:text-lg'>
    Responsive text
</div>

<div className='flex flex-col md:flex-row gap-4'>
    Stacks on mobile, row on desktop
</div>

<div className='hidden md:flex'>
    Only visible on md+
</div>
```

---

## Ant Design Theme Config

Configure antd theme tokens in your app setup — do NOT override antd component styles with raw CSS:

```typescript
// main.tsx or App.tsx
import { ConfigProvider } from 'antd';

<ConfigProvider
    theme={{
        token: {
            colorPrimary: '#1677ff',
            borderRadius: 8,
            fontFamily: 'Inter, sans-serif',
        },
        components: {
            Button: {
                borderRadius: 6,
            },
        },
    }}
>
    <App />
</ConfigProvider>
```

---

## Ant Design + Tailwind Together

Use Tailwind for layout/spacing; use antd props for component appearance:

```typescript
import { Button, Input } from 'antd';

// ✅ Layout with Tailwind, component style with antd props
<div className='flex items-center gap-2 p-4'>
    <Input placeholder='Search...' className='flex-1' />
    <Button type='primary'>Search</Button>
</div>

// ✅ Wrapper layout in Tailwind
<div className='grid grid-cols-2 gap-4'>
    <Button type='default' block>Cancel</Button>
    <Button type='primary' block>Confirm</Button>
</div>
```

---

## What NOT to Do

### ❌ Inline style objects (use Tailwind instead)

```typescript
// ❌ AVOID
<div style={{ padding: '16px', display: 'flex', gap: '8px' }}>

// ✅ CORRECT
<div className='p-4 flex gap-2'>
```

### ❌ CSS modules or styled-components

```typescript
// ❌ AVOID - No CSS modules
import styles from './Component.module.css';

// ❌ AVOID - No styled-components
import styled from 'styled-components';
const StyledDiv = styled.div`padding: 16px;`;
```

### ❌ Overriding antd styles with !important

```typescript
// ❌ AVOID
<Button className='!bg-red-500'>

// ✅ CORRECT - configure via theme token or antd props
<Button danger>Delete</Button>
```

---

## Code Style Standards

### Indentation

**4 spaces** (not 2, not tabs)

### Quotes

**Single quotes** for strings

```typescript
// ✅ CORRECT
import { Button } from 'antd';
const label = 'Submit';

// ❌ WRONG
import { Button } from "antd";
```

### Trailing Commas

**Always use trailing commas** in objects and arrays

```typescript
// ✅ CORRECT
const items = [
    'item1',
    'item2',  // trailing comma
];
```

---

## Summary

**Styling Checklist:**
- ✅ Tailwind classes for all layout, spacing, color
- ✅ `cn()` for conditional classes
- ✅ antd `theme` config for component tokens
- ✅ Separate `.classes.ts` if >100 lines of class strings
- ✅ 4 space indentation, single quotes, trailing commas
- ❌ No inline style objects
- ❌ No CSS modules, styled-components
- ❌ No `!important` overrides on antd

**See Also:**
- [component-patterns.md](component-patterns.md) - Component structure
- [complete-examples.md](complete-examples.md) - Full styling examples
