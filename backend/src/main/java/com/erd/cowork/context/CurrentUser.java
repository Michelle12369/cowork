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
 * background work) the {@code userId} MUST be captured into a value object / local variable while
 * still on the request thread — never read this bean from a worker thread. {@code deptId} is
 * subject to the same rule.
 */
@Component
@RequestScope
@Getter
@Setter
public class CurrentUser {

  private String userId;

  /**
   * 部門代碼。主線（{@link CurrentUserFilter}）NEVER 填這個欄位——{@code deptId} 是公司環境才有的概念，只有公司側的身分 filter
   * 知道怎麼填。留著這個欄位是為了讓 downstream 程式碼可以引用它，但在主線上永遠是 null；async/SSE 邊界前同樣 MUST 值物件化。
   */
  private String deptId;
}
