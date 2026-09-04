import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider } from 'antd';
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CredentialBindingView } from '../../../generated/credential-bindings/credential-bindings.schemas';
import AppsPage from '../AppsPage';

/** vitest 下 `import.meta.url` 是 jsdom 的 URL 实例，`fileURLToPath` 不认，先取 `.href`。 */
function readSource(relativePath: string): string {
  return readFileSync(
    fileURLToPath(new URL(relativePath, import.meta.url).href),
    'utf8',
  );
}

/** 2026-08-27 §九：前台不得出现这些内部对象名。 */
const FORBIDDEN_INTERNAL_TERMS = [
  'Skill',
  'Capability',
  'App',
  'capability_id',
  'input_schema',
  'intent_tags',
  'WorkCandidate',
  'Work Object',
];

/**
 * 后端**没有**这三个系统的地址，也读不到用户在那边的账号状态。它们的名字只许出现在「还没接进来」的
 * 说明里，绝不许带着一个状态标签出现在卡片上——那个标签只能是编的。
 */
const SYSTEMS_WITHOUT_A_DATA_SOURCE = ['财务系统', '公文交换平台', '督查督办系统'];

/** 状态文案闭集（2026-09-02 第二轮意见）。「未开通」当前没有任何真实来源，任何一屏都不该出现。 */
const FABRICATED_STATUS_LABELS = ['未开通'];

const apiMocks = vi.hoisted(() => ({ getBinding: vi.fn() }));

vi.mock('../../../generated/credential-bindings/credential-bindings', () => ({
  getBindingApiV1CredentialBindingsTargetSystemGet: apiMocks.getBinding,
}));

/*
 * 部署配置（OA 地址与放行前缀）是编译期注入的，测试环境里是空串。这里**不替换校验逻辑**，只把一份
 * 合成的部署配置喂给真实的 `oaWorkbenchNavigation`——origin 比对、路径前缀白名单、控制字符与
 * `..` 拒绝全部照常执行。校验被放宽或链接改成绕过投影直接拼字符串，下面的断言都会变红。
 */
const navigationConfigMock = vi.hoisted(() => ({
  current: null as { baseUrl: string; pathPrefixes: string[] } | null,
}));

vi.mock('../../../contracts/runtimeProjection', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../../contracts/runtimeProjection')>();
  return {
    ...actual,
    oaWorkbenchNavigation: () =>
      actual.oaWorkbenchNavigation(navigationConfigMock.current),
  };
});

