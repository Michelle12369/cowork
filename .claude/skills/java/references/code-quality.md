# Code Quality Review Skill

Systematic code review combining clean code principles, API design, and Java best practices.

## When to Use
- "review this code" / "code review" / "check this PR"
- "refactor" / "clean this code" / "improve readability"
- "review API" / "check endpoints" / "REST review"
- "mapper" / "convert DTO" / "entity to DTO" → use MapStruct
- "template" / "generate text" / "email template" → use Velocity
- Before merging PR or releasing API changes

## Review Strategy

1. **Quick scan** - Understand intent, identify scope
2. **Checklist pass** - Apply relevant categories below
3. **Summary** - List findings by severity (Critical → Minor → Good)

---

## Lombok Best Practices

Always prefer Lombok annotations over manual boilerplate. Add `lombok` dependency in `pom.xml`:

```xml
<dependency>
    <groupId>org.projectlombok</groupId>
    <artifactId>lombok</artifactId>
    <optional>true</optional>
</dependency>
```

### @Getter / @Setter - Replace Manual Accessors

```java
// ❌ Manual boilerplate
public class User {
    private Long id;
    private String name;
    private String email;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getEmail() { return email; }
    public void setEmail(String email) { this.email = email; }
}

// ✅ Lombok - class level generates all getters/setters
@Getter
@Setter
public class User {
    private Long id;
    private String name;
    private String email;
}

// ✅ Field level - fine-grained control
@Getter
public class User {
    private Long id;
    private String name;
    @Setter private String email;  // only email has setter
}
```

### @Data - Full POJO in One Annotation

`@Data` = `@Getter` + `@Setter` + `@ToString` + `@EqualsAndHashCode` + `@RequiredArgsConstructor`

```java
// ❌ Verbose POJO
public class UserRequest {
    private String name;
    private String email;
    // ... getters, setters, equals, hashCode, toString all manually written
}

// ✅ Clean with @Data
@Data
public class UserRequest {
    private String name;
    private String email;
}
```

**Warning:** Avoid `@Data` on JPA `@Entity` classes — generates `equals`/`hashCode` based on all fields which causes issues with Hibernate proxies. Use `@Getter`/`@Setter` + `@EqualsAndHashCode(of = "id")` instead.

```java
// ✅ Correct for JPA entity
@Entity
@Getter
@Setter
@EqualsAndHashCode(of = "id")
@ToString(exclude = {"orders", "roles"})  // exclude lazy collections to avoid N+1
public class User {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String name;

    @OneToMany(mappedBy = "user")
    private List<Order> orders;
}
```

### @Builder - Fluent Object Construction

```java
// ❌ Telescoping constructors or fragile positional args
User user = new User(null, "Alice", "alice@example.com", 25, true, "ADMIN");

// ✅ @Builder - readable, order-independent, safe
@Builder
@Getter
public class User {
    private Long id;
    private String name;
    private String email;
    private int age;
    private boolean active;
    private String role;
}

User user = User.builder()
    .name("Alice")
    .email("alice@example.com")
    .age(25)
    .active(true)
    .role("ADMIN")
    .build();
```

**With defaults:**
```java
@Builder
@Getter
public class UserConfig {
    @Builder.Default
    private int maxRetries = 3;
    @Builder.Default
    private boolean enabled = true;
    private String baseUrl;
}
```

**Combine with `@AllArgsConstructor` for JPA:**
```java
@Entity
@Getter
@Builder
@NoArgsConstructor          // required by JPA
@AllArgsConstructor         // required by @Builder
public class Product {
    @Id @GeneratedValue
    private Long id;
    private String name;
    private BigDecimal price;
}
```

### @Value - Immutable Objects

Use `@Value` for DTOs and value objects that should never change after creation.
`@Value` = `@Getter` + `@FieldDefaults(makeFinal=true)` + `@AllArgsConstructor` + `@ToString` + `@EqualsAndHashCode`

```java
// ❌ Mutable DTO - risky, fields can be changed
@Data
public class MoneyAmount {
    private BigDecimal amount;
    private String currency;
}

// ✅ Immutable value object
@Value
public class MoneyAmount {
    BigDecimal amount;
    String currency;
}
// Fields are automatically private final; no setters generated
```

