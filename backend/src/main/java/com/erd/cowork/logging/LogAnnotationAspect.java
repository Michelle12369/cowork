package com.erd.cowork.logging;

import com.erd.cowork.context.CoworkContext;
import com.erd.cowork.context.CoworkContextHolder;
import java.lang.reflect.Method;
import java.util.Arrays;
import java.util.stream.Collectors;
import lombok.extern.slf4j.Slf4j;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.reflect.MethodSignature;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

/**
 * {@link LogAnnotation} 的環繞切面:被標註的類別/方法在進入與離開時各印一行 info log,附上
 * userId;離開時附耗時,拋例外時另記例外類名(例外照原樣往上拋,不吞)。userId 取自 {@link CoworkContextHolder};非請求執行緒(排程/背景/尚未進
 * filter)時以「-」代替, NEVER 讓 log 反過來弄壞主流程。
 */
@Aspect
@Component
@Slf4j
public class LogAnnotationAspect {

  private static final String NO_USER = "-";

  @Around(
      "@annotation(com.erd.cowork.logging.LogAnnotation)"
          + " || @within(com.erd.cowork.logging.LogAnnotation)")
  public Object logEnterExit(ProceedingJoinPoint joinPoint) throws Throwable {
    MethodSignature signature = (MethodSignature) joinPoint.getSignature();
    LogAnnotation annotation = resolveAnnotation(signature);
    String target = signature.getDeclaringType().getSimpleName() + "." + signature.getName();
    String userId = resolveUserId();
    String sessionPart = resolveSessionPart(signature, joinPoint.getArgs());
    String argsPart =
        annotation != null && annotation.args() ? formatArgs(joinPoint.getArgs(), annotation) : "";

    log.info("[LOG] ENTER {} userId={}{}{}", target, userId, sessionPart, argsPart);
    long startNanos = System.nanoTime();
    try {
      Object result = joinPoint.proceed();
      log.info(
          "[LOG] EXIT  {} userId={}{} elapsedMs={}",
          target,
          userId,
          sessionPart,
          elapsedMs(startNanos));
      return result;
    } catch (Throwable error) {
      log.info(
          "[LOG] EXIT  {} userId={}{} elapsedMs={} threw={}",
          target,
          userId,
          sessionPart,
          elapsedMs(startNanos),
          error.getClass().getSimpleName());
      throw error;
    }
  }

  /**
   * 方法若有名為 {@code sessionId} 的參數,回傳 " session=&lt;值&gt;";否則空字串(參數名需 編譯帶 {@code -parameters},Spring
   * Boot parent 預設已開)。
   */
  private String resolveSessionPart(MethodSignature signature, Object[] args) {
    String[] names = signature.getParameterNames();
    if (names == null) {
      return "";
    }
    for (int index = 0; index < names.length && index < args.length; index++) {
      if ("sessionId".equals(names[index])) {
        return args[index] == null ? "" : " session=" + args[index];
      }
    }
    return "";
  }

  /** 方法上的 {@link LogAnnotation} 優先;沒有才退回類別上的。 */
  private LogAnnotation resolveAnnotation(MethodSignature signature) {
    Method method = signature.getMethod();
    LogAnnotation onMethod = method.getAnnotation(LogAnnotation.class);
    if (onMethod != null) {
      return onMethod;
    }
    return method.getDeclaringClass().getAnnotation(LogAnnotation.class);
  }

  /** 非請求執行緒(排程/背景/尚未進 filter)時 holder 為 null,回傳「-」。 */
  private String resolveUserId() {
    CoworkContext context = CoworkContextHolder.get();
    if (context == null || !StringUtils.hasText(context.userId())) {
      return NO_USER;
    }
    return context.userId();
  }

  private String formatArgs(Object[] args, LogAnnotation annotation) {
    if (args.length == 0) {
      return " args=[]";
    }
    String joined = Arrays.stream(args).map(String::valueOf).collect(Collectors.joining(", "));
    if (joined.length() <= annotation.maxArgsLength()) {
      return " args=[" + joined + "]";
    }
    if (annotation.onOverflow() == ArgsOverflow.OMIT) {
      return " args=(" + joined.length() + " chars, omitted)";
    }
    return " args=[" + joined.substring(0, annotation.maxArgsLength()) + "…]";
  }

  private static long elapsedMs(long startNanos) {
    return (System.nanoTime() - startNanos) / 1_000_000;
  }
}
