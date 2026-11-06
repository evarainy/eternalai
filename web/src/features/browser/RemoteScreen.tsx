/**
 * Live view of the server-side OA window, with single-owner takeover in a sealed handoff.
 *
 * The server enforces every rule (owner, single controller, input allowlist); this component
 * only maps pointer positions and forwards finished text. Copy is business language only.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Space } from 'antd';

export interface RemoteScreenProps {
  readonly taskId: string;
  readonly runId: string;
  /** The chat session the run belongs to; sent in the first socket message, never in the URL. */
  readonly sessionId: string;
}

type Phase = 'preparing' | 'filling' | 'handoff';
type Control = 'self' | 'other' | 'none' | 'pending';

interface ScreenDialog {
  readonly id: string;
  readonly kind: string;
  readonly message: string;
  readonly defaultValue: string;
}

const NAMED_KEYS = new Set(['Enter', 'Tab', 'Backspace', 'Delete', 'Escape', 'ArrowUp', 'ArrowDown',
  'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown']);
const TEXT_CHUNK = 2000;
const RECONNECT_MS = 2000;
const MAX_RECONNECTS = 5;
/** Pointer moves and wheel deltas are coalesced so the server's bounded input queue never fills. */
const MOVE_INTERVAL_MS = 50;

interface Viewport { readonly width: number; readonly height: number }

/** Page CSS pixels of a pointer position, using the viewport of the frame on screen. */
function toPagePoint(canvas: HTMLCanvasElement | null, viewport: Viewport | null,
  event: { clientX: number; clientY: number }): { x: number; y: number } | null {
  if (canvas === null || viewport === null) return null;
  const box = canvas.getBoundingClientRect();
  if (box.width <= 0 || box.height <= 0) return null;
  return {
    x: ((event.clientX - box.left) / box.width) * viewport.width,
    y: ((event.clientY - box.top) / box.height) * viewport.height,
  };
}

/** The allowlisted name for a key press, or null when it is not forwarded. */
function keyName(event: React.KeyboardEvent): string | null {
  if (event.altKey || event.metaKey) return null;
  if (event.ctrlKey) {
    if (event.shiftKey) return null;
    const letter = event.key.toLowerCase();
    return letter === 'a' || letter === 'z' ? `Control+${letter}` : null;
  }
  if (event.shiftKey && event.key.startsWith('Arrow')) return `Shift+${event.key}`;
  return NAMED_KEYS.has(event.key) ? event.key : null;
}