### @Slf4j - Replace Manual Logger Declaration

```java
// ❌ Boilerplate logger field
public class UserService {
    private static final Logger log = LoggerFactory.getLogger(UserService.class);
}

// ✅ @Slf4j injects `log` field automatically
@Slf4j
@Service
public class UserService {
    public void createUser(UserRequest req) {
        log.info("Creating user: {}", req.getEmail());
        log.debug("Full request: {}", req);
    }
}
```

Other logger variants: `@Log4j2`, `@CommonsLog`, `@JBossLog`

### @RequiredArgsConstructor - Constructor Injection

```java
// ❌ Manual constructor injection boilerplate
@Service
public class OrderService {
    private final OrderRepository orderRepository;
    private final UserService userService;
    private final EmailService emailService;

    public OrderService(OrderRepository orderRepository,
                        UserService userService,
                        EmailService emailService) {
        this.orderRepository = orderRepository;
        this.userService = userService;
        this.emailService = emailService;
    }
}

// ✅ @RequiredArgsConstructor generates constructor for all final fields
@Slf4j
@Service
@RequiredArgsConstructor
public class OrderService {
    private final OrderRepository orderRepository;
    private final UserService userService;
    private final EmailService emailService;
}
```

### @ToString - Controlled String Representation

```java
// ✅ Exclude sensitive data and lazy collections
@ToString(exclude = {"password", "orders"})
@Getter
public class User {
    private String name;
    private String password;  // excluded from toString
    private List<Order> orders;  // excluded - avoids LazyInitializationException
}

// ✅ Include only specific fields
@ToString(onlyExplicitlyIncluded = true)
@Getter
public class User {
    @ToString.Include private Long id;
    @ToString.Include private String name;
    private String password;  // not included
}
```

### @EqualsAndHashCode - Safe Identity Comparison

```java
// ❌ Default @EqualsAndHashCode includes all fields - breaks with JPA proxies and mutable state
@Data
public class User { ... }

// ✅ Use only stable, immutable identity fields
@Getter
@EqualsAndHashCode(of = "id")
public class User {
    private Long id;
    private String name;
    private String email;
}

// ✅ For DTOs with no ID, use all fields (default) but exclude mutable/computed ones
@Getter
@EqualsAndHashCode(exclude = "createdAt")
public class UserResponse {
    private Long id;
    private String name;
    private LocalDateTime createdAt;
}
```

### @SneakyThrows - Suppress Checked Exceptions

Use sparingly — only when you cannot propagate the checked exception and are certain it won't occur.

```java
// ❌ Forced to handle checked exception in lambda
list.forEach(item -> {
    try {
        process(item);  // throws IOException
    } catch (IOException e) {
        throw new RuntimeException(e);
    }
});

// ✅ @SneakyThrows wraps checked exception transparently
@SneakyThrows
private void process(Item item) {
    // ... code that throws IOException
}
```

### @Accessors - Fluent/Chained Setters

```java
// ✅ @Accessors(chain = true) enables method chaining
@Getter
@Setter
@Accessors(chain = true)
public class UserRequest {
    private String name;
    private String email;
}

// Usage
UserRequest req = new UserRequest()
    .setName("Alice")
    .setEmail("alice@example.com");

// ✅ @Accessors(fluent = true) removes get/set prefix
@Getter
@Setter
@Accessors(fluent = true)
public class Config {
    private int timeout;
    private boolean retry;
}

// Usage
config.timeout(30).retry(true);
int t = config.timeout();
```

---

## MapStruct - Object Mapping

Always use MapStruct instead of manual mapping code or ModelMapper for type-safe, compile-time-checked conversions.

Add dependency in `pom.xml` — must declare both `mapstruct` and `lombok-mapstruct-binding` when using Lombok together:

