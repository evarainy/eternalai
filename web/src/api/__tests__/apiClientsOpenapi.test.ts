import { spawnSync } from 'node:child_process';
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join, relative, resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { ApiError, customInstance } from '../mutator';
import { UserActionOutcome as GeneratedOutcomes } from '../../generated/runtime/runtime.schemas';
import { TaskStatus as GeneratedTaskStatuses } from '../../generated/admin/admin.schemas';
import { USER_ACTION_OUTCOMES } from '../../contracts/userActionOutcome';
import { listWorkObjectsApiV1WorkObjectsGet } from '../../generated/work-objects/work-objects';
import { useAuthStore } from '../../stores/authStore';

interface OpenApiSchema {
  format?: string;
  properties?: Record<string, OpenApiSchema>;
  writeOnly?: boolean;
}

interface OpenApiOperation {
  operationId?: string;
  parameters?: Array<{ name: string; in: string; required?: boolean; schema?: unknown }>;
  responses?: Record<string, unknown>;
  requestBody?: {
    content?: {
      'application/json'?: {
        schema?: OpenApiSchema;
      };
    };
  };
}

interface OpenApiDocument {
  paths: Record<string, Record<string, OpenApiOperation>>;
}

const PROJECTS = [
  {
    project: 'mcp', input: './openapi/mcp.openapi.json', target: './src/generated/mcp/mcp.ts',
    operations: [
      { path: '/api/v1/mcp/connections', method: 'get', operationId: 'connections_api_v1_mcp_connections_get' },
      { path: '/api/v1/mcp/connections/{connection_id}/disconnect', method: 'post', operationId: 'disconnect_api_v1_mcp_connections__connection_id__disconnect_post' },
      { path: '/api/v1/mcp/connections/{service_config_id}/authorize', method: 'post', operationId: 'authorize_api_v1_mcp_connections__service_config_id__authorize_post' },
      { path: '/api/v1/mcp/oauth/callback', method: 'get', operationId: 'callback_api_v1_mcp_oauth_callback_get' },
      { path: '/api/v1/mcp/operations', method: 'get', operationId: 'operations_api_v1_mcp_operations_get' },
      { path: '/api/v1/mcp/operations/{operation_id}', method: 'get', operationId: 'operation_api_v1_mcp_operations__operation_id__get' },
      { path: '/api/v1/mcp/operations/{operation_id}/resume', method: 'post', operationId: 'resume_api_v1_mcp_operations__operation_id__resume_post' },
      { path: '/api/v1/mcp/services', method: 'get', operationId: 'services_api_v1_mcp_services_get' },
    ],
  },
  {
    project: 'auth',
    input: './openapi/auth.openapi.json',
    target: './src/generated/auth/auth.ts',
    operations: [
      {
        path: '/api/v1/auth/login',
        method: 'post',
        operationId: 'login_api_v1_auth_login_post',
      },
      { path: '/api/v1/auth/logout', method: 'post', operationId: 'logout_api_v1_auth_logout_post' },
      { path: '/api/v1/auth/renew', method: 'post', operationId: 'renew_api_v1_auth_renew_post' },
      { path: '/api/v1/auth/session', method: 'get', operationId: 'session_api_v1_auth_session_get' },
    ],
  },
  {
    project: 'runtime',
    input: './openapi/runtime.openapi.json',
    target: './src/generated/runtime/runtime.ts',
    operations: [
      {
        path: '/api/v1/runtime/action',
        method: 'post',
        operationId: 'handle_action_api_v1_runtime_action_post',
      },
      {
        path: '/api/v1/runtime/handle',
        method: 'post',
        operationId: 'handle_api_v1_runtime_handle_post',
      },
    ],
  },
  {
    project: 'adminTrace',
    input: './openapi/admin-trace.openapi.json',
    target: './src/generated/admin-trace/admin-trace.ts',
    operations: [
      {
        path: '/api/v1/admin/traces',
        method: 'get',
        operationId: 'list_traces_api_v1_admin_traces_get',
      },
    ],
  },
  {
    project: 'workObjects',
    input: './openapi/work-objects.openapi.json',
    target: './src/generated/work-objects/work-objects.ts',
    operations: [
      { path: '/api/v1/work-object-series', method: 'get', operationId: 'list_series_api_v1_work_object_series_get' },
      { path: '/api/v1/work-object-series', method: 'post', operationId: 'create_series_api_v1_work_object_series_post' },
      { path: '/api/v1/work-object-series/{series_id}', method: 'get', operationId: 'get_series_api_v1_work_object_series__series_id__get' },
      { path: '/api/v1/work-object-series/{series_id}', method: 'put', operationId: 'update_series_api_v1_work_object_series__series_id__put' },
      { path: '/api/v1/work-object-series/{series_id}/disable', method: 'post', operationId: 'disable_series_api_v1_work_object_series__series_id__disable_post' },
      { path: '/api/v1/work-object-series/{series_id}/enable', method: 'post', operationId: 'enable_series_api_v1_work_object_series__series_id__enable_post' },
      { path: '/api/v1/work-object-series/{series_id}/history', method: 'get', operationId: 'series_history_api_v1_work_object_series__series_id__history_get' },
      { path: '/api/v1/work-object-series/{series_id}/occurrences', method: 'get', operationId: 'series_occurrences_api_v1_work_object_series__series_id__occurrences_get' },
      { path: '/api/v1/work-objects', method: 'get', operationId: 'list_work_objects_api_v1_work_objects_get' },
      { path: '/api/v1/work-objects/attachments', method: 'post', operationId: 'upload_attachment_api_v1_work_objects_attachments_post' },
      { path: '/api/v1/work-objects/dispatch', method: 'post', operationId: 'dispatch_work_objects_api_v1_work_objects_dispatch_post' },
      { path: '/api/v1/work-objects/dispatch-options', method: 'get', operationId: 'list_dispatch_options_api_v1_work_objects_dispatch_options_get' },
      { path: '/api/v1/work-objects/sync', method: 'post', operationId: 'sync_work_objects_api_v1_work_objects_sync_post' },
      { path: '/api/v1/work-objects/{work_object_id}', method: 'get', operationId: 'get_work_object_api_v1_work_objects__work_object_id__get' },
      { path: '/api/v1/work-objects/{work_object_id}/attachments', method: 'get', operationId: 'list_attachments_api_v1_work_objects__work_object_id__attachments_get' },
      { path: '/api/v1/work-objects/{work_object_id}/attachments', method: 'post', operationId: 'link_attachments_api_v1_work_objects__work_object_id__attachments_post' },
      { path: '/api/v1/work-objects/{work_object_id}/attachments/{attachment_id}', method: 'delete', operationId: 'detach_attachment_api_v1_work_objects__work_object_id__attachments__attachment_id__delete' },
      { path: '/api/v1/work-objects/{work_object_id}/attachments/{attachment_id}/content', method: 'get', operationId: 'download_attachment_api_v1_work_objects__work_object_id__attachments__attachment_id__content_get' },
      { path: '/api/v1/work-objects/{work_object_id}/handling-mark', method: 'patch', operationId: 'set_work_object_handling_mark_api_v1_work_objects__work_object_id__handling_mark_patch' },
      { path: '/api/v1/work-objects/{work_object_id}/lifecycle', method: 'get', operationId: 'get_work_object_lifecycle_api_v1_work_objects__work_object_id__lifecycle_get' },
      { path: '/api/v1/work-objects/{work_object_id}/lifecycle/commands', method: 'post', operationId: 'command_work_object_lifecycle_api_v1_work_objects__work_object_id__lifecycle_commands_post' },
      { path: '/api/v1/work-objects/{work_object_id}/lifecycle/events', method: 'get', operationId: 'list_work_object_lifecycle_events_api_v1_work_objects__work_object_id__lifecycle_events_get' },
      { path: '/api/v1/work-objects/{work_object_id}/remarks', method: 'get', operationId: 'get_remark_api_v1_work_objects__work_object_id__remarks_get' },
      { path: '/api/v1/work-objects/{work_object_id}/remarks', method: 'patch', operationId: 'edit_remark_api_v1_work_objects__work_object_id__remarks_patch' },
      { path: '/api/v1/work-objects/{work_object_id}/remarks/history', method: 'get', operationId: 'list_remark_history_api_v1_work_objects__work_object_id__remarks_history_get' },
    ],
  },
  {
    project: 'me',
    input: './openapi/me.openapi.json',
    target: './src/generated/me/me.ts',
    operations: [
      {
        path: '/api/v1/me',
        method: 'get',
        operationId: 'read_me_api_v1_me_get',
      },
      {
        path: '/api/v1/me/avatar',
        method: 'get',
        operationId: 'read_avatar_api_v1_me_avatar_get',
      },
    ],
  },
  {
    project: 'credentialBindings',
    input: './openapi/credential-bindings.openapi.json',
    target: './src/generated/credential-bindings/credential-bindings.ts',
    operations: [
      {
        path: '/api/v1/credential-bindings/{target_system}',
        method: 'get',
        operationId:
          'get_binding_api_v1_credential_bindings__target_system__get',
      },
      {
        path: '/api/v1/credential-bindings/{target_system}',
        method: 'put',
        operationId:
          'bind_password_api_v1_credential_bindings__target_system__put',
      },
      {
        path: '/api/v1/credential-bindings/{target_system}',
        method: 'delete',
        operationId:
          'unbind_password_api_v1_credential_bindings__target_system__delete',
      },
    ],
  },
] as const;

