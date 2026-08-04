package com.erd.cowork.context;

import lombok.Getter;
import lombok.Setter;
import org.springframework.stereotype.Component;
import org.springframework.web.context.annotation.RequestScope;

/**
 * Request-scoped holder for the caller identity, populated by {@link CurrentUserFilter} from the
 * {@code X-User-Id} header. Services inject this instead of threading a {@code userId} parameter
 * through every signature.
 *
 * <p>Request scope does not cross threads. Before any async/SSE boundary (agent streaming,
 * background work) every field MUST be captured into a value object / local variable while still on
 * the request thread — never read this bean from a worker thread.
 */
@Component
@RequestScope
@Getter
@Setter
public class CurrentUser {

  private String userId;

  /** 部門代碼。只有 internal 側的身分 filter 會填，{@link CurrentUserFilter} 永遠留它為 null。 */
  private String deptId;
}