```xml
<properties>
    <mapstruct.version>1.6.3</mapstruct.version>
    <lombok-mapstruct-binding.version>0.2.0</lombok-mapstruct-binding.version>
</properties>

<dependencies>
    <dependency>
        <groupId>org.mapstruct</groupId>
        <artifactId>mapstruct</artifactId>
        <version>${mapstruct.version}</version>
    </dependency>
</dependencies>

<build>
    <plugins>
        <plugin>
            <groupId>org.apache.maven.plugins</groupId>
            <artifactId>maven-compiler-plugin</artifactId>
            <configuration>
                <annotationProcessorPaths>
                    <path>
                        <groupId>org.projectlombok</groupId>
                        <artifactId>lombok</artifactId>
                        <version>${lombok.version}</version>
                    </path>
                    <!-- lombok-mapstruct-binding must come after lombok -->
                    <path>
                        <groupId>org.projectlombok</groupId>
                        <artifactId>lombok-mapstruct-binding</artifactId>
                        <version>${lombok-mapstruct-binding.version}</version>
                    </path>
                    <path>
                        <groupId>org.mapstruct</groupId>
                        <artifactId>mapstruct-processor</artifactId>
                        <version>${mapstruct.version}</version>
                    </path>
                </annotationProcessorPaths>
            </configuration>
        </plugin>
    </plugins>
</build>
```

### Basic Mapper - Entity to DTO

```java
// Entity
@Entity
@Getter
@Setter
public class User {
    @Id @GeneratedValue
    private Long id;
    private String firstName;
    private String lastName;
    private String email;
    private LocalDateTime createdAt;
}

// DTO
@Value
public class UserResponse {
    Long id;
    String firstName;
    String lastName;
    String email;
}

// ❌ Manual mapping - verbose and error-prone
public UserResponse toResponse(User user) {
    return new UserResponse(
        user.getId(),
        user.getFirstName(),
        user.getLastName(),
        user.getEmail()
    );
}

// ✅ MapStruct mapper - generated at compile time
@Mapper(componentModel = "spring")
public interface UserMapper {
    UserResponse toResponse(User user);
    List<UserResponse> toResponseList(List<User> users);
}

// Usage in service (injected as Spring bean)
@Service
@RequiredArgsConstructor
public class UserService {
    private final UserMapper userMapper;
    private final UserRepository userRepository;

    public UserResponse findById(Long id) {
        return userRepository.findById(id)
            .map(userMapper::toResponse)
            .orElseThrow(() -> new NotFoundException("User not found"));
    }
}
```

### Field Name Mapping - @Mapping

```java
// ❌ Different field names break automatic mapping silently
public class Order {
    private Long orderId;
    private String clientName;
    private BigDecimal totalPrice;
}

public class OrderResponse {
    private Long id;
    private String customerName;
    private BigDecimal amount;
}

// ✅ Explicit field mapping with @Mapping
@Mapper(componentModel = "spring")
public interface OrderMapper {
    @Mapping(source = "orderId",    target = "id")
    @Mapping(source = "clientName", target = "customerName")
    @Mapping(source = "totalPrice", target = "amount")
    OrderResponse toResponse(Order order);
}
```

### Nested Object Mapping

```java
@Entity
@Getter @Setter
public class Order {
    @Id @GeneratedValue
    private Long id;

    @ManyToOne
    private User user;

    private BigDecimal amount;
}

@Value
public class OrderResponse {
    Long id;
    String userName;   // from order.user.firstName + lastName
    BigDecimal amount;
}

@Mapper(componentModel = "spring")
public interface OrderMapper {
    @Mapping(source = "user.firstName", target = "userName")
    OrderResponse toResponse(Order order);
}
```

### Custom Mapping with @Named / default method

```java
@Mapper(componentModel = "spring")
public interface ProductMapper {

    @Mapping(source = "priceInCents", target = "price", qualifiedByName = "centsToDecimal")
    @Mapping(target = "category", expression = "java(product.getCategory().getDisplayName())")
    ProductResponse toResponse(Product product);

    @Named("centsToDecimal")
    default BigDecimal centsToDecimal(long cents) {
        return BigDecimal.valueOf(cents).movePointLeft(2);
    }
}
```

### Bidirectional Mapping - Request to Entity

```java
@Mapper(componentModel = "spring")
public interface UserMapper {
    UserResponse toResponse(User user);

    @Mapping(target = "id",        ignore = true)   // id is generated by DB
    @Mapping(target = "createdAt", ignore = true)   // set by @PrePersist
    User toEntity(UserRequest request);

    // Partial update - merge request fields into existing entity
    @BeanMapping(nullValuePropertyMappingStrategy = NullValuePropertyMappingStrategy.IGNORE)
    void updateEntity(UserRequest request, @MappingTarget User user);
}

// Usage in service
@Transactional
public UserResponse updateUser(Long id, UserRequest request) {
    User user = userRepository.findById(id).orElseThrow();
    userMapper.updateEntity(request, user);  // only non-null fields are updated
    return userMapper.toResponse(user);
}
```

