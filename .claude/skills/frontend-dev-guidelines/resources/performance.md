# Performance Optimization

Patterns for optimizing React component performance, preventing unnecessary re-renders, and avoiding memory leaks.

---

## Memoization Patterns

### useMemo for Expensive Computations

```typescript
import { useMemo } from 'react';

export const DataDisplay: React.FC<{ items: Item[], searchTerm: string }> = ({
    items,
    searchTerm,
}) => {
    // ❌ AVOID - Runs on every render
    const filteredItems = items
        .filter(item => item.name.includes(searchTerm))
        .sort((a, b) => a.name.localeCompare(b.name));

    // ✅ CORRECT - Memoized, only recalculates when dependencies change
    const filteredItems = useMemo(() => {
        return items
            .filter(item => item.name.toLowerCase().includes(searchTerm.toLowerCase()))
            .sort((a, b) => a.name.localeCompare(b.name));
    }, [items, searchTerm]);

    return <List items={filteredItems} />;
};
```

**When to use useMemo:**
- Filtering/sorting **large** arrays (hundreds+ of items)
- Genuinely expensive computations (loops, recursion, heavy transforms)
- A referentially-stable object/array passed to a `React.memo` child or a hook dependency array

**When NOT to use useMemo:**
- Small arrays / cheap `.map()` / `.filter()` — the memo bookkeeping costs more than it saves
- Simple string concatenation or arithmetic
- "Just in case" — `useMemo` is not free; it adds memory + a comparison every render. Profile before reaching for it.

> Rule of thumb: default to writing the plain computation. Add `useMemo` only when profiling shows a real cost, or when you specifically need a **stable reference** (not just a cached value) for `React.memo`/dependency arrays.

---

## useCallback for Event Handlers

### The Problem

```typescript
// ❌ AVOID - Creates new function on every render
export const Parent: React.FC = () => {
    const handleClick = (id: string) => {
        console.log('Clicked:', id);
    };

    // Child re-renders every time Parent renders
    // because handleClick is a new function reference each time
    return <Child onClick={handleClick} />;
};
```

### The Solution

`useCallback` only prevents child re-renders **if the child is also wrapped in `React.memo`**. On its own, a stable function reference does nothing — a plain child re-renders whenever its parent renders, regardless of prop identity. The two must be used together:

```typescript
import { useCallback } from 'react';

// 1. Child MUST be memoized for the stable reference to matter
const Child = React.memo<{ onClick: (id: string) => void }>(({ onClick }) => {
    return <button onClick={() => onClick('1')}>Click</button>;
});

export const Parent: React.FC = () => {
    // 2. Stable function reference across renders
    const handleClick = useCallback((id: string) => {
        console.log('Clicked:', id);
    }, []); // Empty deps = reference never changes

    // Now Child skips re-render when Parent re-renders,
    // because both `onClick` identity AND React.memo hold
    return <Child onClick={handleClick} />;
};
```

**`useCallback` alone (child NOT memoized) = no benefit** — you paid the memoization cost for nothing. Reach for it only when the child is `React.memo`, or the function is a `useEffect`/`useMemo` dependency.

**When to use useCallback:**
- Function passed to a `React.memo` child (both are required)
- Function used as a dependency in `useEffect` / `useMemo`
- Event handlers passed to memoized list items

**When NOT to use useCallback:**
- Child is not wrapped in `React.memo` (most cases — skip it)
- Handlers not passed down: `onClick={() => doSomething()}`
- Premature optimization — profile first

---

## React.memo for Component Memoization

### Basic Usage

```typescript
import React from 'react';

interface ExpensiveComponentProps {
    data: ComplexData;
    onAction: () => void;
}

// ✅ Wrap expensive components in React.memo
export const ExpensiveComponent = React.memo<ExpensiveComponentProps>(
    function ExpensiveComponent({ data, onAction }) {
        // Complex rendering logic
        return <ComplexVisualization data={data} />;
    }
);
```

**When to use React.memo:**
- Component renders frequently
- Component has expensive rendering
- Props don't change often
- Component is a list item
- DataGrid cells/renderers

**When NOT to use React.memo:**
- Props change frequently anyway
- Rendering is already fast
- Premature optimization

---

## Debounced Search

### Using use-debounce Hook

> **Note:** `useSuspenseQuery` does **not** support the `enabled` option in TanStack Query v5 — a suspense query is always enabled. For conditional/"only-fetch-when-typed" fetching, use regular `useQuery` (below), or guard the search term inside `queryFn` and return an empty result.

```typescript
import { useState } from 'react';
import { useDebounce } from 'use-debounce';
import { useQuery } from '@tanstack/react-query';
import { Input } from 'antd';

export const SearchComponent: React.FC = () => {
    const [searchTerm, setSearchTerm] = useState('');

    // Debounce for 300ms
    const [debouncedSearchTerm] = useDebounce(searchTerm, 300);

    // Conditional fetch → useQuery + enabled (NOT useSuspenseQuery)
    const { data, isFetching } = useQuery({
        queryKey: ['search', debouncedSearchTerm],
        queryFn: () => api.search(debouncedSearchTerm),
        enabled: debouncedSearchTerm.length > 0,
    });

    return (
        <Input.Search
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder='Search...'
            loading={isFetching}
        />
    );
};
```

**Optimal Debounce Timing:**
- **300-500ms**: Search/filtering
- **1000ms**: Auto-save
- **100-200ms**: Real-time validation

---

## Memory Leak Prevention

