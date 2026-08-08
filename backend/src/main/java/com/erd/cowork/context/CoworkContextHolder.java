package com.erd.cowork.context;

import java.util.concurrent.Callable;

/**
 * {@link CoworkContext} 的 ThreadLocal 持有者(比照 Spring 的 SecurityContextHolder)。filter 於 請求進入時 {@link
 * #set} 、離開時 {@link #clear};service/aspect 透過 {@link CurrentContext} 門面讀取。
 *
 * <p>用一般 {@link ThreadLocal}(NEVER 用 InheritableThreadLocal——對 thread pool 會殘留舊 context、跨 user
 * 汙染)。要把身分帶到別的執行緒時,用 {@link #wrap} 顯式 capture/restore。 MUST 在 finally 清除,否則 pool 重用執行緒會洩漏/串味。
 */
public final class CoworkContextHolder {

  private static final ThreadLocal<CoworkContext> HOLDER = new ThreadLocal<>();

  private CoworkContextHolder() {
    throw new UnsupportedOperationException("no instances");
  }

  public static void set(CoworkContext context) {
    HOLDER.set(context);
  }

  /** 當前 context;非請求執行緒(排程/未進 filter)為 {@code null}。 */
  public static CoworkContext get() {
    return HOLDER.get();
  }

  public static void clear() {
    HOLDER.remove();
  }

  /** 以「現在這條執行緒的 context」包裝 task,在別的執行緒執行時 restore、跑完還原。 */
  public static Runnable wrap(Runnable task) {
    CoworkContext captured = HOLDER.get();
    return () -> {
      CoworkContext previous = HOLDER.get();
      set(captured);
      try {
        task.run();
      } finally {
        restore(previous);
      }
    };
  }

  public static <T> Callable<T> wrap(Callable<T> task) {
    CoworkContext captured = HOLDER.get();
    return () -> {
      CoworkContext previous = HOLDER.get();
      set(captured);
      try {
        return task.call();
      } finally {
        restore(previous);
      }
    };
  }

  private static void restore(CoworkContext previous) {
    if (previous == null) {
      clear();
    } else {
      set(previous);
    }
  }
}
