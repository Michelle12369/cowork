/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { AxiosError, type AxiosResponse } from 'axios';
import { describe, expect, test } from 'vitest';
import { MCP_ERROR_CODES } from '@/config/mcpBridge';
import { foldMcpFailure } from './mcpResult';

function axiosErrorWithStatus(status: number): AxiosError {
  return new AxiosError('request failed', 'ERR_BAD_RESPONSE', undefined, undefined, {
    status,
  } as AxiosResponse);
}

describe('foldMcpFailure', () => {
  test.each([401, 403, 404])('HTTP %i folds to AUTH with status in message', (status) => {
    const result = foldMcpFailure(axiosErrorWithStatus(status));
    expect(result).toEqual({
      error: { code: 'AUTH', message: expect.stringContaining(`HTTP ${status}`) },
    });
  });

  test.each([400, 422])('HTTP %i folds to INVALID_CALL', (status) => {
    const result = foldMcpFailure(axiosErrorWithStatus(status));
    expect(result).toEqual({
      error: { code: 'INVALID_CALL', message: expect.stringContaining(`HTTP ${status}`) },
    });
  });

  test.each([500, 502, 503])('HTTP %i folds to RETRYABLE', (status) => {
    const result = foldMcpFailure(axiosErrorWithStatus(status));
    expect(result).toEqual({
      error: { code: 'RETRYABLE', message: expect.stringContaining(`HTTP ${status}`) },
    });
  });

  test('network failure without a response folds to RETRYABLE', () => {
    const result = foldMcpFailure(new AxiosError('Network Error', 'ERR_NETWORK'));
    expect(result).toEqual({
      error: { code: 'RETRYABLE', message: expect.stringContaining('network') },
    });
  });

  test('non-axios error folds to RETRYABLE', () => {
    const result = foldMcpFailure(new TypeError('boom'));
    expect(result).toEqual({ error: { code: 'RETRYABLE', message: expect.any(String) } });
  });

  test('folded results never carry a data field', () => {
    const result = foldMcpFailure(axiosErrorWithStatus(500));
    expect('data' in result).toBe(false);
  });
});

describe('contract fixture', () => {
  // Path built as a variable, not a literal, so Vite's `new URL(literal, import.meta.url)`
  // static asset transform does not intercept it and rewrite it to a dev-server URL.
  const fixtureRelativePath = '../../../deepagent-service/tests/fixtures/mcp_result_examples.json';
  const fixturePath = fileURLToPath(new URL(fixtureRelativePath, import.meta.url));
  const examples = JSON.parse(readFileSync(fixturePath, 'utf8')) as Record<
    string,
    { error?: { code: string } }
  >;

  test('every error example uses one of the five known codes', () => {
    const codesInFixture = Object.values(examples)
      .filter((example) => example.error)
      .map((example) => example.error?.code);
    expect(codesInFixture).toHaveLength(5);
    for (const code of codesInFixture) {
      expect(MCP_ERROR_CODES).toContain(code);
    }
  });
});