### Cleanup Timeouts/Intervals

```typescript
import { useEffect, useState } from 'react';

export const MyComponent: React.FC = () => {
    const [count, setCount] = useState(0);

    useEffect(() => {
        // ✅ CORRECT - Cleanup interval
        const intervalId = setInterval(() => {
            setCount(c => c + 1);
        }, 1000);

        return () => {
            clearInterval(intervalId);  // Cleanup!
        };
    }, []);

    useEffect(() => {
        // ✅ CORRECT - Cleanup timeout
        const timeoutId = setTimeout(() => {
            console.log('Delayed action');
        }, 5000);

        return () => {
            clearTimeout(timeoutId);  // Cleanup!
        };
    }, []);

    return <div>{count}</div>;
};
```

### Cleanup Event Listeners

```typescript
useEffect(() => {
    const handleResize = () => {
        console.log('Resized');
    };

    window.addEventListener('resize', handleResize);

    return () => {
        window.removeEventListener('resize', handleResize);  // Cleanup!
    };
}, []);
```

### Abort Controllers for Fetch

```typescript
useEffect(() => {
    const abortController = new AbortController();

    fetch('/api/data', { signal: abortController.signal })
        .then(response => response.json())
        .then(data => setState(data))
        .catch(error => {
            if (error.name === 'AbortError') {
                console.log('Fetch aborted');
            }
        });

    return () => {
        abortController.abort();  // Cleanup!
    };
}, []);
```

**Note**: With TanStack Query, this is handled automatically.

---

## Form Performance

### Watch Specific Fields (Not All)

```typescript
import { useForm } from 'react-hook-form';

export const MyForm: React.FC = () => {
    const { register, watch, handleSubmit } = useForm();

    // ❌ AVOID - Watches all fields, re-renders on any change
    const formValues = watch();

    // ✅ CORRECT - Watch only what you need
    const username = watch('username');
    const email = watch('email');

    // Or multiple specific fields
    const [username, email] = watch(['username', 'email']);

    return (
        <form onSubmit={handleSubmit(onSubmit)}>
            <input {...register('username')} />
            <input {...register('email')} />
            <input {...register('password')} />

            {/* Only re-renders when username/email change */}
            <p>Username: {username}, Email: {email}</p>
        </form>
    );
};
```

---

## List Rendering Optimization

### Key Prop Usage

```typescript
// ✅ CORRECT - Stable unique keys
{items.map(item => (
    <ListItem key={item.id}>
        {item.name}
    </ListItem>
))}

// ❌ AVOID - Index as key (unstable if list changes)
{items.map((item, index) => (
    <ListItem key={index}>  // WRONG if list reorders
        {item.name}
    </ListItem>
))}
```

### Memoized List Items

```typescript
const ListItem = React.memo<ListItemProps>(({ item, onAction }) => {
    return (
        <div className='cursor-pointer' onClick={() => onAction(item.id)}>
            {item.name}
        </div>
    );
});

export const List: React.FC<{ items: Item[] }> = ({ items }) => {
    // Stable reference — safe because ListItem is wrapped in React.memo
    const handleAction = useCallback((id: string) => {
        console.log('Action:', id);
    }, []);

    return (
        <div className='flex flex-col'>
            {items.map(item => (
                <ListItem
                    key={item.id}
                    item={item}
                    onAction={handleAction}
                />
            ))}
        </div>
    );
};
```

---

## Preventing Component Re-initialization

### The Problem

```typescript
// ❌ AVOID - Component recreated on every render
export const Parent: React.FC = () => {
    // New component definition each render!
    const ChildComponent = () => <div>Child</div>;

    return <ChildComponent />;  // Unmounts and remounts every render
};
```

### The Solution

```typescript
// ✅ CORRECT - Define outside or use useMemo
const ChildComponent: React.FC = () => <div>Child</div>;

export const Parent: React.FC = () => {
    return <ChildComponent />;  // Stable component
};

// ✅ OR if dynamic, use useMemo
export const Parent: React.FC<{ config: Config }> = ({ config }) => {
    const DynamicComponent = useMemo(() => {
        return () => <div>{config.title}</div>;
    }, [config.title]);

    return <DynamicComponent />;
};
```

---

## Lazy Loading Heavy Dependencies

### Code Splitting

```typescript
// ❌ AVOID - Import heavy libraries at top level
import jsPDF from 'jspdf';  // Large library loaded immediately
import * as XLSX from 'xlsx';  // Large library loaded immediately

// ✅ CORRECT - Dynamic import when needed
const handleExportPDF = async () => {
    const { jsPDF } = await import('jspdf');
    const doc = new jsPDF();
    // Use it
};

const handleExportExcel = async () => {
    const XLSX = await import('xlsx');
    // Use it
};
```

---

## Summary

**Performance Checklist:**
- ✅ `useMemo` for expensive computations (filter, sort, map)
- ✅ `useCallback` for functions passed to children
- ✅ `React.memo` for expensive components
- ✅ Debounce search/filter (300-500ms)
- ✅ Cleanup timeouts/intervals in useEffect
- ✅ Watch specific form fields (not all)
- ✅ Stable keys in lists
- ✅ Lazy load heavy libraries
- ✅ Code splitting with React.lazy

**See Also:**
- [component-patterns.md](component-patterns.md) - Lazy loading
- [data-fetching.md](data-fetching.md) - TanStack Query optimization
- [complete-examples.md](complete-examples.md) - Performance patterns in context