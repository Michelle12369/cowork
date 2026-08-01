# Spring Boot Skill

Enterprise Spring Boot 3.x development with focus on clean architecture and production-ready code.

## Core Workflow

1. **Analyze** - Understand requirements, identify service boundaries, APIs, data models
2. **Design** - Plan architecture, confirm design before coding
3. **Implement** - Build with constructor injection and layered architecture
4. **Document** - Annotate all endpoints with SpringDoc OpenAPI (`@Tag`, `@Operation`, `@ApiResponse`)
5. **Secure** - Add Spring Security, OAuth2, method security; permit Swagger UI paths; verify tests pass
6. **Test** - Write unit, integration tests; run `./mvnw test` and confirm all pass
7. **Deploy** - Configure health checks via Actuator; validate `/actuator/health` returns UP

## API Documentation (SpringDoc OpenAPI)

Every Spring Boot project **must** include SpringDoc OpenAPI. Add to `pom.xml`:

```xml
<dependency>
    <groupId>org.springdoc</groupId>
    <artifactId>springdoc-openapi-starter-webmvc-ui</artifactId>
    <version>2.8.9</version>
</dependency>
```

For WebFlux (reactive) projects use `springdoc-openapi-starter-webflux-ui` instead.

### OpenAPI Config Bean

```java
@Configuration
public class OpenApiConfig {

    @Bean
    public OpenAPI openAPI() {
        return new OpenAPI()
            .info(new Info()
                .title("Product API")
                .description("REST API for product management")
                .version("1.0.0")
                .contact(new Contact()
                    .name("Team Name")
                    .email("team@example.com")))
            .addSecurityItem(new SecurityRequirement().addList("bearerAuth"))
            .components(new Components()
                .addSecuritySchemes("bearerAuth", new SecurityScheme()
                    .type(SecurityScheme.Type.HTTP)
                    .scheme("bearer")
                    .bearerFormat("JWT")));
    }
}
```

### Controller Annotations

Always annotate controllers and methods with SpringDoc annotations:

```java
@Tag(name = "Products", description = "Product management endpoints")
@RestController
@RequestMapping("/api/v1/products")
@Validated
@RequiredArgsConstructor
public class ProductController {

    private final ProductService service;

    @Operation(summary = "Search products", description = "Returns all products matching the name filter")
    @ApiResponses({
        @ApiResponse(responseCode = "200", description = "Products found",
            content = @Content(array = @ArraySchema(schema = @Schema(implementation = ProductResponse.class)))),
        @ApiResponse(responseCode = "400", description = "Invalid request parameters",
            content = @Content(schema = @Schema(implementation = ErrorResponse.class)))
    })
    @GetMapping
    public List<ProductResponse> search(@RequestParam(defaultValue = "") String name) {
        return service.search(name);
    }

    @Operation(summary = "Create product")
    @ApiResponses({
        @ApiResponse(responseCode = "201", description = "Product created",
            content = @Content(schema = @Schema(implementation = ProductResponse.class))),
        @ApiResponse(responseCode = "400", description = "Validation failed",
            content = @Content(schema = @Schema(implementation = ErrorResponse.class)))
    })
    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ProductResponse create(@Valid @RequestBody ProductRequest request) {
        return service.create(request);
    }

    @Operation(summary = "Get product by ID")
    @ApiResponses({
        @ApiResponse(responseCode = "200", description = "Product found"),
        @ApiResponse(responseCode = "404", description = "Product not found",
            content = @Content(schema = @Schema(implementation = ErrorResponse.class)))
    })
    @GetMapping("/{id}")
    public ProductResponse getById(@Parameter(description = "Product ID") @PathVariable Long id) {
        return service.findById(id);
    }

    @Operation(summary = "Update product")
    @PutMapping("/{id}")
    public ProductResponse update(
        @Parameter(description = "Product ID") @PathVariable Long id,
        @Valid @RequestBody ProductRequest request) {
        return service.update(id, request);
    }

    @Operation(summary = "Delete product")
    @ApiResponse(responseCode = "204", description = "Product deleted")
    @DeleteMapping("/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@Parameter(description = "Product ID") @PathVariable Long id) {
        service.delete(id);
    }
}
```

### Schema Annotations on DTOs

