import { useAuthStore } from '../../../stores/authStore';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider } from 'antd';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MockInstance } from 'vitest';
import AppsPage from '../AppsPage';

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
 * 本轮软件中心按系统显示真实账号状态；进入系统的地址仍只有 OA，其他四个进入按钮保持禁用。
 */
const OTHER_SYSTEMS = ['财务软件', '综合业务平台', '数据中台', '执法办案平台'];

/** 状态文案闭集（2026-09-02 第二轮意见）。「未开通」当前没有任何真实来源，任何一屏都不该出现。 */
const FABRICATED_STATUS_LABELS = ['未开通'];

const apiMocks = vi.hoisted(() => ({ getBinding: vi.fn(), mcpServices: vi.fn(), mcpConnections: vi.fn(), mcpOperations: vi.fn() }));
vi.mock('../../../api/mcp', () => ({
  listMcpServices: apiMocks.mcpServices, listMcpConnections: apiMocks.mcpConnections,
  listMcpOperations: apiMocks.mcpOperations,
  safeMcpLink: () => undefined,
}));

vi.mock('../../../generated/credential-bindings/credential-bindings', () => ({
  getBindingApiV1CredentialBindingsTargetSystemGet: apiMocks.getBinding,
}));

/*
 * 部署配置（OA 地址与放行前缀）是编译期注入的，测试环境里是空串。这里**不替换校验逻辑**，只把一份
 * 合成的部署配置喂给真实的 `oaWorkbenchNavigation`——origin 比对、路径前缀白名单、控制字符与
 * `..` 拒绝全部照常执行。校验被放宽或地址改成绕过投影直接拼字符串，下面的断言都会变红。
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

async function renderPage(registryItems: unknown[] = []) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  client.setQueryData(['admin', 'registry'], { items: registryItems });
  const view = render(
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
  await waitFor(() => expect(client.isFetching()).toBe(0));
  return view;
}

/** 名称下新增了账号状态，齿轮也含系统名；从精确的名称定位进入按钮。 */
function oaTile(): HTMLElement {
  return screen.getByText('OA 办公系统', { selector: 'b' }).closest('button')!;
}

/** 页面上所有系统卡片（含「添加软件」），按显示顺序；名称在进入按钮的 `<b>` 中。 */
function tileNames(): string[] {
  return screen.getAllByRole('button').flatMap((button) => {
    const name = button.querySelector('b')?.textContent;
    return name === undefined || name === null ? [] : [name];
  });
}

/** 业务系统磁贴（不含「添加软件」）。 */
function systemTiles(): HTMLElement[] {
  return screen.getAllByRole('button').filter((button) => {
    const name = button.querySelector('b')?.textContent;
    return name === 'OA 办公系统' || OTHER_SYSTEMS.includes(name ?? '');
  });
}

let openWindow: MockInstance<typeof window.open>;

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.mcpServices.mockResolvedValue([]);
  apiMocks.mcpOperations.mockResolvedValue([]);
  apiMocks.mcpConnections.mockResolvedValue([]);
  useAuthStore.getState().markAuthenticated();
  apiMocks.getBinding.mockReset().mockImplementation((target: string) => Promise.resolve({
    bound: false, poll_failure_count: 0, poll_status: 'active', target_system: target, updated_at: null,
  }));
  navigationConfigMock.current = {
    baseUrl: 'http://oa.synthetic.invalid',
    pathPrefixes: ['/oa'],
  };
  window.localStorage.clear();
  openWindow = vi.spyOn(window, 'open').mockReturnValue(null);
});

afterEach(() => vi.restoreAllMocks());

