---
name: java
description: Java backend development for this repo - Spring Boot 3.x, code quality review, JPA/Hibernate patterns, and design patterns. Use when writing or reviewing any backend Java code, REST APIs, entities/repositories, mappers, templates, or when user says "review code", "refactor", "N+1", "implement pattern".
---

# Java Backend Skill

Consolidated Java skill for this repo. Read the reference that matches the task —
don't load everything.

## Quick Reference

| Task | Read | Covers |
|---|---|---|
| 寫/改 Spring Boot 程式 | `references/spring-boot.md` | 專案結構、DI、config、REST、profiles;入口文件 |
| Controller / REST API | `references/spring-web.md` | Controllers、validation、exception handling |
| Entity / Repository / 交易 | `references/spring-data.md` | JPA、repositories、transactions、queries |
| JPA 效能與陷阱 | `references/jpa-patterns.md` | N+1、lazy loading、fetch 策略、LazyInitializationException |
| 測試 | `references/spring-testing.md` | Unit、integration、slice tests(@WebMvcTest 等) |
| Security(若引入) | `references/spring-security.md` | Spring Security 6、OAuth2、JWT |
| Code review / 重構 | `references/code-quality.md` | Clean code、API 契約、null safety、例外處理、MapStruct、Velocity |
| 設計模式選型 | `references/design-patterns.md` | Factory、Builder、Strategy、Observer、Decorator(Java 範例) |

## Ground Rules(全部細節見各 reference 與根目錄 CLAUDE.md)

- CLAUDE.md 的專案規則優先於本 skill;衝突時以 CLAUDE.md 為準
- Java 17;constructor injection;DTO 一律 record;類別命名分類法見 CLAUDE.md
- 寫 code 前:讀 `spring-boot.md` + 任務對應的 reference;review 前:讀 `code-quality.md`
