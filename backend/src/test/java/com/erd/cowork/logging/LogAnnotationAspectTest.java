package com.erd.cowork.logging;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.erd.cowork.context.CoworkContext;
import com.erd.cowork.context.CoworkContextHolder;
import java.util.List;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.aop.aspectj.annotation.AspectJProxyFactory;

class LogAnnotationAspectTest {

  private ListAppender<ILoggingEvent> appender;
  private Logger aspectLogger;

  @BeforeEach
  void setUp() {
    appender = new ListAppender<>();
    appender.start();
    aspectLogger = (Logger) LoggerFactory.getLogger(LogAnnotationAspect.class);
    aspectLogger.addAppender(appender);
    CoworkContextHolder.set(CoworkContext.external("u1"));
  }

  @AfterEach
  void tearDown() {
    aspectLogger.detachAppender(appender);
    CoworkContextHolder.clear();
  }

  private <T> T proxy(T target) {
    AspectJProxyFactory factory = new AspectJProxyFactory(target);
    factory.addAspect(new LogAnnotationAspect());
    return factory.getProxy();
  }

  private List<String> messages() {
    return appender.list.stream().map(ILoggingEvent::getFormattedMessage).toList();
  }

  @Test
  void classAnnotated_logsEnterExit_withUserId() {
    PlainService service = proxy(new PlainService());
    service.greet("world");

    assertThat(messages()).hasSize(2);
    assertThat(messages().get(0)).isEqualTo("[LOG] ENTER PlainService.greet userId=u1");
    assertThat(messages().get(1)).startsWith("[LOG] EXIT  PlainService.greet userId=u1 elapsedMs=");
  }

  @Test
  void argsDisabled_doesNotLogArgs() {
    PlainService service = proxy(new PlainService());
    service.greet("secret");

    assertThat(messages().get(0)).doesNotContain("args=").doesNotContain("secret");
  }

  @Test
  void argsEnabled_logsArgs() {
    ArgsService service = proxy(new ArgsService());
    service.echo("abc", 7);

    assertThat(messages().get(0)).isEqualTo("[LOG] ENTER ArgsService.echo userId=u1 args=[abc, 7]");
  }

  @Test
  void argsTooLong_truncateStrategy_truncatesWithEllipsis() {
    ArgsService service = proxy(new ArgsService());
    service.truncate("abcdefghij");

    assertThat(messages().get(0))
        .isEqualTo("[LOG] ENTER ArgsService.truncate userId=u1 args=[abcde…]");
  }

  @Test
  void argsTooLong_omitStrategy_omitsWholeArgs() {
    ArgsService service = proxy(new ArgsService());
    service.omit("abcdefghij");

    assertThat(messages().get(0))
        .isEqualTo("[LOG] ENTER ArgsService.omit userId=u1 args=(10 chars, omitted)");
  }

  @Test
  void methodAnnotationOverridesClassAnnotation() {
    ArgsService service = proxy(new ArgsService());
    service.echo("x", 1);

    // 類別上沒有 @LogAnnotation;方法上 args=true 生效。
    assertThat(messages().get(0)).contains("args=[x, 1]");
  }

  @Test
  void noRequestScope_userIdIsDash() {
    CoworkContextHolder.clear();
    PlainService service = proxy(new PlainService());
    service.greet("world");

    assertThat(messages().get(0)).isEqualTo("[LOG] ENTER PlainService.greet userId=-");
  }

  @Test
  void whenTargetThrows_logsThrewAndPropagates() {
    PlainService service = proxy(new PlainService());

    assertThatThrownBy(service::boom).isInstanceOf(IllegalStateException.class);
    assertThat(messages().get(1)).contains("threw=IllegalStateException");
  }

  // ── test targets ──────────────────────────────────────────────────────────

  @LogAnnotation
  public static class PlainService {
    public String greet(String name) {
      return "hi " + name;
    }

    public void boom() {
      throw new IllegalStateException("boom");
    }
  }

  public static class ArgsService {
    @LogAnnotation(args = true)
    public String echo(String first, int second) {
      return first + second;
    }

    @LogAnnotation(args = true, maxArgsLength = 5)
    public String truncate(String value) {
      return value;
    }

    @LogAnnotation(args = true, maxArgsLength = 5, onOverflow = ArgsOverflow.OMIT)
    public String omit(String value) {
      return value;
    }
  }
}