### Mapper with Multiple Sources

```java
@Mapper(componentModel = "spring")
public interface ReportMapper {
    @Mapping(source = "user.name",       target = "userName")
    @Mapping(source = "order.id",        target = "orderId")
    @Mapping(source = "order.amount",    target = "totalAmount")
    @Mapping(source = "payment.method",  target = "paymentMethod")
    ReportResponse toReport(User user, Order order, Payment payment);
}
```

### MapStruct Review Flags

| Problem | Fix |
|---------|-----|
| Manual entity→DTO conversion loops | Replace with MapStruct `List<X> toList(List<Y>)` |
| Unmapped target fields silently ignored | Add `@Mapper(unmappedTargetPolicy = ReportingPolicy.ERROR)` |
| Using ModelMapper (runtime reflection, slow) | Replace with MapStruct (compile-time, type-safe) |
| Missing `ignore = true` on generated fields (id, createdAt) | Always ignore DB-managed fields in `toEntity` |
| No null handling on nested objects | Add `nullValueMappingStrategy` or null-safe expressions |

---

## Velocity - Text Template Processing

Use Apache Velocity when generating structured text output: emails, reports, code files, notifications, or any content that mixes static text with dynamic data.

Add dependency in `pom.xml`:

```xml
<dependency>
    <groupId>org.apache.velocity</groupId>
    <artifactId>velocity-engine-core</artifactId>
    <version>2.4.1</version>
</dependency>
```

For Spring Boot, prefer the Spring integration:

```xml
<dependency>
    <groupId>org.apache.velocity.tools</groupId>
    <artifactId>velocity-tools-generic</artifactId>
    <version>3.1</version>
</dependency>
```

### Template File Structure

Place templates under `src/main/resources/templates/`:

```
src/main/resources/
└── templates/
    ├── email/
    │   ├── welcome.vm
    │   └── order-confirmation.vm
    └── report/
        └── monthly-summary.vm
```

**welcome.vm:**
```velocity
Dear $user.firstName $user.lastName,

Welcome to $appName! Your account has been created.

Your registered email: $user.email
Account created: $dateUtil.format($user.createdAt, "yyyy-MM-dd")

#if($user.isPremium())
You have been enrolled in our Premium plan.
#else
Upgrade to Premium for exclusive features.
#end

#foreach($feature in $features)
  - $feature
#end

Best regards,
The $appName Team
```

### VelocityConfig - Spring Bean Setup

```java
@Configuration
public class VelocityConfig {

    @Bean
    public VelocityEngine velocityEngine() {
        Properties props = new Properties();
        props.setProperty("resource.loaders", "class");
        props.setProperty("resource.loader.class.class",
            "org.apache.velocity.runtime.resource.loader.ClasspathResourceLoader");
        props.setProperty("resource.loader.class.path", "/templates/");
        props.setProperty("input.encoding", "UTF-8");
        props.setProperty("output.encoding", "UTF-8");

        VelocityEngine engine = new VelocityEngine();
        engine.init(props);
        return engine;
    }
}
```

### TemplateService - Reusable Template Renderer

```java
@Slf4j
@Service
@RequiredArgsConstructor
public class TemplateService {

    private final VelocityEngine velocityEngine;

    public String render(String templatePath, Map<String, Object> variables) {
        VelocityContext context = new VelocityContext(variables);
        Template template = velocityEngine.getTemplate(templatePath, "UTF-8");

        StringWriter writer = new StringWriter();
        template.merge(context, writer);
        return writer.toString();
    }
}
```

### Email Template Usage