const ADMIN_PROJECT = {
  project: 'admin',
  input: './openapi/admin.openapi.json',
  target: './src/generated/admin/admin.ts',
} as const;

const CLIENT_PROJECTS = [...PROJECTS, ADMIN_PROJECT] as const;

function project(name: (typeof PROJECTS)[number]['project']) {
  const found = PROJECTS.find((candidate) => candidate.project === name);
  if (found === undefined) {
    throw new Error(`Unknown OpenAPI project: ${name}`);
  }
  return found;
}

const EXPORT_SCRIPT = String.raw`
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any
from fastapi import FastAPI
from app.api.v1.admin import make_router as admin_router
from app.api.v1.auth import make_router as auth_router, make_require_principal
from app.api.v1.csrf import make_require_csrf, make_csrf_protected_principal
from app.api.v1.credential_bindings import make_router as bindings_router
from app.api.v1.mcp import make_router as mcp_router
from app.api.v1.me import make_router as me_router
from app.api.v1.runtime import make_router as runtime_router
from app.api.v1.work_objects import make_router as work_router
from app.api.v1.work_object_series import make_router as series_router


def create_app(*, work_object_service=None, session_tokens=None, session_revocations=None,
               session_binder=None, session_cookie_ttl_seconds=3600,
               csrf_allowed_origins=("https://testserver",)):
    # Same HTTP factories and dependency signature, with no production composition,
    # settings, background jobs, database engine, or application lifespan.
    app = FastAPI(title="EternalAI", version="0.1.0")
    principal = make_require_principal(session_tokens, session_revocations)
    csrf = make_require_csrf(csrf_allowed_origins)
    protected = make_csrf_protected_principal(principal, csrf)
    app.include_router(mcp_router(None, protected), prefix="/api/v1/mcp")
    app.include_router(auth_router(None, session_tokens, require_csrf=csrf,
        session_revocations=session_revocations, session_cookie_ttl_seconds=session_cookie_ttl_seconds,
        session_cookie_secure=False), prefix="/api/v1/auth")
    app.include_router(runtime_router(None, protected, session_binder), prefix="/api/v1/runtime")
    app.include_router(admin_router(None, protected), prefix="/api/v1/admin")
    app.include_router(work_router(work_object_service, protected), prefix="/api/v1/work-objects")
    app.include_router(series_router(None, protected), prefix="/api/v1/work-object-series")
    app.include_router(me_router(None, protected), prefix="/api/v1/me")
    app.include_router(bindings_router(None, protected), prefix="/api/v1/credential-bindings")
    return app


def component_refs(value: Any) -> set[tuple[str, str]]:
    refs: set[tuple[str, str]] = set()
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/"):
            parts = ref.removeprefix("#/components/").split("/", 1)
            if len(parts) == 2:
                name = parts[1].replace("~1", "/").replace("~0", "~")
                refs.add((parts[0], name))
        for child in value.values():
            refs.update(component_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(component_refs(child))
    return refs


def focused_document(
    full: dict[str, Any],
    operations: list[dict[str, str]],
) -> dict[str, Any]:
    paths: dict[str, dict[str, Any]] = {}
    for operation in operations:
        path = operation["path"]
        method = operation["method"]
        paths.setdefault(path, {})[method] = copy.deepcopy(
            full["paths"][path][method]
        )
    pending = list(component_refs(paths))
    required: set[tuple[str, str]] = set()
    while pending:
        section, name = pending.pop()
        if (section, name) in required:
            continue
        component = full["components"][section][name]
        required.add((section, name))
        pending.extend(component_refs(component) - required)

    document = {
        key: copy.deepcopy(value)
        for key, value in full.items()
        if key not in {"paths", "components"}
    }
    document["paths"] = paths
    if required:
        components: dict[str, dict[str, Any]] = {}
        for section, name in sorted(required):
            components.setdefault(section, {})[name] = copy.deepcopy(
                full["components"][section][name]
            )
        document["components"] = components
    return document


output_dir = Path(sys.argv[1])
targets = json.loads(sys.argv[2])
output_dir.mkdir(parents=True, exist_ok=True)
full_schema = create_app().openapi()
for target in targets:
    payload = json.dumps(
        focused_document(full_schema, target["operations"]),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    (output_dir / target["filename"]).write_text(
        payload,
        encoding="utf-8",
        newline="\n",
    )
`;