function socketUrl(taskId: string, runId: string): string {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${window.location.host}/api/v1/browser-runs/${encodeURIComponent(taskId)}`
    + `/${encodeURIComponent(runId)}/screen`;
}

function stateLine(phase: Phase | null, control: Control): string {
  if (control === 'self') return '您正在操作 OA 窗口，提交前请核对内容。';
  if (control === 'other') return '另一个页面正在操作 OA 窗口，这里只能查看。';
  if (control === 'pending') return '正在让机器停下，做完当前这一步后交给您操作…';
  if (phase === 'filling') return '机器正在填写报修单。需要时可点“接管操作”，机器做完当前一步就停下。';
  if (phase === 'handoff') return '报修单已交给您。点“接管操作”后可在这里直接操作 OA 窗口。';
  return '正在登录 OA、打开报修单，此时只能查看，登录完成后可接管。';
}

const takeFailures: Record<string, string> = {
  busy: '已有其他页面在操作 OA 窗口。',
  not_ready: 'OA 登录和打开报修单期间不能接管，请稍候再试。',
};

export function RemoteScreen({ taskId, runId, sessionId }: RemoteScreenProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  /** CSS viewport of the frame currently on the canvas; clicks map only with this one. */
  const viewportRef = useRef<Viewport | null>(null);
  const moveRef = useRef<{ x: number; y: number; dx: number; dy: number; wheel: boolean } | null>(null);
  const moveTimerRef = useRef<number | undefined>(undefined);
  const composingRef = useRef(false);
  const pressedRef = useRef(false);
  const [phase, setPhase] = useState<Phase | null>(null);
  const [control, setControl] = useState<Control>('none');
  const [attached, setAttached] = useState(false);
  const [dialog, setDialog] = useState<ScreenDialog | null>(null);
  const [prompt, setPrompt] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const [lost, setLost] = useState(false);

  const send = useCallback((message: Record<string, unknown>) => {
    const socket = socketRef.current;
    if (socket !== null && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
  }, []);

  useEffect(() => {
    let disposed = false;
    let reconnects = 0;
    let timer: number | undefined;
    let drawing = Promise.resolve();

    const draw = (data: ArrayBuffer) => {
      if (data.byteLength <= 8) return;
      const header = new DataView(data, 0, 8);
      const width = header.getUint32(0);
      const height = header.getUint32(4);
      const blob = new Blob([data.slice(8)], { type: 'image/jpeg' });
      // Frames draw in arrival order; the viewport travels with its own frame.
      drawing = drawing.then(async () => {
        const bitmap = await createImageBitmap(blob);
        const canvas = canvasRef.current;
        if (disposed || canvas === null) { bitmap.close(); return; }
        if (canvas.width !== bitmap.width) canvas.width = bitmap.width;
        if (canvas.height !== bitmap.height) canvas.height = bitmap.height;
        canvas.getContext('2d')?.drawImage(bitmap, 0, 0);
        bitmap.close();
        viewportRef.current = { width, height };
      }).catch(() => undefined);
    };

    const onMessage = (text: string) => {
      let data: unknown;
      try { data = JSON.parse(text); } catch { return; }
      if (typeof data !== 'object' || data === null) return;
      const item = data as Record<string, unknown>;
      if (item.type === 'state') {
        if (item.phase === 'preparing' || item.phase === 'filling' || item.phase === 'handoff') {
          setPhase(item.phase);
        }
        if (item.control === 'self' || item.control === 'other' || item.control === 'none'
            || item.control === 'pending') setControl(item.control);
        setAttached(item.attached === true);
      } else if (item.type === 'take_result') {
        if (item.ok === true) setNotice(null);
        else setNotice(takeFailures[String(item.reason)] ?? '接管没有成功，请稍后再试。');
      } else if (item.type === 'dialog' && typeof item.id === 'string') {
        setDialog({ id: item.id, kind: String(item.kind ?? ''), message: String(item.message ?? ''),
          defaultValue: String(item.default ?? '') });
        setPrompt(String(item.default ?? ''));
      } else if (item.type === 'dialog_closed') {
        setDialog(null);
      } else if (item.type === 'notice') {
        setNotice(item.code === 'new_window'
          ? 'OA 打开了新窗口，远程画面暂只显示报修单页面。'
          : '暂时无法显示 OA 窗口，请稍后重新打开。');
      }
    };

    const connect = () => {
      const socket = new WebSocket(socketUrl(taskId, runId));
      socket.binaryType = 'arraybuffer';
      socketRef.current = socket;
      socket.onopen = () => {
        reconnects = 0;
        setLost(false);
        socket.send(JSON.stringify({ type: 'init', session_id: sessionId }));
      };
      socket.onmessage = (event: MessageEvent<ArrayBuffer | string>) => {
        if (typeof event.data === 'string') onMessage(event.data);
        else draw(event.data);
      };
      socket.onclose = () => {
        if (socketRef.current === socket) socketRef.current = null;
        // A new connection always starts view-only; nothing typed earlier is replayed.
        setControl('none');
        setDialog(null);
        pressedRef.current = false;
        if (disposed) return;
        if (reconnects < MAX_RECONNECTS) {
          reconnects += 1;
          timer = window.setTimeout(connect, RECONNECT_MS);
        } else {
          setLost(true);
        }
      };
    };
    connect();
    return () => {
      disposed = true;
      if (timer !== undefined) window.clearTimeout(timer);
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [taskId, runId, sessionId]);

  const controlling = control === 'self';
  const pagePoint = (event: { clientX: number; clientY: number }) =>
    toPagePoint(canvasRef.current, viewportRef.current, event);

  const flushMove = useCallback(() => {
    moveTimerRef.current = undefined;
    const pending = moveRef.current;
    moveRef.current = null;
    if (pending === null) return;
    if (pending.wheel) send({ type: 'mouse', action: 'wheel', x: pending.x, y: pending.y, dx: pending.dx, dy: pending.dy });
    else send({ type: 'mouse', action: 'move', x: pending.x, y: pending.y });
  }, [send]);

  /** Latest pointer position wins; wheel deltas add up until the next send. */
  const queueMove = (point: { x: number; y: number }, wheel: boolean, dx = 0, dy = 0) => {
    const previous = moveRef.current;
    if (previous !== null && previous.wheel !== wheel) flushMove();
    const base = moveRef.current;
    moveRef.current = { ...point, wheel, dx: (base?.dx ?? 0) + dx, dy: (base?.dy ?? 0) + dy };
    if (moveTimerRef.current === undefined) {
      moveTimerRef.current = window.setTimeout(flushMove, MOVE_INTERVAL_MS);
    }
  };

  useEffect(() => () => {
    if (moveTimerRef.current !== undefined) window.clearTimeout(moveTimerRef.current);
  }, []);

  useEffect(() => {
    if (!controlling) return undefined;
    // A drag that leaves the picture still releases the button (the server clamps the point).
    const onUp = (event: MouseEvent) => {
      if (!pressedRef.current || event.button !== 0) return;
      pressedRef.current = false;
      flushMove();
      const point = toPagePoint(canvasRef.current, viewportRef.current, event);
      if (point !== null) send({ type: 'mouse', action: 'up', ...point });
    };
    window.addEventListener('mouseup', onUp);
    return () => window.removeEventListener('mouseup', onUp);
  }, [controlling, send, flushMove]);

  const flushText = () => {
    const box = inputRef.current;
    if (box === null || composingRef.current) return;
    const text = box.value;
    box.value = '';
    for (let start = 0; start < text.length; start += TEXT_CHUNK) {
      send({ type: 'text', text: text.slice(start, start + TEXT_CHUNK) });
    }
  };

  const canTake = attached && control === 'none' && (phase === 'filling' || phase === 'handoff');
  return (
    <div aria-label="OA 窗口画面">
      <p>{stateLine(phase, control)}</p>
      {notice !== null ? <Alert type="info" showIcon title={notice} /> : null}
      {lost ? <Alert type="warning" showIcon title="与 OA 窗口的连接已断开，请收起后重新打开。" /> : null}
      <Space>
        {controlling ? (
          <Button onClick={() => send({ type: 'release' })}>交还</Button>
        ) : (
          <Button type="primary" disabled={!canTake} onClick={() => {
            setNotice(null);
            send({ type: 'take' });
          }}>接管操作</Button>
        )}
      </Space>
      {dialog !== null ? (
        <Alert type="warning" showIcon title={`OA 窗口弹出提示：${dialog.message}`}
          description={controlling && dialog.kind === 'prompt'
            ? <Input value={prompt} maxLength={500} onChange={(event) => setPrompt(event.target.value)} />
            : controlling ? null : '接管操作后才能回应这个提示。'}
          action={controlling ? (
            <Space>
              <Button onClick={() => send({ type: 'dialog', id: dialog.id, accept: false })}>取消</Button>
              <Button type="primary" onClick={() => send({ type: 'dialog', id: dialog.id, accept: true,
                ...(dialog.kind === 'prompt' ? { text: prompt } : {}) })}>确定</Button>
            </Space>
          ) : undefined} />
      ) : null}
      <div style={{ position: 'relative', marginTop: 8 }}>
        <canvas ref={canvasRef}
          style={{ width: '100%', height: 'auto', display: 'block', background: '#f0f0f0',
            cursor: controlling ? 'default' : 'not-allowed', touchAction: 'none' }}
          onMouseDown={(event) => {
            if (!controlling || event.button !== 0) return;
            event.preventDefault();
            inputRef.current?.focus();
            const point = pagePoint(event);
            if (point === null) return;
            flushMove();
            pressedRef.current = true;
            send({ type: 'mouse', action: 'down', ...point });
          }}
          onMouseMove={(event) => {
            if (!controlling) return;
            const point = pagePoint(event);
            if (point !== null) queueMove(point, false);
          }}
          onWheel={(event) => {
            if (!controlling) return;
            const point = pagePoint(event);
            if (point !== null) queueMove(point, true, event.deltaX, event.deltaY);
          }}
          onContextMenu={(event) => event.preventDefault()} />
        {/* Hidden text sink: IME composition and paste land here and are sent once, as text. */}
        <textarea ref={inputRef} aria-label="向 OA 窗口输入"
          style={{ position: 'absolute', left: 0, top: 0, width: 1, height: 1, opacity: 0, border: 0,
            padding: 0, resize: 'none' }}
          disabled={!controlling}
          onCompositionStart={() => { composingRef.current = true; }}
          onCompositionEnd={() => { composingRef.current = false; flushText(); }}
          onInput={() => flushText()}
          onPaste={(event) => {
            event.preventDefault();
            const text = event.clipboardData.getData('text/plain');
            if (inputRef.current !== null) inputRef.current.value = text;
            flushText();
          }}
          onKeyDown={(event) => {
            if (composingRef.current || event.nativeEvent.isComposing) return;
            const name = keyName(event);
            if (name === null) return;
            event.preventDefault();
            send({ type: 'key', key: name });
          }} />
      </div>
    </div>
  );
}