function binding(overrides: Partial<CredentialBindingView> = {}): CredentialBindingView {
  return {
    bound: true,
    poll_failure_count: 0,
    poll_status: 'active',
    target_system: 'oa',
    updated_at: null,
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return render(
    <ConfigProvider>
      <AntApp>
        <QueryClientProvider client={client}>
          <MemoryRouter>
            <AppsPage />
          </MemoryRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>,
  );
}

function oaCard(): HTMLElement {
  return screen.getByRole('article');
}

beforeEach(() => {
  apiMocks.getBinding.mockReset();
  apiMocks.getBinding.mockResolvedValue(binding());
  navigationConfigMock.current = {
    baseUrl: 'http://oa.synthetic.invalid',
    pathPrefixes: ['/oa'],
  };
  window.localStorage.clear();
});

describe('AppsPage business systems', () => {
  it('opens the OA system in a new window through the deployment-configured deep link', async () => {
    renderPage();

    const link = await screen.findByRole('link', { name: /打开/ });
    expect(link).toHaveAttribute('href', 'http://oa.synthetic.invalid/oa');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('refuses to offer a link when the deployment has no OA address configured', async () => {
    navigationConfigMock.current = null;
    renderPage();

    expect(
      await screen.findByText(/OA 地址没配好/),
    ).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /打开/ })).not.toBeInTheDocument();
  });

  /*
   * 覆盖判据：`//elsewhere.invalid/oa` 是一条**协议相对**前缀，解析出来落在别的 origin 上。放行前缀
   * 表的校验必须拒掉整份配置、一个链接也不给。把那道校验放宽，这里就会冒出一个可点的链接 → 变红。
   *
   * 说明：本入口的路径只来自已归一化的放行前缀表，因此 `untrusted` 与 `missing` 两支在这里走不到，
   * 保留它们只为覆盖 `navigation` 联合类型的全部取值，不在此断言。
   */
  it('rejects a protocol-relative allowed prefix that would resolve to another origin', async () => {
    navigationConfigMock.current = {
      baseUrl: 'http://oa.synthetic.invalid',
      pathPrefixes: ['//elsewhere.invalid/oa'],
    };
    renderPage();

    expect(
      await screen.findByText(/OA 地址没配好/),
    ).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /打开/ })).not.toBeInTheDocument();
    expect(document.body.textContent ?? '').not.toContain('elsewhere.invalid');
  });

  it('shows the bound status beside the system name, not as a separate column', async () => {
    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId('oa-status')).toHaveTextContent('已绑定'),
    );
    expect(within(oaCard()).getByText('OA 办公系统')).toBeInTheDocument();
    expect(oaCard()).toContainElement(screen.getByTestId('oa-status'));
  });

  it.each([
    ['not bound at all', binding({ bound: false })],
    ['bound but the stored credential is rejected', binding({ poll_status: 'invalid' })],
    [
      'bound but the system now demands a captcha',
      binding({ poll_status: 'captcha_required' }),
    ],
  ])('asks the user to bind an account when %s', async (_case, view) => {
    apiMocks.getBinding.mockResolvedValue(view);
    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId('oa-status')).toHaveTextContent('要先绑账号'),
    );
    expect(screen.getByRole('link', { name: '去绑账号' })).toHaveAttribute(
      'href',
      '/admin/bindings',
    );
  });

  /* 三态齐全：读取中与读不到都不许冒充闭集里的「已绑定 / 要先绑账号 / 未开通」任何一项。 */
  it('keeps the loading state apart from a state it genuinely could not read', async () => {
    apiMocks.getBinding.mockReturnValue(new Promise(() => {}));
    renderPage();

    expect(await screen.findByTestId('oa-status')).toHaveTextContent('正在读取');
    expect(apiMocks.getBinding).toHaveBeenCalledWith('oa');
    expect(screen.queryByText('已绑定')).not.toBeInTheDocument();
    expect(screen.queryByText('要先绑账号')).not.toBeInTheDocument();
  });

  it('says it could not read the status instead of guessing one', async () => {
    apiMocks.getBinding.mockRejectedValue(new Error('network down'));
    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId('oa-status')).toHaveTextContent('读不到'),
    );
    expect(screen.getByText(/这不等于没绑上/)).toBeInTheDocument();
    expect(screen.queryByText('已绑定')).not.toBeInTheDocument();
  });
});

describe('AppsPage backend gaps', () => {
  /*
   * 承重断言：后端只有 OA 一个真实来源，所以整页**只能有一张卡**。谁把画板上那三张示意卡照抄进来
   * （连同它们编出来的「要先绑账号 / 未开通」），卡片数就变成 4 → 变红。
   */
  it('draws a card only for the one system it can actually tell the truth about', async () => {
    renderPage();
    await screen.findByTestId('oa-status');

    const cards = screen.getAllByRole('article');
    expect(cards).toHaveLength(1);
    expect(within(cards[0]!).getByText('OA 办公系统')).toBeInTheDocument();
  });

  /*
   * `owner` 在展示字段白名单里，但后端没有来源。画板上的「· 信息中心维护」是稿子里的示意值，照抄进
   * 生产就是替这个单位指定了一个负责科室。这条钉死卡片上不出现任何维护方声明。
   */
  it('claims no maintainer for the OA card, because no field supplies one', async () => {
    renderPage();
    await screen.findByTestId('oa-status');

    const card = screen.getByRole('article');
    for (const claim of ['信息中心', '维护', '值班表']) {
      expect(card).not.toHaveTextContent(claim);
    }
  });

  it('names the systems it cannot reach in prose, never with a made-up status', async () => {
    renderPage();
    await screen.findByTestId('oa-status');

    const note = screen.getByText(/还没有接进来。下一步/);
    for (const system of SYSTEMS_WITHOUT_A_DATA_SOURCE) {
      expect(note).toHaveTextContent(system);
    }
    for (const label of FABRICATED_STATUS_LABELS) {
      expect(screen.queryByText(label)).not.toBeInTheDocument();
    }
  });

  /*
   * 「单位软件」「我的功能」的数据在 `/admin/registry`（admin 上下文）。2026-08-27 §三：软件中心与
   * `/admin/registry` 不合并、权限与字段不互借。所以这两块只许如实占位，既不许借那个接口，也不许把
   * 画板上的示意条目（`任务交办 已装上 · v2.1`）当成真数据抄下来。
   */
  it('leaves the published-software and my-features sections honestly empty', async () => {
    renderPage();
    await screen.findByTestId('oa-status');

    for (const heading of ['单位软件', '我的功能']) {
      expect(screen.getByRole('heading', { level: 2, name: heading })).toBeInTheDocument();
    }
    expect(screen.getByText(/单位发布的软件还没有接进来/)).toBeInTheDocument();
    expect(screen.getByText(/你自己的功能还没有接进来/)).toBeInTheDocument();
    for (const fabricated of ['已装上', '可以装', 'v2.1', 'v1.0', '只查不改']) {
      expect(screen.queryByText(new RegExp(fabricated))).not.toBeInTheDocument();
    }
  });

  it('does not borrow the admin registry to fill this page', async () => {
    renderPage();
    await screen.findByTestId('oa-status');

    expect(
      screen.queryByRole('link', { name: /功能管理|注册表/ }),
    ).not.toBeInTheDocument();
    expect(document.body.textContent ?? '').not.toContain('/admin/registry');
  });
});

