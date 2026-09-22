import { Component, Suspense } from 'react';
import type { ReactNode } from 'react';
import styles from './RouteLoadingFallback.module.css';

type LoadSurface = 'dock' | 'page' | 'workspace';

interface RouteLoadingFallbackProps {
  label: string;
  surface?: LoadSurface;
}

interface RouteLoadBoundaryProps extends RouteLoadingFallbackProps {
  children: ReactNode;
}

interface RouteLoadBoundaryState {
  failed: boolean;
}

function surfaceClassName(surface: LoadSurface | undefined): string {
  switch (surface) {
    case 'dock':
      return styles.dock ?? '';
    case 'workspace':
      return styles.workspace ?? '';
    default:
      return styles.page ?? '';
  }
}

/**
 * 动态模块下载时只替换它所属的内容区。回退本身不用玻璃滤镜，正文也有不透明底，避免下载态造成壳层跳动。
 */
export function RouteLoadingFallback({
  label,
  surface = 'page',
}: RouteLoadingFallbackProps) {
  return (
    <section
      aria-busy="true"
      aria-live="polite"
      className={`${styles.fallback} ${surfaceClassName(surface)}`}
      data-testid={`lazy-${surface}-loading`}
      role="status"
    >
      <span>{label}</span>
    </section>
  );
}

function RouteLoadFailure({ label, surface }: RouteLoadingFallbackProps) {
  return (
    <section
      className={`${styles.fallback} ${surfaceClassName(surface)}`}
      data-testid={`lazy-${surface}-failure`}
      role="alert"
    >
      <span>{label}加载失败。</span>
      <button className={styles.refresh} onClick={() => window.location.reload()} type="button">
        刷新
      </button>
    </section>
  );
}

class RouteLoadErrorBoundary extends Component<
  RouteLoadBoundaryProps,
  RouteLoadBoundaryState
> {
  override state: RouteLoadBoundaryState = { failed: false };

  static getDerivedStateFromError(): RouteLoadBoundaryState {
    return { failed: true };
  }

  componentDidCatch(): void {
    // 下载失败已由局部回退呈现；不要向页面注入额外的错误文本或自动重试。
  }

  override render() {
    const { children, label, surface = 'page' } = this.props;
    if (this.state.failed) {
      return <RouteLoadFailure label={label} surface={surface} />;
    }
    return children;
  }
}

/**
 * 每个懒模块使用自己的下载与失败边界。没有重试逻辑；用户只有明确的刷新出口。
 */
export function RouteLoadBoundary({
  children,
  label,
  surface = 'page',
}: RouteLoadBoundaryProps) {
  return (
    <RouteLoadErrorBoundary label={label} surface={surface}>
      <Suspense fallback={<RouteLoadingFallback label={label} surface={surface} />}>
        {children}
      </Suspense>
    </RouteLoadErrorBoundary>
  );
}
