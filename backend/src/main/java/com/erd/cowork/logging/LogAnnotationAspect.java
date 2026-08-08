package com.erd.cowork.logging;

import com.erd.cowork.context.CurrentUser;
import java.lang.reflect.Method;
import java.util.Arrays;
import java.util.stream.Collectors;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.reflect.MethodSignature;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.context.request.RequestContextHolder;

/**
 * {@link LogAnnotation} 的環繞切面:被標註的類別/方法在進入與離開時各印一行 info log,附上
 * userId;離開時附耗時,拋例外時另記例外類名(例外照原樣往上拋,不吞)。userId 取自 request-scoped {@link
 * CurrentUser}——非請求執行緒(排程/背景/尚未進 filter)時以「-」代替, NEVER 讓 log 反過來弄壞主流程。
 */
@Aspect
@Component
@Slf4j
@RequiredArgsConstructor
public class LogAnnotationAspect {

  private static final String NO_USER = "-";

  private final CurrentUser currentUser;

  @Around(
      "@annotation(com.erd.cowork.logging.LogAnnotation)"
          + " || @within(com.erd.cowork.logging.LogAnnotation)")
  public Object logEnterExit(ProceedingJoinPoint joinPoint) throws Throwable {
    MethodSignature signature = (MethodSignature) joinPoint.getSignature();
    LogAnnotation annotation = resolveAnnotation(signature);
    String target = signature.getDeclaringType().getSimpleName() + "." + signature.getName();
    String userId = resolveUserId();
    String argsPart =
        annotation != null && annotation.args() ? formatArgs(joinPoint.getArgs(), annotation) : "";

    log.info("[LOG] ENTER {} userId={}{}", target, userId, argsPart);
    long startNanos = System.nanoTime();
    try {
      Object result = joinPoint.proceed();
      log.info("[LOG] EXIT  {} userId={} elapsedMs={}", target, userId, elapsedMs(startNanos));
      return result;
    } catch (Throwable error) {
      log.info(
          "[LOG] EXIT  {} userId={} elapsedMs={} threw={}",
          target,
          userId,
          elapsedMs(startNanos),
          error.getClass().getSimpleName());
      throw error;
    }
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

  /** request scope 未啟用(非請求執行緒)時回傳「-」,不去碰 request-scoped proxy 以免拋例外。 */
  private String resolveUserId() {
    if (RequestContextHolder.getRequestAttributes() == null) {
      return NO_USER;
    }
    String userId = currentUser.getUserId();
    return StringUtils.hasText(userId) ? userId : NO_USER;
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