const webRoot = process.cwd();
const repositoryRoot = resolve(webRoot, '..');
const mutatorPath = './src/api/mutator.ts';
const python = resolve(repositoryRoot, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');

it('maps actual completion 422 to ApiError', async () => {
  const script = EXPORT_SCRIPT.split('output_dir = Path(sys.argv[1])')[0] + String.raw`
from fastapi.testclient import TestClient
from tests.auth_fakes import StaticSessionTokens, MemorySessionRevocations, auth_cookies, make_session_binder

with TestClient(create_app(
    session_tokens=StaticSessionTokens(roles=("user",)),
    session_revocations=MemorySessionRevocations(), session_binder=make_session_binder(),
    session_cookie_ttl_seconds=3600, csrf_allowed_origins=("https://testserver",),
), base_url="https://testserver") as client:
    client.cookies.update(auth_cookies())
    response = client.get("/api/v1/work-objects", params={"completion": "SYNTHETIC_UNKNOWN"})
    print(json.dumps({"status": response.status_code, "body": response.json()}))
`;
  const result = spawnSync(python,
    ['-c', script],
    { cwd: repositoryRoot, encoding: 'utf8', env: process.env, windowsHide: true });
  expect(result.error).toBeUndefined();
  expect(result.status, result.stderr).toBe(0);
  const response: { status: number; body: unknown } = JSON.parse(result.stdout);
  expect(response.status).toBe(422);
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(response.body), {
    status: response.status, headers: { 'Content-Type': 'application/json' },
  }));
  const previousAuth = useAuthStore.getState();
  useAuthStore.getState().markAuthenticated();
  const generation = useAuthStore.getState().generation;
  vi.stubGlobal('fetch', fetchMock);
  try {
    // Deliberately send an invalid runtime value through the generated client.
    await expect(listWorkObjectsApiV1WorkObjectsGet({ completion: 'SYNTHETIC_UNKNOWN' as 'active' })).rejects.toEqual(
      expect.objectContaining<ApiError>({
        name: 'ApiError', status: 422, code: 'work_object_lifecycle_request_invalid',
        message: 'Invalid work object lifecycle request.',
      }),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(useAuthStore.getState()).toMatchObject({ status: 'authenticated', generation });
  } finally {
    vi.unstubAllGlobals();
    useAuthStore.setState(previousAuth);
  }
}, 60_000);