describe('AppsPage business systems', () => {
  it('opens the OA system in a new window through the deployment-configured deep link', async () => {
    await renderPage();

    const tile = oaTile();
    expect(tile).toBeEnabled();
    expect(tile).toHaveAccessibleName('OA 办公系统 未绑定');
    expect(tile).toHaveAttribute('title', '打开OA 办公系统');
    fireEvent.click(tile);
    expect(openWindow).toHaveBeenCalledExactlyOnceWith(
      'http://oa.synthetic.invalid/oa',
      '_blank',
      'noopener,noreferrer',
    );
  });

  it('refuses to offer a link when the deployment has no OA address configured', async () => {
    navigationConfigMock.current = null;
    await renderPage();

    const tile = oaTile();
    expect(tile).toBeDisabled();
    expect(tile).not.toHaveAttribute('title');
    expect(tile).toHaveAccessibleName('OA 办公系统 未绑定 入口地址未配置');
    fireEvent.click(tile);
    expect(openWindow).not.toHaveBeenCalled();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  /*
   * 覆盖判据：`//elsewhere.invalid/oa` 是一条**协议相对**前缀，解析出来落在别的 origin 上。放行前缀
   * 表的校验必须拒掉整份配置、一个地址也不给。把那道校验放宽，这里的磁贴就会变成可点 → 变红。
   */
  it('rejects a protocol-relative allowed prefix that would resolve to another origin', async () => {
    navigationConfigMock.current = {
      baseUrl: 'http://oa.synthetic.invalid',
      pathPrefixes: ['//elsewhere.invalid/oa'],
    };
    await renderPage();

    const tile = oaTile();
    expect(tile).toBeDisabled();
    expect(tile).not.toHaveAttribute('title');
    expect(tile).toHaveAccessibleName('OA 办公系统 未绑定 入口地址未配置');
    fireEvent.click(tile);
    expect(openWindow).not.toHaveBeenCalled();
    expect(document.body.textContent ?? '').not.toContain('elsewhere.invalid');
    expect(document.body.innerHTML).not.toContain('elsewhere.invalid');
  });
});

describe('AppsPage backend gaps', () => {
  it('locks the single business-systems section', async () => {
    await renderPage();
    expect(screen.getAllByRole('heading', { level: 2 }).map((node) => node.textContent))
      .toEqual(['业务系统']);
  });

  // The real user-list contract does not exist. These are admin-cache values, NOT an invented user DTO.
  it.each([
    ['draft', '1.2'], ['active', 'release-next'], ['disabled', ''], ['deprecated', '0.3'],
    ['future-status', 'future-version'], [null, null],
  ])('keeps status %s and version %s unavailable without borrowing admin data', async (status, version) => {
    const fetch = vi.spyOn(globalThis, 'fetch');
    await renderPage([{ name: '合成管理条目', status, version }]);
    expect(oaTile()).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/已发布|已装上|v1\.2|v0\.3|future-status|future-version|合成管理条目/);
    expect(apiMocks.getBinding.mock.calls).toEqual([['oa'], ['u8']]);
    expect(apiMocks.mcpServices).toHaveBeenCalledExactlyOnceWith();
    expect(apiMocks.mcpConnections).toHaveBeenCalledExactlyOnceWith();
    expect(fetch).not.toHaveBeenCalled();
  });

  /*
   * 进入地址与账号状态分别核对：只有 OA 能进入，但 OA/财务的密码绑定和业务服务配置已经有读取来源。
   */
  it('makes only the one system it can actually tell the truth about actionable', async () => {
    await renderPage();

    expect(tileNames()).toEqual(['OA 办公系统', ...OTHER_SYSTEMS, '添加软件']);
    const tiles = systemTiles();
    expect(tiles).toHaveLength(1 + OTHER_SYSTEMS.length);
    expect(tiles.filter((tile) => !(tile as HTMLButtonElement).disabled)).toEqual([oaTile()]);
    const statuses = ['未绑定', '未绑定', '管理员还没有配置', '待接入', '待接入'];
    tiles.forEach((tile, index) => {
      expect(within(tile).getByText(statuses[index]!, { exact: true })).toBeInTheDocument();
      expect(tile.querySelectorAll('[data-state]')).toHaveLength(1);
    });
    for (const tile of tiles.slice(1)) {
      expect(tile).toBeDisabled();
      fireEvent.click(tile);
    }
    expect(openWindow).not.toHaveBeenCalled();
  });

  /*
   * `owner` 在展示字段白名单里，但后端没有来源。画板上的「· 信息中心维护」是稿子里的示意值，照抄进
   * 生产就是替这个单位指定了一个负责科室。这条钉死磁贴上不出现任何维护方声明。
   */
  it('claims no maintainer for the OA tile, because no field supplies one', async () => {
    await renderPage();

    const tile = oaTile();
    for (const claim of ['信息中心', '维护', '值班表']) {
      expect(tile).not.toHaveTextContent(claim);
    }
  });

  it('reports only account statuses backed by the binding responses', async () => {
    apiMocks.getBinding.mockImplementation((target: string) => Promise.resolve({
      bound: true, poll_failure_count: 0, poll_status: target === 'oa' ? 'invalid' : 'active',
      target_system: target, updated_at: null,
    }));
    await renderPage();
    expect(oaTile()).toBeInTheDocument();

    for (const label of FABRICATED_STATUS_LABELS) {
      expect(screen.queryByText(label)).not.toBeInTheDocument();
    }
    for (const label of ['要先绑账号', '正在读取', '读不到']) {
      expect(document.body.textContent).not.toContain(label);
    }
    expect(oaTile()).toHaveAccessibleName('OA 办公系统 密码已失效');
    expect(oaTile().querySelector('[data-state]')).toHaveAttribute('data-state', 'alert');
    const finance = systemTiles()[1]!;
    expect(finance).toHaveAccessibleName('财务软件 已保存，接入后验证');
    expect(finance.querySelector('[data-state]')).toHaveAttribute('data-state', 'on');
  });

  /*
   * 单位软件与个人功能的数据在 `/admin/registry`（admin 上下文）。2026-08-27 §三：软件中心与
   * `/admin/registry` 不合并、权限与字段不互借。所以本页不许把画板上的示意条目
   * （`任务交办 已装上 · v2.1`）当成真数据抄下来。
   */
  it('shows no fabricated published-software or my-feature entries', async () => {
    await renderPage();
    expect(oaTile()).toBeInTheDocument();

    for (const fabricated of ['已装上', '可以装', 'v2.1', 'v1.0', '只查不改']) {
      expect(screen.queryByText(new RegExp(fabricated))).not.toBeInTheDocument();
    }
  });

  it('does not borrow the admin registry to fill this page', async () => {
    await renderPage();
    expect(oaTile()).toBeInTheDocument();

    expect(
      screen.queryByRole('link', { name: /功能管理|注册表/ }),
    ).not.toBeInTheDocument();
    expect(document.body.textContent ?? '').not.toContain('/admin/registry');
  });
});

describe('AppsPage vocabulary', () => {
  it.each(['configured', 'unconfigured'] as const)(
    'keeps internal terms out of text and accessible names when OA is %s and in its dialog', async (state) => {
      if (state === 'unconfigured') navigationConfigMock.current = null;
      await renderPage();
      expect(oaTile()).toHaveAccessibleName(state === 'configured'
        ? 'OA 办公系统 未绑定' : 'OA 办公系统 未绑定 入口地址未配置');
      expect(oaTile()).toHaveProperty('disabled', state === 'unconfigured');
      const checkCopy = () => {
        const text = [document.body.textContent, ...Array.from(
          document.body.querySelectorAll('[aria-label], [title]'),
          (node) => `${node.getAttribute('aria-label') ?? ''} ${node.getAttribute('title') ?? ''}`,
        )].join(' ');
        for (const term of FORBIDDEN_INTERNAL_TERMS) expect(text).not.toContain(term);
      };
      checkCopy();
      fireEvent.click(screen.getByRole('button', { name: '添加软件' }));
      await waitFor(() => expect(screen.getByRole('dialog', { name: '新建应用' })).toBeVisible());
      checkCopy();
    },
  );

  it('uses an aria-hidden OA stamp and inline stroke SVG for its add icon', async () => {
    await renderPage();

    const stamp = within(oaTile()).getByText('文');
    expect(stamp).toHaveAttribute('aria-hidden', 'true');
    const icon = screen.getByRole('button', { name: '添加软件' }).querySelector('svg');
    expect(icon).not.toBeNull();
    expect(icon?.getAttribute('stroke')).toBe('currentColor');
    expect(icon?.getAttribute('fill')).toBe('none');
    expect(icon?.getAttribute('aria-hidden')).toBe('true');
  });

  it('offers the add-software entry among the system tiles', async () => {
    await renderPage();

    expect(screen.getByRole('button', { name: '添加软件' })).toBeInTheDocument();
  });
});

it('reopens_a_clean_software_dialog_after_authentication_changes', async () => {
  await renderPage();
  expect(oaTile()).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '添加软件' }));
  fireEvent.change(screen.getByLabelText('叫什么名字'), { target: { value: 'A-private' } });
  fireEvent.click(screen.getByRole('button', { name: '存草稿' }));
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  act(() => useAuthStore.getState().markAuthenticated());
  fireEvent.click(screen.getByRole('button', { name: '添加软件' }));
  await waitFor(() => expect(screen.getByRole('dialog')).toBeVisible());
  expect(screen.getByLabelText('叫什么名字')).toHaveValue('');
  expect(screen.queryByRole('status')).toBeNull();
  expect(oaTile()).toHaveTextContent('OA');
});