```java
// ❌ String concatenation - unmaintainable
public String buildWelcomeEmail(User user) {
    return "Dear " + user.getFirstName() + ",\n\n"
        + "Welcome to " + appName + "!\n"
        + "Your email: " + user.getEmail() + "\n";
}

// ✅ Velocity template
@Slf4j
@Service
@RequiredArgsConstructor
public class EmailService {

    private final TemplateService templateService;
    private final JavaMailSender mailSender;

    public void sendWelcomeEmail(User user) {
        Map<String, Object> variables = new HashMap<>();
        variables.put("user", user);
        variables.put("appName", "MyApp");
        variables.put("features", List.of("Dashboard", "Reports", "API Access"));

        String body = templateService.render("email/welcome.vm", variables);

        SimpleMailMessage message = new SimpleMailMessage();
        message.setTo(user.getEmail());
        message.setSubject("Welcome to MyApp");
        message.setText(body);
        mailSender.send(message);

        log.info("Welcome email sent to {}", user.getEmail());
    }
}
```

### Template with Conditionals and Loops

**order-confirmation.vm:**
```velocity
Order Confirmation #$order.id

Customer: $order.user.firstName $order.user.lastName
Order Date: $dateUtil.format($order.createdAt, "dd MMM yyyy HH:mm")

Items:
#foreach($item in $order.items)
$velocityCount. $item.productName
   Qty: $item.quantity x $$item.unitPrice = $$item.totalPrice
#end

Subtotal:  $$order.subtotal
#if($order.discount > 0)
Discount:  -$$order.discount
#end
Total:     $$order.totalAmount

#if($order.isPaid())
Payment Status: PAID via $order.paymentMethod
#else
Payment Status: PENDING - Please complete payment within 24 hours.
#end
```

```java
public String renderOrderConfirmation(Order order) {
    Map<String, Object> vars = new HashMap<>();
    vars.put("order", order);
    vars.put("dateUtil", new DateTool());  // Velocity generic tool
    return templateService.render("email/order-confirmation.vm", vars);
}
```

### Dynamic Template from Database / String

When templates are stored in a database or generated dynamically:

```java
public String renderFromString(String templateContent, Map<String, Object> variables) {
    VelocityContext context = new VelocityContext(variables);
    StringWriter writer = new StringWriter();
    // "dynamic-template" is just a log tag, not a file path
    velocityEngine.evaluate(context, writer, "dynamic-template", templateContent);
    return writer.toString();
}

// Usage: notification templates stored in DB
NotificationTemplate tmpl = templateRepository.findByCode("PROMO_EMAIL");
String rendered = renderFromString(tmpl.getContent(), Map.of(
    "user", user,
    "promoCode", "SAVE20",
    "expiryDate", LocalDate.now().plusDays(7)
));
```

### Velocity Review Flags

| Problem | Fix |
|---------|-----|
| String concatenation for multi-line text output | Replace with Velocity template |
| Template files hardcoded inline as Java strings | Move to `.vm` files under `resources/templates/` |
| Logic-heavy templates (complex Java expressions in `.vm`) | Move logic to a context builder / service; keep templates presentational |
| Missing encoding config (`input.encoding`) | Always set UTF-8 explicitly |
| Calling `velocityEngine.getTemplate()` on every request | Template is cached by default; safe to call repeatedly |
| Using `VelocityEngine` directly in controllers | Inject `TemplateService` abstraction instead |

---

## Clean Code Principles

### DRY - Don't Repeat Yourself

**Violation:**
```java
// ❌ Duplicated validation logic
public void createUser(UserRequest req) {
    if (req.getEmail() == null || !req.getEmail().contains("@")) {
        throw new ValidationException("Invalid email");
    }
}

public void updateUser(UserRequest req) {
    if (req.getEmail() == null || !req.getEmail().contains("@")) {
        throw new ValidationException("Invalid email");
    }
}
```

**Fix:**
```java
// ✅ Single source of truth
public class EmailValidator {
    public void validate(String email) {
        if (email == null || !email.contains("@")) {
            throw new ValidationException("Invalid email");
        }
    }
}
```

### KISS - Keep It Simple

**Violation:**
```java
// ❌ Over-engineered
public interface UserFactory {
    User createUser();
}
public class ConcreteUserFactory implements UserFactory {
    public User createUser() { return new User(); }
}
```

**Fix:**
```java
// ✅ Simple
public User createUser() { return new User(); }
```

### YAGNI - You Aren't Gonna Need It

**Violation:**
```java
// ❌ Premature abstraction
public class ConfigurableUserServiceFactoryProvider { }
```