Annotate request/response DTOs so Swagger UI shows useful field descriptions:

```java
@Schema(description = "Product creation request")
public record ProductRequest(
    @Schema(description = "Product name", example = "Wireless Mouse")
    @NotBlank String name,

    @Schema(description = "Product price in USD", example = "29.99")
    @DecimalMin("0.0") BigDecimal price
) {}

@Schema(description = "Product response")
@Value
@Builder
public class ProductResponse {
    @Schema(description = "Product ID", example = "1")
    Long id;

    @Schema(description = "Product name", example = "Wireless Mouse")
    String name;

    @Schema(description = "Product price in USD", example = "29.99")
    BigDecimal price;
}
```

### Permit Swagger UI in Spring Security

When Spring Security is enabled, always whitelist Swagger UI paths:

```java
.authorizeHttpRequests(auth -> auth
    .requestMatchers(
        "/actuator/health",
        "/v3/api-docs/**",
        "/swagger-ui/**",
        "/swagger-ui.html"
    ).permitAll()
    .anyRequest().authenticated())
```

### application.properties

```properties
# Swagger UI available at /swagger-ui.html
springdoc.swagger-ui.path=/swagger-ui.html
springdoc.api-docs.path=/v3/api-docs
# Disable in production if needed
springdoc.swagger-ui.enabled=true
```

Swagger UI is accessible at: `http://localhost:8080/swagger-ui.html`
OpenAPI JSON spec at: `http://localhost:8080/v3/api-docs`

---

## Quick Start Templates

### Entity
```java
@Entity
@Table(name = "products")
@Getter
@Setter
@NoArgsConstructor
@EqualsAndHashCode(of = "id")
@ToString(exclude = {})
public class Product {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @NotBlank
    private String name;

    @DecimalMin("0.0")
    private BigDecimal price;
}
```

### Repository
```java
public interface ProductRepository extends JpaRepository<Product, Long> {
    List<Product> findByNameContainingIgnoreCase(String name);
}
```

### Service
```java
@Slf4j
@Service
@Transactional(readOnly = true)
@RequiredArgsConstructor
public class ProductService {
    private final ProductRepository repo;

    public List<Product> search(String name) {
        return repo.findByNameContainingIgnoreCase(name);
    }

    @Transactional
    public Product create(ProductRequest request) {
        var product = new Product();
        product.setName(request.name());
        product.setPrice(request.price());
        return repo.save(product);
    }
}
```

### REST Controller
```java
@Tag(name = "Products", description = "Product management endpoints")
@RestController
@RequestMapping("/api/v1/products")
@Validated
@RequiredArgsConstructor
public class ProductController {
    private final ProductService service;

    @Operation(summary = "Search products")
    @ApiResponse(responseCode = "200", description = "Products returned")
    @GetMapping
    public List<ProductResponse> search(@RequestParam(defaultValue = "") String name) {
        return service.search(name);
    }

    @Operation(summary = "Create product")
    @ApiResponses({
        @ApiResponse(responseCode = "201", description = "Product created"),
        @ApiResponse(responseCode = "400", description = "Validation failed")
    })
    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ProductResponse create(@Valid @RequestBody ProductRequest request) {
        return service.create(request);
    }
}
```

### DTO (Record)
```java
public record ProductRequest(
    @NotBlank String name,
    @DecimalMin("0.0") BigDecimal price
) {}
```

### Global Exception Handler
```java
@RestControllerAdvice
public class GlobalExceptionHandler {
    @ExceptionHandler(MethodArgumentNotValidException.class)
    @ResponseStatus(HttpStatus.BAD_REQUEST)
    public Map<String, String> handleValidation(MethodArgumentNotValidException ex) {
        return ex.getBindingResult().getFieldErrors().stream()
            .collect(Collectors.toMap(FieldError::getField,
                    error -> error.getDefaultMessage() != null ? error.getDefaultMessage() : "Invalid"));
    }

    @ExceptionHandler(EntityNotFoundException.class)
    @ResponseStatus(HttpStatus.NOT_FOUND)
    public Map<String, String> handleNotFound(EntityNotFoundException ex) {
        return Map.of("error", ex.getMessage());
    }
}
```