it('classifies the actual Work Object directory failure by its backend code', async () => {
  const script = EXPORT_SCRIPT.split('output_dir = Path(sys.argv[1])')[0] + String.raw`
from fastapi.testclient import TestClient
from app.api.v1.work_objects import WorkObjectService
from app.ports.auth import Principal, PrincipalOrgContext
from tests.auth_fakes import StaticSessionTokens, MemorySessionRevocations, auth_cookies, make_session_binder

class FailedDirectory:
    async def read_view(self):
        raise RuntimeError("synthetic directory unavailable")

tokens = StaticSessionTokens(roles=("user",))
tokens.principal = Principal(
    ai_user_id="ai-synthetic", display_name="Synthetic", roles=("user",),
    org_ctx=PrincipalOrgContext(tenant_id="default", directory_user_id="synthetic-key"),
)
service = WorkObjectService(
    store=object(), gateway=object(),
    capability_registry=object(), organization_directory=FailedDirectory(),
)
with TestClient(create_app(
    work_object_service=service, session_tokens=tokens, session_revocations=MemorySessionRevocations(), session_binder=make_session_binder(),
    session_cookie_ttl_seconds=3600, csrf_allowed_origins=("https://testserver",),
), base_url="https://testserver") as client:
    client.cookies.update(auth_cookies())
    response = client.get("/api/v1/work-objects")
    print(json.dumps({"status": response.status_code, "body": response.json()}))
`;
  const result = spawnSync(python,
    ['-c', script],
    { cwd: repositoryRoot, encoding: 'utf8', env: process.env, windowsHide: true });
  expect(result.error).toBeUndefined();
  expect(result.status, result.stderr).toBe(0);
  const response: { status: number; body: unknown } = JSON.parse(result.stdout);
  expect(response.status).toBe(503);
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(response.body), {
    status: response.status,
    headers: { 'Content-Type': 'application/json' },
  }));
  vi.stubGlobal('fetch', fetchMock);
  try {
    await expect(customInstance({ url: '/api/v1/work-objects', method: 'GET' })).rejects.toEqual(
      expect.objectContaining<ApiError>({
        name: 'ApiError', status: 503, code: 'organization_directory_unavailable',
        message: 'Organization directory is unavailable.',
      }),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  } finally {
    vi.unstubAllGlobals();
  }
}, 60_000);