describe('AppsPage vocabulary', () => {
  it('keeps internal object names out of the user-facing copy', async () => {
    renderPage();
    await screen.findByTestId('oa-status');

    const text = document.body.textContent ?? '';
    expect(text.length).toBeGreaterThan(0);
    for (const term of FORBIDDEN_INTERNAL_TERMS) {
      expect(text).not.toContain(term);
    }
  });

  it('draws its icons as inline stroke SVG instead of text glyphs', async () => {
    const { container } = renderPage();
    await screen.findByTestId('oa-status');

    const icon = container.querySelector('svg');
    expect(icon).not.toBeNull();
    expect(icon?.getAttribute('stroke')).toBe('currentColor');
    expect(icon?.getAttribute('fill')).toBe('none');
    expect(icon?.getAttribute('aria-hidden')).toBe('true');
  });

  it('offers the create-software entry that the finalized canvas puts in the header', async () => {
    renderPage();
    await screen.findByTestId('oa-status');

    expect(screen.getByRole('button', { name: /新建应用/ })).toBeInTheDocument();
  });

  /*
   * 雨爷 2026-09-04 走查第 3 条：「整个纯白的的底，按钮在这个页面下也不明显。」这条钉死改后的两点，
   * 都会被回滚打红：
   *
   * 1. 卡片**不是纯白**——底不再是 `rgb(255 255 255 / 52%)` 那种只有白的填充，而且有一道看得见的
   *    深色描边把它从面板上分出来；
   * 2. 卡片上的动作按钮带**可辨边界 + 主题色投影**（WCAG 2.2 SC 1.4.11：阴影不能替代 3:1 的边界），
   *    不是只有一圈白色高光。
   */
  it('lifts the system card off the panel instead of leaving it white on white', () => {
    const css = readSource('../AppsPage.module.css');

    const card = /\.appCard\s*\{([^}]*)\}/.exec(css)?.[1] ?? '';
    expect(card).not.toContain('background: rgb(255 255 255 / 52%)');
    expect(card).toContain('rgb(226 234 250 / 76%)');
    expect(card).toContain('inset 0 0 0 1px rgb(22 29 46 / 14%)');

    const action = /\.openLink,\s*\.bindLink\s*\{([^}]*)\}/.exec(css)?.[1] ?? '';
    expect(action).toContain('var(--workbench-control-ring)');
    expect(action).toContain('var(--workbench-control-glow)');

    // 第一条是 `.openLink, .bindLink` 合并规则，第二条才是「去绑账号」自己的主动作规则。
    const bindRules = [...css.matchAll(/\.bindLink\s*\{([^}]*)\}/g)].map(
      (match) => match[1],
    );
    expect(bindRules).toHaveLength(2);
    expect(bindRules[1]).toContain('var(--workbench-control-ring-primary)');
    expect(bindRules[1]).toContain('var(--workbench-control-glow-strong)');
  });
});