### Test Slice
```java
@WebMvcTest(ProductController.class)
class ProductControllerTest {
    @Autowired MockMvc mockMvc;
    @MockBean ProductService service;

    @Test
    void createProduct_validRequest_returns201() throws Exception {
        var product = new Product();
        product.setName("Widget");
        when(service.create(any())).thenReturn(product);

        mockMvc.perform(post("/api/v1/products")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""{"name":"Widget","price":10.0}"""))
            .andExpect(status().isCreated())
            .andExpect(jsonPath("$.name").value("Widget"));
    }
}
```

## Reference Guide

Load detailed patterns based on context:

| Topic | Reference | When to Load |
|-------|-----------|-------------|
| Web/REST | `spring-web.md` | Controllers, validation, exception handling |
| Data Access | `spring-data.md` | JPA, repositories, transactions, queries |
| Security | `spring-security.md` | Spring Security 6, OAuth2, JWT, auth |
| Testing | `spring-testing.md` | Unit, integration, slice tests |

## Constraints

### MUST DO
- Constructor injection (no field injection)
- `@Valid` on all request bodies
- `@Transactional` for multi-step writes
- `@Transactional(readOnly = true)` for reads
- Type-safe config with `@ConfigurationProperties`
- Global exception handling with `@RestControllerAdvice`
- Externalize secrets (use env vars, not properties files)
- `springdoc-openapi-starter-webmvc-ui` dependency in every project
- `@Tag` on every controller class
- `@Operation` + `@ApiResponse` on every endpoint method
- `@Schema` with `description` and `example` on every DTO field
- Whitelist `/v3/api-docs/**` and `/swagger-ui/**` in Spring Security config

### MUST NOT DO
- Field injection (`@Autowired` on fields)
- Skip input validation on endpoints
- Mix blocking and reactive code
- Store secrets in application.properties
- Use deprecated Spring Boot 2.x patterns
- Hardcode URLs, credentials, environment values
- Create endpoints without `@Operation` / `@ApiResponse` annotations
- Use Springfox (deprecated, incompatible with Spring Boot 3)

## Architecture Patterns

**Project Structure:**
```
src/main/java/pl/piomin/services/
├── controller/     # REST endpoints (@Tag, @Operation)
├── service/        # Business logic
├── repository/     # Data access
├── model/          # Entities
├── dto/            # Request/Response DTOs (@Schema)
├── config/         # Configuration (OpenApiConfig, SecurityConfig)
└── exception/      # Custom exceptions + handler
```

**Layering:**
- Controller → Service → Repository
- Controller handles HTTP, validation
- Service handles business logic, transactions
- Repository handles data persistence

**Clean Architecture Principles:**
- Domain models independent of frameworks
- Use case driven design
- Dependency inversion (interfaces)
- Clear boundaries between layers

## Common Annotations

| Annotation | Purpose |
|------------|---------|
| `@RestController` | REST controller (combines @Controller + @ResponseBody) |
| `@Service` | Business logic component |
| `@Repository` | Data access component |
| `@Transactional` | Transaction management |
| `@Valid` | Trigger validation |
| `@ConfigurationProperties` | Bind properties to class |
| `@EnableMethodSecurity` | Enable method security |
| `@Tag` | Group controller endpoints in Swagger UI |
| `@Operation` | Describe a single endpoint |
| `@ApiResponse` / `@ApiResponses` | Document possible HTTP responses |
| `@Parameter` | Describe path/query/header parameters |
| `@Schema` | Describe DTO fields with type, example, description |


## Spring Security JWT

```java
@Configuration
@EnableMethodSecurity
public class SecurityConfig {
    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        return http
                .csrf(AbstractHttpConfigurer::disable)
                .sessionManagement(s -> s.sessionCreationPolicy(STATELESS))
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers("/actuator/health").permitAll()
                        .anyRequest().authenticated())
                .oauth2ResourceServer(oauth2 -> oauth2.jwt(Customizer.withDefaults()))
                .build();
    }
}
```

## Knowledge Base

Spring Boot 3.x, Java 21, Spring WebFlux, Project Reactor, Spring Data JPA, Spring Security 6, OAuth2/JWT, Hibernate, R2DBC, Resilience4j, Micrometer, JUnit 5, TestContainers, Mockito, Maven/Gradle
