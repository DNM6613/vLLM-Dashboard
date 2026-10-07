import { describe, expect, it } from 'vitest';
import { apiErrorMessage } from './deployment';

describe('apiErrorMessage', () => {
  it('prefers the backend error envelope (error.message)', () => {
    const err = {
      message: 'Request failed with status code 409',
      response: { data: { error: { code: 'conflict', message: 'A task is already running' } } },
    };
    expect(apiErrorMessage(err)).toBe('A task is already running');
  });

  it('falls back to a plain detail string', () => {
    const err = {
      message: 'Request failed with status code 400',
      response: { data: { detail: 'Invalid version' } },
    };
    expect(apiErrorMessage(err)).toBe('Invalid version');
  });

  it('falls back to the first detail array msg (FastAPI validation)', () => {
    const err = {
      message: 'Request failed with status code 422',
      response: {
        data: {
          detail: [
            { loc: ['body', 'version'], msg: 'field required' },
            { loc: ['body', 'venv'], msg: 'second' },
          ],
        },
      },
    };
    expect(apiErrorMessage(err)).toBe('field required');
  });

  it('uses the axios message when there is no response, but not the status boilerplate', () => {
    expect(apiErrorMessage({ message: 'Network Error' })).toBe('Network Error');
    expect(apiErrorMessage({ message: 'Request failed with status code 500' })).toBe('Request failed');
  });

  it('returns a default when nothing is usable', () => {
    expect(apiErrorMessage(new Error('boom'))).toBe('boom');
    expect(apiErrorMessage(null)).toBe('Request failed');
    expect(apiErrorMessage(undefined)).toBe('Request failed');
  });
});
