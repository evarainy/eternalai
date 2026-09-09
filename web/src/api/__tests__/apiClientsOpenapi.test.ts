import { spawnSync } from 'node:child_process';
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join, relative, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { UserActionOutcome as GeneratedOutcomes } from '../../generated/runtime/runtime.schemas';
import { TaskStatus as GeneratedTaskStatuses } from '../../generated/admin/admin.schemas';
import { USER_ACTION_OUTCOMES } from '../../contracts/userActionOutcome';

interface OpenApiSchema {
  format?: string;
  properties?: Record<string, OpenApiSchema>;
  writeOnly?: boolean;
}

interface OpenApiOperation {
  operationId?: string;
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
    project: 'auth',
    input: './openapi/auth.openapi.json',
    target: './src/generated/auth/auth.ts',
    operations: [
      {
        path: '/api/v1/auth/login',
        method: 'post',
        operationId: 'login_api_v1_auth_login_post',
      },
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
      {
        path: '/api/v1/work-objects',
        method: 'get',
        operationId: 'list_work_objects_api_v1_work_objects_get',
      },
      {
        path: '/api/v1/work-objects/sync',
        method: 'post',
        operationId: 'sync_work_objects_api_v1_work_objects_sync_post',
      },
      {
        path: '/api/v1/work-objects/{work_object_id}',
        method: 'get',
        operationId: 'get_work_object_api_v1_work_objects__work_object_id__get',
      },
      {
        path: '/api/v1/work-objects/{work_object_id}/handling-mark',
        method: 'patch',
        operationId:
          'set_work_object_handling_mark_api_v1_work_objects__work_object_id__handling_mark_patch',
      },
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

import base64
import copy
import json
import os
from pathlib import Path
import sys
from typing import Any

key = base64.b64encode(bytes(range(32))).decode("ascii")
os.environ.update({
    "ENV": "testing",
    "DATABASE_URL": "postgresql+psycopg://database.invalid/eternalai",
    "REDIS_URL": "redis://redis.invalid:6379/0",
    "OA_BASE_URL": "https://oa.invalid",
    "OA_CREDENTIAL_TTL_S": "3600",
    "SESSION_COOKIE_TTL_S": "3600",
    "CSRF_ALLOWED_ORIGINS": "https://testserver",
    "LLM_BASE_URL": "https://vllm.invalid/v1",
    "LLM_MODEL": "openapi-export",
    "ETERNALAI_CREDENTIAL_ENCRYPTION_KEY_B64": key,
    "ETERNALAI_IDENTITY_HMAC_KEY_B64": key,
    "ETERNALAI_SESSION_SIGNING_KEY_B64": key,
    "ETERNALAI_SESSION_BINDING_KEY_B64": key,
})

from app.main import create_app


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

function readOpenApi(path: string): OpenApiDocument {
  return JSON.parse(readFileSync(path, 'utf8')) as OpenApiDocument;
}

describe('FastAPI-derived Orval clients', () => {
  it(
    're-exports six FastAPI specs, copies curated Admin, and regenerates byte-identical clients',
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
        expect(configSource.match(/path: '\.\/src\/api\/mutator\.ts'/g)).toHaveLength(8);
        expect(configSource.match(/name: 'customInstance'/g)).toHaveLength(8);

        const exportTargets = PROJECTS.map((target) => ({
          filename: basename(target.input),
          operations: target.operations,
        }));

        run(
          process.platform === 'win32' ? 'uv.exe' : 'uv',
          [
            'run',
            'python',
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
          expect(readFileSync(regeneratedSpec)).toEqual(readFileSync(trackedSpec));

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
            expect(readFileSync(join(regeneratedDirectory, file))).toEqual(
              readFileSync(join(trackedDirectory, file)),
            );
          }
        }
      } finally {
        rmSync(temporaryRoot, { recursive: true, force: true });
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