function run(command: string, args: string[], cwd: string): void {
  const result = spawnSync(command, args, {
    cwd,
    encoding: 'utf8',
    env: process.env,
    windowsHide: true,
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(
      [
        `Command failed (${result.status ?? 'no status'}): ${command} ${args.join(' ')}`,
        result.stdout,
        result.stderr,
      ].join('\n'),
    );
  }
}

function relativeFiles(root: string, current = root): string[] {
  return readdirSync(current, { withFileTypes: true })
    .flatMap((entry) => {
      const absolute = join(current, entry.name);
      if (entry.isDirectory()) {
        return relativeFiles(root, absolute);
      }
      return [relative(root, absolute).replace(/\\/g, '/')];
    })
    .sort();
}

function removeExportTemporaryRoot(root: string) {
  const resolvedRoot = realpathSync(root);
  if (dirname(resolvedRoot) !== realpathSync(tmpdir()) || !basename(resolvedRoot).startsWith('eternalai-openapi-')) {
    throw new Error('Unexpected OpenAPI temporary directory');
  }
  rmSync(resolvedRoot, { recursive: true, force: true });
}

function readOpenApi(path: string): OpenApiDocument {
  return JSON.parse(readFileSync(path, 'utf8')) as OpenApiDocument;
}

describe('FastAPI-derived Orval clients', () => {
  it('declares_lifecycle_headers_errors_and_closed_command_contract', () => {
    const document = JSON.parse(readFileSync(resolve(webRoot, 'openapi/work-objects.openapi.json'), 'utf8'));
    const base = '/api/v1/work-objects/{work_object_id}/lifecycle';
    const command = document.paths[`${base}/commands`]!.post!;
    expect(command.parameters?.filter((parameter: { in: string }) => parameter.in === 'header')).toEqual([
      { name: 'Idempotency-Key', in: 'header', required: true, schema: { format: 'uuid', type: 'string' } },
      { name: 'If-Match', in: 'header', required: true, schema: { pattern: '"wolc-[0-9a-f]{64}"', type: 'string' } },
    ]);
    for (const [path, method] of [
      ['/api/v1/work-object-series/{series_id}', 'put'],
      ['/api/v1/work-object-series/{series_id}/enable', 'post'],
      ['/api/v1/work-object-series/{series_id}/disable', 'post'],
    ] as const) {
      expect(document.paths[path]![method]!.parameters).toContainEqual({
        name: 'If-Match', in: 'header', required: true,
        schema: { pattern: '"wosr-[0-9a-f]{64}"', type: 'string' },
      });
    }
    const commandModels = ['AssignCommand', 'AcceptCommand', 'FeedbackCommand', 'SubmitCommand', 'CompleteCommand', 'DepartmentReviewCommand', 'SubmitSummaryCommand', 'ConfirmCommand', 'ReturnCommand', 'ReviseCommand', 'WithdrawCommand', 'ExtendCommand', 'ReassignCommand'];
    expect(command.requestBody?.content?.['application/json']?.schema).toMatchObject({
      discriminator: { propertyName: 'operation' },
      oneOf: commandModels.map((name) => ({ $ref: `#/components/schemas/${name}` })),
    });
    for (const name of commandModels) expect(document.components.schemas[name].additionalProperties).toBe(false);
    expect(document.components.schemas.AcceptCommand).toMatchObject({ properties: { operation: { const: 'accept' }, handling_relation_id: { format: 'uuid' } }, required: ['operation', 'handling_relation_id'] });
    expect(document.components.schemas.FeedbackCommand).toMatchObject({ properties: { operation: { const: 'feedback' }, text: { minLength: 1, maxLength: 2000 } }, required: ['operation', 'handling_relation_id', 'text'] });
    expect(document.components.schemas.CompleteCommand).toMatchObject({ properties: { operation: { const: 'complete' }, text: { minLength: 1, maxLength: 2000 } }, required: ['operation', 'text'] });
    for (const operation of [document.paths[base]!.get!, command, document.paths[`${base}/events`]!.get!]) {
      expect(Object.keys(operation.responses!)).toEqual(['200', '401', '403', '404', '409', '412', '413', '422', '428', '503']);
      for (const response of Object.values(operation.responses!)) expect(response).toMatchObject({ headers: { 'Cache-Control': { schema: { const: 'no-store' } } } });
    }
    expect(document.paths[base]!.get!.responses!['200']).toMatchObject({ headers: { ETag: { schema: { pattern: '"wolc-[0-9a-f]{64}"' } } } });
    expect(document.paths['/api/v1/work-objects']!.get!.parameters).toContainEqual(expect.objectContaining({ name: 'completion', in: 'query' }));
    expect(document.paths['/api/v1/work-objects']!.get!.responses!['422']).toEqual({
      description: 'Invalid completion uses WorkObjectError without input echo and with Cache-Control: no-store. Other query validation retains HTTPValidationError.',
      content: { 'application/json': { schema: { oneOf: [
        { $ref: '#/components/schemas/WorkObjectError' },
        { $ref: '#/components/schemas/HTTPValidationError' },
      ] } } },
      headers: { 'Cache-Control': {
        description: 'Present on fixed completion validation failures.',
        schema: { type: 'string', const: 'no-store' },
      } },
    });
  });
  it(
    're-exports seven FastAPI specs, copies curated Admin, and regenerates byte-identical clients',
    () => {
      const temporaryRoot = mkdtempSync(join(tmpdir(), 'eternalai-openapi-'));
      const temporaryWeb = join(temporaryRoot, 'web');
      const temporaryOpenApi = join(temporaryWeb, 'openapi');

      try {
        const configSource = readFileSync(resolve(webRoot, 'orval.config.ts'), 'utf8');
        for (const target of CLIENT_PROJECTS) {
          expect(configSource).toContain(`  ${target.project}: {`);
          expect(configSource).toContain(`input: '${target.input}'`);
          expect(configSource).toContain(`target: '${target.target}'`);
        }
        expect(configSource.match(/path: '\.\/src\/api\/mutator\.ts'/g)).toHaveLength(9);
        expect(configSource.match(/name: 'customInstance'/g)).toHaveLength(9);

        const exportTargets = PROJECTS.map((target) => ({
          filename: basename(target.input),
          operations: target.operations,
        }));

        run(
          python,
          [
            '-c',
            EXPORT_SCRIPT,
            temporaryOpenApi,
            JSON.stringify(exportTargets),
          ],
          repositoryRoot,
        );

        const temporaryAdminSpec = resolve(temporaryWeb, ADMIN_PROJECT.input);
        mkdirSync(dirname(temporaryAdminSpec), { recursive: true });
        copyFileSync(resolve(webRoot, ADMIN_PROJECT.input), temporaryAdminSpec);

        for (const target of PROJECTS) {
          const trackedSpec = resolve(webRoot, target.input);
          const regeneratedSpec = resolve(temporaryWeb, target.input);
          expect(readFileSync(regeneratedSpec), target.project).toEqual(readFileSync(trackedSpec));

          const document = readOpenApi(regeneratedSpec);
          expect(Object.keys(document.paths)).toEqual(
            [...new Set(target.operations.map((operation) => operation.path))],
          );
          for (const operation of target.operations) {
            expect(document.paths[operation.path]?.[operation.method]?.operationId).toBe(
              operation.operationId,
            );
          }
        }

        const authProject = project('auth');
        const authDocument = readOpenApi(resolve(temporaryWeb, authProject.input));
        const loginSchema =
          authDocument.paths[authProject.operations[0].path]?.post?.requestBody?.content?.[
            'application/json'
          ]?.schema;
        expect(loginSchema?.properties?.loginid).toMatchObject({
          format: 'password',
          writeOnly: true,
        });
        expect(loginSchema?.properties?.userpassword).toMatchObject({
          format: 'password',
          writeOnly: true,
        });

        const bindingProject = project('credentialBindings');
        const bindingDocument = readOpenApi(
          resolve(temporaryWeb, bindingProject.input),
        );
        const bindingSchema =
          bindingDocument.paths['/api/v1/credential-bindings/{target_system}']?.put
            ?.requestBody
            ?.content?.['application/json']?.schema;
        expect(bindingSchema?.properties?.login_id).toMatchObject({
          format: 'password',
          writeOnly: true,
        });
        expect(bindingSchema?.properties?.password).toMatchObject({
          format: 'password',
          writeOnly: true,
        });

        const temporaryMutator = resolve(temporaryWeb, mutatorPath);
        mkdirSync(dirname(temporaryMutator), { recursive: true });
        copyFileSync(resolve(webRoot, mutatorPath), temporaryMutator);

        const temporaryNormalizer = join(
          temporaryWeb,
          'scripts',
          'normalize-generated.mjs',
        );
        mkdirSync(dirname(temporaryNormalizer), { recursive: true });
        copyFileSync(
          resolve(webRoot, 'scripts/normalize-generated.mjs'),
          temporaryNormalizer,
        );
        copyFileSync(
          resolve(webRoot, 'package.json'),
          resolve(temporaryWeb, 'package.json'),
        );

        const temporaryConfig = Object.fromEntries(
          CLIENT_PROJECTS.map((target) => {
            return [
              target.project,
              {
                input: resolve(temporaryWeb, target.input),
                output: {
                  ...(target.project === 'workObjects' ? { headers: true } : {}),
                  mode: 'split',
                  target: resolve(temporaryWeb, target.target),
                  mock: false,
                  override: {
                    mutator: {
                      path: resolve(temporaryWeb, mutatorPath),
                      name: 'customInstance',
                    },
                  },
                },
              },
            ];
          }),
        );
        const temporaryConfigPath = join(temporaryWeb, 'orval.config.mjs');
        writeFileSync(
          temporaryConfigPath,
          `export default ${JSON.stringify(temporaryConfig, null, 2)};\n`,
          'utf8',
        );

        run(
          process.execPath,
          [
            resolve(webRoot, 'node_modules/orval/dist/bin/orval.js'),
            '--config',
            temporaryConfigPath,
          ],
          webRoot,
        );
        run(process.execPath, [temporaryNormalizer], temporaryWeb);

        for (const target of CLIENT_PROJECTS) {
          const trackedDirectory = dirname(resolve(webRoot, target.target));
          const regeneratedDirectory = dirname(resolve(temporaryWeb, target.target));
          const trackedFiles = relativeFiles(trackedDirectory);
          expect(relativeFiles(regeneratedDirectory)).toEqual(trackedFiles);
          for (const file of trackedFiles) {
            expect(readFileSync(join(regeneratedDirectory, file)), `${target.project}/${file}`).toEqual(
              readFileSync(join(trackedDirectory, file)),
            );
          }
        }
      } finally {
        removeExportTemporaryRoot(temporaryRoot);
      }
    },
    120_000,
  );
});

it('keeps generated terminal contracts and the discriminated actions in sync', () => {
  const runtime = JSON.parse(readFileSync(resolve(webRoot, 'openapi/runtime.openapi.json'), 'utf8'));
  const admin = JSON.parse(readFileSync(resolve(webRoot, 'openapi/admin.openapi.json'), 'utf8'));
  const trace = JSON.parse(readFileSync(resolve(webRoot, 'openapi/admin-trace.openapi.json'), 'utf8'));
  expect(runtime.components.schemas.UserAction).toEqual({
    discriminator: { propertyName: 'action_type', mapping: {
      confirm: '#/components/schemas/ConfirmUserAction',
      reject: '#/components/schemas/RejectUserAction',
      cancel: '#/components/schemas/CancelUserAction',
    } },
    oneOf: [
      { $ref: '#/components/schemas/ConfirmUserAction' },
      { $ref: '#/components/schemas/RejectUserAction' },
      { $ref: '#/components/schemas/CancelUserAction' },
    ],
  });
  expect(runtime.components.schemas.ConfirmUserAction.required).toEqual(['action_type', 'response_id', 'confirmed']);
  for (const name of ['RejectUserAction', 'CancelUserAction']) {
    expect(runtime.components.schemas[name].required).toEqual(['action_type', 'response_id']);
    expect(runtime.components.schemas[name].additionalProperties).toBe(false);
    expect(runtime.components.schemas[name].properties).not.toHaveProperty('confirmed');
  }
  expect(runtime.components.schemas.UserActionOutcome.enum).toEqual(Object.values(GeneratedOutcomes));
  expect(new Set(runtime.components.schemas.UserActionOutcome.enum)).toEqual(new Set(USER_ACTION_OUTCOMES));
  expect(admin.components.schemas.TaskStatus.enum).toEqual(Object.values(GeneratedTaskStatuses));
  expect(runtime.components.schemas.ResponseEnvelope.properties.status.enum).toContain('cancelled');
  expect(runtime.components.schemas.ResponseEnvelope.properties.status.enum).toContain('confirmation_invalidated');
  expect(trace.components.schemas.AdminTracePersistedView.properties.event_type.enum).toContain('task_cancelled');
  expect(trace.components.schemas.AdminTracePersistedView.properties.event_type.enum).toContain('task_confirmation_invalidated');
});


it('requires OA observations and batch metadata and keeps active as the list default', () => {
  const schema = JSON.parse(readFileSync(resolve(webRoot, 'openapi/work-objects.openapi.json'), 'utf8'));
  expect(schema.components.schemas.OAWorkObjectView.required).toContain('oa_observation');
  expect(schema.components.schemas.InternalWorkObjectView.properties).not.toHaveProperty('oa_observation');
  expect(schema.components.schemas.WorkObjectListResponse.required).toContain('oa_sync');
  expect(Object.keys(schema.components.schemas.OASyncStatusView.properties).sort()).toEqual([
    'attempt_revision', 'failure_code', 'last_attempt_at', 'last_success_at', 'revision', 'status',
  ]);
  const parameter = schema.paths['/api/v1/work-objects'].get.parameters.find((item: { name: string }) => item.name === 'oa_view');
  expect(parameter.schema).toMatchObject({ default: 'active', enum: ['active', 'unconfirmed', 'all'] });
  expect(Object.keys(schema.paths)).not.toContain('/api/v1/work-objects/oa-completed');
});
