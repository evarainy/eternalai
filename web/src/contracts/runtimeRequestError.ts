import {
  incompatibleResponse,
  projectTextResponse,
  type ProjectedResponse,
} from './runtimeProjection';

interface RuntimeRequestFailure {
  code: string;
  status: number;
}

function isRuntimeRequestFailure(error: unknown): error is RuntimeRequestFailure {
  if (typeof error !== 'object' || error === null) {
    return false;
  }
  const candidate = error as Partial<RuntimeRequestFailure>;
  return typeof candidate.code === 'string' && typeof candidate.status === 'number';
}

export function projectRequestError(error: unknown): ProjectedResponse | null {
  if (error instanceof SyntaxError) {
    return incompatibleResponse();
  }
  if (isRuntimeRequestFailure(error)) {
    if (error.status === 401) {
      return null;
    }
    if (error.status === 403 && error.code === 'csrf_validation_failed') {
      return projectTextResponse(
        '当前请求来源未通过安全校验，请联系管理员检查部署配置。',
        'csrf',
      );
    }
    if (error.status === 404) {
      return projectTextResponse(
        '当前会话不可用，请刷新页面后重试。',
        'session',
      );
    }
    if (error.status === 422) {
      return projectTextResponse(
        '请求格式未通过校验，请重新输入后再试。',
        'validation',
      );
    }
    if (error.status === 503) {
      return projectTextResponse(
        '办理服务暂时不可用，请稍后再试。',
        'service',
      );
    }
    return projectTextResponse(
      '请求未能完成，请稍后再试。',
      'request_error',
    );
  }
  return projectTextResponse('网络连接异常，请稍后再试。', 'network');
}