**Fix:**
```java
// ✅ Implement when actually needed
public class UserService { }
```

---

## API Contract Review

### HTTP Verb Semantics

| Verb | Use For | Idempotent | Safe |
|------|---------|------------|------|
| GET | Retrieve resource | Yes | Yes |
| POST | Create new resource | No | No |
| PUT | Replace entire resource | Yes | No |
| PATCH | Partial update | No* | No |
| DELETE | Remove resource | Yes | No |

**Common Mistakes:**
```java
// ✅ GET with query params
@GetMapping("/users")
public List<User> search(@RequestParam String name) { }

// ❌ GET for state change
@GetMapping("/users/{id}/activate")
public void activate(@PathVariable Long id) { }

// ✅ POST/PATCH for state change
@PostMapping("/users/{id}/activate")
public ResponseEntity<Void> activate(@PathVariable Long id) { }
```

### Response Status Codes

| Code | Use Case | Example |
|------|----------|---------|
| 200 OK | Successful GET/PUT/PATCH | Found resource |
| 201 Created | Successful POST | New resource created |
| 204 No Content | Successful DELETE | Resource deleted |
| 400 Bad Request | Validation failure | Invalid input |
| 404 Not Found | Resource doesn't exist | User not found |
| 409 Conflict | State conflict | Duplicate email |
| 500 Server Error | Unexpected error | Database down |

### DTO vs Entity Exposure

```java
// ❌ Exposing JPA entity
@GetMapping("/{id}")
public User getUser(@PathVariable Long id) {
    return userRepository.findById(id).get();  // Exposes internals, N+1 risk
}

// ✅ Use DTO with Lombok
@Value
public class UserResponse {
    Long id;
    String name;
    String email;
}

@GetMapping("/{id}")
public UserResponse getUser(@PathVariable Long id) {
    return userService.findById(id);
}
```

---

## Java Code Review Checklist

### Null Safety

**Check for:**
```java
// ❌ NPE risk
String name = user.getName().toUpperCase();

// ✅ Safe with Optional
String name = Optional.ofNullable(user.getName())
    .map(String::toUpperCase)
    .orElse("");

// ✅ Safe with early return
if (user.getName() == null) return "";
return user.getName().toUpperCase();
```

**Flags:**
- Chained calls without null checks
- `Optional.get()` without `isPresent()`
- Returning `null` instead of `Optional` or empty collection
- Missing `@Nullable`/`@NonNull` on public APIs

### Exception Handling

**Check for:**
```java
// ❌ Swallowing exceptions
try {
    process();
} catch (Exception e) { }  // Silent failure

// ❌ Losing stack trace
catch (IOException e) {
    throw new RuntimeException(e.getMessage());  // Lost context
}

// ✅ Proper handling
catch (IOException e) {
    log.error("Failed to process file: {}", filename, e);
    throw new ProcessingException("File processing failed", e);
}
```

**Flags:**
- Empty catch blocks
- Catching `Exception` or `Throwable` (too broad)
- Not logging exceptions
- Creating new exception without original cause

### Resource Management

**Check for:**
```java
// ❌ Resource leak
FileInputStream fis = new FileInputStream(file);
String content = read(fis);
fis.close();  // Won't execute if read() throws

// ✅ Try-with-resources
try (FileInputStream fis = new FileInputStream(file)) {
    return read(fis);
}  // Auto-closed
```

### Transaction Boundaries

**Check for:**
```java
// ❌ Missing transaction
public void createUser(UserRequest request) {
    User user = new User();
    userRepository.save(user);
    roleRepository.save(new Role(user));  // Two separate transactions
}

// ✅ Proper transaction
@Transactional
public void createUser(UserRequest request) {
    User user = new User();
    userRepository.save(user);
    roleRepository.save(new Role(user));  // Single atomic transaction
}
```

### Naming Conventions

**Good:**
```java
// ✅ Clear intent
public List<User> findActiveUsersByRole(String role) { }
public boolean isEmailValid(String email) { }
public void activateUser(Long userId) { }
```

**Bad:**
```java
// ❌ Unclear
public List<User> get(String s) { }
public boolean check(String str) { }
public void doStuff(Long id) { }
```

### Performance

**Check for:**
```java
// ❌ N+1 query problem
List<User> users = userRepository.findAll();
for (User user : users) {
    List<Order> orders = orderRepository.findByUserId(user.getId());  // N queries
}

// ✅ Join fetch
@Query("SELECT u FROM User u LEFT JOIN FETCH u.orders")
List<User> findAllWithOrders();

// ❌ Loading all data
List<User> allUsers = userRepository.findAll();  // Could be millions

// ✅ Pagination
Page<User> users = userRepository.findAll(PageRequest.of(0, 20));
```

---

## Review Output Format

```markdown
## Code Review: [Component/Feature Name]

### Critical Issues
- **Null safety violation** (UserService.java:42) - `user.getName().toUpperCase()` can NPE. Use Optional or null check.
- **Resource leak** (FileHandler.java:15) - FileInputStream not closed. Use try-with-resources.

### Important Improvements
- **API design** - POST used for idempotent update (UserController.java:28). Use PUT instead.
- **Transaction missing** - Multi-step operation needs @Transactional (OrderService.java:56).
- **N+1 query** - Loop fetches orders individually (line 89). Use JOIN FETCH.
- **Missing Lombok** - Manual getters/setters/constructors in UserDto.java. Replace with @Data or @Value + @Builder.

### Code Smells
- **Long method** - extractUserData() is 80 lines. Consider extracting sub-methods.
- **Magic number** - Use named constant instead of `86400` (line 123).
- **Inconsistent naming** - Mix of camelCase and snake_case in variables.

### Good Practices Observed
- ✅ Constructor injection used throughout
- ✅ DTOs properly separate from entities
- ✅ Comprehensive validation on all endpoints
- ✅ Good test coverage (87%)
- ✅ Lombok used consistently to reduce boilerplate
- ✅ MapStruct used for all entity↔DTO conversions
- ✅ Velocity templates for email/report generation
```

---

## Quick Reference Flags

| Category | Red Flags |
|----------|-----------|
| **Null Safety** | Chained calls, Optional.get(), returning null |
| **Exceptions** | Empty catch, broad catch, lost stack trace |
| **Resources** | Manual close(), missing try-with-resources |
| **API Design** | Wrong HTTP verb, no versioning, entity exposure |
| **Transactions** | Multi-step writes without @Transactional |
| **Performance** | N+1 queries, loading all data, missing indexes |
| **Clean Code** | Code duplication, magic numbers, unclear names |
| **Lombok** | Manual getters/setters, missing @Slf4j, @Data on @Entity, @EqualsAndHashCode without `of=` on entities |
| **MapStruct** | Manual entity→DTO conversion, using ModelMapper, unmapped fields silently ignored, missing `ignore=true` on DB-managed fields |
| **Velocity** | String concat for multi-line output, logic-heavy templates, missing UTF-8 encoding config, templates inline in Java code |

---

## Lombok Annotation Cheat Sheet

| Annotation | Generates | Best For |
|-----------|-----------|---------|
| `@Getter` | Getter methods | Any class |
| `@Setter` | Setter methods | Mutable classes |
| `@Data` | Getter+Setter+equals+hashCode+toString+constructor | Simple DTOs/POJOs (not JPA entities) |
| `@Value` | Immutable version of @Data (no setters, all final) | Value objects, response DTOs |
| `@Builder` | Builder pattern | Complex object construction |
| `@NoArgsConstructor` | No-arg constructor | JPA entities, deserialization |
| `@AllArgsConstructor` | Constructor with all fields | Used with @Builder |
| `@RequiredArgsConstructor` | Constructor for `final` fields | Spring constructor injection |
| `@Slf4j` | `private static final Logger log` | Any class needing logging |
| `@ToString` | toString() method | Debugging, logging |
| `@EqualsAndHashCode` | equals() and hashCode() | Collections, comparisons |
| `@Accessors` | Fluent/chained setters | Builder-style setters |
| `@SneakyThrows` | Suppresses checked exception | Lambdas with checked exceptions |

---

## Severity Levels

- **Critical** - Security, data loss, crash risk → Must fix before merge
- **Important** - Performance, maintainability, correctness → Should fix
- **Code Smell** - Style, complexity, minor issues → Nice to have
- **Good** - Positive feedback to reinforce good practices
